"""Bounded meal locality checks using real POIs and actual Amap road routes."""
import math
import re
from functools import partial

from .models import city_key
from .administrative import cached_division, contains_place


def _explicit_station(value):
    value = value.strip()
    if (len(value) < 3 or len(value) > 80
            or re.fullmatch(r'(?:当地|目的地|本地|市内|市区)?(?:火车站|高铁站|车站|机场|航站楼)', value)
            or re.search(r'[,，;；。！？/／或及→]|酒店|餐厅|用餐|休息|附近|一带|周边|抵达|到达|前往|乘坐|出发', value)):
        return ''
    return value if re.fullmatch(r'[^\s]+(?:站|机场|航站楼)', value) else ''


def explicit_arrival_station(stop):
    """A named station in a pure arrival label is a search candidate, not proof."""
    if stop.kind != 'break' or stop.booking_anchor:
        return ''
    match = re.fullmatch(r'(?:抵达|到达)\s*(.+?)(?:后)?', stop.name.strip())
    return _explicit_station(match.group(1) if match else stop.place_name or stop.name)


def parse_arrival_movement(stop) -> dict | None:
    """Parse only one explicit station followed by one explicit destination.

    The description may mention luggage, but does not supply a booked hotel or
    another routing endpoint. Meal/sight activities and verified points are not
    reinterpreted. No coordinates or timetable facts are inferred.
    """
    if (stop.kind != 'break' or stop.booking_anchor or stop.location_status == 'verified'
            or stop.location_behavior == 'same_as_previous'):
        return None
    match = re.fullmatch(
        r'(?:抵达|到达)\s*(.+?)(?:后)?\s*[,，;；]\s*'
        r'(?:(?:乘坐?|搭乘?)?\s*(?:地铁|公交|打车|出租车|网约车|步行)'
        r'(?:\s*[0-9一二三四五六七八九十]+\s*号线)?\s*)?'
        r'(?:前往|到|去)\s*([^,，;；。！？\n]{2,80})', stop.name.strip())
    if not match or not (station := _explicit_station(match.group(1))):
        return None
    destination = match.group(2).strip()
    # An itinerary may contain these legitimate activities, but they cannot be
    # collapsed into an invented station-to-hotel/restaurant or multi-stop trip.
    if (re.search(r'酒店|宾馆|旅馆|住宿|住所|住处|餐|吃|饭|寄存|行李|参观|游览|游玩|休息|购物|拍照|烟花|演出|然后|并|或者|及|/|／|→', destination)
            or destination in ('市中心', '市区', '城区', '目的地', '附近', '酒店')):
        return None
    if stop.place_name and stop.place_name not in (stop.name, station, destination):
        return None
    return {'station': station, 'destination': destination,
            'has_luggage_instruction': bool(re.search(r'寄存|行李', stop.description))}


def separation(a, b):
    """Only a cheap candidate filter; never a route distance or duration."""
    x = math.radians(b[0] - a[0]) * math.cos(math.radians((a[1] + b[1]) / 2))
    y = math.radians(b[1] - a[1])
    return 6371000 * math.hypot(x, y)


def _place_in_scope(amap, city, place):
    # Reuse the provider-resolved administrative identity. County POIs report
    # their parent city in cityname, so string city matching would reject them.
    division = cached_division(amap, city)
    if division:
        return contains_place(division, place)
    reported = {city_key(str(place.get(field, ''))) for field in ('cityname', 'adname')}
    return city_key(city) in reported


