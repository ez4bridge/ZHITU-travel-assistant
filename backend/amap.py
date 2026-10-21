"""Signed Amap queries, city-scoped POIs and verified road geometry."""
import hashlib
import asyncio
import re
import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher

import httpx
from .config import settings
from .models import RouteLeg, city_key
from .map_rate_limit import map_rate_limiter
from .administrative import AdministrativeMatchError, cached_division, contains_place, resolve_division

logger = logging.getLogger(__name__)


class MapServiceError(RuntimeError):
    def __init__(self, code: str, message: str, fatal: bool = False, provider_code: str = '', path: str = ''):
        super().__init__(message)
        self.code = code
        self.fatal = fatal
        self.provider_code = provider_code
        self.path = path


MODE_NOTES = {
    'walking': '留意过街、台阶及当天累计步行量。',
    'driving': '行车时间不含停车、取车及停车场步行；请核实限行。',
    'taxi': '按驾车路线估算；不含候车，费用和多人出行所需车辆数需在打车平台核实。',
    'transit': '含接口返回的步行接驳；查询时方案不保证未来出发日班次，请核实首末班与票价。',
    'bicycling': '请核实车辆、骑行限制、天气和停车位置。',
}


def place_query(stop) -> str:
    if stop.place_name.strip():
        return stop.place_name.strip()
    # Activity labels are not part of a restaurant's searchable name.
    match = re.match(r'^(?:早餐|午餐|晚餐|用餐)[（(](.+)[）)]$', stop.name)
    return match.group(1) if match else stop.name


def signed_params(params, key, secret=''):
    values = {k: str(v) for k, v in params.items() if v is not None and k != 'sig'}
    values['key'] = key
    if not key:
        raise RuntimeError('请在 .env.local 中配置 AMAP_API_KEY。')
    if secret:
        raw = '&'.join(f'{k}={values[k]}' for k in sorted(values)) + secret
        values['sig'] = hashlib.md5(raw.encode('utf-8')).hexdigest()
    return values


def map_city_key(city: str) -> str:
    # Amap uses the full SAR name in cityname even when the request uses 香港/澳门.
    # Keep this normalization local to map matching; stored itinerary cities stay unchanged.
    return city_key(city).removesuffix('特别行政区').removesuffix('特別行政區')


