"""Prepare explicit meal needs after station/hotel anchors, before map lookup.

This creates no coordinates, restaurant facts or prices. Existing Amap generic
meal resolution chooses a real nearby candidate, or leaves the need unknown.
"""
import hashlib
import re

from .meals import LABELS, PREFERRED, WINDOWS, meal_notes, period
from .models import Stop, city_key
from .planning import clock
from .validation import minutes


def _declined(key, text):
    label = LABELS[key]
    return bool(re.search(rf'(?:不吃|不安排|无需|不用安排){label}|{label}(?:自理|自备|自行解决)', text))


def _meal_slot(day, city, start):
    """Place the need before a sightseeing block that otherwise spans its meal.

    Clock values are provisional. Real roads and fixed deadlines are checked by
    the atomic forward scheduler after lookup; a failure must not be published.
    """
    same = [(i, stop) for i, stop in enumerate(day.stops) if city_key(stop.city) == city]
    hotel_positions = [i for i, stop in same if stop.booking_anchor == 'hotel_departure']
    arrival_positions = [i for i, stop in same if stop.booking_anchor == 'transport_arrival']
    floor = max([*hotel_positions, *arrival_positions], default=-1) + 1
    for index, stop in same:
        if index < floor:
            continue
        if stop.booking_anchor == 'hotel_return' or minutes(stop.end) > start:
            return index
    return (same[-1][0] + 1) if same else len(day.stops)


