"""Signed Amap access. Signing follows NVIDIA-TRAVEL/amap_service.py (Mulan PSL v2).
Requests, city-limited search, route geometry and caches are implemented here.
"""
import hashlib
from datetime import datetime, timezone
from difflib import SequenceMatcher

import httpx
from .config import settings
from .models import RouteLeg


def signed_params(params, key, secret=''):
    values = {k: str(v) for k, v in params.items() if v is not None and k != 'sig'}
    values['key'] = key
    if not key:
        raise RuntimeError('请在 .env.local 中配置 AMAP_API_KEY。')
    if secret:
        raw = '&'.join(f'{k}={values[k]}' for k in sorted(values)) + secret
        values['sig'] = hashlib.md5(raw.encode('utf-8')).hexdigest()
    return values


class Amap:
    def __init__(self, city, emit=None):
        self.city = city
        self.emit = emit
        self.places = {}
        self.routes = {}
        self.client = httpx.AsyncClient(timeout=15, trust_env=False)

    async def close(self):
        await self.client.aclose()

    async def request(self, path, params):
        params = signed_params(params, settings.amap_key, settings.amap_sign_secret)
        try:
            response = await self.client.get('https://restapi.amap.com' + path, params=params)
            response.raise_for_status()
            data = response.json()
            if str(data.get('status', '0')) != '1':
                raise RuntimeError(f"高德返回 {data.get('info', '未知错误')}（{data.get('infocode', '')}）")
            return data
        except Exception as error:
            raise RuntimeError(settings.safe_error(error)) from None

    async def search(self, keywords):
        keywords = str(keywords).strip()[:120]
        if keywords in self.places:
            return self.places[keywords]
        data = await self.request('/v3/place/text', {
            'keywords': keywords, 'city': self.city, 'citylimit': 'true', 'offset': 5, 'page': 1,
            'extensions': 'base',
        })
        found = []
        for item in data.get('pois', []):
            raw = item.get('location', '')
            if not raw or ',' not in raw:
                continue
            lon, lat = map(float, raw.split(','))
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                continue
            found.append({
                'id': item['id'], 'name': item['name'], 'location': [lon, lat],
                'address': item.get('address') if isinstance(item.get('address'), str) else '',
                'city': item.get('cityname', ''), 'district': item.get('adname', ''),
            })
        self.places[keywords] = found
        return found

    async def resolve(self, stop):
        # Discard all coordinates and verification claims supplied by the model.
        stop.location = None
        stop.location_status = 'unknown'
        stop.address = ''
        stop.matched_name = ''
        stop.opening_hours_status = 'unknown'
        if stop.kind == 'break':
            stop.poi_id = ''
            stop.location_note = '自由活动，不绑定具体地点'
            return
        candidates = [p for group in self.places.values() for p in group if p['id'] == stop.poi_id] if stop.poi_id else []
        if not candidates:
            candidates = await self.search(stop.name)
        candidates = [p for p in candidates if stop.name in p['name'] or p['name'] in stop.name
                      or SequenceMatcher(None, stop.name, p['name']).ratio() >= .6]
        if not candidates:
            stop.poi_id = ''
            stop.location_note = '在目的地城市内没有找到足够匹配的地点，请核对名称'
            return
        place = candidates[0]
        stop.poi_id = place['id']
        stop.location = tuple(place['location'])
        stop.address = place['address']
        stop.matched_name = place['name']
        stop.location_status = 'verified'
        stop.location_note = '高德城市内 POI；营业时间未验证'

    async def route(self, origin, destination, mode):
        if mode not in ('walking', 'driving'):
            raise ValueError('仅支持步行或驾车')
        for point in (origin, destination):
            if len(point) != 2 or not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90):
                raise ValueError('无效坐标')
        cache_key = (tuple(origin), tuple(destination), mode)
        if cache_key in self.routes:
            return self.routes[cache_key]
        data = await self.request(f'/v3/direction/{mode}', {
            'origin': ','.join(map(str, origin)), 'destination': ','.join(map(str, destination)),
        })
        paths = data.get('route', {}).get('paths', [])
        if not paths:
            raise RuntimeError('高德未返回可用路线')
        path = paths[0]
        geometry = []
        for step in path.get('steps', []):
            for pair in step.get('polyline', '').split(';'):
                if ',' in pair:
                    point = tuple(map(float, pair.split(',')))
                    if not geometry or point != geometry[-1]:
                        geometry.append(point)
        if not geometry:
            raise RuntimeError('高德未返回路线形状')
        result = {'distance_m': int(path['distance']), 'duration_s': int(path['duration']), 'polyline': geometry}
        self.routes[cache_key] = result
        return result

    async def enrich(self, itinerary, mode):
        routes = []
        for day_index, day in enumerate(itinerary.days):
            for index, stop in enumerate(day.stops):
                stop.id = f'd{day_index + 1}s{index + 1}'
                if self.emit:
                    await self.emit('status', {'message': f'核对第 {day_index + 1} 天地点：{stop.name}'})
                try:
                    await self.resolve(stop)
                except RuntimeError as error:
                    stop.location_note = str(error)
                if self.emit:
                    await self.emit('tool', {'name': '核对地点', 'arguments': {'city': self.city, 'name': stop.name},
                                            'summary': stop.matched_name or stop.location_note})
            for previous, current in zip(day.stops, day.stops[1:]):
                leg = RouteLeg(day_index=day_index, from_id=previous.id, to_id=current.id, mode=mode,
                               queried_at=datetime.now(timezone.utc), note='地点未定位，无法计算路线')
                if previous.location and current.location:
                    try:
                        result = await self.route(previous.location, current.location, mode)
                        leg = leg.model_copy(update={**result, 'available': True, 'note': '高德实时查询，时长仅供规划参考'})
                    except RuntimeError as error:
                        leg.note = str(error)
                routes.append(leg)
                if self.emit:
                    await self.emit('tool', {'name': '查询步行路线' if mode == 'walking' else '查询驾车路线',
                        'arguments': {'from': previous.name, 'to': current.name},
                        'summary': f'{leg.distance_m} 米 · {round(leg.duration_s / 60)} 分钟' if leg.available else leg.note})
        return routes
