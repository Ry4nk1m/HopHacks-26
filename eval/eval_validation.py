"""Measure how well the photo checker agrees with your own labels.

Put real photos in folders:

    eval/photos/<flag_type>/<label>/*.jpg

    tree_water, drain_clear, cooling_check:   label = pass | fail     (task really done, or not)
    flood_report, problem_report:             label = present | absent (problem visible, or not)

Then run (uses the real Gemini key from .env, so keep the sets small):

    .venv/bin/python -m eval.eval_validation --dir eval/photos
"""
import argparse
import json
import sys
from pathlib import Path

from app import photos
from app.config import load_settings
from app.missions import decide
from app.rules import FLAG_TYPES
from app.validation import ValidatorUnavailable, make_validator

POSITIVE = {"pass", "present"}


def classify(validator, flag_type, jpeg, ctx=None, lang="en"):
    """Returns (predicted_positive, outcome) for one photo, mirroring the app's own decision logic."""
    purpose = FLAG_TYPES[flag_type]["purpose"]
    verdict = validator.check(purpose, flag_type, [jpeg], ctx or {}, lang)
    outcome, _ = decide(verdict, purpose, False)
    if purpose == "confirm":
        positive = outcome == "verified" and verdict.problem_present is True
    else:
        positive = outcome == "verified"
    return positive, outcome


def summarize(results):
    """results: list of (label, predicted_positive, outcome) -> metrics dict."""
    tp = sum(1 for l, p, _ in results if l in POSITIVE and p)
    fn = sum(1 for l, p, _ in results if l in POSITIVE and not p)
    fp = sum(1 for l, p, _ in results if l not in POSITIVE and p)
    tn = sum(1 for l, p, _ in results if l not in POSITIVE and not p)
    n = len(results)
    return {
        "n": n, "accuracy": (tp + tn) / n if n else None,
        "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
        "false_accepts": fp, "false_rejects": fn, "sent_to_review": sum(1 for _, _, o in results if o == "pending"),
    }


# Run the validator over a folder of labeled photos and print accuracy/precision/recall per flag type.
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="eval/photos")
    ap.add_argument("--json", help="also write the raw results to this file")
    args = ap.parse_args(argv)

    settings = load_settings()
    validator = make_validator(settings)
    print(f"validator: {validator.name}" + ("  (simulated: results are meaningless, add a Gemini key)" if validator.name == "mock" else f"  model: {settings.gemini_model}"))
    root = Path(args.dir)
    if not root.exists():
        sys.exit(f"{root} does not exist; see the docstring for the folder layout")

    # Walk each flag_type/label folder, run the validator on every photo, and record predicted vs actual.
    per_type, raw = {}, []
    for type_dir in sorted(p for p in root.iterdir() if p.is_dir() and p.name in FLAG_TYPES):
        for label_dir in sorted(p for p in type_dir.iterdir() if p.is_dir()):
            for img in sorted(list(label_dir.glob("*.jpg")) + list(label_dir.glob("*.jpeg")) + list(label_dir.glob("*.png"))):
                try:
                    photo = photos.process_upload(img.read_bytes())
                    positive, outcome = classify(validator, type_dir.name, photo.jpeg)
                except photos.PhotoError as exc:
                    positive, outcome = False, f"unusable photo ({exc.code})"
                except ValidatorUnavailable as exc:
                    print(f"  skipped {img.name}: {str(exc)[:80]}")
                    continue
                per_type.setdefault(type_dir.name, []).append((label_dir.name, positive, outcome))
                raw.append({"type": type_dir.name, "label": label_dir.name, "file": img.name, "predicted_positive": positive, "outcome": outcome})

    if not per_type:
        sys.exit("no photos found")
    pct = lambda v: "n/a" if v is None else f"{100 * v:.0f}%"
    print(f"\n{'flag type':16}{'photos':>7}{'accuracy':>10}{'precision':>11}{'recall':>8}{'false+':>8}{'false-':>8}{'review':>8}")
    for ftype, res in per_type.items():
        m = summarize(res)
        print(f"{ftype:16}{m['n']:>7}{pct(m['accuracy']):>10}{pct(m['precision']):>11}{pct(m['recall']):>8}{m['false_accepts']:>8}{m['false_rejects']:>8}{m['sent_to_review']:>8}")
    if args.json:
        Path(args.json).write_text(json.dumps(raw, indent=2))


if __name__ == "__main__":
    main()
