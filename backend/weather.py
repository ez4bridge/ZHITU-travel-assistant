"""Server-owned daily forecasts; only public city weather is cached across requests."""
import asyncio
from collections import OrderedDict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import math
import time
import unicodedata

import httpx
from .config import settings
from .models import TripRequest, WeatherDay, WeatherSnapshot
from .amap import MapServiceError
from .administrative import AdministrativeMatchError, resolve_division, select_division

SHANGHAI = timezone(timedelta(hours=8))
QWEATHER_SOURCE = 'https://dev.qweather.com/docs/api/weather/weather-daily-forecast/'
WEATHER_FAILURES = {
    'timeout': ('天气查询超时，可稍后重试天气，无需重新生成行程。', True),
    'network': ('天气服务连接失败，可检查网络后重试天气。', True),
    'rate_limit': ('天气查询暂时限流，请稍后再试。', True),
    'configuration': ('天气服务鉴权或权限不足，请先检查服务配置，再重新查询。', False),
    'quota': ('天气服务配额或余额不足，请先检查服务控制台；立即重试可能仍会失败。', False),
    'city_not_found': ('无法唯一匹配目的地行政区，请核对城市名称；省份不能代替具体城市。', False),
    'invalid_data': ('天气服务未返回可用预报，可稍后重试；未使用其他日期或城市的天气代替。', True),
    'unknown': ('天气查询未成功，暂未确定具体原因，可稍后重试。', True),
}


