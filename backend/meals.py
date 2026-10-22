"""Fit meals into ordinary hours when verified transfers and adjacent activities allow it."""
import math
import re
from datetime import datetime, time

from .validation import minutes
from .models import Stop, Expense, city_key

WINDOWS = {'breakfast': (7 * 60, 9 * 60 + 30), 'lunch': (11 * 60, 14 * 60), 'dinner': (17 * 60, 20 * 60)}
LABELS = {'breakfast': '早餐', 'lunch': '午餐', 'dinner': '晚餐'}


def restaurant_key(stop):
    """Different branches of a chain are the same dining experience."""
    return restaurant_name_key(stop.city, stop.matched_name or stop.place_name or stop.name)


def restaurant_name_key(city, name):
    name = re.split(r'[（(]', name)[0]
    name = re.sub(r'(?:早餐|午餐|晚餐|用餐)$', '', name).strip()
    name = re.sub(r'(?<=..)(?:餐厅|餐馆|饭店|酒楼|酒家|生煎馆|生煎|面馆|汤包馆|汤包)$', '', name)
    return city_key(city), re.sub(r'\s+', '', name).casefold()


def provisions(day):
    return {key: e for key in ('lunch', 'dinner') for e in day.expenses
            if e.category == 'food' and not e.stop_id and e.note.startswith(f'在途用餐预算预留：{key}；')}


def required_periods(trip, itinerary, index):
    if trip.study:
        return []
    day = itinerary.days[index]
    beginning = minutes(trip.start_time) if index == 0 else 0
    end = 23 * 60 + 59
    if index == len(itinerary.days) - 1:
        returns = [t for t in itinerary.transfers if trip.return_to_origin and
                   t.arrival_date == day.date and city_key(t.to_city) == city_key(trip.origin)]
        end = minutes(returns[-1].arrival_time) if returns else max(
            [minutes(s.end) for s in day.stops] + ([20 * 60] if trip.days > 1 else [beginning]))
    return [key for key, center in (('lunch', 12 * 60 + 30), ('dinner', 18 * 60 + 30))
            if beginning <= center <= end]