def meal_area_query(meal):
    """Extract an explicit dining area, never a made-up restaurant name."""
    text = (meal.area_name or meal.place_name or meal.name).strip()
    # “午餐（人民广场一带）” and “午餐：田子坊一带简餐” both only name an area.
    text = re.sub(r'^(?:早餐|午餐|晚餐|用餐|夜宵|简餐)\s*[（(：:]\s*', '', text)
    text = re.sub(r'[）)]\s*$', '', text)
    text = re.sub(r'^(?:返回|回到|前往|在|到)', '', text)
    # Meal activity titles often concatenate a real district with cuisine/time.
    text = re.split(r'附近|周边|一带|朝鲜族|东北菜|俄式|韩式|风味|地道|本地菜|早餐|午餐|晚餐|用餐|就餐', text, maxsplit=1)[0]
    text = re.sub(r'(?:简餐|正餐|套餐|小吃|快餐)\s*$', '', text).strip()
    text = re.sub(r'市区$', '市', text)
    if text in ('', '市', '市区', '城区', '附近', '当地', '园区内', '景区内', '自由选择', '自行选择'):
        return ''
    return text[:60]


def _nearby_meal_anchors(day, index, city):
    anchors = []
    # Do not jump across an intervening other-city activity to find an anchor.
    for indexes in (range(index - 1, -1, -1), range(index + 1, len(day.stops))):
        for position in indexes:
            stop = day.stops[position]
            if city_key(stop.city or city) != city_key(city):
                break
            if stop.location_status == 'verified' and stop.location and stop.poi_id:
                anchors.append(stop)
                break
    return anchors


