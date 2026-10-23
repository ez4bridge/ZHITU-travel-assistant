"""User-declared bookings and fixed day anchors; no ticket/availability claims."""
from datetime import datetime, time, timedelta
from decimal import Decimal
import hashlib
import math
import re

from .models import Stop, Expense, city_key
from .validation import minutes
from .planning import clock


def hotel_ref(hotel):
    if hotel.booking_id:
        return hotel.booking_id
    raw = f'{city_key(hotel.city)}|{hotel.name}|{hotel.check_in}|{hotel.check_out}'
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def confirmed_hotels(trip):
    if trip.confirmed_hotels:
        return trip.confirmed_hotels
    if trip.confirmed_hotel.strip():
        from .models import ConfirmedHotel
        return [ConfirmedHotel(city=trip.city_route[0], name=trip.confirmed_hotel,
            check_in=trip.start_date, check_out=trip.start_date + timedelta(days=trip.days))]
    return []


def is_confirmed_hotel(trip, stop, day_date):
    return stop.kind == 'hotel' and any(city_key(h.city) == city_key(stop.city)
        and h.check_in <= day_date <= h.check_out and h.name in (stop.name, stop.place_name)
        for h in confirmed_hotels(trip))


def validate_bookings(trip):
    end = trip.start_date + timedelta(days=trip.days)
    hotels = confirmed_hotels(trip)
    refs = [hotel_ref(h) for h in hotels]
    if len(refs) != len(set(refs)):
        raise ValueError('酒店入住信息重复，请合并或删除重复项')
    for index, hotel in enumerate(hotels):
        if city_key(hotel.city) not in {city_key(c) for c in trip.city_route}:
            raise ValueError(f'酒店「{hotel.name}」请关联行程中的目的地城市；下属区县／分店位置可填在地址中')
        if hotel.check_in >= end or hotel.check_out < trip.start_date:
            raise ValueError(f'酒店「{hotel.name}」与当前行程日期没有交集')
        if any(max(hotel.check_in, other.check_in) < min(hotel.check_out, other.check_out)
               for other in hotels[:index]):
            raise ValueError('同一晚填写了多家酒店，请按实际换酒店日期拆分，不重复占用过夜日期')
    pairs = list(zip(trip.journey_cities, trip.journey_cities[1:]))
    used = set()
    for booking in trip.booked_transports:
        matches = [i for i, (a, b) in enumerate(pairs)
                   if city_key(a) == city_key(booking.from_city) and city_key(b) == city_key(booking.to_city)]
        if booking.transfer_index is not None:
            matches = [i for i in matches if i == booking.transfer_index]
        if len(matches) != 1 or matches[0] in used:
            raise ValueError('已订交通须对应唯一跨城段，不能改变原城市顺序或重复填写同一段')
        used.add(matches[0])
        if booking.departure_date < trip.start_date or booking.arrival_date >= end:
            raise ValueError('已订交通超出本次行程日期，请先调整整体出行日期／天数')
        if booking.departure_date == trip.start_date and minutes(booking.departure_time) < minutes(trip.start_time):
            raise ValueError('已订首日交通早于当前出发时间，请先把首日开始设为更早时间')


def normalize_booking_start_time(trip):
    """A first-day ticket may start before the form's "first day from" clock."""
    from .transfer_buffers import departure_buffer
    for booking in trip.booked_transports:
        if booking.departure_date == trip.start_date:
            buffer = departure_buffer(booking.mode, booking.departure_buffer_min)
            trip.start_time = clock(min(minutes(trip.start_time), max(0, minutes(booking.departure_time) - buffer)))
    return trip


_LODGING_MOVE = (r'(?:地铁|打车|公交|步行|驾车|乘坐|乘)?\s*(?:返回|回到|前往|去往|回)\s*'
                 r'(?:已填|已订|确认)?\s*(?:酒店|住处|住所|宾馆|民宿|客栈|住地|住宿)')