def _prepare_arrival_flow(trip, itinerary, only_day=None, protected_text=''):
    """Add missing meals only; selected restaurants and prices are never invented.

    Breakfast07:00–09:30 (30min), lunch11:00–14:00 (45min),
    dinner17:00–20:00 (45min). Arrivals after20:00 get a30min dinner
    need before sightseeing; hotel luggage drop, if confirmed, stays first.
    Existing meals and explicit opt-outs take precedence. No late-night meal
    can wrap to a false earlier time.
    """
    if trip.study:
        return []
    notices = []
    text = f'{trip.preferences} {protected_text}'
    for day_index, day in enumerate(itinerary.days):
        if only_day is not None and day_index != only_day:
            continue
        original = list(day.stops)
        cities = list(dict.fromkeys(city_key(stop.city) for stop in original
            if city_key(stop.city) in {city_key(city) for city in trip.city_route}))
        for city in cities:
            local = [s for s in original if city_key(s.city) == city]
            if not local:
                continue
            arrivals = [s for s in local if s.booking_anchor == 'transport_arrival']
            hotels = [s for s in local if s.booking_anchor == 'hotel_departure']
            ready = max((minutes(s.end) for s in [*arrivals, *hotels]),
                        default=minutes(trip.start_time) if day_index == 0 else minutes(local[0].start))
            from .transfer_buffers import arrival_buffer, departure_buffer
            incoming = [t for t in itinerary.transfers if t.arrival_date == day.date and city_key(t.to_city) == city]
            for transfer in incoming:
                booking = next((b for b in trip.booked_transports if city_key(b.from_city) == city_key(transfer.from_city)
                    and city_key(b.to_city) == city_key(transfer.to_city)), None)
                ready = max(ready, minutes(transfer.arrival_time) + arrival_buffer(transfer.mode,
                    booking.arrival_buffer_min if booking else None))
            has_arrival = bool(arrivals or incoming)
            deadlines = [minutes(stop.start) for stop in local if stop.booking_anchor == 'transport_departure'
                         and minutes(stop.start) >= ready]
            for transfer in itinerary.transfers:
                if transfer.departure_date != day.date or city_key(transfer.from_city) != city:
                    continue
                booking = next((b for b in trip.booked_transports if city_key(b.from_city) == city_key(transfer.from_city)
                    and city_key(b.to_city) == city_key(transfer.to_city)), None)
                before = departure_buffer(transfer.mode, booking.departure_buffer_min if booking else None)
                at = minutes(transfer.departure_time) - before
                if at >= ready:
                    deadlines.append(at)
            deadline = min(deadlines, default=1440)
            end = min(max(minutes(s.end) for s in local), deadline)
            meals = [s for s in local if s.kind == 'meal']
            for key, at in PREFERRED.items():
                if (deadline < 1440 and ready <= WINDOWS[key][1] and deadline <= at
                        and WINDOWS[key][0] <= deadline
                        and not any(period(stop) == key for stop in meals)):
                    notices.append(f'第{day_index + 1}天{LABELS[key]}前需赶往车站／机场，未硬塞餐馆；请提前备餐或确认在途供餐，餐费待核实。')
            required = []
            if ready <= 9 * 60 and end >= 8 * 60:
                required.append('breakfast')
            if ready <= 13 * 60 + 30 and end >= 11 * 60 + 30:
                required.append('lunch')
            elif has_arrival and 11 * 60 <= ready <= 16 * 60:
                required.append('lunch')
            if ready <= 19 * 60 + 30 and end >= 17 * 60 + 30:
                required.append('dinner')
            if has_arrival and ready > WINDOWS['dinner'][1]:
                required.append('dinner')
            for key in required:
                late = key in ('lunch', 'dinner') and ready > WINDOWS[key][1]
                first_meal = bool(has_arrival and (key == 'lunch' and 11 * 60 <= ready <= 16 * 60
                                  or key == 'dinner' and ready > WINDOWS['dinner'][1]))
                existing = next((stop for stop in meals if period(stop) == key), None) if first_meal else None
                if existing is None and late and key == 'dinner':
                    existing = next((stop for stop in meals if minutes(stop.start) >= ready), None)
                if existing is not None:
                    # Eat first only when the arrival is past the meal window and
                    # the chosen meal starts right after it. Otherwise keep the
                    # saved order: hoisting a 16:25 lunch in front of a 14:15
                    # sight makes the forward scheduler push the day to 23:30.
                    boundary = max((i for i, stop in enumerate(day.stops) if city_key(stop.city) == city
                        and stop.booking_anchor in ('transport_arrival', 'hotel_departure')), default=-1) + 1
                    position = day.stops.index(existing)
                    eats_first = late and minutes(existing.start) - ready <= 90
                    if not eats_first and position >= boundary and minutes(existing.start) >= ready:
                        continue
                    day.stops.remove(existing)
                    day.stops.insert(boundary - 1 if position < boundary else boundary, existing)
                    notices.append(f'第{day_index + 1}天抵达较晚，优先保留已有餐饮安排作为补餐，未重复增加另一餐。')
                    continue
                if any(period(stop) == key for stop in meals):
                    continue
                if _declined(key, text):
                    notices.append(f'第{day_index + 1}天{LABELS[key]}按你的要求自理／不另安排，消费与可用餐时间仍请自行确认。')
                    continue
                duration = 30 if key == 'breakfast' or late else 45
                start = ready if late else max(ready, PREFERRED[key], WINDOWS[key][0])
                if deadline < 1440 and start + duration > deadline:
                    start = max(ready, WINDOWS[key][0])
                    if start + duration > deadline:
                        notices.append(f'第{day_index + 1}天{LABELS[key]}前需赶往车站／机场，未硬塞餐馆；请提前备餐或确认在途供餐，餐费待核实。')
                        continue
                if (not late and start > WINDOWS[key][1]) or start + duration > 1439:
                    raise ValueError(f'第{day_index + 1}天抵达后已无法在当天安排{LABELS[key]}，请减少活动或明确自行补餐安排')
                token = hashlib.sha256(f'{day.date}|{city}|{key}'.encode()).hexdigest()[:16]
                name = f'{LABELS[key]}（附近餐饮待选）'
                stop = Stop(id=('late-meal-' if late else 'meal-need-') + token,
                    city=local[0].city, name=name, kind='meal', meal_period=key,
                    start=clock(start), end=clock(start + duration), location_behavior='unlocated',
                    description=(('抵达较晚，先在已填酒店寄存后补餐，再安排可行的景点。' if hotels
                                  else '抵达较晚，优先补餐，再安排可行的景点；住宿尚待选择。') if late else '按常见用餐窗口补充的用餐需求。') +
                        '餐馆、营业、排队及餐费待核实；地点须由地图选取真实餐饮POI，不表示已预订或免费。',
                    location_note='用餐需求尚未定位具体餐馆，时间为待路线核对的预留；餐费待核实，不按免费计价。')
                cuisines = re.findall(r'本帮菜|川菜|粤菜|东北菜|湘菜|海鲜|素食|清真', text)
                stop.food_recommendations = list(dict.fromkeys(cuisines))[:6]
                position = _meal_slot(day, city, start)
                day.stops.insert(position, stop)
                meals.append(stop)
                notices.append(f'第{day_index + 1}天补充{LABELS[key]}需求；具体餐馆、营业与费用待地图核实／用户确认。')
        if len(day.stops) > 12:
            raise ValueError(f'第{day_index + 1}天加入酒店与用餐需求后超过12项，请减少景点；未截断已有活动')
        day.meal_note = meal_notes(day)
    if trip.confirmed_hotels or trip.confirmed_hotel:
        # New meal needs may mean leaving the hotel again after initial luggage
        # drop. Rebuild the evening endpoint only when there is an outside leg.
        from .bookings import apply_hotel_anchors
        apply_hotel_anchors(itinerary, trip, only_day)
    return list(dict.fromkeys(notices))


def prepare_arrival_flow(trip, itinerary, only_day=None, protected_text=''):
    """Atomic preparation; overflow never leaves only some new needs inserted."""
    candidate = itinerary.model_copy(deep=True)
    notices = _prepare_arrival_flow(trip, candidate, only_day, protected_text)
    itinerary.days = candidate.days
    return notices