async def resolve_unlocated_meals(amap, day, protected_text=''):
    """Choose real nearby restaurants for generic placeholders, including day ends.

    Each placeholder uses at most one area lookup, two nearby lookups and three
    road candidates. Area centres only seed a search; only a real restaurant POI
    can become the meal endpoint. Specific ambiguous restaurant branches stay
    unresolved rather than being silently replaced.
    """
    provider_codes = {'map_auth', 'map_ip_limit', 'map_network', 'map_rate_limit',
                      'map_quota', 'map_response', 'map_permission'}

    def remember_provider_codes(codes):
        if not codes:
            return
        if not hasattr(amap, 'unresolved_provider_errors'):
            amap.unresolved_provider_errors = []
        for code in codes:
            if code in provider_codes and code not in amap.unresolved_provider_errors:
                amap.unresolved_provider_errors.append(code)

    for index, meal in enumerate(day.stops):
        if meal.kind != 'meal' or meal.location_status == 'verified':
            continue
        generic = (meal.location_behavior in ('area', 'unlocated')
                   or bool(re.search(r'附近|周边|一带|自行选择', meal.place_name or meal.name)))
        if not generic or (meal.place_name and meal.place_name in protected_text):
            continue
        try:
            city = amap.canonical_city(meal.city)
            anchors = _nearby_meal_anchors(day, index, city)
            query = meal_area_query(meal)
            source_anchor = None
            if query:
                # Reuse a currently verified area before spending another query.
                source_anchor = next((s for s in anchors if query in
                    (s.name, s.place_name, s.matched_name, s.area_name)), None)
                if source_anchor is None and city_key(query) == city_key(city) and anchors:
                    source_anchor = anchors[0]
                if source_anchor is None:
                    found = await amap.search(query, city) if len(amap.cities) > 1 else await amap.search(query)
                    exact = [p for p in found if p['name'] == query and '餐饮' not in p.get('type', '')]
                    scenic = [p for p in found if query in p['name'] and
                              ('风景名胜' in p.get('type', '') or '特色商业街' in p.get('type', '')
                               or '购物' in p.get('type', ''))]
                    civic = [p for p in found if p['name'] == query + '人民政府']
                    # A unique administrative POI is only a nearby-search anchor.
                    if len(exact) == 1:
                        candidates = exact
                    elif scenic:
                        # 田子坊／南京路 come back as several sub-areas; any of them
                        # seeds the restaurant search, and the road check still runs.
                        candidates = sorted(scenic, key=lambda p: (len(p['name']), p['name']))[:1]
                    elif len(civic) == 1:
                        candidates = civic
                    else:
                        candidates = []
                    if not candidates:
                        meal.location_note = '餐饮区域尚未明确定位，请补充具体餐厅或片区名称；未用附近其他城区替代'
                        continue
                    source_anchor = candidates[0]
            elif anchors:
                source_anchor = anchors[0]
            if source_anchor is None:
                meal.location_note = '尚无已核实的同城邻近地点，请先明确餐厅或餐饮片区'
                continue
            point = source_anchor['location'] if isinstance(source_anchor, dict) else source_anchor.location
            params = {'location': ','.join(map(str, point)), 'types': '050000', 'radius': 1500,
                      'sortrule': 'distance', 'offset': 3, 'page': 1, 'extensions': 'base'}
            if meal.food_recommendations:
                params['keywords'] = meal.food_recommendations[0][:40]
            data = await amap.request('/v3/place/around', params)
            if not data.get('pois') and 'keywords' in params:
                data = await amap.request('/v3/place/around', {k: v for k, v in params.items() if k != 'keywords'})
            used = {s.poi_id for s in day.stops if s.kind == 'meal' and s.location_status == 'verified'}
            candidates, road_provider_errors = [], []
            for item in data.get('pois', [])[:3]:
                if (not item.get('id') or not item.get('name') or item['id'] in used
                        or not (str(item.get('typecode', '')).startswith('05') or '餐饮' in str(item.get('type', '')))
                        or not _place_in_scope(amap, city, item)):
                    continue
                try:
                    candidate_point = tuple(map(float, item.get('location', '').split(',')))
                    if (len(candidate_point) != 2 or not all(math.isfinite(v) for v in candidate_point)
                            or not (-180 <= candidate_point[0] <= 180 and -90 <= candidate_point[1] <= 90)):
                        continue
                except (ValueError, TypeError, AttributeError):
                    continue
                # Validate provider road geometry, not the radial filter distance.
                try:
                    road = await amap.route_in_city(point, candidate_point, 'driving', city)
                except RuntimeError as error:
                    if getattr(error, 'code', '') in ('map_auth', 'map_ip_limit'):
                        raise
                    code = getattr(error, 'code', '')
                    if code in provider_codes:
                        road_provider_errors.append(code)
                    continue
                if road.get('distance_m') is None or len(road.get('polyline', [])) < 2:
                    continue
                candidates.append((road['distance_m'], item, candidate_point))
            if not candidates:
                # Failed provider calls are not evidence that a restaurant is
                # unreachable. If another candidate did resolve, preserve that
                # normal fallback instead of blocking on the failed alternative.
                remember_provider_codes(road_provider_errors)
                meal.location_note = '餐饮片区已定位，但未取得道路可达的具体餐厅；请补充餐厅或稍后更新路线'
                continue
            distance, item, point = min(candidates, key=lambda value: (value[0], value[1]['id']))
            old_name = meal.name
            meal.name = meal.place_name = meal.matched_name = item['name']
            meal.poi_id, meal.location, meal.location_status = item['id'], point, 'verified'
            meal.address = item.get('address') if isinstance(item.get('address'), str) else ''
            meal.area_name = meal.area_poi_id = ''
            meal.location_behavior = 'poi'
            meal.location_note = '高德真实餐饮POI与道路已查询；这是就近候选，营业、菜品和预约仍待核实'
            meal.description = (f'将「{old_name}」落实为可更换的具体餐厅建议。' +
                (f'按明确餐饮片区「{query}」查找。' if query else '按相邻已核实活动就近查找。') +
                '最终接驳耗时与方式见下方路线；不表示已预订或已确认营业。')
            if amap.emit:
                await amap.emit('tool', {'name': '落实餐饮地点', 'arguments': {'city': city, 'activity': old_name},
                                       'summary': meal.name + '：真实餐厅POI及道路已查询，营业待核实'})
        except (RuntimeError, ValueError) as error:
            if getattr(error, 'code', '') in ('map_auth', 'map_ip_limit'):
                raise
            remember_provider_codes([getattr(error, 'code', '')])
            meal.location_note = '具体餐厅核实未完成，保留餐饮需求；请补充明确地点或稍后更新路线'


