from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.config import settings
from backend.models import TripRequest
from backend import weather


def payload():
    now = datetime.now(weather.SHANGHAI)
    return {'code': '200', 'updateTime': now.isoformat(), 'daily': [
        {'fxDate': str(now.date() + timedelta(days=i)), 'textDay': '多云', 'textNight': '晴',
         'tempMax': '26', 'tempMin': '18', 'windDirDay': '东', 'windDirNight': '东北',
         'windScaleDay': '1-3', 'windScaleNight': '1-3'} for i in range(7)]}


def enable_qweather(monkeypatch, response):
    monkeypatch.setattr(settings, 'qweather_key', 'test-private-qweather-key')
    monkeypatch.setattr(settings, 'qweather_host', 'test.qweatherapi.com')
    original = httpx.AsyncClient
    seen = []
    def handle(request):
        seen.append(request)
        assert request.url.host == 'test.qweatherapi.com'
        assert request.url.path == '/v7/weather/7d'
        assert request.headers['X-QW-Api-Key'] == settings.qweather_key
        assert 'key' not in request.url.params
        return response(request)
    monkeypatch.setattr(weather.httpx, 'AsyncClient', lambda **_: original(transport=httpx.MockTransport(handle)))
    return seen


@pytest.mark.asyncio
async def test_qweather_seven_days_sar_city_cache_and_uncovered_date(monkeypatch):
    seen = enable_qweather(monkeypatch, lambda _: httpx.Response(200, json=payload()))
    amap = SimpleNamespace(request=AsyncMock(return_value={'count': '1', 'districts': [
        {'name': '香港特别行政区', 'level': 'province', 'adcode': '810000', 'center': '114.171203,22.277468'}]}))
    service = weather.WeatherService()
    today = datetime.now(weather.SHANGHAI).date()
    request = TripRequest(destination='香港', start_date=today+timedelta(days=6), days=1)
    first = await service.forecast(request, amap)
    assert first.provider == 'qweather' and first.status == 'available'
    assert first.city == '香港特别行政区' and first.adcode == '810000'
    assert first.days[0].temp_max == 26 and first.days[0].temp_min == 18
    assert first.days[0].day_temp is None and first.days[0].night_temp is None
    assert seen[0].url.params['location'] == '114.17,22.28'
    assert settings.qweather_key not in first.model_dump_json()
    later = await service.forecast(request.model_copy(update={'destination': '香港特别行政区',
        'start_date': today+timedelta(days=7)}), amap)
    assert later.from_cache and later.status == 'not_covered'
    assert str(today+timedelta(days=6)) in later.message
    assert len(seen) == 1 and amap.request.await_count == 1
    first.days[0].temp_max = 90
    assert (await service.forecast(request, amap)).days[0].temp_max == 26


@pytest.mark.parametrize('mutation', ['expired', 'no_zone', 'duplicate', 'bad_code'])
def test_invalid_qweather_data_rejected(mutation):
    data = payload()
    if mutation == 'expired': data['updateTime'] = (datetime.now(weather.SHANGHAI)-timedelta(days=3)).isoformat()
    if mutation == 'no_zone': data['updateTime'] = datetime.now().isoformat()
    if mutation == 'duplicate': data['daily'].append(deepcopy(data['daily'][0]))
    if mutation == 'bad_code': data['code'] = '403'
    with pytest.raises(ValueError):
        weather.parse_qweather({'name': '西安市', 'adcode': '610100'}, data, datetime.now(weather.SHANGHAI))


@pytest.mark.asyncio
async def test_qweather_auth_failure_never_leaks_key_or_caches_failure(monkeypatch):
    enable_qweather(monkeypatch, lambda _: httpx.Response(403, json={'code': '403'}))
    amap = SimpleNamespace(request=AsyncMock(return_value={'count':'1','districts':[
        {'name':'西安市','level':'city','adcode':'610100','center':'108.94,34.34'}]}))
    service = weather.WeatherService()
    result = await service.forecast(TripRequest(destination='西安',start_date=datetime.now(weather.SHANGHAI).date()),amap)
    assert result.provider == 'qweather' and result.status == 'unavailable' and not service.cache
    assert settings.qweather_key not in result.model_dump_json()


def test_weather_error_redaction_includes_qweather_key(monkeypatch):
    monkeypatch.setattr(settings, 'qweather_key', 'qweather-test-secret')
    assert 'qweather-test-secret' not in settings.safe_error(RuntimeError('qweather-test-secret'))
