"""Fit meals into ordinary hours when verified transfers and adjacent activities allow it."""
import math

from .validation import minutes

WINDOWS = {'breakfast': (7 * 60, 9 * 60 + 30), 'lunch': (11 * 60, 14 * 60), 'dinner': (17 * 60, 20 * 60)}
LABELS = {'breakfast': '早餐', 'lunch': '午餐', 'dinner': '晚餐'}


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
    notes = []
    if not day.stops:
        return '本日未安排正式餐饮地点，请结合交通时间在车站或沿途就餐，或自带干粮和饮水。'
    start, end = minutes(day.stops[0].start), minutes(day.stops[-1].end)
    expected = []
    if start <= 13 * 60 + 30 and end >= 11 * 60 + 30:
        expected.append('lunch')
    if start <= 19 * 60 + 30 and end >= 17 * 60 + 30:
        expected.append('dinner')
    for key in expected:
        if not any(period(s) == key for s in meals):
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