class WeatherFailure(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(WEATHER_FAILURES[code][0])


def failure_code(error: Exception) -> str:
    if isinstance(error, AdministrativeMatchError):
        return 'city_not_found'
    if isinstance(error, WeatherFailure):
        return error.code
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return 'timeout'
    if isinstance(error, httpx.RequestError):
        return 'network'
    if isinstance(error, MapServiceError):
        return {'map_network': 'network', 'map_rate_limit': 'rate_limit', 'map_quota': 'quota',
                'map_auth': 'configuration', 'map_permission': 'configuration', 'map_ip_limit': 'configuration'}.get(error.code, 'invalid_data')
    if isinstance(error, httpx.HTTPStatusError):
        return 'configuration' if error.response.status_code in (401, 403) else 'quota' if error.response.status_code == 402 else 'rate_limit' if error.response.status_code == 429 else 'invalid_data'
    return 'invalid_data' if isinstance(error, (ValueError, KeyError, TypeError)) else 'unknown'
WEATHER_GUIDANCE = '''天气快照是第三方API日级预报，不是场馆开放公告，也不是历史原文。
temp_max/temp_min是全天最高/最低气温，不能改称白天/夜间气温；day_temp/night_temp才对应白天/夜间。
只使用状态available的出行日期，不补写未覆盖日期的天气，不将某天预报套到别的日期。
雨雪天气优先减少长时间露天停留，在地点资料支持时安排主题相关室内参观；冷热天气适当休息。
保留用户明确要求的地点、主题、预算与结束时间；无法避雨时说明，不自动取消必去地点。
不推断精确小时降雨、降水概率、官方预警、闭馆或交通停运。未知是否室内时不作确定声明。
有可用预报时，在现有摘要或tips中简短说明天气如何影响安排；已经合适的安排无需强行改变。
天气不可用或未覆盖时，明确未据此调整，不编造天气。天气数字以快照为准，不根据常识改写。
修改时只以本次快照为天气依据，旧方案摘要或tips中的旧天气描述不能直接沿用。'''


def normalize_city(city: str) -> str:
    return unicodedata.normalize('NFKC', city).strip().removesuffix('市').removesuffix('特别行政区').removesuffix('特別行政區')


def requested_days(request: TripRequest) -> list[date]:
    return [request.start_date + timedelta(days=i) for i in range(request.days)]


def resolve_district(city: str, data: dict) -> dict:
    return select_division(city, data)


def bounded_text(value, length=40) -> str:
    return value.strip()[:length] if isinstance(value, str) else ''


def temperature(value) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and -100 <= number <= 80 else None
    except (ValueError, TypeError):
        return None


def parse_forecast(district: dict, data: dict, now: datetime) -> dict:
    rows = [f for f in data.get('forecasts', []) if f.get('adcode') == district['adcode']]
    if len(rows) != 1:
        raise ValueError('天气结果与目的地不一致')
    row = rows[0]
    issued = datetime.strptime(row.get('reporttime', ''), '%Y-%m-%d %H:%M:%S').replace(tzinfo=SHANGHAI)
    if not -600 <= (now - issued).total_seconds() <= 86400:
        raise ValueError('天气发布时间已过期或无效')
    days = {}
    for cast in row.get('casts', []):
        try:
            day = date.fromisoformat(cast['date'])
        except (ValueError, KeyError, TypeError):
            continue
        # This endpoint is a forecast, never a historical-weather data source.
        if day < now.astimezone(SHANGHAI).date():
            continue
        item = WeatherDay(date=day, day_weather=bounded_text(cast.get('dayweather')),
            night_weather=bounded_text(cast.get('nightweather')), day_temp=temperature(cast.get('daytemp')),
            night_temp=temperature(cast.get('nighttemp')), day_wind=bounded_text(cast.get('daywind')),
            night_wind=bounded_text(cast.get('nightwind')), day_power=bounded_text(cast.get('daypower')),
            night_power=bounded_text(cast.get('nightpower')))
        item.status = 'available' if item.day_weather or item.night_weather else 'unavailable'
        if day in days:
            raise ValueError('同一天返回重复天气，无法确认')
        days[day] = item
    if not days:
        raise ValueError('未返回有效预报')
    return {'city': district['name'], 'adcode': district['adcode'], 'issued_at': issued,
            'fetched_at': now, 'days': days}


async def fetch_qweather(district: dict, now: datetime) -> dict:
    # Coordinates come from the uniquely matched administrative division, never the model.
    parts = district.get('center', '').split(',')
    if len(parts) != 2:
        raise ValueError('行政区没有有效中心坐标')
    lon, lat = map(float, parts)
    if not math.isfinite(lon) or not math.isfinite(lat) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError('行政区坐标无效')
    async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
        response = await client.get(f'https://{settings.qweather_host}/v7/weather/7d',
            params={'location': f'{lon:.2f},{lat:.2f}', 'lang': 'zh', 'unit': 'm'},
            headers={'X-QW-Api-Key': settings.qweather_key})
        response.raise_for_status()
        return parse_qweather(district, response.json(), datetime.now(SHANGHAI))


def parse_qweather(district: dict, data: dict, now: datetime) -> dict:
    if str(data.get('code')) != '200':
        code = str(data.get('code'))
        raise WeatherFailure('configuration' if code in ('401', '403') else 'quota' if code == '402' else 'rate_limit' if code == '429' else 'invalid_data')
    issued = datetime.fromisoformat(data.get('updateTime', '').replace('Z', '+00:00'))
    if issued.tzinfo is None or not -600 <= (now - issued).total_seconds() <= 86400:
        raise ValueError('天气发布时间已过期或无效')
    days = {}
    for row in data.get('daily', []):
        try:
            day = date.fromisoformat(row['fxDate'])
        except (ValueError, KeyError, TypeError):
            continue
        if day < now.astimezone(SHANGHAI).date():
            continue
        if day in days:
            raise ValueError('同一天返回重复天气，无法确认')
        item = WeatherDay(date=day, day_weather=bounded_text(row.get('textDay')),
            night_weather=bounded_text(row.get('textNight')), temp_max=temperature(row.get('tempMax')),
            temp_min=temperature(row.get('tempMin')), day_wind=bounded_text(row.get('windDirDay')),
            night_wind=bounded_text(row.get('windDirNight')), day_power=bounded_text(row.get('windScaleDay')),
            night_power=bounded_text(row.get('windScaleNight')))
        item.status = 'available' if item.day_weather or item.night_weather else 'unavailable'
        days[day] = item
    if not days:
        raise ValueError('未返回有效预报')
    return {'provider': 'qweather', 'source_url': QWEATHER_SOURCE, 'city': district['name'],
        'adcode': district['adcode'], 'issued_at': issued, 'fetched_at': now, 'days': days}


def snapshot(request: TripRequest, value: dict, from_cache: bool) -> WeatherSnapshot:
    today = datetime.now(SHANGHAI).date()
    days = [deepcopy(value['days'].get(day, WeatherDay(date=day))) if day >= today
            else WeatherDay(date=day) for day in requested_days(request)]
    available = sum(day.status == 'available' for day in days)
    status = 'available' if available == len(days) else 'partial' if available else (
        'not_covered' if all(day.status == 'not_covered' for day in days) else 'unavailable')
    messages = {'available': '已取得本次出行日期的天气预报', 'partial': '部分出行日期暂无有效预报',
                'not_covered': '该日期暂无预报', 'unavailable': '天气暂不可用，本次未据此调整'}
    if status == 'not_covered':
        messages[status] = f"当前预报覆盖 {min(value['days'])} 至 {max(value['days'])}，所选日期尚未覆盖"
    return WeatherSnapshot(provider=value.get('provider', 'amap'),
        source_url=value.get('source_url', WeatherSnapshot.model_fields['source_url'].default),
        requested_city=request.destination, city=value['city'], adcode=value['adcode'],
        status=status, message=messages[status], issued_at=value['issued_at'], fetched_at=value['fetched_at'],
        from_cache=from_cache, days=days)


def unavailable(request: TripRequest, now: datetime, provider: str = 'amap', reason: str = 'unknown') -> WeatherSnapshot:
    message, retryable = WEATHER_FAILURES[reason]
    return WeatherSnapshot(provider=provider,
        source_url=QWEATHER_SOURCE if provider == 'qweather' else WeatherSnapshot.model_fields['source_url'].default,
        requested_city=request.destination, fetched_at=now, failure_code=reason, failure_reason=message, retryable=retryable,
        message='天气暂不可用，本次未据此调整', days=[WeatherDay(date=d, status='unavailable') for d in requested_days(request)])


class WeatherService:
    def __init__(self, timeout: float = 5, ttl: float = 1800):
        self.timeout, self.ttl = timeout, ttl
        self.cache = OrderedDict()

    async def forecast(self, request: TripRequest, amap, *, force: bool = False) -> WeatherSnapshot:
        provider = 'qweather' if settings.qweather_key and settings.qweather_host else 'amap'
        key, now = provider + ':' + normalize_city(request.destination), datetime.now(SHANGHAI)
        cached = self.cache.get(key)
        if not force and cached and time.monotonic() - cached[0] < self.ttl and (now - cached[1]['issued_at']).total_seconds() <= 86400:
            self.cache.move_to_end(key)
            return snapshot(request, cached[1], True)
        self.cache.pop(key, None)
        try:
            async with asyncio.timeout(self.timeout):
                district = await resolve_division(amap, request.destination)
                if provider == 'qweather':
                    value = await fetch_qweather(district, datetime.now(SHANGHAI))
                else:
                    data = await amap.request('/v3/weather/weatherInfo', {
                        'city': district['adcode'], 'extensions': 'all', 'output': 'JSON'})
                    value = parse_forecast(district, data, datetime.now(SHANGHAI))
            self.cache[key] = (time.monotonic(), value)
            while len(self.cache) > 64:
                self.cache.popitem(last=False)
            return snapshot(request, value, False)
        except Exception as error:
            # Never return a signed URL/provider error in a public snapshot.
            return unavailable(request, datetime.now(SHANGHAI), provider, failure_code(error))


def weather_context(value: WeatherSnapshot | None) -> dict:
    if value is None:
        return {'status': 'unavailable', 'message': '本次没有天气快照，不得据此推测天气'}
    return {'provider': value.provider, 'city': value.city or value.requested_city, 'status': value.status,
            'issued_at': value.issued_at.isoformat() if value.issued_at else None,
            'days': [day.model_dump(mode='json', exclude_none=True) for day in value.days], 'message': value.message}


async def forecast_trip(request: TripRequest, amap, service: WeatherService):
    """Share one five-second deadline across the destination forecasts."""
    cities = list(dict.fromkeys(request.city_route))
    if len(cities) == 1:
        return await service.forecast(request, amap), {}
    requests = {city: request.model_copy(update={'destination': city, 'destinations': []}) for city in cities}
    tasks = {city: asyncio.create_task(service.forecast(value, amap)) for city, value in requests.items()}
    try:
        await asyncio.wait(tasks.values(), timeout=getattr(service, 'timeout', 5))
        values = {}
        for city, task in tasks.items():
            if task.done() and not task.cancelled() and task.exception() is None:
                values[city] = task.result()
            else:
                provider = 'qweather' if settings.qweather_key and settings.qweather_host else 'amap'
                values[city] = unavailable(requests[city], datetime.now(SHANGHAI), provider, 'timeout')
        return None, values
    finally:
        for task in tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
