"""Provider-backed opening references and real regional routing anchors."""
import re
import secrets
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .models import city_key
from .administrative import cached_division, contains_place

SOURCE = 'https://lbs.amap.com/api/webservice/guide/api/newpoisearch'


def coordinate(raw):
    if not isinstance(raw, str):
        return None
    try:
        point = tuple(map(float, raw.split(',')))
        return point if len(point) == 2 and -180 <= point[0] <= 180 and -90 <= point[1] <= 90 else None
    except ValueError:
        return None


def area_query(stop):
    query = stop.area_name.strip() or stop.place_name.strip() or stop.name
    return re.sub(r'(?:一带|周边|附近|区域|片区|自由漫步|游览|漫步|散步)', '', query).strip(' （）()·：:')


def wants_area(stop):
    query = stop.area_name or stop.place_name or stop.name
    return stop.location_behavior == 'area' or bool(stop.area_name) or bool(re.search(r'一带|片区|区域|周边|附近', query))


async def detail(amap, poi_id, city):
    cache = getattr(amap, 'place_details', None)
    if cache is None:
        cache = amap.place_details = {}
    key = (city_key(city), poi_id)
    if key in cache:
        return cache[key]
    if len(cache) >= 40:
        return None
    # Cache optional failures too: do not retry an unsupported capability per stop.
    cache[key] = None
    if getattr(amap, 'place_details_unavailable', False):
        return None
    try:
        data = await amap.request('/v5/place/detail', {'id': poi_id, 'show_fields': 'business,children,navi'})
    except RuntimeError as error:
        if getattr(error, 'code', '') in ('map_auth', 'map_ip_limit'):
            raise
        if getattr(error, 'code', '') in ('map_permission', 'map_quota', 'map_rate_limit'):
            amap.place_details_unavailable = True
        return None
    for item in data.get('pois', []):
        if item.get('id') != poi_id:
            continue
        division = cached_division(amap, city)
        if division:
            if not contains_place(division, item):
                continue
        else:
            reported = {city_key(str(item.get(field))) for field in ('cityname', 'adname') if item.get(field)}
            if reported and city_key(city) not in reported:
                continue
        cache[key] = item
        return item
    return None


def apply_business(stop, item, scope='place'):
    business = item.get('business') or {}
    if not isinstance(business, dict):
        return
    weekly, today = business.get('opentime_week'), business.get('opentime_today')
    stop.opening_hours = weekly[:1200] if isinstance(weekly, str) else ''
    stop.opening_hours_today = today[:500] if isinstance(today, str) else ''
    stop.opening_hours_status = 'map_reference' if stop.opening_hours or stop.opening_hours_today else 'unknown'
    stop.opening_hours_scope = scope
    stop.information_source = f'https://www.amap.com/place/{item["id"]}'
    stop.information_queried_at = datetime.now(timezone.utc)
    if stop.kind == 'meal':
        tag = business.get('tag')
        stop.food_reference = tag[:500] if isinstance(tag, str) else ''
    try:
        cost = Decimal(str(business.get('cost')))
        if cost.is_finite() and 0 <= cost <= 10000000:
            stop.average_cost_yuan = cost
    except InvalidOperation:
        pass


async def enrich_place(amap, stop, previous_anchor=''):
    if not stop.poi_id or stop.location_status != 'verified':
        return
    parent = await detail(amap, stop.area_poi_id or stop.poi_id, stop.city or amap.city)
    if not parent:
        if wants_area(stop):
            stop.location = None
            stop.location_status = 'unknown'
            stop.anchor_note = '区域内具体点位未核验成功；未使用随机坐标冒充通勤起终点'
        return
    explicit = wants_area(stop)
    automatic_area = stop.kind in ('sight', 'break') and bool(re.search(
        r'(?:景区|公园|广场|步行街|古镇|古城|湖|海)$', stop.place_name or stop.name))
    if wants_area(stop) or automatic_area:
        stop.area_name = stop.area_name or parent.get('name', stop.name)
        stop.area_poi_id = parent['id']
        children = parent.get('children') or []
        if not isinstance(children, list):
            children = []
        candidates = []
        for child in children:
            if not isinstance(child, dict) or not child.get('id') or not child.get('name'):
                continue
            point = coordinate(child.get('location'))
            category = str(child.get('typecode', ''))
            if re.search(r'停车|车场|大巴|卫生间|厕所|售票|管理|办公|职工|后勤', child['name']):
                continue
            if point and (category.startswith('11') or child.get('subtype') in ('门', '入口', '出入口')
                          or re.search(r'入口|出入口|宫门|[东西南北正]门|景点|观景|码头', child['name'])):
                candidates.append((child, point))
        if candidates:
            # Refresh retains its verified anchor; a newly generated region gets variety.
            if not hasattr(amap, 'area_anchors_used'):
                amap.area_anchors_used = {}
            used = amap.area_anchors_used.setdefault(parent['id'], set())
            selected = next((c for c in candidates if c[0]['id'] == (previous_anchor or stop.poi_id)), None)
            child, point = selected or secrets.choice([c for c in candidates if c[0]['id'] not in used] or candidates)
            used.add(child['id'])
            stop.poi_id, stop.location, stop.matched_name = child['id'], point, child['name']
            stop.address = child.get('address') if isinstance(child.get('address'), str) else stop.address
            stop.anchor_note = f'在「{stop.area_name}」的高德真实子地点中选取「{child["name"]}」作为通勤点；区域内游览距离另计，可修改选点'
            business = parent.get('business') or {}
            if isinstance(business, dict) and (business.get('opentime_week') or business.get('opentime_today')):
                apply_business(stop, parent, scope='area')
            else:
                item = await detail(amap, child['id'], stop.city or amap.city)
                if item:
                    apply_business(stop, item)
            return
        navi = parent.get('navi') or {}
        point = coordinate(navi.get('entr_location')) if isinstance(navi, dict) else None
        if point:
            stop.location = point
            stop.matched_name = f'{parent.get("name", stop.name)}（地图入口）'
            stop.anchor_note = '未返回适合的区域子地点，使用高德明确提供的入口；没有随机生成区域坐标'
        elif explicit:
            stop.location, stop.location_status = None, 'unknown'
            stop.anchor_note = '未返回可核实的区域子地点或入口，请指定具体景点；未以区域中心冒充入口'
            return
        else:
            stop.area_name = stop.area_poi_id = ''
            stop.anchor_note = '地图未提供可选子地点或入口，保留原地点；若需区域内游览路线，请指定具体点位'
    apply_business(stop, parent, scope='area' if stop.area_name else 'place')
