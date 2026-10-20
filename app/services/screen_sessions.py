"""Keep synchronous database reads out of the per-frame ASGI event loop."""
import asyncio
import time


class ScreenSessionCache:
    def __init__(self, load, ttl=1.0, clock=time.monotonic):
        self.load = load
        self.ttl = ttl
        self.clock = clock
        self.entries = {}
        self.generations = {}

    def invalidate(self, device_id):
        self.entries.pop(device_id, None)
        self.generations[device_id] = self.generations.get(device_id, 0) + 1

    def clear(self):
        self.entries.clear()
        self.generations.clear()

    async def active(self, device_id):
        while True:
            cached = self.entries.get(device_id)
            if cached and cached[0] > self.clock():
                return cached[1]
            generation = self.generations.get(device_id, 0)
            # The loader owns and closes its database session in this thread.
            session_ids = tuple(await asyncio.to_thread(self.load, device_id))
            if generation != self.generations.get(device_id, 0):
                # A stop/start happened while SQL was in flight. Do not publish
                # the stale authorization result back into the frame cache.
                continue
            self.entries[device_id] = (self.clock() + self.ttl, session_ids)
            return session_ids
