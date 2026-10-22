"""User-declared bookings and fixed day anchors; no ticket/availability claims."""
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import math

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


def apply_hotel_anchors(itinerary, trip, only_day=None):
    hotels = confirmed_hotels(trip)
    for index, day in enumerate(itinerary.days):
        if only_day is not None and index != only_day:
            continue
        # Idempotent refresh; retain the requested departure clock before removing old anchors.
        old_depart = next((s.start for s in day.stops if s.booking_anchor == 'hotel_departure'), None)
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
        if morning:
            start = minutes(old_depart or (real[0].start if real else '09:00'))
            if index == 0:
                start = max(start, minutes(trip.start_time))
            day.stops.insert(0, _anchor(day, morning.city, morning.name, start, 'hotel', 'hotel_departure', hotel_ref(morning)))
            day.stops[0].booking_address = morning.address
        if evening:
            end = max((minutes(s.end) for s in day.stops), default=minutes(trip.start_time))
            end = max(end, max((minutes(t.arrival_time) for t in arrivals if city_key(t.to_city) == city_key(evening.city)), default=0))
            day.stops.append(_anchor(day, evening.city, evening.name, min(end, 1434), 'hotel', 'hotel_return', hotel_ref(evening)))
            day.stops[-1].booking_address = evening.address
            day.stops[-1].description += '到店能否立即入住或寄存行李，请向酒店确认。'
            day.accommodation = f'已填酒店：{evening.name}（用户确认，未查验订单）；{evening.check_in}入住，{evening.check_out}退房。'
        if morning and not evening:
            day.accommodation = f'从已填酒店「{morning.name}」出发；当天住宿／返程以实际安排为准。'
        if len(day.stops) > 12:
            raise ValueError(f'第{index + 1}天加入酒店后超过12项，请先精简活动')
    # A known total replaces the covered nights' estimates once, never once per person/night.
    for hotel in hotels:
        if hotel.total_cost is None:
            continue
        ref = hotel_ref(hotel)
        covered = [d for d in itinerary.days if any(s.booking_anchor == 'hotel_return' and s.booking_ref == ref for s in d.stops)]
        posting = next((d for d in itinerary.days if d.date >= hotel.check_in), itinerary.days[0])
        for day in itinerary.days:
            day.expenses = [e for e in day.expenses if e.booking_ref != ref]
        for day in covered:
            day.expenses = [e for e in day.expenses if e.category != 'hotel' or e.booking_ref]
        posting.expenses.append(Expense(label=f'已填住宿总额：{hotel.name}', category='hotel',
            unit_cost=hotel.total_cost, basis='group', booking_ref=ref,
            note='用户填写的本次入住全员总额；非酒店实时房价或订单核验。'))


def apply_transport_anchors(itinerary, trip):
    from .transfer_buffers import arrival_buffer

    for day in itinerary.days:
        day.stops = [s for s in day.stops if not s.booking_anchor.startswith('transport_')]
    for index, booking in enumerate(trip.booked_transports):
        ref = f'transport-{index}'
        before = booking.departure_buffer_min if booking.departure_buffer_min is not None else (120 if booking.mode == 'flight' else 60)
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
                departure_buffer = (booking.departure_buffer_min if booking.departure_buffer_min is not None else (120 if booking.mode == 'flight' else 60)) if booking else 0
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
