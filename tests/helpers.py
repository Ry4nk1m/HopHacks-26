import cv2
import numpy as np

from app.validation import ValidatorUnavailable, Verdict


def photo_bytes(seed=0, size=(480, 640)):
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 255, (size[0], size[1], 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    assert ok
    return buf.tobytes()


def blurry_photo():
    rng = np.random.default_rng(3)
    img = cv2.GaussianBlur(rng.integers(0, 255, (480, 640, 3), dtype=np.uint8), (0, 0), 40)
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()


class Scripted:
    """Validator whose verdict tests control directly."""

    name = "scripted"

    def __init__(self, **verdict):
        base = dict(subject_ok=True, task_done=True, problem_present=True, confidence=0.9, reasoning="Looks right.")
        base.update(verdict)
        self.verdict = Verdict(**base)
        self.calls = []
        self.unavailable = False

    def set(self, **verdict):
        base = self.verdict.model_dump()
        base.update(verdict)
        self.verdict = Verdict(**base)

    def check(self, purpose, flag_type, images, ctx, lang):
        self.calls.append({"purpose": purpose, "flag_type": flag_type, "n_images": len(images), "ctx": ctx, "lang": lang})
        if self.unavailable:
            raise ValidatorUnavailable("down")
        return self.verdict