def prepare_meal_slots(trip, itinerary, only_day=None):
    """Cover whole travel days, including evenings after sightseeing and rail meals.

    New local slots remain unverified until the map enrichment chooses a real,
    priced nearby restaurant. Never fabricate a restaurant on a moving train.
    """
    if trip.study:
        return []
    midnight = datetime.combine(trip.start_date, time())
    def moment(date, value):
        return int((datetime.combine(date, time.fromisoformat(value)) - midnight).total_seconds() // 60)
    windows, lower = [], minutes(trip.start_time)
    city = trip.journey_cities[0]
    for transfer in itinerary.transfers:
        windows.append((city, lower, moment(transfer.departure_date, transfer.departure_time) - 45))
        city, lower = transfer.to_city, moment(transfer.arrival_date, transfer.arrival_time) + 45
    windows.append((city, lower, trip.days * 1440 - 1))
    destinations = {city_key(c) for c in trip.city_route}
    ids = {s.id for d in itinerary.days for s in d.stops}
    changes = []
    for index, day in enumerate(itinerary.days):
        if only_day is not None and index != only_day:
            continue
        for key in required_periods(trip, itinerary, index):
            lo, hi = WINDOWS[key]
            has_dining_window = any(city_key(c) in destinations and
                max(lo, a - index * 1440) <= min(hi, b - index * 1440 - 45) for c, a, b in windows)
            invalid = [s for s in day.stops if s.kind == 'meal' and period(s) == key and
                (re.search(r'列车|车上|提前备餐|在途|候车', s.name) or
                 (not has_dining_window and not lo <= minutes(s.start) <= hi))]
            if invalid:
                removed = {s.id for s in invalid}
                day.stops = [s for s in day.stops if s not in invalid]
                day.expenses = [e for e in day.expenses if e.stop_id not in removed]
            existing = [s for s in day.stops if s.kind == 'meal' and period(s) == key]
            if existing:
                day.expenses = [e for e in day.expenses if e is not provisions(day).get(key)]
                continue
            preferred = 12 * 60 + 30 if key == 'lunch' else 18 * 60
            slots = []
            for city, start, end in windows:
                if city_key(city) not in destinations:
                    continue
                first, last = max(lo, start - index * 1440), min(hi, end - index * 1440 - 45)
                if first > last:
                    continue
                # Prefer a real free gap. The schedule repair still checks actual routes.
                candidates = [max(first, min(preferred, last)), first]
                candidates += [minutes(s.end) + buffer for s in day.stops if city_key(s.city) == city_key(city)
                               for buffer in (20, 10, 0)]
                for value in candidates:
                    if first <= value <= last and not any(minutes(s.start) < value + 45 and minutes(s.end) > value
                                                         for s in day.stops):
                        slots.append((abs(value - preferred), value, city))
            if slots and len(day.stops) < 12:
                _, value, city = min(slots)
                identifier = f'd{index + 1}-{key}'
                while identifier in ids:
                    identifier += '-new'
                ids.add(identifier)
                day.stops.append(Stop(id=identifier, name=f'{LABELS[key]}（待核实餐馆）', city=city, kind='meal',
                    meal_period=key, start=f'{value // 60:02d}:{value % 60:02d}',
                    end=f'{(value + 45) // 60:02d}:{(value + 45) % 60:02d}', location_behavior='unlocated'))
                day.stops.sort(key=lambda s: s.start)
                day.expenses = [e for e in day.expenses if e is not provisions(day).get(key)]
                changes.append(f'第{index + 1}天补充{LABELS[key]}时段，随后核对附近真实餐馆、人均与路线。')
            elif not has_dining_window:
                if key not in provisions(day):
                    day.expenses.append(Expense(label=f'{LABELS[key]} · 在途用餐预算预留', category='food',
                        unit_cost=50, basis='person', note=f'在途用餐预算预留：{key}；每人50元仅为备餐预算，并非车餐或餐馆报价；请提前购买食物和饮水，不保证列车供餐。'))
                    changes.append(f'第{index + 1}天{LABELS[key]}时段受跨城交通占用，安排提前备餐并计入明确标注的预算预留。')
            else:
                # A destination dining window exists: do not disguise a packed
                # sightseeing schedule as a rail meal or silently omit dinner.
                raise ValueError(f'第{index + 1}天没有为{LABELS[key]}留足45分钟，请减少可选活动并保留用餐时间')
    return changes


def require_meal_coverage(trip, itinerary, only_day=None):
    for index, day in enumerate(itinerary.days):
        if only_day is not None and index != only_day:
            continue
        for key in required_periods(trip, itinerary, index):
            if not any(s.kind == 'meal' and period(s) == key and WINDOWS[key][0] <= minutes(s.start) <= WINDOWS[key][1]
                       for s in day.stops) and key not in provisions(day):
                raise ValueError(f'第{index + 1}天遗漏{LABELS[key]}，请保留用餐安排及费用')


def period(stop):
    for key, label in LABELS.items():
        if label in stop.name:
            return key
    if stop.meal_period:
        return stop.meal_period
    start = minutes(stop.start)
    return 'breakfast' if start < 10 * 60 + 30 else 'lunch' if start < 16 * 60 else 'dinner'


def meal_notes(day):
    meals = [s for s in day.stops if s.kind == 'meal']
    notes = [f'{LABELS[key]}：在途或候车时段，请提前备好食物和饮水；已按每人{expense.unit_cost}元预留预算，非餐馆或车餐报价，不保证列车供餐。'
             for key, expense in provisions(day).items()]
    if not day.stops:
        return ' '.join(notes) or '本日未安排正式餐饮地点，请结合交通时间在车站或沿途就餐，或自带干粮和饮水。'
    start, end = minutes(day.stops[0].start), minutes(day.stops[-1].end)
    expected = []
    if start <= 13 * 60 + 30 and end >= 11 * 60 + 30:
        expected.append('lunch')
    if start <= 19 * 60 + 30 and end >= 17 * 60 + 30:
        expected.append('dinner')
    for key in expected:
        if not any(period(s) == key for s in meals) and key not in provisions(day):
            notes.append(f'{LABELS[key]}未安排正式餐饮地点，请在沿途就餐，或自带干粮和饮水；这部分消费需自行预留。')
    for stop in meals:
        key = period(stop)
        lower, upper = WINDOWS[key]
        if not lower <= minutes(stop.start) <= upper:
            notes.append(f'{LABELS[key]}安排在{stop.start}，超出常见用餐时段；可提前沿途补餐或自带干粮，避免空腹等待。')
        if stop.location_status != 'verified':
            notes.append(f'{LABELS[key]}具体餐馆尚未核实，请在沿途选择可营业餐馆，或自带干粮和饮水。')
    if not meals and not expected:
        notes.append('本段行程未安排餐饮；如需用餐，请在沿途就餐或自带干粮和饮水。')
    return ' '.join(dict.fromkeys(notes))[:1000]


def align_meals(trip, itinerary, routes, only_day=None, protected_text=''):
    adjustments = []
    for day_index, day in enumerate(itinerary.days):
        if only_day is not None and day_index != only_day:
            continue
        for index, stop in enumerate(day.stops):
            if stop.kind != 'meal':
                continue
            key = period(stop)
            lower, upper = WINDOWS[key]
            original = minutes(stop.start)
            duration = minutes(stop.end) - original
            if lower <= original <= upper or duration <= 0 or stop.start in protected_text:
                continue
            earliest = minutes(trip.start_time) if day_index == 0 else 0
            latest = 23 * 60 + 59 - duration
            constraints_known = True
            if index:
                previous = day.stops[index - 1]
                incoming = next((r for r in routes if r.day_index == day_index and r.from_id == previous.id and r.to_id == stop.id), None)
                if not incoming or not incoming.available or incoming.duration_s is None:
                    constraints_known = False
                else:
                    earliest = minutes(previous.end) + math.ceil(incoming.duration_s / 60)
            if index < len(day.stops) - 1:
                after = day.stops[index + 1]
                outgoing = next((r for r in routes if r.day_index == day_index and r.from_id == stop.id and r.to_id == after.id), None)
                if not outgoing or not outgoing.available or outgoing.duration_s is None:
                    constraints_known = False
                else:
                    latest = minutes(after.start) - math.ceil(outgoing.duration_s / 60) - duration
            # Do not move a meal across an unverified/intercity leg or another activity.
            low, high = max(earliest, lower), min(latest, upper)
            if constraints_known and low <= high:
                preferred = {'breakfast': 8 * 60, 'lunch': 12 * 60 + 30, 'dinner': 18 * 60 + 30}[key]
                start = min(max(preferred, low), high)
                stop.start = f'{start // 60:02d}:{start % 60:02d}'
                end = start + duration
                stop.end = f'{end // 60:02d}:{end % 60:02d}'
                adjustments.append(f'第{day_index + 1}天{LABELS[key]}从{original // 60:02d}:{original % 60:02d}调整为{stop.start}；保留用餐时长与已查询的前后通行时间。')
        day.meal_note = meal_notes(day)
    return adjustments