# Full label only: “前往酒店观景台” is a real place, not a movement.
RETURN_TO_LODGING = re.compile(rf'^{_LODGING_MOVE}(?:[。.]|[（(][^）)]*[）)])?$')
# “酒店退房并前往上海虹桥站” is the same movement the station anchor times.
MOVE_TO_HUB = re.compile(
    r'^(?:从|在)?[^，,。]{0,20}(?:退房|寄存行李|取行李)?\s*(?:并|后)?\s*'
    r'(?:前往|去往|赶往|出发前往|乘车前往|步行前往|乘地铁前往|乘地铁到|打车前往)\s*'
    r'[^，,。]{1,40}(?:站|机场|航站楼)(?:[。.]|[（(][^）)]*[）)])?$')


def _removable_activities(itinerary, *, day_indices=None, protected_ids=()):
    """Select explicit, unique activity ids without losing user facts or money.

    Activity state lives outside Itinerary, so callers pass locked/completed ids
    in protected_ids. Ledger protections are intrinsic and always apply, even
    when an expense happens to be posted on a different day from its activity.
    """
    selected = set(range(len(itinerary.days))) if day_indices is None else set(day_indices)
    protected = set(protected_ids)
    protected.update(expense.stop_id for day in itinerary.days for expense in day.expenses
                     if expense.stop_id and (expense.commitment in ('paid', 'committed') or expense.booking_ref))
    references = [(index, stop) for index, day in enumerate(itinerary.days) for stop in day.stops]
    counts = {}
    for _, stop in references:
        counts[stop.id] = counts.get(stop.id, 0) + 1
    return [(index, stop) for index, stop in references
            if index in selected and stop.id and counts[stop.id] == 1 and stop.id not in protected
            and not stop.booking_anchor and not stop.booking_ref and stop.kind != 'hotel']


def drop_activity_ids(itinerary, stop_ids, *, day_indices=None, protected_ids=()) -> list[str]:
    """Remove only the requested ids within the selected zero-based day scope.

    Hotel/station anchors, explicitly protected state and paid/committed/booked
    expenses are never automatically deleted. Only the removed activities'
    planned, unbooked estimates are discarded. Ambiguous duplicate ids and
    missing ids are left intact instead of guessing which activity was meant.
    The returned display names are for notices, never used as identity keys.
    """
    requested = set(stop_ids)
    eligible = _removable_activities(itinerary, day_indices=day_indices, protected_ids=protected_ids)
    targets = {(index, stop.id) for index, stop in eligible if stop.id in requested}
    dropped = []
    removed_ids = {sid for _, sid in targets}
    for index, day in enumerate(itinerary.days):
        removed = {stop.id for stop in day.stops if (index, stop.id) in targets}
        dropped.extend(stop.name for stop in day.stops if stop.id in removed)
        day.stops = [stop for stop in day.stops if stop.id not in removed]
        day.expenses = [expense for expense in day.expenses
                        if not (expense.stop_id in removed_ids and expense.commitment == 'planned'
                                and not expense.booking_ref)]
    return dropped


def drop_return_movement_stops(itinerary, *, day_indices=None, protected_ids=()):
    """“返回酒店” is a movement, not an activity: the route already times it.

    Keeping it as a stop counted the same trip twice and pushed check-in an hour
    later (reported 2026-10-22: 34-minute taxi plus a 40-minute “返回酒店”).
    """
    ids = [stop.id for _, stop in _removable_activities(itinerary, day_indices=day_indices,
                                                       protected_ids=protected_ids)
           if stop.kind != 'meal' and (RETURN_TO_LODGING.match(stop.name.strip())
               or RETURN_TO_LODGING.match((stop.place_name or '').strip())
               or MOVE_TO_HUB.match(stop.name.strip()))]
    return drop_activity_ids(itinerary, ids, day_indices=day_indices, protected_ids=protected_ids)


