"""Deterministic constraints applied before publishing a plan."""
from datetime import datetime, time, timedelta, timezone
import math

from .models import Expense, city_key


def check_departure(trip, now=None):
    now = now or datetime.now(timezone(timedelta(hours=8)))
    departure = datetime.combine(trip.start_date, time.fromisoformat(trip.start_time), tzinfo=now.tzinfo)
    if departure <= now:
        raise ValueError('出行日期和首日开始时间必须晚于当前北京时间，请更新后再规划')


def schedule_journey(trip, itinerary, routes=(), only_day=None):
    """Keep visit durations and city order; fit each block into its transport window.

    Never move trains, invent travel times or silently discard activities.
    A fixed 45 minute station allowance is a minimum, not verified station access.
    """
    midnight = datetime.combine(trip.start_date, time())
    def moment(day, value):
        return int((datetime.combine(day, time.fromisoformat(value)) - midnight).total_seconds() // 60)
    lower = moment(trip.start_date, trip.start_time)
    city = city_key(trip.journey_cities[0])
    windows = []
    for transfer in itinerary.transfers:
        departure = moment(transfer.departure_date, transfer.departure_time)
        arrival = moment(transfer.arrival_date, transfer.arrival_time)
        if city_key(transfer.from_city) != city or departure < lower or arrival <= departure:
            raise ValueError('跨城交通顺序或时间不成立，请先确定连续且不重叠的交通安排')
        windows.append((city, lower, departure - 45))
        city, lower = city_key(transfer.to_city), arrival + 45
    windows.append((city, lower, trip.days * 1440 - 1))
    changes = []
    proposals = []
    cursor, window_index = moment(trip.start_date, trip.start_time), 0
    for day_index, day in enumerate(itinerary.days):
        for index, stop in enumerate(day.stops):
            duration = moment(day.date, stop.end) - moment(day.date, stop.start)
            if duration <= 0:
                raise ValueError(f'{stop.name}的活动时长无效')
            city = city_key(stop.city or trip.city_route[0])
            while window_index < len(windows) and (windows[window_index][0] != city or windows[window_index][2] < max(cursor, day_index * 1440)):
                window_index += 1
            if window_index == len(windows):
                raise ValueError(f'第{day_index + 1}天「{stop.name}」不在该城市的到达与离开时间窗口内；纯在途日不要安排目的地活动')
            _, beginning, deadline = windows[window_index]
            deadline = min(deadline, (day_index + 1) * 1440 - 1)
            transfer_minutes = 0
            if index:
                before = day.stops[index - 1]
                leg = next((r for r in routes if r.day_index == day_index and r.from_id == before.id and r.to_id == stop.id and r.available), None)
                if leg and leg.duration_s is not None and city_key(before.city or trip.city_route[0]) == city:
                    transfer_minutes = math.ceil(leg.duration_s / 60)
            earliest = max(cursor + transfer_minutes, beginning, day_index * 1440)
            # Reserve the known minimum duration of the rest of this city's block.
            remaining = duration
            previous = stop
            for following in day.stops[index + 1:]:
                if city_key(following.city or trip.city_route[0]) != city:
                    break
                remaining += moment(day.date, following.end) - moment(day.date, following.start)
                leg = next((r for r in routes if r.day_index == day_index and r.from_id == previous.id and r.to_id == following.id and r.available), None)
                if leg and leg.duration_s is not None:
                    remaining += math.ceil(leg.duration_s / 60)
                previous = following
            latest = deadline - remaining
            if earliest > latest:
                raise ValueError(f'第{day_index + 1}天「{stop.name}」及后续活动无法在跨城交通前完成；请减少活动或选择更晚车次，不能压缩实际通行和游览时长')
            start = min(max(moment(day.date, stop.start), earliest), latest)
            end = start + duration
            new_times = (f'{start % 1440 // 60:02d}:{start % 60:02d}', f'{end % 1440 // 60:02d}:{end % 60:02d}')
            if only_day is not None and day_index != only_day and (stop.start, stop.end) != new_times:
                raise ValueError(f'第{day_index + 1}天原有安排也存在时间冲突；单日修改不能改变其他日期，请使用整体修改')
            proposals.append((stop, new_times))
            if (stop.start, stop.end) != new_times:
                changes.append(f'第{day_index + 1}天「{stop.name}」按跨城交通、45分钟进出站缓冲及实际市内路线调整为{new_times[0]}–{new_times[1]}。')
            cursor = end
    for stop, times in proposals:
        stop.start, stop.end = times
    return changes


def price_meals(itinerary, only_day=None):
    for index, day in enumerate(itinerary.days):
        if only_day is not None and index != only_day:
            continue
        meals = [s for s in day.stops if s.kind == 'meal']
        if not meals:
            continue  # Retain explicit provisions for meals in transit.
        from .meals import provisions
        reserved = list(provisions(day).values())
        expenses = [e for e in day.expenses if e.category != 'food' or any(e is value for value in reserved)]
        for stop in meals:
            if (stop.location_status != 'verified' or not stop.information_source.startswith('https://www.amap.com/place/')
                    or stop.average_cost_yuan is None or stop.average_cost_yuan <= 0):
                raise ValueError(f'「{stop.name}」未取得高德餐馆人均消费，不能发布凭空估计的餐费；请更换有消费参考的餐馆或检查高德详情接口权限')
            expenses.append(Expense(label=f'{stop.name} · 高德人均参考'[:160], category='food',
                unit_cost=stop.average_cost_yuan, quantity=1, basis='person', estimated=True,
                stop_id=stop.id, note=f'高德餐馆人均消费×同行人数；非菜单实价或锁价。来源：{stop.information_source}'[:500]))
        if len(expenses) > 20:
            raise ValueError('费用项目过多，请减少活动后再规划')
        day.expenses = expenses


def require_feasible(report):
    if report.errors:
        messages = list(dict.fromkeys(issue.message for issue in report.errors))
        raise ValueError('方案未通过可行性校验，未发布或保存。' + '；'.join(messages[:6]))
