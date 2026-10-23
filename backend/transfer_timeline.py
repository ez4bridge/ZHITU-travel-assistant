"""Put actual selected stations in the day; intercity fares remain in transfers."""
from datetime import datetime, timedelta
import hashlib
import re

from .models import Stop, city_key
from .locality import parse_arrival_movement
from .validation import minutes
from .transfer_buffers import arrival_buffer, departure_buffer


def station_query(name, mode):
    """Railway's official short station names omit 站; never choose an airport."""
    value = name.strip()
    entrance = re.search(r'站.+(?:广场|进站口|出站口|出口|入口)', value)
    return value + '站' if value and mode == 'train' and not value.endswith('站') and not entrance else value


def _matching_booking(trip, transfer, index):
    return next((b for b in trip.booked_transports if city_key(b.from_city) == city_key(transfer.from_city)
                 and city_key(b.to_city) == city_key(transfer.to_city)
                 and (b.transfer_index is None or b.transfer_index == index)), None)


def _station_event(stop, station):
    """Only a pure duplicate transfer event, not a café/meal/rest at a station."""
    if stop.kind != 'break' or stop.booking_anchor:
        return False
    label = re.sub(r'[（(][A-Za-z]?\d+[）)]', '', stop.name).strip()
    label = re.sub(r'\s+', '', label)
    names = {station, station.removesuffix('站')}
    names.discard('')
    for name in names:
        escaped = re.escape(name)
        if (re.fullmatch(rf'(?:从)?{escaped}(?:出发|到达|抵达|候车|进站|安检|进站安检|安检候车)', label)
                or re.fullmatch(rf'(?:到达|抵达|前往){escaped}', label)):
            return True
    return False


def _same_station(left, right):
    normalize = lambda value: re.sub(r'(?:火车站|高铁站)$', '站', value.strip())
    return normalize(left) == normalize(right)


def _station_meal_label(stop, station):
    """Only a meal label that names the station itself, never a compound trip.

    “抵达虹桥站，地铁前往人民广场用餐” describes a journey and must stay as the
    model wrote it: renaming it to “虹桥站附近用餐” would move a real meal to an
    invented place.
    """
    label = re.sub(r'[（(][A-Za-z]?\d+[）)]', '', stop.name).strip()
    if re.search(r'[,，;；]\s*(?:(?:乘坐?|搭乘?)?\s*(?:地铁|公交|打车|出租车|网约车|步行)'
                 r'(?:\s*[0-9一二三四五六七八九十]+\s*号线)?\s*)?(?:前往|到|去)\s*\S', label):
        return False
    names = {station, station.removesuffix('站')}
    names.discard('')
    return any(name in label for name in names) and bool(re.search(r'到达|抵达', label))


def _user_arrival_overrides(itinerary, trip):
    """Return only declarations already applied to the matching transfer.

    A queried timetable alone cannot authorize changing a model's station. The
    booking-preview caller opts in after applying the user's new declaration;
    cross-check its endpoints and clocks rather than trusting a status flag.
    """
    arrivals = {}
    for index, transfer in enumerate(itinerary.transfers):
        booking = _matching_booking(trip, transfer, index)
        if not booking or transfer.booking_status != 'user_confirmed' or not booking.arrival_place:
            continue
        if (transfer.mode != booking.mode
                or any(getattr(transfer, key) != getattr(booking, key) for key in
                       ('departure_date', 'departure_time', 'arrival_date', 'arrival_time'))
                or not _same_station(station_query(transfer.arrival_station, transfer.mode),
                                     station_query(booking.arrival_place, booking.mode))):
            continue
        key = (transfer.arrival_date, city_key(transfer.to_city))
        arrivals.setdefault(key, set()).add(station_query(booking.arrival_place, booking.mode))
    return arrivals