def drop_named_activities(itinerary, names, *, day_indices=None, protected_ids=()):
    """Compatibility helper: resolve only unambiguous names, then delete by id.

    New callers should use drop_activity_ids with structured validation ids.
    A repeated display name is not enough evidence to choose an activity.
    """
    if not names:
        return []
    selected = set(range(len(itinerary.days))) if day_indices is None else set(day_indices)
    matches = {}
    for index, day in enumerate(itinerary.days):
        if index in selected:
            for stop in day.stops:
                if stop.name in names:
                    matches.setdefault(stop.name, []).append(stop.id)
    ids = [ids[0] for ids in matches.values() if len(ids) == 1]
    return drop_activity_ids(itinerary, ids, day_indices=selected, protected_ids=protected_ids)


def drop_last_optional_stop(itinerary, day_index, *, day_indices=None, protected_ids=()):
    """Drop the day's last model activity so fixed anchors can still fit.

    Only a non-anchor, non-hotel stop is removed (anchors may trail it); its
    expenses go too. Callers must refresh that day's routes afterwards.
    """
    selected = {day_index} if day_indices is None else {day_index} & set(day_indices)
    optional = [stop for _, stop in _removable_activities(itinerary, day_indices=selected,
                                                         protected_ids=protected_ids)]
    if not optional:
        return None
    latest = max(optional, key=lambda stop: minutes(stop.end))
    dropped = drop_activity_ids(itinerary, {latest.id}, day_indices=selected, protected_ids=protected_ids)
    return dropped[0] if dropped else None


def drop_movement_to_anchors(itinerary, *, day_indices=None, protected_ids=()):
    """A stop that only restates a trip to an existing anchor is not an activity.

    This is structural, not a list of phrasings: if an activity names a hotel or
    station that already has an anchor and uses a movement verb, the anchor and
    the route charge for that trip — the activity would double-count it.
    """
    ids = []
    for index, stop in _removable_activities(itinerary, day_indices=day_indices, protected_ids=protected_ids):
        if stop.kind == 'meal':
            continue
        anchor_names = {name for anchor in itinerary.days[index].stops
                        if anchor.booking_anchor and city_key(anchor.city) == city_key(stop.city)
                        for name in (anchor.place_name, anchor.name) if name}
        text = f'{stop.name} {stop.place_name}'
        if (any(name in text for name in anchor_names)
                and re.search(r'前往|去往|赶往|返回|回到|退房|出发|抵达|到达|到', stop.name)):
            ids.append(stop.id)
    return drop_activity_ids(itinerary, ids, day_indices=day_indices, protected_ids=protected_ids)


def ease_conflicting_transfers(itinerary, trip):
    """Move intercity legs the model placed inside the day's own schedule.

    A planned or queried train that cannot leave after the day's activities is
    downgraded to a planning estimate and pushed later. The user's own booking
    never moves — that stays a hard conflict for the user to resolve.
    """
    from .transfer_buffers import departure_buffer
    booked = {(city_key(b.from_city), city_key(b.to_city)) for b in trip.booked_transports}
    eased = []
    for transfer in itinerary.transfers:
        if (city_key(transfer.from_city), city_key(transfer.to_city)) in booked:
            continue
        day = next((item for item in itinerary.days if item.date == transfer.departure_date), None)
        if day is None:
            continue
        local = [stop for stop in day.stops if city_key(stop.city) == city_key(transfer.from_city)
                 and not stop.booking_anchor.startswith('transport_')]
        if not local:
            continue
        earliest = max(minutes(stop.end) for stop in local) + 45 + departure_buffer(transfer.mode, None)
        if minutes(transfer.departure_time) >= earliest:
            continue
        departure = (datetime.combine(transfer.departure_date, time.fromisoformat(transfer.departure_time))
                     + timedelta(minutes=earliest - minutes(transfer.departure_time)))
        arrival = (datetime.combine(transfer.arrival_date, time.fromisoformat(transfer.arrival_time))
                   + timedelta(minutes=earliest - minutes(transfer.departure_time)))
        transfer.departure_date, transfer.departure_time = departure.date(), departure.strftime('%H:%M')
        transfer.arrival_date, transfer.arrival_time = arrival.date(), arrival.strftime('%H:%M')
        transfer.schedule_status = 'estimated'
        transfer.rail_option_id = transfer.train_code = ''
        if transfer.price_status == 'reference':
            transfer.price_status = 'estimated'
        kind = '航班' if transfer.mode == 'flight' else '班次'
        transfer.note = (f'原定{kind}与当天活动冲突，已顺延为规划估算；'
                         f'实际{kind}与票价请自行核对。'
                         + (' ' + transfer.note if transfer.note else ''))[:500]
        eased.append(f'{transfer.from_city}→{transfer.to_city} 顺延为 {departure.strftime("%m-%d %H:%M")} 出发的估算班次')
    return eased


