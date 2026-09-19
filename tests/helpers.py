# Shared test helpers for building fake photos and a scriptable fake validator.
import cv2
import numpy as np

from app.validation import ValidatorUnavailable, Verdict


# Generate a random JPEG image of the given size, used as a fake photo upload.
def photo_bytes(seed=0, size=(480, 640)):
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 255, (size[0], size[1], 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    assert ok
    return buf.tobytes()


# Generate a heavily blurred JPEG, used to test blur detection.
def blurry_photo():
    rng = np.random.default_rng(3)
    img = cv2.GaussianBlur(rng.integers(0, 255, (480, 640, 3), dtype=np.uint8), (0, 0), 40)
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()


class Scripted:
    """Validator whose verdict tests control directly."""

    name = "scripted"

    # Start with a default valid verdict, then apply any overrides.
    def __init__(self, **verdict):
        base = dict(subject_ok=True, task_done=True, problem_present=True, confidence=0.9, reasoning="Looks right.")
        base.update(verdict)
        self.verdict = Verdict(**base)
        self.calls = []
        self.unavailable = False

    # Update the current verdict with new field values.
    def set(self, **verdict):
        base = self.verdict.model_dump()
        base.update(verdict)
        self.verdict = Verdict(**base)

    # Record the call and return the scripted verdict, or raise if marked unavailable.
    def check(self, purpose, flag_type, images, ctx, lang):
        self.calls.append({"purpose": purpose, "flag_type": flag_type, "n_images": len(images), "ctx": ctx, "lang": lang})
        if self.unavailable:
            raise ValidatorUnavailable("down")
        return self.verdict