def prepare_accommodation(itinerary, trip, only_day=None, *, tolerant=False, notices=None):
    if trip.confirmed_hotels:
        from .bookings import apply_hotel_anchors, validate_bookings
        validate_bookings(trip)
        for index, day in enumerate(itinerary.days):
            if only_day is None or index == only_day:
                day.stops = [s for s in day.stops if s.kind != 'hotel' or s.booking_anchor]
        apply_hotel_anchors(itinerary, trip, only_day, tolerant=tolerant, notices=notices)
        return
    # Old summaries can still assert a booked hotel after its route anchors disappear.
    if not trip.confirmed_hotel.strip() and re.search(r'住宿|酒店|宾馆', itinerary.summary):
        itinerary.summary = re.sub(r'[^。！？\n]*(?:住宿|酒店|宾馆)[^。！？\n]*[。！？]?', '', itinerary.summary)
        itinerary.summary += '住宿仅推荐片区，具体酒店由你选择；酒店接驳尚未确认。'
    itinerary.summary = re.sub(r'(?:全部|全程)(?:用|采用|使用|以)[^。！？\n]*?(?:接驳|出行|游玩|为主)[。！？]',
                               '逐段交通以当前选择为准。', itinerary.summary)
    for index, day in enumerate(itinerary.days):
        if only_day is not None and index != only_day:
            continue
        day_city = day.stops[-1].city if day.stops and day.stops[-1].city else next(
            (t.to_city for t in reversed(itinerary.transfers) if t.arrival_date == day.date), trip.city_route[0])
        hotels = [s for s in day.stops if s.kind == 'hotel']
        confirmed_city = city_key(trip.city_route[0])
        confirmed = [s for s in hotels if trip.confirmed_hotel.strip()
                     and city_key(s.city or trip.city_route[0]) == confirmed_city]
        if confirmed:
            for stop in confirmed:
                stop.place_name = trip.confirmed_hotel.strip()
                stop.name = trip.confirmed_hotel.strip()
                stop.poi_id = ''
                stop.area_name = stop.area_poi_id = ''
                stop.location_behavior = 'poi'
        remaining = [s for s in day.stops if s.kind != 'hotel' or s in confirmed]
        if remaining or not day.stops:
            day.stops = remaining
        elif hotels:
            # Pure travel days may be empty; a suggested hotel is not a confirmed activity.
            day.stops = []
        if trip.days > 1:
            city = day_city
            if trip.confirmed_hotel.strip() and city_key(city) == confirmed_city:
                day.accommodation = f'首站城市已确认酒店：{trip.confirmed_hotel.strip()}；其余城市住宿仍需选择。'
                continue
            if city_key(city) == city_key('北京'):
                area = '西直门／新街口等地铁沿线片区'
            elif city_key(city) == city_key('上海'):
                area = '人民广场／静安寺等地铁沿线片区'
            else:
                area = '当天首末景点附近的地铁沿线片区'
            day.accommodation = f'住宿建议：{area}，具体酒店由你选择。尚未确认住宿，不把某家酒店固定为起终点；酒店接驳与房价待确认。'