def apply_booked_transports(itinerary, trip):
    validate_bookings(trip)
    for booking in trip.booked_transports:
        indexes = [i for i, t in enumerate(itinerary.transfers)
            if city_key(t.from_city) == city_key(booking.from_city) and city_key(t.to_city) == city_key(booking.to_city)
            and (booking.transfer_index is None or i == booking.transfer_index)]
        if len(indexes) != 1:
            raise ValueError('生成结果缺少已订交通对应的跨城段，请重新核对城市顺序')
        transfer = itinerary.transfers[indexes[0]]
        for key in ('departure_date', 'departure_time', 'arrival_date', 'arrival_time', 'mode'):
            setattr(transfer, key, getattr(booking, key))
        transfer.booking_status = 'user_confirmed'
        transfer.schedule_status = 'estimated'  # User assertion is not a fresh provider query.
        transfer.rail_option_id = transfer.seat_availability = ''
        transfer.queried_at = None
        transfer.train_code = booking.number
        transfer.departure_station, transfer.arrival_station = booking.departure_place, booking.arrival_place
        transfer.source = '用户填写的已订交通，未向承运方核验'
        transfer.query_note = '以用户订单为准；系统不办理预订、改签、退票或查验订单。'
        if booking.total_cost is not None:
            transfer.unit_cost, transfer.basis = booking.total_cost, 'group'
            transfer.price_status = 'user_reported'
            transfer.note = '全员该段费用由用户填写，非实时查询报价；请核对订单。'
        else:
            transfer.price_status = 'estimated'
            transfer.note = '已订时刻由用户填写；尚未填写成交总额，保留原费用估算，不能视为已付金额。'


def _anchor(day, city, name, at, kind, role, ref):
    # Distinct stable ids make later route refresh and fact protection predictable.
    token = hashlib.sha256(f'{day.date}|{ref}|{role}'.encode()).hexdigest()[:16]
    return Stop(id='booking-' + token, city=city, name=name, place_name=name,
        kind=kind, start=clock(at), end=clock(min(1439, at + 5)),
        booking_anchor=role, booking_ref=ref,
        description='用户提供的已订信息；地点仍须高德核实，预订不由系统查验。')