def split_arrival_movements(itinerary, day_indices=None, *, _user_arrivals=None):
    """Make explicit arrival+movement labels routeable without inventing facts.

    Existing queried/booked arrival anchors remain authoritative. With only a
    model's station name, create a normal estimated-time POI candidate, never a
    booking anchor or a queried timetable. Keep the original id and expense
    bindings on the destination; do not turn luggage advice into a hotel.
    """
    for day_index, day in enumerate(itinerary.days):
        if day_indices is not None and day_index not in day_indices:
            continue
        planned = []
        for stop in day.stops:
            parsed = parse_arrival_movement(stop)
            if not parsed:
                planned.append(stop)
                continue
            start, end = minutes(stop.start), minutes(stop.end)
            if end - start < 10:
                # Two honest preparation markers need time. A single short
                # interval cannot establish a journey between unknown points.
                stop.location_note = '复合抵达与移动动作需要分别安排站点和目的地，请补足接驳时间'
                planned.append(stop)
                continue
            station, destination = parsed['station'], parsed['destination']
            declared = (_user_arrivals or {}).get((day.date, city_key(stop.city)), set())
            # Only the booking-supplement flow can replace a stale, unverified
            # composite label. Real/verified activities never reach this parser.
            corrected_station = len(declared) == 1 and not any(_same_station(station, value) for value in declared)
            if corrected_station:
                station = next(iter(declared))
            known = [t for t in itinerary.transfers if t.arrival_date == day.date
                     and city_key(t.to_city) == city_key(stop.city)
                     and (t.booking_status == 'user_confirmed' or t.schedule_status == 'queried')
                     and t.arrival_station]
            if known and not any(_same_station(station, station_query(t.arrival_station, t.mode)) for t in known):
                raise ValueError(f'抵达描述中的「{station}」与已查／已填班次的到达站不一致，请确认实际站点后再规划接驳')
            existing = next((s for s in [*planned, *day.stops] if s.id != stop.id
                             and s.booking_anchor == 'transport_arrival'
                             and city_key(s.city) == city_key(stop.city)
                             and _same_station(s.place_name or s.name, station)), None)
            if existing is None:
                token = hashlib.sha256(f'{day.date}|{stop.id}|{station}'.encode()).hexdigest()[:16]
                marker_id = 'arrival-point-' + token
                existing = next((s for s in [*planned, *day.stops] if s.id == marker_id), None)
                if existing is None:
                    from .planning import clock
                    existing = Stop(id=marker_id, name=station, place_name=station, city=stop.city,
                        kind='break', start=clock(start), end=clock(start + 5), location_behavior='poi',
                        description='原行程明确提及的到站地点候选；到达时刻及5分钟出站准备仍为估算。'
                            '仅向地图核实地点与道路，不代表车票、班次、余票或酒店已经确认。')
                    planned.append(existing)
            destination_start = max(end - 5, minutes(existing.end))
            if destination_start + 5 > 1439:
                raise ValueError('拆分抵达与接驳后目的地准备时间跨日，请调整日期或减少活动；未伪造更早到达')
            from .planning import clock
            original_name, original_description = stop.name, stop.description
            if declared:
                original_description = ('原预览备注（仅作参考，以本次已填酒店与交通为准）：' + original_description)
            stop.name = destination
            stop.place_name = re.sub(r'(?:一带|附近|周边)$', '', destination).strip()
            regional = stop.place_name != destination
            stop.location_behavior = 'area' if regional else 'poi'
            stop.area_name = stop.place_name if regional else ''
            stop.area_poi_id = stop.poi_id = stop.matched_name = stop.address = ''
            stop.location, stop.location_status = None, 'unknown'
            stop.start, stop.end = clock(destination_start), clock(destination_start + 5)
            correction = (f'原预览的到达站「{parsed["station"]}」已按本次用户填写更新为「{station}」；'
                          if corrected_station else '')
            stop.description = (correction + f'从原预览「{original_name}」拆出目的地，5分钟为抵达准备估算；'
                '站点到此处的通行方式、耗时与费用由实际地图路线另列，不把原句中的地铁说法当已核实路线。'
                + (('原文寄存需求以本次酒店节点为准，原片区不代表酒店；未确认酒店时不得替用户指定酒店。'
                    if declared else '原文有寄存行李需求，酒店未确认时不得替用户指定酒店。')
                   if parsed['has_luggage_instruction'] else '')
                + original_description)[:1000]
            planned.append(stop)
        if len(planned) > 12:
            raise ValueError('拆分车站与目的地后当天超过12项，请先精简活动')
        day.stops = planned


