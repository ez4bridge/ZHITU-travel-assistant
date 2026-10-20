import asyncio
import time
from unittest.mock import AsyncMock

import httpx
import pytest

import backend.amap as module
from backend.amap import Amap, MapServiceError
from backend.config import settings
from backend.map_rate_limit import MapRateLimiter
from test_core import sample


class Clock:
    now = 0.0

    def __init__(self):
        self.sleeps = []

    async def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_two_planners_share_endpoint_pacing(monkeypatch):
    monkeypatch.setattr(module, 'map_rate_limiter', MapRateLimiter())
    monkeypatch.setattr(settings, 'amap_key', 'test-secret')
    monkeypatch.setattr(settings, 'amap_qps', 20)
    first, second = Amap('上海'), Amap('北京')
    stamps = []
    async def respond(request):
        stamps.append(time.monotonic())
        return httpx.Response(200, json={'status': '1'})
    try:
        for client in (first, second):
            await client.client.aclose()
            client.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        await asyncio.gather(*(client.request('/v3/place/text', {}) for client in (first, second) for _ in range(3)))
        assert len(stamps) == 6
        # Six shared slots need five intervals; tolerate transport setup jitter.
        # Per-planner limiting would incorrectly complete both triples in ~0.1s.
        assert stamps[-1] - stamps[0] >= .20
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_different_interfaces_have_independent_budgets():
    clock = Clock()
    limiter = MapRateLimiter(clock=lambda: clock.now, sleep=clock.sleep)
    await limiter.wait('place', 2)
    await limiter.wait('walking', 2)
    assert clock.now == 0
    await limiter.wait('place', 2)
    assert clock.now == .5


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_reserve_future_slots():
    limiter = MapRateLimiter()
    await limiter.wait('place', 2)
    task = asyncio.create_task(limiter.wait('place', 2))
    await asyncio.sleep(0)
    before = limiter._next['place']
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert limiter._next['place'] == before


async def mock_amap(monkeypatch, responses):
    clock = Clock()
    monkeypatch.setattr(module, 'map_rate_limiter', MapRateLimiter(clock=lambda: clock.now, sleep=clock.sleep))
    monkeypatch.setattr(settings, 'amap_key', 'test-secret')
    amap = Amap('上海')
    await amap.client.aclose()
    calls = []
    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=responses.pop(0))
    amap.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    return amap, calls, clock


@pytest.mark.asyncio
async def test_qps_limit_recovers_after_two_bounded_retries(monkeypatch, caplog):
    amap, calls, clock = await mock_amap(monkeypatch, [
        {'status': '0', 'infocode': '10021'}, {'status': '0', 'infocode': '10021'}, {'status': '1'}])
    try:
        assert (await amap.request('/v3/place/text', {}))['status'] == '1'
        assert len(calls) == 3 and clock.now >= 3
        assert amap.fatal_error is None
        assert '10021' in caplog.text and 'test-secret' not in caplog.text
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_exhausted_qps_retry_does_not_poison_other_interfaces(monkeypatch):
    amap, calls, _ = await mock_amap(monkeypatch, [*([{'status': '0', 'infocode': '10021'}] * 3), {'status': '1'}])
    try:
        with pytest.raises(MapServiceError) as error:
            await amap.request('/v3/direction/driving', {})
        assert error.value.code == 'map_rate_limit' and not error.value.fatal
        assert error.value.provider_code == '10021'
        assert (await amap.request('/v3/direction/walking', {}))['status'] == '1'
        assert len(calls) == 4
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_http429_also_has_bounded_recovery(monkeypatch):
    amap, _, clock = await mock_amap(monkeypatch, [])
    calls = []
    await amap.client.aclose()
    def respond(request):
        calls.append(request)
        return httpx.Response(429 if len(calls) < 3 else 200, json={'status': '1'})
    amap.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        await amap.request('/v3/place/text', {})
        assert len(calls) == 3 and clock.now >= 3
    finally:
        await amap.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_code', ['10003', '10044', '40000'])
async def test_daily_quota_is_not_retried_and_is_scoped_to_interface(monkeypatch, provider_code):
    amap, calls, _ = await mock_amap(monkeypatch, [{'status': '0', 'infocode': provider_code}, {'status': '1'}])
    try:
        for _ in range(2):
            with pytest.raises(MapServiceError) as error:
                await amap.request('/v3/direction/driving', {})
            assert error.value.code == 'map_quota' and error.value.fatal
        assert calls == ['/v3/direction/driving']
        await amap.request('/v3/direction/walking', {})
        assert len(calls) == 2
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_driving_and_taxi_coalesce_concurrent_wire_requests():
    amap = Amap('上海')
    amap.request = AsyncMock(return_value={'route': {'paths': [{'distance': '1000', 'duration': '300',
        'steps': [{'polyline': '121.49,31.23;121.49,31.22'}]}]}})
    try:
        driving, taxi = await asyncio.gather(amap.route([121.49,31.23], [121.49,31.22], 'driving'),
                                            amap.route([121.49,31.23], [121.49,31.22], 'taxi'))
        assert amap.request.await_count == 1
        assert driving['polyline'] == taxi['polyline'] and driving['note'] != taxi['note']
    finally:
        await amap.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('category,fatal', [('map_rate_limit', False), ('map_quota', True)])
async def test_optional_failure_retains_main_route(category, fatal):
    _, itinerary, routes = sample()
    amap = Amap('上海')
    amap.resolve = AsyncMock()
    async def route(origin, destination, mode):
        if mode == 'walking':
            return {'duration_s': 1200, 'distance_m': 1800, 'polyline': routes[0].polyline}
        raise MapServiceError(category, '受限', fatal)
    amap.route = route
    try:
        enriched = await amap.enrich(itinerary, 'walking', compare_modes=True)
        assert enriched[0].available
        assert len(enriched[0].alternatives) == 5
        assert all(not option.available for option in enriched[0].alternatives if option.mode != 'walking')
    finally:
        await amap.close()
