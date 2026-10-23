"""Fit meals into ordinary hours when verified transfers and adjacent activities allow it."""
import math
import re

from .validation import minutes

WINDOWS = {'breakfast': (7 * 60, 9 * 60 + 30), 'lunch': (11 * 60, 14 * 60), 'dinner': (17 * 60, 20 * 60)}
LABELS = {'breakfast': '早餐', 'lunch': '午餐', 'dinner': '晚餐'}
PREFERRED = {'breakfast': 8 * 60, 'lunch': 12 * 60 + 30, 'dinner': 18 * 60 + 30}


def period(stop):
    if stop.meal_period:
        return stop.meal_period
    for key, label in LABELS.items():
        if label in stop.name:
            return key
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


def meal_opening_reference(stop, start, end):
    """Interpret only a restaurant's explicit map hours, never area hours.

    Even ``open`` means a map reference; departure-date opening still needs
    official confirmation. No matching clock ranges means unknown, not closed.
    """
    if stop.opening_hours_status != 'map_reference' or stop.opening_hours_scope != 'place':
        return 'unknown'
    text = (stop.opening_hours_today or stop.opening_hours).strip()
    if not text:
        return 'unknown'
    if re.fullmatch(r'(?:全天)?(?:暂停营业|停止营业|暂不营业|休息|闭店)[。；;\s]*', text):
        return 'closed'
    if re.search(r'24\s*小时|全天营业', text):
        return 'open'
    ranges = []
    for ah, am, bh, bm in re.findall(r'(\d{1,2}):(\d{2})\s*[-~～—–至]\s*(\d{1,2}):(\d{2})', text):
        left, right = int(ah) * 60 + int(am), int(bh) * 60 + int(bm)
        if not 0 <= left < 1440 or not 0 <= right <= 1440 or int(am) >= 60 or int(bm) >= 60:
            continue
        ranges.extend([(left, right)] if right > left else [(left, 1440), (0, right)] if right < left else [])
    if not ranges:
        return 'unknown'
    return 'open' if any(left <= start and end <= right for left, right in ranges) else 'closed'


def align_meals(trip, itinerary, routes, only_day=None, protected_text='', respect_opening=False):
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
                preferred = PREFERRED[key]
                start = min(max(preferred, low), high)
                if (respect_opening and stop.location_status == 'verified'
                        and meal_opening_reference(stop, start, start + duration) != 'open'):
                    continue
                stop.start = f'{start // 60:02d}:{start % 60:02d}'
                end = start + duration
                stop.end = f'{end // 60:02d}:{end % 60:02d}'
                adjustments.append(f'第{day_index + 1}天{LABELS[key]}从{original // 60:02d}:{original % 60:02d}调整为{stop.start}；保留用餐时长与已查询的前后通行时间。')
        day.meal_note = meal_notes(day)
    return adjustments


def meal_schedule_checks(trip, itinerary, routes, only_day=None, protected_text='', accepted_late=()):
    """Check final meal clocks; missing facts are notices, definite conflicts stop.

    This does not claim a reservation, price, current opening or onboard food.
    The caller decides how to display notices and whether to publish conflicts.
    """
    from .models import city_key
    from .transfer_buffers import arrival_buffer

    conflicts, notices = [], []
    for index, day in enumerate(itinerary.days):
        if only_day is not None and index != only_day:
            continue
        for stop in day.stops:
            if stop.kind != 'meal':
                continue
            key = period(stop)
            lower, upper = WINDOWS[key]
            start, end = minutes(stop.start), minutes(stop.end)
            arrivals = [t for t in itinerary.transfers if t.arrival_date == day.date
                        and city_key(t.to_city) == city_key(stop.city)]
            ready = 0
            for transfer in arrivals:
                booking = next((b for b in trip.booked_transports
                    if city_key(b.from_city) == city_key(transfer.from_city)
                    and city_key(b.to_city) == city_key(transfer.to_city)), None)
                ready = max(ready, minutes(transfer.arrival_time) + arrival_buffer(
                    transfer.mode, booking.arrival_buffer_min if booking else None))
            late_arrival = ready > upper and start >= ready
            # A meal requirement prepared around the actual arrival can become
            # later after real station→hotel→restaurant travel. Only this
            # bounded arrival lunch gets the grace; a packed sightseeing day
            # cannot use it to silently postpone an ordinary existing meal.
            arrival_lunch = bool(arrivals and key == 'lunch' and 11 * 60 <= ready <= 16 * 60
                and stop.id.startswith(('meal-need-', 'late-meal-')) and start <= 16 * 60 + 30)
            if key == 'lunch' and start > 16 * 60 + 30:
                late_arrival = False
            explicit_time = stop.start in protected_text
            if end <= start:
                conflicts.append(f'第{index + 1}天「{stop.name}」用餐时长无效')
            elif not lower <= start <= upper:
                if late_arrival or arrival_lunch:
                    notices.append(f'第{index + 1}天抵达较晚，{stop.start}优先补餐；能否营业与实际等待仍待核实。')
                elif explicit_time:
                    notices.append(f'第{index + 1}天保留你明确指定的{stop.start}用餐；超出常见{LABELS[key]}时段。')
                elif stop.id in accepted_late:
                    notices.append(f'第{index + 1}天{LABELS[key]}在{stop.start}，沿用旧版本已存在的常见时段外安排；'
                        '本次补录没有新增这个问题。')
                else:
                    conflicts.append(f'第{index + 1}天{LABELS[key]}排在{stop.start}，无法兼顾常见用餐时段，请减少前序活动或明确晚吃需求')
            reference = meal_opening_reference(stop, start, end)
            if reference == 'closed':
                conflicts.append(f'第{index + 1}天「{stop.name}」{stop.start}—{stop.end}不在已取得的餐馆营业时间参考内，请更换餐馆或时间')
            elif reference == 'unknown':
                notices.append(f'第{index + 1}天「{stop.name}」营业时间尚未核实，不能保证此时可用餐。')
            else:
                notices.append(f'第{index + 1}天「{stop.name}」符合已取得的地图营业参考，出行日营业仍请向店家确认。')
            if stop.location_status != 'verified':
                notices.append(f'第{index + 1}天{LABELS[key]}尚未确定具体餐馆，需确认地点后才能核对接驳。')
            if not any(e.category == 'food' and e.stop_id == stop.id for e in day.expenses):
                notices.append(f'第{index + 1}天「{stop.name}」缺少对应餐费预留；未当作免费或已付款。')
        for transfer in itinerary.transfers:
            if transfer.departure_date != day.date or transfer.arrival_date != day.date:
                continue
            for key, at in PREFERRED.items():
                if minutes(transfer.departure_time) <= at <= minutes(transfer.arrival_time):
                    notices.append(f'第{index + 1}天跨城在途覆盖常见{LABELS[key]}时间；车上供餐未核实，请提前准备补给或自行确认沿途用餐。')
    return list(dict.fromkeys(conflicts)), list(dict.fromkeys(notices))
