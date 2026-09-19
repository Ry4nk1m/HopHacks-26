# In-process pub/sub used to push live flag updates to connected browsers over
# Server-Sent Events. Only meant for a single-process deployment (see Dockerfile: one uvicorn worker).

import asyncio
import json


class Broadcaster:
    def __init__(self):
        self._subscribers = set()
        self._loop = None

    # Call once, from an async context, so publish() (called from worker threads) can hand
    # events back to the event loop that owns the subscriber queues.
    def bind_loop(self, loop):
        self._loop = loop

    def subscribe(self):
        q = asyncio.Queue(maxsize=32)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q):
        self._subscribers.discard(q)

    @staticmethod
    def _put_nowait(q, payload):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass  # a slow client can miss a ping; it'll catch up on its next poll

    # Thread-safe: FastAPI runs sync route handlers in a worker thread, so this may be
    # called off the event loop.
    def publish(self, event):
        if not self._loop or not self._subscribers:
            return
        payload = json.dumps(event)
        for q in list(self._subscribers):
            self._loop.call_soon_threadsafe(self._put_nowait, q, payload)
