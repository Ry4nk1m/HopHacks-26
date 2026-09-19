# Handles mission photo uploads: checking, resizing, hashing, saving, and cleanup.

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np

MIN_SIDE = 320
MAX_SIDE = 1600
BLUR_MIN = 20.0
NEAR_DUPLICATE_BITS = 4


# Error raised when an uploaded photo fails a check (too small, blurry, etc).
class PhotoError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# Holds a processed photo and the info computed about it.
@dataclass
class Photo:
    jpeg: bytes
    sha256: str
    ahash: str
    sharpness: float
    width: int
    height: int


# Make a short fingerprint of an image's look, used to spot near-duplicate photos.
def average_hash(img):
    small = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (8, 8), interpolation=cv2.INTER_AREA)
    bits = (small > small.mean()).flatten()
    return "".join("1" if b else "0" for b in bits)


# Count how many bits differ between two hashes (used to compare average_hash results).
def hamming(a, b):
    return sum(x != y for x, y in zip(a, b))


def process_upload(data, blur_min=BLUR_MIN):
    """Decode, sanity-check and re-encode a photo. Re-encoding drops EXIF metadata (GPS, device info)."""
    if not data:
        raise PhotoError("bad_photo")
    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise PhotoError("bad_photo")
    h, w = img.shape[:2]
    if min(h, w) < MIN_SIDE:
        raise PhotoError("bad_photo")
    scale = MAX_SIDE / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        h, w = img.shape[:2]
    probe = img
    if max(h, w) > 640:
        s = 640 / max(h, w)
        probe = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    sharp = float(cv2.Laplacian(cv2.cvtColor(probe, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
    if sharp < blur_min:
        raise PhotoError("too_blurry")
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        raise PhotoError("bad_photo")
    jpeg = buf.tobytes()
    return Photo(jpeg, hashlib.sha256(jpeg).hexdigest(), average_hash(img), round(sharp, 1), w, h)


# Save a photo's bytes to disk under a dated folder and return its relative path.
def save_photo(settings, jpeg):
    now = datetime.now(timezone.utc)
    rel = f"photos/{now:%Y/%m}/{uuid.uuid4().hex}.jpg"
    path = settings.data_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(jpeg)
    return rel


def find_duplicate(conn, photo, flag_id=None, user_id=None):
    """Exact re-uploads are always duplicates. A visually near-identical image counts only when it was used for a
    different flag, or by the same person, so two volunteers photographing the same drain are not blocked."""
    if conn.execute("SELECT 1 FROM photo_hashes WHERE sha256=?", (photo.sha256,)).fetchone():
        return True
    for r in conn.execute("SELECT ahash, flag_id, user_id FROM photo_hashes"):
        if hamming(photo.ahash, r["ahash"]) <= NEAR_DUPLICATE_BITS and (r["flag_id"] != flag_id or (user_id is not None and r["user_id"] == user_id)):
            return True
    return False


# Store a photo's hashes in the database so future uploads can be checked against it.
def remember_photo(conn, photo, user_id, flag_id, now_iso):
    conn.execute("INSERT OR IGNORE INTO photo_hashes (sha256, ahash, user_id, flag_id, created_at) VALUES (?,?,?,?,?)",
                 (photo.sha256, photo.ahash, user_id, flag_id, now_iso))


def purge_old_photos(settings, conn):
    """Delete stored photo files past the retention window (pending reviews are kept). Returns files removed."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=settings.photo_retention_days)).isoformat(timespec="seconds")
    removed = 0
    for table, col, where in (
        ("missions", "photo_path", "created_at < ? AND status != 'pending'"),
        ("missions", "before_path", "created_at < ? AND status != 'pending'"),
        ("flags", "photo_path", "created_at < ?"),
    ):
        for r in conn.execute(f"SELECT rowid AS rid, {col} AS p FROM {table} WHERE {col} IS NOT NULL AND {where}", (cutoff,)).fetchall():
            f = settings.data_dir / r["p"]
            if f.exists():
                f.unlink()
                removed += 1
            conn.execute(f"UPDATE {table} SET {col}=NULL WHERE rowid=?", (r["rid"],))
    return removed
