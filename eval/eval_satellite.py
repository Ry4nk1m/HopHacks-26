"""Does the satellite priority actually predict real problems?

Volunteers answer "Did this really need attention?" (yes / no / not sure) on every mission. This script joins those
answers with the satellite-based need score stored on each flag and reports the share of "yes" answers by need tercile.
If the satellite signal is useful, the top tercile should have a clearly higher "yes" rate than the bottom one.

    .venv/bin/python -m eval.eval_satellite
"""
import json

from app import db
from app.config import load_settings


def tercile_table(rows):
    """rows: [(need_score, was_problem)] -> {tercile: {"n", "yes", "no", "precision"}} (tercile 3 = highest need)."""
    answered = sorted((r for r in rows if r[1] in ("yes", "no") and r[0] is not None), key=lambda r: r[0])
    out = {}
    if not answered:
        return out
    size = max(len(answered) / 3, 1)
    for i, (need, ans) in enumerate(answered):
        t = min(int(i / size) + 1, 3)
        b = out.setdefault(t, {"n": 0, "yes": 0, "no": 0})
        b["n"] += 1
        b[ans] += 1
    for b in out.values():
        b["precision"] = b["yes"] / b["n"]
    return out


# Pull every answered mission (yes/no) with its flag's satellite need score and flag type.
def collect(conn):
    rows = []
    for r in conn.execute("SELECT m.was_problem, f.context, f.type FROM missions m JOIN flags f ON f.id=m.flag_id "
                          "WHERE m.status IN ('verified','pending') AND m.was_problem IS NOT NULL"):
        need = (json.loads(r["context"] or "{}")).get("need")
        rows.append((need, r["was_problem"], r["type"]))
    return rows


# Load real mission answers, split tree missions into need terciles, and print the precision table.
def main():
    settings = load_settings()
    with db.connect(settings.data_dir / "app.db") as conn:
        rows = collect(conn)
    trees = [(n, a) for n, a, t in rows if t == "tree_water"]
    print(f"answered missions: {len(rows)} (tree missions with a satellite score: {sum(1 for n, _ in trees if n is not None)})")
    table = tercile_table(trees)
    if not table:
        print("no answered tree missions yet; do some walking first")
        return
    print(f"{'need tercile':14}{'answers':>9}{'yes':>6}{'no':>6}{'said it needed water':>24}")
    for t in sorted(table):
        b = table[t]
        print(f"{['low', 'middle', 'high'][t - 1]:14}{b['n']:>9}{b['yes']:>6}{b['no']:>6}{100 * b['precision']:>23.0f}%")


if __name__ == "__main__":
    main()
