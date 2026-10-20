import asyncio
import threading
from app.services.screen_sessions import ScreenSessionCache
from app.websocket.monitoring import MonitoringConnectionManager


def test_video_frames_share_a_cached_lookup_and_invalidation_refreshes_it():
    calls = []
    current = [37]
    clock = [0.0]
    def load(device_id):
        calls.append(device_id)
        return current.copy()
    cache = ScreenSessionCache(load, clock=lambda: clock[0])
    async def scenario():
        for _ in range(60):
            assert await cache.active(6) == (37,)
        assert calls == [6]
        current.clear()
        cache.invalidate(6)
        assert await cache.active(6) == ()
        current.append(38)
        clock[0] += 2
        assert await cache.active(6) == (38,)
    asyncio.run(scenario())
    assert calls == [6, 6, 6]


def test_slow_screen_database_lookup_does_not_block_other_requests():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    def load(_):
        started.set()
        release.wait(timeout=2)
        finished.set()
        return [37]
    cache = ScreenSessionCache(load)
    async def scenario():
        task = asyncio.create_task(cache.active(6))
        try:
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.001)
            assert started.is_set()
            # The event loop can serve a health/login request before SQL ends.
            await asyncio.sleep(0)
            assert not finished.is_set()
        finally:
            release.set()
            await task
    asyncio.run(scenario())


def test_stop_during_lookup_does_not_restore_stale_authorization():
    started = threading.Event()
    release = threading.Event()
    calls = []
    def load(_):
        calls.append(1)
        if len(calls) == 1:
            started.set()
            release.wait(timeout=2)
            return [37]
        return []
    cache = ScreenSessionCache(load)
    async def scenario():
        task = asyncio.create_task(cache.active(6))
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.001)
        cache.invalidate(6)
        release.set()
        assert await task == ()
    asyncio.run(scenario())
    assert len(calls) == 2


def test_failed_video_viewer_does_not_prevent_delivery_to_healthy_viewer():
    class Viewer:
        def __init__(self, fail=False):
            self.fail = fail
            self.frames = []
            self.closed = False
        async def send_bytes(self, data):
            if self.fail:
                raise ConnectionError("viewer disconnected")
            self.frames.append(data)
        async def close(self, code):
            self.closed = True
    manager = MonitoringConnectionManager()
    good, bad = Viewer(), Viewer(fail=True)
    manager.connections[37] = {good, bad}
    asyncio.run(manager.broadcast_bytes(37, b"frame"))
    assert good.frames == [b"frame"]
    assert manager.connections[37] == {good}
    assert bad.closed