def prepare_transfer_anchors(itinerary, trip, *, prefer_user_bookings=False):
    """Build anchors, optionally replacing stale labels with new user bookings.

    Keep the default strict for ordinary timetable refresh/generation. Booking
    supplement may explicitly opt in, on its copied candidate, after applying
    booked transports and before map lookup/replanning.
    """
    user_arrivals = _user_arrival_overrides(itinerary, trip) if prefer_user_bookings else None
    from .bookings import _anchor
    for day in itinerary.days:
        day.stops = [s for s in day.stops if not s.booking_anchor.startswith('transport_')]
    for index, transfer in enumerate(itinerary.transfers):
        known = transfer.booking_status == 'user_confirmed' or transfer.schedule_status == 'queried'
        if not known:
            continue
        booking = _matching_booking(trip, transfer, index)
        before = departure_buffer(transfer.mode, booking.departure_buffer_min if booking else None)
        after = arrival_buffer(transfer.mode, booking.arrival_buffer_min if booking else None)
        station_names = [(transfer.from_city, transfer.departure_date, station_query(transfer.departure_station, transfer.mode)),
                         (transfer.to_city, transfer.arrival_date, station_query(transfer.arrival_station, transfer.mode))]
        for day in itinerary.days:
            retained = []
            for stop in day.stops:
                text = stop.name + ' ' + stop.place_name
                unresolved = stop.location_behavior == 'unlocated' or stop.location_status != 'verified'
                station = next((n for city, date, n in station_names if n and day.date == date
                                and (city_key(stop.city) == city_key(city)
                                     or (unresolved and _station_event(stop, n)))
                                and (n in text or n.removesuffix('站') in text)), '')
                if station and _station_event(stop, station):
                    continue
                if (station and stop.kind == 'meal' and stop.location_behavior == 'unlocated'
                        and stop.location_status != 'verified' and _station_meal_label(stop, station)):
                    stop.name = stop.place_name = station + '附近用餐'
                retained.append(stop)
            day.stops = retained
        points = [('transport_departure', transfer.from_city, station_query(transfer.departure_station, transfer.mode),
                   datetime.fromisoformat(f'{transfer.departure_date}T{transfer.departure_time}') - timedelta(minutes=before)),
                  ('transport_arrival', transfer.to_city, station_query(transfer.arrival_station, transfer.mode),
                   datetime.fromisoformat(f'{transfer.arrival_date}T{transfer.arrival_time}') + timedelta(minutes=after))]
        for role, city, station, at in points:
            if not station:
                continue
            day = next((d for d in itinerary.days if d.date == at.date()), None)
            if day is None:
                note = '车站／机场接驳预留时间超出行程日期，范围外接驳未核实，请调整整体日期。'
                if note not in transfer.query_note:
                    transfer.query_note = (transfer.query_note + '；' + note).strip('；')[:500]
                continue
            if at.hour * 60 + at.minute > 1434:
                raise ValueError('车站／机场接驳节点距次日不足5分钟，请调整跨日接驳或行程日期；未提前伪造到站时间')
            ref = f'selected-transfer-{index}'
            if booking:
                ref = f'transport-{trip.booked_transports.index(booking)}'
            anchor = _anchor(day, city, station, at.hour * 60 + at.minute, 'break', role, ref)
            anchor.description = ('用户填写的已订交通，未查验订单。' if transfer.booking_status == 'user_confirmed'
                else '已查询跨城时刻对应站点，车票尚未预订；请核对出行日班次。')
            if role == 'transport_departure':
                anchor.description += f'班次{transfer.departure_time}出发，提前{before}分钟到站，预留进站、安检及候车；此节点表示到站准备时间，不是车辆发车时间。'
            else:
                anchor.description += f'班次{transfer.arrival_time}到达。'
                anchor.description += (f'另预留{after}分钟出站／取行李；' if after else '不额外统一增加30分钟等待；')
                anchor.description += '本接驳节点另显示5分钟出站准备估算，实际出站快慢请自行确认；后续市内路程另算。'
            day.stops.append(anchor)
    split_arrival_movements(itinerary, _user_arrivals=user_arrivals)
    for day in itinerary.days:
        arrivals = {}
        for stop in day.stops:
            if stop.booking_anchor == 'transport_arrival':
                key = city_key(stop.city)
                arrivals[key] = min(arrivals.get(key, 1440), minutes(stop.start))
        day.stops.sort(key=lambda s: (max(minutes(s.start), arrivals.get(city_key(s.city), 0)),
                                      0 if s.booking_anchor == 'transport_arrival' else 1))
        if len(day.stops) > 12:
            raise ValueError('加入车站／机场后每天超过12项，请先精简活动')