def apply_hotel_anchors(itinerary, trip, only_day=None, *, tolerant=False, notices=None):
    hotels = confirmed_hotels(trip)
    for index, day in enumerate(itinerary.days):
        if only_day is not None and index != only_day:
            continue
        # Idempotent refresh; retain the requested departure clock before removing old anchors.
        old_departures = {s.booking_ref: s.start for s in day.stops if s.booking_anchor == 'hotel_departure'}
        old_depart = next(iter(old_departures.values()), None)
        had_hotel = any(s.booking_anchor.startswith('hotel_') for s in day.stops)
        day.stops = [s for s in day.stops if not s.booking_anchor.startswith('hotel_')]
        real = [s for s in day.stops if not s.booking_anchor]
        first_city = day.stops[0].city if day.stops else ''
        last_city = day.stops[-1].city if day.stops else first_city
        arrivals = [t for t in itinerary.transfers if t.arrival_date == day.date]
        if arrivals:
            last_city = max(arrivals, key=lambda t: t.arrival_time).to_city
        if not first_city:
            first_city = last_city
        morning = next((h for h in hotels if h.check_in < day.date <= h.check_out
                        and city_key(h.city) == city_key(first_city)), None)
        evening = next((h for h in hotels if h.check_in <= day.date < h.check_out
                        and city_key(h.city) == city_key(last_city)), None)
        if had_hotel and not morning and not evening and day.accommodation.startswith(('已填酒店：', '从已填酒店')):
            day.accommodation = '尚未确认当天酒店，请选择当晚住宿；原填写费用不自动假定退款。'
        arrival_hotels = set()
        for transfer in sorted(arrivals, key=lambda item: item.arrival_time):
            hotel = next((h for h in hotels if h.check_in <= day.date < h.check_out
                          and city_key(h.city) == city_key(transfer.to_city)), None)
            if hotel is None or hotel_ref(hotel) in arrival_hotels:
                continue
            ref = hotel_ref(hotel)
            station = next(((position, stop) for position, stop in enumerate(day.stops)
                if stop.booking_anchor == 'transport_arrival' and city_key(stop.city) == city_key(hotel.city)
                and minutes(stop.start) >= minutes(transfer.arrival_time)), None)
            if station:
                position, anchor = station
                insertion, ready = position + 1, minutes(anchor.end)
            else:
                # No guessed station/airport: use only the selected transfer's
                # time as a lower bound and keep its missing endpoint unknown.
                from .transfer_buffers import arrival_buffer
                booking = next((b for b in trip.booked_transports
                    if city_key(b.from_city) == city_key(transfer.from_city)
                    and city_key(b.to_city) == city_key(transfer.to_city)), None)
                ready = minutes(transfer.arrival_time) + arrival_buffer(transfer.mode,
                    booking.arrival_buffer_min if booking else None)
                insertion = next((position for position, stop in enumerate(day.stops)
                    if city_key(stop.city) == city_key(hotel.city)), len(day.stops))
            at = max(ready, minutes(old_departures.get(ref, clock(ready))))
            if at + 15 > 1439:
                message = f'第{index + 1}天到达后没有足够时间办理酒店寄存／入住，请调整到达日期或住宿安排'
                if not tolerant:
                    raise ValueError(message)
                if notices is not None:
                    notices.append(message + '；已保留原安排，当晚住宿需自行确认')
                continue
            visit = _anchor(day, hotel.city, hotel.name, at, 'hotel', 'hotel_departure', ref)
            visit.end, visit.booking_address = clock(at + 15), hotel.address
            visit.description = ('抵达后先到酒店寄存行李／办理入住，先按15分钟预留；'
                '能否寄存、何时可入住及等候时间须向酒店确认，尚不表示已入住。'
                '车站／机场到酒店的交通另按实际地图路线计算。')
            day.stops.insert(insertion, visit)
            arrival_hotels.add(ref)
        if morning and hotel_ref(morning) not in arrival_hotels:
            start = minutes(old_depart or (real[0].start if real else '09:00'))
            if index == 0:
                start = max(start, minutes(trip.start_time))
            # Leaving the hotel must happen before a same-day departure from this
            # city; the day's first activity may already be in the next city.
            departures = [item for item in itinerary.transfers if item.departure_date == day.date
                          and city_key(item.from_city) == city_key(morning.city)]
            if departures:
                from .transfer_buffers import departure_buffer
                first = min(departures, key=lambda item: minutes(item.departure_time))
                booking = next((b for b in trip.booked_transports
                    if city_key(b.from_city) == city_key(first.from_city)
                    and city_key(b.to_city) == city_key(first.to_city)), None)
                buffer = departure_buffer(first.mode, booking.departure_buffer_min if booking else None)
                start = min(start, max(0, minutes(first.departure_time) - buffer - 20))
            day.stops.insert(0, _anchor(day, morning.city, morning.name, start, 'hotel', 'hotel_departure', hotel_ref(morning)))
            day.stops[0].booking_address = morning.address
        if evening:
            already_at_hotel = bool(day.stops and day.stops[-1].booking_anchor == 'hotel_departure'
                                    and day.stops[-1].booking_ref == hotel_ref(evening))
            if not already_at_hotel:
                end = max((minutes(s.end) for s in day.stops), default=minutes(trip.start_time))
                end = max(end, max((minutes(t.arrival_time) for t in arrivals if city_key(t.to_city) == city_key(evening.city)), default=0))
                day.stops.append(_anchor(day, evening.city, evening.name, min(end, 1434), 'hotel', 'hotel_return', hotel_ref(evening)))
                day.stops[-1].booking_address = evening.address
                day.stops[-1].description += '到店能否立即入住或寄存行李，请向酒店确认。'
            day.accommodation = f'已填酒店：{evening.name}（用户提供，未查验订单）；计划{evening.check_in}入住，{evening.check_out}退房；可入住与寄存时间待酒店确认。'
        if morning and not evening:
            day.accommodation = f'从已填酒店「{morning.name}」出发；当天住宿／返程以实际安排为准。'
        if len(day.stops) > 12:
            message = f'第{index + 1}天加入酒店后超过12项，请先精简活动'
            if not tolerant:
                raise ValueError(message)
            # Planning-quality problem: trim model suggestions instead of failing.
            while len(day.stops) > 12:
                dropped = drop_last_optional_stop(itinerary, index)
                if not dropped:
                    break
                if notices is not None:
                    notices.append(f'第{index + 1}天活动过多，已移除：{dropped}')
            if notices is not None and len(day.stops) > 12:
                notices.append(f'第{index + 1}天加入酒店后仍有 {len(day.stops)} 项，请自行精简')
    # A known total replaces the covered nights' estimates once, never once per person/night.
    for hotel in hotels:
        if hotel.total_cost is None:
            continue
        ref = hotel_ref(hotel)
        covered = [d for d in itinerary.days if hotel.check_in <= d.date < hotel.check_out
            and any(s.booking_anchor in ('hotel_return', 'hotel_departure') and s.booking_ref == ref for s in d.stops)]
        posting = next((d for d in itinerary.days if d.date >= hotel.check_in), itinerary.days[0])
        for day in itinerary.days:
            day.expenses = [e for e in day.expenses if e.booking_ref != ref]
        for day in covered:
            day.expenses = [e for e in day.expenses if e.category != 'hotel' or e.booking_ref]
        posting.expenses.append(Expense(label=f'已填住宿总额：{hotel.name}', category='hotel',
            unit_cost=hotel.total_cost, basis='group', booking_ref=ref,
            note='用户填写的本次入住全员总额；非酒店实时房价或订单核验。'))


