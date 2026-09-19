import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """In-memory sliding window; good enough for a single-process deployment."""

    def __init__(self, clock=time.monotonic):
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()
        self._clock = clock

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

    def reset(self):
        with self._lock:
            self._hits.clear()
