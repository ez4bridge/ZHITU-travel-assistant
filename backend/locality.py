"""Bounded meal locality checks using real POIs and actual Amap road routes."""
import math
import re
from functools import partial
from decimal import Decimal, InvalidOperation

from .models import city_key, Expense


def separation(a, b):
    """Only a cheap candidate filter; never a route distance or duration."""
    x = math.radians(b[0] - a[0]) * math.cos(math.radians((a[1] + b[1]) / 2))
    y = math.radians(b[1] - a[1])
    return 6371000 * math.hypot(x, y)


def prepare_accommodation(itinerary, trip, only_day=None):
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
        overnight_train = any(t.departure_date <= day.date < t.arrival_date for t in itinerary.transfers)
        if index < len(itinerary.days) - 1 and not overnight_train and not any(e.category == 'hotel' for e in day.expenses):
            if len(day.expenses) >= 20:
                raise ValueError('当天费用项目已满，无法完整计入住宿预算，请减少费用项目')
            day.expenses.append(Expense(label=f'{day_city}住宿预算预留（非酒店报价）', category='hotel',
                unit_cost=300, quantity=math.ceil(trip.people / 2), basis='group',
                note='原方案未提供住宿费用，按每间每晚300元、每间2人预留；并非已查询酒店价格，未预订，需按实际报价调整。'))
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


async def repair_day_meals(amap, day, protected_text='', used_restaurants=None):
    """Resolve a missing restaurant locally, retaining real POI and price evidence."""
    from .place_info import detail, enrich_place, coordinate
    from .meals import period, restaurant_key, restaurant_name_key
    used = used_restaurants if used_restaurants is not None else set()
    for meal in day.stops:
        if meal.kind != 'meal':
            continue
        identity = restaurant_key(meal)
        repeated = identity in used
        valid = meal.location_status == 'verified' and meal.average_cost_yuan is not None and meal.average_cost_yuan > 0
        if valid and not repeated:
            used.add(identity)
            continue
        names = [n for n in (meal.name, meal.place_name) if n and n not in ('早餐', '午餐', '晚餐', '用餐')]
        if any(n in protected_text for n in names):
            used.add(identity)
            continue
        anchors = [s for s in day.stops if s is not meal and s.location and s.location_status == 'verified'
                   and city_key(s.city) == city_key(meal.city)]
        if not anchors:
            continue
        anchor = min(anchors, key=lambda s: abs(int(s.start[:2]) * 60 + int(s.start[3:]) - int(meal.start[:2]) * 60 - int(meal.start[3:])))
        cached = {p['id']: p for values in amap.places.values() for p in values
                  if city_key(p.get('city', '')) == city_key(meal.city) and '餐饮' in p.get('type', '')
                  and p.get('location') and separation(anchor.location, p['location']) <= 2000}
        candidates = sorted((p for p in cached.values() if restaurant_name_key(meal.city, p['name']) not in used),
            key=lambda p: separation(anchor.location, p['location']))[:3]
        async def nearby():
            data = await amap.request('/v3/place/around', {'location': ','.join(map(str, anchor.location)),
                'types': '050100', 'radius': 1200, 'sortrule': 'distance', 'offset': 12, 'page': 1, 'extensions': 'all'})
            result = []
            for item in data.get('pois', []):
                point = coordinate(item.get('location'))
                if (point and item.get('id') and item.get('name') and
                        (not item.get('cityname') or city_key(str(item['cityname'])) == city_key(meal.city)) and
                        (str(item.get('typecode', '')).startswith('05') or '餐饮' in str(item.get('type', '')))):
                    candidate_key = restaurant_name_key(meal.city, item['name'])
                    if candidate_key not in used and separation(anchor.location, point) <= 1500:
                        result.append({**item, 'location': point})
            return result
        # Try request-local evidence first, then one bounded nearby lookup. A
        # missing price on a cached restaurant must not exhaust the alternatives.
        checked = set()
        async def options():
            for candidate in candidates:
                yield candidate
            for candidate in await nearby():
                yield candidate
        async for candidate in options():
            if candidate['id'] in checked:
                continue
            if len(checked) >= 6:
                break
            checked.add(candidate['id'])
            item = await detail(amap, candidate['id'], meal.city)
            if item and restaurant_name_key(meal.city, item.get('name') or candidate['name']) in used:
                continue
            business = item.get('business') if item else None
            try:
                price = Decimal(str(business.get('cost'))) if isinstance(business, dict) else None
            except InvalidOperation:
                continue
            if price is None or not price.is_finite() or price <= 0:
                continue
            old_name, meal_period = meal.name, period(meal)
            meal.name = meal.place_name = meal.matched_name = candidate['name']
            meal.poi_id, meal.location = candidate['id'], tuple(candidate['location'])
            meal.city = anchor.city
            meal.location_status, meal.location_behavior = 'verified', 'poi'
            meal.area_name = meal.area_poi_id = ''
            meal.address = candidate.get('address') if isinstance(candidate.get('address'), str) else ''
            meal.food_recommendations = []
            meal.meal_period = meal_period
            reason = '避免同品牌餐馆重复' if repeated else '补齐真实餐馆和用餐安排'
            meal.description = f'{reason}，替换「{old_name}」；人均为高德参考，非菜单实价。'
            await enrich_place(amap, meal)
            used.add(restaurant_key(meal))
            if amap.emit:
                await amap.emit('tool', {'name': '补全真实餐馆与人均', 'arguments': {'city': meal.city, 'original': old_name},
                    'summary': f'{meal.name}；高德人均参考{meal.average_cost_yuan}元'})
            break
        used.add(restaurant_key(meal))
        if repeated and restaurant_key(meal) == identity:
            meal.location_note += '；附近未找到有高德人均参考的不同品牌餐馆，暂保留重复餐馆，建议自行更换。'


async def optimize_day_meals(amap, day, protected_text='', require_prices=False):
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
                    if item.get('cityname') and city_key(str(item['cityname'])) != city_key(city):
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
                if require_prices:
                    from .place_info import detail
                    information = await detail(amap, item['id'], city)
                    business = information.get('business') if information else None
                    try:
                        price = Decimal(str(business.get('cost'))) if isinstance(business, dict) else None
                    except InvalidOperation:
                        continue
                    if price is None or not price.is_finite() or price <= 0:
                        continue
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