def apply_transport_anchors(itinerary, trip):
    from .transfer_buffers import arrival_buffer, departure_buffer

    for day in itinerary.days:
        day.stops = [s for s in day.stops if not s.booking_anchor.startswith('transport_')]
    for index, booking in enumerate(trip.booked_transports):
        ref = f'transport-{index}'
        before = departure_buffer(booking.mode, booking.departure_buffer_min)
        after = arrival_buffer(booking.mode, booking.arrival_buffer_min)
        for day in itinerary.days:
            if day.date == booking.departure_date and booking.departure_place and city_key(booking.from_city) in {city_key(c) for c in trip.city_route}:
                at = minutes(booking.departure_time) - before
                if at < 0:
                    raise ValueError('出发前预留时间跨至前一天，请调整行程日期或填写更短且合理的预留时间')
                day.stops.append(_anchor(day, booking.from_city, booking.departure_place, at, 'break', 'transport_departure', ref))
            if day.date == booking.arrival_date and booking.arrival_place and city_key(booking.to_city) in {city_key(c) for c in trip.city_route}:
                at = minutes(booking.arrival_time) + after
                if at > 1434:
                    # Not enough time for city touring today; don't wrap to a false earlier clock.
                    raise ValueError('到达加预留时间已至次日，请调整行程日期后再补录')
                day.stops.append(_anchor(day, booking.to_city, booking.arrival_place, at, 'break', 'transport_arrival', ref))
    for day in itinerary.days:
        arrivals = {city_key(b.to_city): minutes(b.arrival_time) + arrival_buffer(b.mode, b.arrival_buffer_min)
                    for b in trip.booked_transports if b.arrival_date == day.date}
        day.stops.sort(key=lambda s: (max(minutes(s.start), arrivals.get(city_key(s.city), 0)),
                                     0 if s.booking_anchor == 'transport_arrival' else 1))
        if len(day.stops) > 12:
            raise ValueError('加入车站／机场接驳后每天超过12项，请先精简活动')