class Amap:
    def __init__(self, city, emit=None):
        # A request-local allowlist prevents cross-city names and POI IDs from mixing.
        self.cities = [city] if isinstance(city, str) else list(city)
        self.city = self.cities[0]
        self.emit = emit
        self.places = {}
        self.routes = {}
        self.fatal_error = None
        self._blocked_paths = {}
        self._route_locks = {}
        self._route_failures = {}
        self._slots = asyncio.Semaphore(3)
        self.client = httpx.AsyncClient(timeout=15, trust_env=False)

    async def close(self):
        await self.client.aclose()

    async def request(self, path, params):
        if self.fatal_error:
            raise self.fatal_error
        if path in self._blocked_paths:
            raise self._blocked_paths[path]
        params = signed_params(params, settings.amap_key, settings.amap_sign_secret)
        for attempt in range(3):
            try:
                async with self._slots:
                    if self.fatal_error:
                        raise self.fatal_error
                    await map_rate_limiter.wait(path, settings.amap_qps)
                    if self.fatal_error:
                        raise self.fatal_error
                    if path in self._blocked_paths:
                        raise self._blocked_paths[path]
                    response = await self.client.get('https://restapi.amap.com' + path, params=params)
                if response.status_code == 429:
                    logger.warning('amap_request_failed path=%s provider_code=HTTP429 attempt=%s', path, attempt + 1)
                    if attempt < 2:
                        map_rate_limiter.cooldown(path, 2 ** attempt)
                        continue
                    raise MapServiceError('map_rate_limit', '地图接口请求过于频繁（HTTP429），已重试两次；请稍后刷新。',
                                          provider_code='HTTP429', path=path)
                response.raise_for_status()
                data = response.json()
                success = str(data.get('status', '0')) == '1' or ('errcode' in data and str(data['errcode']) == '0')
                if not success:
                    code = str(data.get('infocode', data.get('errcode', '')))
                    logger.warning('amap_request_failed path=%s provider_code=%s attempt=%s', path, code, attempt + 1)
                    if code in ('10014', '10015', '10019', '10020', '10021', '10029'):
                        if attempt < 2:
                            map_rate_limiter.cooldown(path, 2 ** attempt)
                            continue
                        raise MapServiceError('map_rate_limit', f'地图接口暂时限流（{code}），已重试两次；请稍后刷新该路线。',
                                              provider_code=code, path=path)
                    if code == '10004':
                        map_rate_limiter.cooldown(path, 60)
                        raise MapServiceError('map_rate_limit', '地图接口分钟访问频率超限（10004），请等待一分钟后重试。',
                                              provider_code=code, path=path)
                    if code == '10012':
                        raise MapServiceError('map_permission', '该地图能力尚未授权，请选择其他交通方式或检查服务权限。')
                    if code in ('10001', '10002', '10005', '10006', '10007', '10008', '10009', '10013'):
                        self.fatal_error = MapServiceError('map_auth', f'地图服务鉴权失败（{code}），请检查 Web 服务 Key、签名和权限。', True, code, path)
                        raise self.fatal_error
                    if code in ('10003', '10044', '10045', '40000', '40003'):
                        error = MapServiceError('map_quota', f'该地图接口日配额或余额已用尽（{code}），请检查高德控制台；立即重试无效。', True, code, path)
                        self._blocked_paths[path] = error
                        raise error
                    if code == '10010':
                        self.fatal_error = MapServiceError('map_ip_limit', '地图服务 IP 访问受限（10010），请检查控制台或联系高德支持。', True, code, path)
                        raise self.fatal_error
                    raise MapServiceError('map_response', f'地图查询失败（{code}），请核对地点或路线参数。')
                return data
            except MapServiceError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError):
                if not attempt:
                    await asyncio.sleep(.2)
                    continue
                raise MapServiceError('map_network', '地图服务网络暂时不可用，请稍后刷新路线。') from None
            except (httpx.HTTPStatusError, ValueError, KeyError):
                raise MapServiceError('map_response', '地图服务响应异常，请稍后刷新路线。') from None

    def canonical_city(self, city: str = '') -> str:
        if not city and len(self.cities) == 1:
            return self.city
        found = next((value for value in self.cities if map_city_key(value) == map_city_key(city)), None)
        if not found:
            # Only provider-resolved names may extend the request's city allowlist.
            found = next((value for value in self.cities if (division := cached_division(self, value))
                          and map_city_key(division['name']) == map_city_key(city)), None)
        if not found:
            raise ValueError('查询城市必须来自本次目的地列表')
        return found

    async def search(self, keywords, city=''):
        city = self.canonical_city(city)
        keywords = str(keywords).strip()[:120]
        cache_key = (city_key(city), keywords)
        if cache_key in self.places:
            return self.places[cache_key]
        try:
            division = await resolve_division(self, city)
        except AdministrativeMatchError as error:
            raise MapServiceError('map_city_ambiguous', str(error)) from None
        data = await self.request('/v3/place/text', {
            'keywords': keywords, 'city': division['adcode'], 'citylimit': 'true', 'offset': 5, 'page': 1,
            'extensions': 'base',
        })
        found = []
        for item in data.get('pois', []):
            # Some upstream responses ignore citylimit (e.g. ambiguous city names).
            if not contains_place(division, item):
                continue
            raw = item.get('location', '')
            if not raw or ',' not in raw:
                continue
            lon, lat = map(float, raw.split(','))
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                continue
            found.append({
                'id': item['id'], 'name': item['name'], 'location': [lon, lat],
                'address': item.get('address') if isinstance(item.get('address'), str) else '',
                'city': city, 'district': item.get('adname', ''),
                'type': item.get('type', ''),
            })
        self.places[cache_key] = found
        return found

    async def resolve(self, stop):
        # Discard all coordinates and verification claims supplied by the model.
        stop.location = None
        stop.location_status = 'unknown'
        stop.address = ''
        stop.matched_name = ''
        stop.opening_hours_status = 'unknown'
        stop.opening_hours = stop.opening_hours_today = stop.information_source = ''
        stop.opening_hours_scope = 'place'
        stop.information_queried_at = None
        stop.average_cost_yuan = None
        stop.food_reference = ''
        stop.anchor_note = ''
        try:
            stop.city = self.canonical_city(stop.city)
        except ValueError:
            stop.poi_id = ''
            stop.location_note = '城市缺失或不在本次目的地列表中，无法核验地点'
            return
        if stop.location_behavior in ('same_as_previous', 'unlocated'):
            stop.poi_id = ''
            stop.location_note = '原地休息，位置由前一活动继承' if stop.location_behavior == 'same_as_previous' else '尚未指定活动地点'
            return
        query = place_query(stop)
        if stop.kind == 'meal' and (stop.location_behavior == 'area' or re.search(r'一带|周边|附近|自行选择|自由漫步', query)):
            stop.poi_id = ''
            stop.location_note = '这是餐饮区域建议，需要选择具体餐馆，不能用区域中心冒充餐馆'
            return
        if re.search(r'一带|周边|附近|自行选择|自由漫步', query):
            from .place_info import area_query
            query = area_query(stop)
            stop.area_name = query
            stop.location_behavior = 'area'
        elif stop.area_name:
            from .place_info import area_query
            query = area_query(stop)
        if not query:
            stop.poi_id = ''
            stop.location_note = '区域名称不明确，请指定真实区域或景点'
            return
        if stop.area_poi_id:
            stop.poi_id = stop.area_poi_id
        candidates = [p for group in self.places.values() for p in group
                      if p['id'] == stop.poi_id and city_key(p.get('city', self.city if len(self.cities) == 1 else '')) == city_key(stop.city)] if stop.poi_id else []
        if not candidates:
            candidates = await self.search(query) if len(self.cities) == 1 else await self.search(query, stop.city)
        candidates = [p for p in candidates if query in p['name'] or p['name'] in query
                      or SequenceMatcher(None, query, p['name']).ratio() >= .75]
        candidates = list({p['id']: p for p in candidates}.values())
        if stop.kind == 'meal':
            candidates = [p for p in candidates if not p.get('type') or '餐饮' in p['type']]
        exact = [p for p in candidates if p['name'] == query or p['id'] == stop.poi_id]
        if stop.kind == 'sight' and len(exact) > 1:
            scenic = [p for p in exact if '风景名胜' in p.get('type', '')]
            if len(scenic) == 1:
                exact = scenic
        if stop.kind == 'sight' and not exact and len(candidates) > 1:
            scenic = [p for p in candidates if '风景名胜' in p.get('type', '')]
            auxiliary = [p for p in candidates if any(category in p.get('type', '')
                         for category in ('停车场', '售票处', '信息咨询中心'))]
            # One attraction plus its auxiliary facilities is not a branch ambiguity.
            if len(scenic) == 1 and len(auxiliary) + 1 == len(candidates):
                candidates = scenic
        if len(exact) == 1:
            candidates = exact
        elif len(candidates) > 1:
            stop.poi_id = ''
            stop.location_note = '存在多个相似地点或分店，请指定完整地点名称'
            return
        if not candidates:
            stop.poi_id = ''
            stop.location_note = f'在{stop.city}内没有找到足够匹配的地点，请核对名称'
            return
        place = candidates[0]
        stop.poi_id = place['id']
        stop.area_poi_id = place['id'] if stop.area_name or stop.location_behavior == 'area' else ''
        stop.location = tuple(place['location'])
        stop.address = place['address']
        stop.matched_name = place['name']
        stop.location_status = 'verified'
        stop.location_note = '高德城市内 POI；营业时间未验证'

    async def route_in_city(self, origin, destination, mode, city=''):
        if len(self.cities) == 1:
            return await self.route(origin, destination, mode)
        return await self.route(origin, destination, mode, city=city)

    async def route(self, origin, destination, mode, city=''):
        if mode not in MODE_NOTES:
            raise ValueError('不支持的交通方式')
        wire_mode = 'driving' if mode == 'taxi' else mode
        city = self.canonical_city(city) if mode == 'transit' else city
        key = (tuple(origin), tuple(destination), wire_mode, city_key(city) if mode == 'transit' else '')
        lock = self._route_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key in self._route_failures:
                raise self._route_failures[key]
            try:
                result = await self._route(origin, destination, wire_mode, city)
            except MapServiceError as error:
                if error.code in ('map_rate_limit', 'map_quota'):
                    self._route_failures[key] = error
                raise
        return {**result, 'note': MODE_NOTES[mode]}

    async def _route(self, origin, destination, mode, city=''):
        if mode not in MODE_NOTES:
            raise ValueError('不支持的交通方式')
        for point in (origin, destination):
            if len(point) != 2 or not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90):
                raise ValueError('无效坐标')
        cache_key = (tuple(origin), tuple(destination), mode, city_key(city) if mode == 'transit' else '')
        if cache_key in self.routes:
            return self.routes[cache_key]
        endpoint = {'transit': '/v3/direction/transit/integrated', 'bicycling': '/v4/direction/bicycling',
                'taxi': '/v3/direction/driving'}.get(mode, f'/v3/direction/{mode}')
        params = {
            'origin': ','.join(map(str, origin)), 'destination': ','.join(map(str, destination)),
        }
        if mode == 'transit':
            params.update(city=city or self.city, cityd=city or self.city, extensions='all')
        elif mode in ('driving', 'taxi'):
            params['extensions'] = 'all'
        data = await self.request(endpoint, params)
        route = data.get('route') or data.get('data') or {}
        if not isinstance(route, dict):
            raise MapServiceError('no_route', '地图服务未返回可用路线对象')
        paths = route.get('transits' if mode == 'transit' else 'paths', [])
        if not paths:
            raise MapServiceError('no_route', '该交通方式未返回可用路线，请尝试其他方式或地点出入口')
        results, first_error = [], None
        for index, path in enumerate(paths[:20]):
            if not isinstance(path, dict):
                continue
            try:
                # route.taxi_cost is the provider's first-plan quote, not every alternative's quote.
                quote = route if index == 0 else {**route, 'taxi_cost': None}
                results.append(self._parse_route_path(quote, path, mode, endpoint))
            except MapServiceError as error:
                first_error = first_error or error
        if not results:
            raise first_error or MapServiceError('no_route', '未返回具备真实道路形状的可用方案')
        result = min(results, key=lambda value: (value['duration_s'], value['distance_m']))
        self.routes[cache_key] = result
        return result

    @staticmethod
    def _parse_route_path(route, path, mode, endpoint):
        geometry = []
        steps = path.get('steps', [])
        instructions = []
        if mode == 'transit':
            steps = []
            for segment in path.get('segments', []):
                steps.extend((segment.get('walking') or {}).get('steps', []))
                lines = (segment.get('bus') or {}).get('buslines', [])
                if lines:
                    steps.append(lines[0])
                    instructions.append(lines[0].get('name', '公共交通'))
                if (segment.get('railway') or {}).get('id'):
                    raise MapServiceError('missing_geometry', '该公交方案含未提供完整道路形状的铁路段，请在地图应用核实')
        for step in steps:
            for pair in (step.get('polyline') or '').split(';'):
                if ',' in pair:
                    try:
                        point = tuple(map(float, pair.split(',')))
                        if len(point) != 2 or not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90):
                            raise ValueError('invalid coordinate')
                    except ValueError:
                        raise MapServiceError('missing_geometry', '地图服务返回了无效的路线坐标') from None
                    if not geometry or point != geometry[-1]:
                        geometry.append(point)
        if len(geometry) < 2:
            raise MapServiceError('missing_geometry', '高德未返回路线形状')
        try:
            distance, duration = int(path['distance']), int(path['duration'])
            if distance < 0 or duration < 0:
                raise ValueError('negative route metrics')
        except (KeyError, ValueError, TypeError):
            raise MapServiceError('map_response', '地图服务未返回有效的路线距离或耗时') from None
        result = {'distance_m': distance, 'duration_s': duration, 'polyline': geometry,
                  'instructions': instructions, 'note': MODE_NOTES[mode], 'fare_yuan': None,
                  'provider_endpoint': endpoint, 'taxi_cost_yuan': None, 'tolls_yuan': None}
        from decimal import Decimal, InvalidOperation
        for field, raw in (('taxi_cost_yuan', route.get('taxi_cost')), ('tolls_yuan', path.get('tolls'))):
            try:
                value = Decimal(str(raw))
                if value.is_finite() and 0 <= value <= 10000000:
                    result[field] = value
            except (InvalidOperation, TypeError, ValueError):
                pass
        if mode == 'transit' and path.get('cost') not in (None, '', []):
            from decimal import Decimal, InvalidOperation
            try:
                fare = Decimal(str(path['cost']))
                if fare.is_finite() and 0 <= fare <= 10000000:
                    result['fare_yuan'] = fare
            except InvalidOperation:
                pass  # A missing/invalid fare does not invalidate verified road geometry.
        return result

    async def enrich(self, itinerary, mode, compare_modes=False, day_indices=None, optimize_meals=False, protected_text='', enrich_details=False):
        routes = []
        for day_index, day in enumerate(itinerary.days):
            if day_indices is not None and day_index not in day_indices:
                continue
            for index, stop in enumerate(day.stops):
                stop.id = stop.id or f'd{day_index + 1}s{index + 1}'
                if self.emit:
                    await self.emit('status', {'message': f'核对第 {day_index + 1} 天地点：{stop.name}'})
                try:
                    regional_rest = False
                    if enrich_details and index and stop.kind == 'break' and stop.location_behavior == 'unlocated':
                        previous = day.stops[index - 1]
                        if (previous.area_name and previous.area_poi_id and previous.location_status == 'verified'
                                and city_key(stop.city or previous.city) == city_key(previous.city)
                                and re.search(r'园区|景区|区域|出口附近|周边休息', stop.name + stop.description)):
                            # Choose a real rest anchor in the already verified region, not a fictitious return route.
                            stop.area_name = stop.place_name = previous.area_name
                            stop.area_poi_id, stop.poi_id = previous.area_poi_id, previous.poi_id
                            stop.city, stop.location_behavior = previous.city, 'area'
                            regional_rest = True
                    previous_anchor = stop.poi_id if stop.area_poi_id else ''
                    await self.resolve(stop)
                    if enrich_details and stop.location_status == 'verified':
                        from .place_info import enrich_place
                        await enrich_place(self, stop, previous_anchor=previous_anchor)
                        if regional_rest:
                            stop.anchor_note += '；此为区域内补充选择的休息点，自行返程路线未纳入核实'
                    if stop.location_behavior == 'same_as_previous' and index > 0:
                        previous = day.stops[index - 1]
                        if city_key(previous.city) != city_key(stop.city):
                            stop.location_note = '跨城活动不能继承上一城市位置，请明确休息地点'
                            continue
                        stop.location = previous.location
                        stop.location_status = previous.location_status
                        stop.poi_id = previous.poi_id
                        stop.matched_name = previous.matched_name
                        stop.address = previous.address
                except RuntimeError as error:
                    if isinstance(error, MapServiceError) and error.fatal:
                        raise
                    stop.location_note = str(error)
                if self.emit:
                    await self.emit('tool', {'name': '核对地点', 'arguments': {'city': stop.city, 'name': stop.name},
                                            'summary': stop.matched_name or stop.location_note})
            if optimize_meals:
                from .locality import optimize_day_meals
                original_meals = {s.id: s.poi_id for s in day.stops if s.kind == 'meal'}
                await optimize_day_meals(self, day, protected_text)
                if enrich_details:
                    from .place_info import enrich_place
                    for stop in day.stops:
                        if stop.kind == 'meal' and original_meals.get(stop.id) != stop.poi_id:
                            stop.opening_hours_status = 'unknown'
                            stop.opening_hours = stop.opening_hours_today = stop.information_source = ''
                            stop.average_cost_yuan = None
                            stop.food_reference = ''
                            stop.information_queried_at = None
                            await enrich_place(self, stop)
            for previous, current in zip(day.stops, day.stops[1:]):
                selected_mode = 'walking' if mode == 'auto' else mode
                leg = RouteLeg(day_index=day_index, from_id=previous.id, to_id=current.id, mode=selected_mode,
                               queried_at=datetime.now(timezone.utc), note='地点未定位，无法计算路线')
                if previous.city and current.city and city_key(previous.city) != city_key(current.city):
                    routes.append(leg.model_copy(update={'scope': 'intercity', 'note': '跨城移动，详见跨城交通；不绘制市内导航路线'}))
                    continue
                if previous.location and current.location:
                    try:
                        if previous.poi_id == current.poi_id and previous.location == current.location:
                            leg = leg.model_copy(update={'available': True, 'stationary': True, 'duration_s': 0,
                                'distance_m': 0, 'note': '同一已核实地点，无需地点间转移'})
                        else:
                            result = await self.route_in_city(previous.location, current.location, selected_mode, current.city)
                            leg = leg.model_copy(update={**result, 'available': True})
                    except RuntimeError as error:
                        if isinstance(error, MapServiceError) and error.fatal and (mode != 'auto' or error.code in ('map_auth', 'map_ip_limit')):
                            raise
                        leg.note = str(error)
                        leg.failure_code = getattr(error, 'code', 'map_response')
                    if (compare_modes or mode == 'auto') and not leg.stationary:
                        from .models import RouteOption
                        async def alternative(candidate_mode):
                            option = RouteOption(mode=candidate_mode, note=MODE_NOTES[candidate_mode])
                            try:
                                result = await self.route_in_city(previous.location, current.location, candidate_mode, current.city)
                                option = option.model_copy(update={**result, 'available': True})
                            except RuntimeError as error:
                                if isinstance(error, MapServiceError) and error.fatal and error.code in ('map_auth', 'map_ip_limit'):
                                    raise
                                option.note = str(error)
                                option.failure_code = getattr(error, 'code', 'map_response')
                            return option
                        tasks = [asyncio.create_task(alternative(m)) for m in MODE_NOTES]
                        try:
                            leg.alternatives = list(await asyncio.gather(*tasks))
                        except BaseException:
                            for task in tasks:
                                if not task.done():
                                    task.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)
                            raise
                        if mode == 'auto':
                            available = [o for o in leg.alternatives if o.available and o.duration_s is not None]
                            if available:
                                order = {'walking': 0, 'transit': 1, 'bicycling': 2, 'taxi': 3, 'driving': 4}
                                fastest = min(available, key=lambda o: (o.duration_s, order[o.mode]))
                                for key, value in fastest.model_dump().items():
                                    setattr(leg, key, value)
                                leg.selection_reason = '自动选择：已查询可用方案中地图通行时间最短；未计入接口以外的候车、停车取车等耗时。可逐段手动更换。'
                        if not leg.available and mode == 'transit' and leg.failure_code == 'no_route':
                            walking = next((o for o in leg.alternatives if o.mode == 'walking' and o.available
                                            and o.distance_m is not None and o.distance_m <= 1500), None)
                            if walking:
                                for key, value in walking.model_dump().items():
                                    setattr(leg, key, value)
                                leg.note = '公交未返回方案；该短途采用高德实际步行路线，可在下方切换其他方式。' + walking.note
                else:
                    leg.failure_code = 'missing_location'
                routes.append(leg)
                if self.emit:
                    await self.emit('tool', {'name': {'auto': '比较并选取最快市内交通', 'walking': '查询步行路线', 'driving': '查询驾车路线',
                        'taxi': '查询打车道路与出租车估价', 'transit': '查询公交地铁路线', 'bicycling': '查询骑行路线'}[mode],
                        'arguments': {'from': previous.name, 'to': current.name},
                        'summary': f'{leg.distance_m} 米 · {round(leg.duration_s / 60)} 分钟' if leg.available else leg.note})
        return routes
