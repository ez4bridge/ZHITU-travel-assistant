"""Process-wide pacing shared by planning tasks, independent of event-loop ownership."""
import asyncio
import threading
import time


class MapRateLimiter:
    def __init__(self, clock=time.monotonic, sleep=asyncio.sleep):
        self._clock = clock
        self._sleep = sleep
        self._next = {}
        self._lock = threading.Lock()

    async def wait(self, service: str, qps: float) -> None:
        while True:
            with self._lock:
                now = self._clock()
                delay = self._next.get(service, 0) - now
                if delay <= 0:
                    self._next[service] = now + 1 / qps
                    return
            # Do not reserve future slots: cancellation must not delay other requests.
            await self._sleep(delay)

    def cooldown(self, service: str, seconds: float) -> None:
        with self._lock:
            self._next[service] = max(self._next.get(service, 0), self._clock() + seconds)


map_rate_limiter = MapRateLimiter()