async def optimize_day_meals(amap, day, protected_text=''):
    for index, meal in enumerate(day.stops):
        if meal.kind != 'meal' or index == 0 or index == len(day.stops) - 1:
            continue
        names = [name for name in (meal.place_name, meal.matched_name, meal.name)
                 if name and name not in ('早餐', '午餐', '晚餐', '用餐')]
        if any(name in protected_text for name in names):
            continue
        before, after = day.stops[index - 1], day.stops[index + 1]
        cities = {city_key(s.city or (amap.city if len(amap.cities) == 1 else '')) for s in (before, meal, after)}
        if len(cities) != 1 or '' in cities:
            continue
        city = meal.city or amap.city
        route = partial(amap.route_in_city, city=city)
        if not before.location or not after.location:
            continue
        if meal.location:
            chord_extra = separation(before.location, meal.location) + separation(meal.location, after.location) - separation(before.location, after.location)
            if chord_extra < 2000:
                continue
        try:
            direct = await route(before.location, after.location, 'driving')
            old_distance = None
            if meal.location:
                incoming = await route(before.location, meal.location, 'driving')
                outgoing = await route(meal.location, after.location, 'driving')
                old_distance = incoming['distance_m'] + outgoing['distance_m']
                if old_distance - direct['distance_m'] < max(3000, direct['distance_m'] * .5):
                    continue
            # Search around real adjacent POIs; no midpoint fabricated as a POI.
            candidates = []
            for anchor in (before, after):
                params = {
                    'location': ','.join(map(str, anchor.location)), 'types': '050000',
                    'radius': 1200, 'sortrule': 'distance', 'offset': 3, 'page': 1, 'extensions': 'base',
                }
                if meal.food_recommendations:
                    params['keywords'] = meal.food_recommendations[0][:40]
                data = await amap.request('/v3/place/around', params)
                matched_food = bool('keywords' in params and data.get('pois'))
                if not data.get('pois') and 'keywords' in params:
                    data = await amap.request('/v3/place/around', {k: v for k, v in params.items() if k != 'keywords'})
                for item in data.get('pois', []):
                    raw = item.get('location', '')
                    if not isinstance(raw, str) or ',' not in raw or not item.get('id') or not item.get('name'):
                        continue
                    if str(item.get('typecode', '')).startswith('05') is False and '餐饮' not in str(item.get('type', '')):
                        continue
                    if not _place_in_scope(amap, city, item):
                        continue
                    try:
                        point = tuple(map(float, raw.split(',')))
                        if len(point) != 2 or not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90):
                            continue
                    except ValueError:
                        continue
                    candidates.append(({**item, '_food_priority': matched_food}, point))
            candidates.sort(key=lambda c: (not c[0]['_food_priority'], separation(before.location, c[1]) + separation(c[1], after.location)))
            best = None
            seen = set()
            for item, point in candidates:
                if item['id'] in seen:
                    continue
                seen.add(item['id'])
                incoming = await route(before.location, point, 'driving')
                outgoing = await route(point, after.location, 'driving')
                distance = incoming['distance_m'] + outgoing['distance_m']
                if distance - direct['distance_m'] <= 2500 and (old_distance is None or distance + 1500 < old_distance):
                    score = (not item['_food_priority'], distance)
                    if best is None or score < best[0]:
                        best = (score, item, point)
                if len(seen) >= 3:
                    break
            if best:
                (_, distance), item, point = best
                old_name = meal.place_name or meal.name
                meal.name = meal.place_name = meal.matched_name = item['name']
                meal.poi_id, meal.location, meal.location_status = item['id'], point, 'verified'
                meal.address = item.get('address') if isinstance(item.get('address'), str) else ''
                meal.location_behavior = 'poi'
                meal.location_note = '高德周边餐饮POI；已按相邻活动的驾车道路距离筛选，营业与预约待核实'
                meal.description = f'就近用餐建议，可自行更换。替换绕路餐厅「{old_name}」；与前后活动衔接的道路总长约{distance / 1000:.1f}公里。'
                if amap.emit:
                    await amap.emit('tool', {'name': '优化顺路餐饮', 'arguments': {'before': before.name, 'after': after.name}, 'summary': meal.name + '：真实周边POI与高德道路距离核对'})
            else:
                meal.location_note += '；餐饮绕路待调整：未找到道路核验通过的就近替代，请自行选择附近餐厅'
        except RuntimeError as error:
            if getattr(error, 'code', '') in ('map_auth', 'map_ip_limit'):
                raise
            meal.location_note += '；餐饮绕路待调整：地图核验未完成，保留原餐厅供手动调整'