def repair_booking_schedule(plan, state=None, only_days=None):
    """Compute a complete forward schedule before committing any clock changes.

    A later fixed booking or midnight overflow can invalidate an earlier shift.
    Keep all selected dates unchanged on any conflict so callers never receive a
    half-repaired itinerary. Fixed bookings/completed times never move.
    """
    from .transfer_buffers import arrival_buffer as default_arrival_buffer
    from .transfer_buffers import departure_buffer as default_departure_buffer

    conflicts, changes, proposed = [], [], []
    activities = (state or {}).get('activities', {})
    for index, day in enumerate(plan.itinerary.days):
        if only_days is not None and index not in only_days:
            continue
        cursor = minutes(plan.request.start_time) if index == 0 else 0
        for position, stop in enumerate(day.stops):
            duration = minutes(stop.end) - minutes(stop.start)
            if duration <= 0:
                conflicts.append(f'第{index + 1}天「{stop.name}」时间无效')
                break
            travel = 0
            if position:
                prior = day.stops[position - 1]
                if city_key(prior.city) == city_key(stop.city):
                    route = next((r for r in plan.routes if r.day_index == index and r.from_id == prior.id and r.to_id == stop.id), None)
                    if route and route.available and route.duration_s is not None:
                        travel = math.ceil(route.duration_s / 60)
                else:
                    transitions = [t for t in plan.itinerary.transfers if city_key(t.from_city) == city_key(prior.city)
                                   and city_key(t.to_city) == city_key(stop.city) and t.arrival_date == day.date]
                    if transitions:
                        cursor = max(cursor, minutes(transitions[0].arrival_time))
            fixed = stop.booking_anchor.startswith('transport_') or activities.get(stop.id, {}).get('locked') or activities.get(stop.id, {}).get('status') == 'completed'
            start = max(minutes(stop.start), cursor + travel)
            # No activity may precede an arrival or overlap a departure for its city.
            for transfer in plan.itinerary.transfers:
                booking = next((b for b in plan.request.booked_transports
                    if city_key(b.from_city) == city_key(transfer.from_city) and city_key(b.to_city) == city_key(transfer.to_city)), None)
                arrival_buffer = default_arrival_buffer(booking.mode, booking.arrival_buffer_min) if booking else 0
                departure_buffer = default_departure_buffer(booking.mode, booking.departure_buffer_min) if booking else 0
                if city_key(transfer.to_city) == city_key(stop.city) and transfer.arrival_date == day.date:
                    start = max(start, minutes(transfer.arrival_time) + arrival_buffer)
                latest = minutes(transfer.departure_time) - (0 if stop.booking_anchor == 'transport_departure' else departure_buffer)
                if city_key(transfer.from_city) == city_key(stop.city) and transfer.departure_date == day.date and start + duration > latest:
                    conflicts.append(f'第{index + 1}天「{stop.name}」赶不上{transfer.departure_time}的已填／原定交通，请减少活动或调整日期')
            if fixed and start != minutes(stop.start):
                conflicts.append(f'第{index + 1}天「{stop.name}」与已订交通／已确认时间冲突，未移动固定安排')
                break
            if start + duration > 1439:
                conflicts.append(f'第{index + 1}天加入接驳后无法当天完成，请减少活动')
                break
            if start != minutes(stop.start):
                old = stop.start
                changes.append(f'第{index + 1}天「{stop.name}」{old}→{clock(start)}，预留实际所选路线时间')
            proposed.append((stop, clock(start), clock(start + duration)))
            cursor = start + duration
    if conflicts:
        return list(dict.fromkeys(conflicts)), []
    for stop, start, end in proposed:
        stop.start, stop.end = start, end
    return [], changes
