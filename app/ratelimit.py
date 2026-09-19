# Simple rate limiter that tracks how many times each key was hit recently.

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """In-memory sliding window; good enough for a single-process deployment."""

    def __init__(self, clock=time.monotonic):
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()
        self._clock = clock

    # Check if a key is still allowed within its limit for the given time window.
    # Drops old hits outside the window, then records this hit if under the limit.
    def allow(self, key, limit, window_s):
        now = self._clock()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    # Clear all recorded hits for every key.
    def reset(self):
        with self._lock:
            self._hits.clear()
