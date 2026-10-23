"""Read-only trip preparation status from saved evidence, never live quotes."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from urllib.parse import urlencode
import re

from .bookings import confirmed_hotels, hotel_ref
from .models import city_key
from .validation import normalized_input, validate_core

MODE_NAMES = {'train': '铁路', 'flight': '飞机', 'bus': '长途汽车', 'driving': '自驾'}

GROUP_TITLES = {'cost': '费用与预算', 'time': '时间与用餐', 'place': '地点与路线',
                'stay': '住宿', 'transport': '跨城交通', 'other': '其他检查'}
GROUP_OF = {
    'over_budget': 'cost', 'stop_cost': 'cost', 'ticket_coverage': 'cost', 'transport_fare': 'cost',
    'missing_hotel_cost': 'stay',
    'meal_arrangement': 'time', 'invalid_duration': 'time', 'time_overlap': 'time', 'transfer_time': 'time',
    'before_start': 'time', 'intercity_overlap': 'time', 'intercity_activity_overlap': 'time',
    'stay_preference': 'time',
    'missing_location': 'place', 'missing_route': 'place', 'duplicate_stop': 'place',
    'opening_hours': 'place',
    'accommodation_unconfirmed': 'stay', 'hotel_arrival': 'stay',
    'booking_departure_buffer': 'transport', 'booking_arrival_buffer': 'transport',
}
# Generic disclaimers already shown in the summary, not per-trip findings.
OMIT_CODES = {'estimated_prices', 'intercity_estimates'}


def _label(message):
    """Short subject of a per-stop message: “「南京路步行街」…” → 南京路步行街."""
    match = re.match(r'^「(.+?)」', message)
    if match:
        return match.group(1)[:24]
    head = re.split(r'[：:]', message, maxsplit=1)[0]
    return head.strip('「」 ') [:24]


def build_issues(report, blocking, itinerary, meal_conflicts):
    """Group the same findings once, with a place to jump to.

    The flat ``blocking`` list stays for compatibility; this one tells the UI
    which items block, which are advice, and which day/stop they refer to.
    """
    stops = {stop.id: (index, stop) for index, day in enumerate(itinerary.days) for stop in day.stops}
    items, covered = [], set()

    def add(code, message, severity, day_index=None, stop_id=None):
        covered.add(message)
        if code in OMIT_CODES:
            return
        if any(item['message'] == message for item in items):
            return
        if (code == 'meal_arrangement' and day_index is not None
                and any(item['code'] == 'meal_arrangement' and item['day_index'] == day_index
                        for item in items)):
            return  # the same late meal, worded once by the validator and once here
        group = GROUP_OF.get(code, 'other')
        entry = {'code': code, 'message': message, 'severity': severity, 'group': group,
                 'group_title': GROUP_TITLES[group], 'count': 1, 'day_index': day_index,
                 'stop_id': stop_id, 'label': _label(message)}
        items.append(entry)

    for entry in report['errors']:
        add(entry['code'], entry['message'], 'block', entry.get('day_index'), entry.get('stop_id'))
    for entry in report['warnings']:
        add(entry['code'], entry['message'], 'advice', entry.get('day_index'), entry.get('stop_id'))
    for entry in report['unknowns']:
        add(entry['code'], entry['message'], 'advice', entry.get('day_index'), entry.get('stop_id'))
    for message in meal_conflicts:
        if message not in covered:
            match = re.match(r'第(\d+)天', message)
            add('meal_arrangement', message, 'advice', int(match.group(1)) - 1 if match else None)
    # Remaining flat blockers are checks added in this module (hotel/transport).
    for message in blocking:
        if message in covered:
            continue
        if '总房费' in message or '金额' in message or '餐费' in message:
            add('cost_incomplete', message, 'advice')
        elif '酒店' in message or '住宿' in message:
            add('stay_incomplete', message, 'block')
        elif '交通' in message or '车站' in message or '接驳' in message or '机场' in message:
            add('transport_incomplete', message, 'block')
        else:
            add('other', message, 'advice')

    merged, seen = [], set()
    for entry in items:
        key = (entry['code'], entry['message'])
        if key in seen:
            continue
        seen.add(key)
        merged.append(entry)
    order = {'block': 0, 'advice': 1}
    merged.sort(key=lambda item: (order.get(item['severity'], 2), item['group'], item['day_index'] or 0))
    for entry in merged:
        if entry.get('code') == 'over_budget':
            entry['target'] = 'budget'
        elif entry.get('day_index') is not None:
            entry['target'] = 'day'
        else:
            entry['target'] = 'validation'
    return merged


def map_search(city, keyword):
    # Official URI search parameters; no API key, claimed availability or fare.
    # https://lbs.amap.com/api/uri-api/guide/search/search
    return 'https://uri.amap.com/search?' + urlencode({
        'keyword': keyword, 'city': city, 'view': 'list', 'callnative': 0, 'src': 'travel-assistant'})


def _night_city(plan, day):
    midnight = datetime.combine(day.date + timedelta(days=1), time())
    for transfer in plan.itinerary.transfers:
        departure = datetime.combine(transfer.departure_date, time.fromisoformat(transfer.departure_time))
        arrival = datetime.combine(transfer.arrival_date, time.fromisoformat(transfer.arrival_time))
        if departure < midnight < arrival:
            return ''  # Overnight travel, not a fabricated hotel requirement.
    # Compare times, not array order. An afternoon transfer can reach a new
    # city after the last old-city activity, even with no arrival stop yet.
    events = [(datetime.combine(day.date, time.fromisoformat(stop.end)), stop.city)
              for stop in day.stops]
    events += [(datetime.combine(t.arrival_date, time.fromisoformat(t.arrival_time)), t.to_city)
               for t in plan.itinerary.transfers if t.arrival_date <= day.date]
    city = max(events, key=lambda event: event[0])[1] if events else plan.request.city_route[0]
    return city if city_key(city) in {city_key(c) for c in plan.request.city_route} else ''


def _suggestions(plan, city):
    suggestions, seen = [], set()
    for day in plan.itinerary.days:
        for stop in day.stops:
            if (city_key(stop.city) != city_key(city) or stop.kind != 'sight'
                    or stop.location_status != 'verified' or not stop.location or not stop.poi_id):
                continue
            name = stop.matched_name or stop.place_name or stop.name
            if name in seen:
                continue
            seen.add(name)
            suggestions.append({'label': name + '周边', 'source_status': 'map_reference',
                'basis': '结合本行程已保存的高德景点位置，优先查询附近或同条地铁沿线住宿；不是酒店评分、房价或有房证明。',
                'source_url': map_search(city, name), 'query_url': map_search(city, name + '附近 酒店')})
            if len(suggestions) == 2:
                break
        if len(suggestions) == 2:
            break
    for day in plan.itinerary.days:
        if day.accommodation.strip() and any(city_key(s.city) == city_key(city) for s in day.stops):
            text = day.accommodation.strip()[:200]
            suggestions.append({'label': text, 'source_status': 'plan_suggestion',
                'basis': '原行程的待核实住宿建议；具体分店、评分、房价、空房、入住和寄存政策请到平台核实。',
                'source_url': '', 'query_url': map_search(city, '酒店')})
            break
    return suggestions


def _booking(request, index, transfer):
    return next((booking for booking in request.booked_transports
        if city_key(booking.from_city) == city_key(transfer.from_city)
        and city_key(booking.to_city) == city_key(transfer.to_city)
        and (booking.transfer_index is None or booking.transfer_index == index)), None)


def preparation_context(plan):
    if plan.request.study:
        raise ValueError('历史研学使用独立的资料与研学准备流程')
    report = validate_core(normalized_input(plan.request, plan.itinerary, plan.routes))
    blocking = list(dict.fromkeys(item['message'] for item in [*report['errors'], *report['warnings']]))
    unknowns = list(dict.fromkeys(item['message'] for item in report['unknowns']))
    blocking += [item['message'] for item in report['unknowns'] if item['code'] in {
        'stop_cost', 'ticket_coverage', 'transport_fare'}]
    for item in report['unknowns']:
        day_index = item.get('day_index')
        if (item['code'] == 'meal_arrangement' and day_index is not None
                and any(label + '未安排正式餐饮地点' in item['message'] for label in ('午餐', '晚餐'))
                and not any(e.category == 'food' for e in plan.itinerary.days[day_index].expenses)):
            blocking.append(f'第{day_index + 1}天跨越饭点但缺少餐费预留，请补充用餐或自理预算；不按免费计价')
    from .meals import meal_schedule_checks
    meal_conflicts, _ = meal_schedule_checks(plan.request, plan.itinerary, plan.routes,
                                            protected_text=plan.request.preferences)
    blocking += meal_conflicts
    missing, hotels, transports = 0, [], []
    nights = {}
    for day in plan.itinerary.days[:-1]:
        city = _night_city(plan, day)
        if city:
            key = city_key(city)
            nights.setdefault(key, {'city': city, 'dates': []})['dates'].append(day.date)
    declared = confirmed_hotels(plan.request)
    for entry in nights.values():
        city, dates = entry['city'], entry['dates']
        selected = [hotel for hotel in declared if city_key(hotel.city) == city_key(city)
                    and any(hotel.check_in <= at < hotel.check_out for at in dates)]
        uncovered = [at for at in dates if not any(h.check_in <= at < h.check_out for h in selected)]
        missing += len(uncovered)
        notes = []
        if uncovered:
            notes.append('以下住宿夜尚未确认：' + '、'.join(map(str, uncovered)))
        located = bool(selected)
        for hotel in selected:
            anchors = [stop for day in plan.itinerary.days for stop in day.stops
                       if stop.booking_anchor.startswith('hotel_') and stop.booking_ref == hotel_ref(hotel)]
            if not anchors or any(s.location_status != 'verified' or not s.location or not s.poi_id for s in anchors):
                located = False
                blocking.append(f'酒店「{hotel.name}」具体分店／位置或接驳尚未核实')
            if hotel.total_cost is None:
                blocking.append(f'酒店「{hotel.name}」未填写本次入住全员总房费，预算仍含估算')
        status = ('missing' if not selected else 'partial' if uncovered else 'located' if located else 'provided')
        hotels.append({'city': city, 'nights': list(map(str, dates)), 'status': status,
            'names': [hotel.name for hotel in selected], 'suggestions': _suggestions(plan, city) if uncovered else [],
            'query_url': map_search(city, '酒店'), 'notes': notes + [
                '到达后优先前往你确认的酒店放行李；提前寄存／入住是否可行仍需酒店确认。'] + ([
                '未确认酒店时只给片区建议，不把某家酒店或坐标当作已确定的起终点。'] if uncovered else [])})
    expected = list(zip(plan.request.journey_cities, plan.request.journey_cities[1:]))
    for index, (from_city, to_city) in enumerate(expected):
        transfer = plan.itinerary.transfers[index] if index < len(plan.itinerary.transfers) else None
        if transfer and (city_key(transfer.from_city), city_key(transfer.to_city)) != (city_key(from_city), city_key(to_city)):
            transfer = None
        booking = _booking(plan.request, index, transfer) if transfer else None
        status = 'user_provided' if booking else 'queried' if transfer and transfer.schedule_status == 'queried' else 'estimated'
        missing += int(booking is None)
        mode = transfer.mode if transfer else plan.request.intercity_mode
        options, notes = [], []
        for allowed in ([booking.mode] if booking else plan.request.intercity_modes):
            selected = transfer is not None and allowed == transfer.mode
            source = ('external_query' if booking else 'queried' if selected and transfer.schedule_status == 'queried'
                      else 'estimate' if selected else 'external_query')
            description = ('本方案采用你填写的交通时刻与金额，尚未向承运方核验；此链接仅供核对，不会自动更换已选交通。' if booking
                else '本方案已查时刻和参考票价，尚未订票；仍请核对本次出行日余票。' if source == 'queried'
                else '当前仅为规划时间和费用估算；请先查询并选择实际班次。' if selected
                else '此方式未获取本次实时班次和价格，可外跳比较后补录。')
            url = ('https://www.12306.cn/index/' if allowed == 'train'
                else 'https://flights.ctrip.com/online/channel/domestic' if allowed == 'flight'
                else map_search(from_city, to_city + ('驾车路线' if allowed == 'driving' else '长途汽车')))
            options.append({'mode': allowed, 'label': MODE_NAMES[allowed], 'source_status': source,
                            'summary': description, 'query_url': url})
        if booking:
            if any(getattr(booking, key) != getattr(transfer, key) for key in (
                    'mode', 'departure_date', 'departure_time', 'arrival_date', 'arrival_time')):
                blocking.append(f'第{index + 1}段交通与用户补录时刻不同，需重新核对')
            if not booking.departure_place or not booking.arrival_place:
                blocking.append(f'第{index + 1}段交通出发／到达车站或机场尚未补全')
            if booking.total_cost is None:
                blocking.append(f'第{index + 1}段交通未填全员金额，预算仍含估算')
            from .transfer_buffers import arrival_buffer, departure_buffer
            before = departure_buffer(booking.mode, booking.departure_buffer_min)
            after = arrival_buffer(booking.mode, booking.arrival_buffer_min)
            endpoints = [
                ('transport_departure', booking.from_city,
                 datetime.combine(booking.departure_date, time.fromisoformat(booking.departure_time)) - timedelta(minutes=before)),
                ('transport_arrival', booking.to_city,
                 datetime.combine(booking.arrival_date, time.fromisoformat(booking.arrival_time)) + timedelta(minutes=after))]
            reference = f'transport-{plan.request.booked_transports.index(booking)}'
            for role, city, at in endpoints:
                anchors = [stop for day in plan.itinerary.days if day.date == at.date() for stop in day.stops
                           if stop.booking_anchor == role and stop.booking_ref == reference
                           and city_key(stop.city) == city_key(city)]
                if not anchors or any(s.location_status != 'verified' or not s.location or not s.poi_id for s in anchors):
                    blocking.append(f'第{index + 1}段{city}车站／机场接驳未落实为已定位的时间轴节点，范围外接驳也需核对')
            notes.append('由用户选择／补录，系统未查验订单，不代办购票、退改或付款。')
        else:
            notes.append('请选定实际交通并补录时刻、站点与全员费用；下面的活动仍是行程预览。')
        amount = (str((Decimal(str(transfer.unit_cost)) * (plan.request.people if transfer.basis == 'person' else 1))
                      .quantize(Decimal('.01'))) if transfer else None)
        transports.append({'index': index, 'from_city': from_city, 'to_city': to_city,
            'travel_date': str(transfer.departure_date if transfer else plan.request.start_date),
            'mode': mode, 'status': status, 'number': (booking.number if booking else transfer.train_code if transfer else ''),
            'departure_time': transfer.departure_time if transfer else '',
            'arrival_time': transfer.arrival_time if transfer else '', 'reference_total_yuan': amount,
            'options': options, 'notes': notes})
    blocking = list(dict.fromkeys(blocking))
    state = 'preview' if missing else 'needs_review' if blocking else 'ready'
    title = {'preview': '行程预览：先选住宿与交通', 'needs_review': '预订信息已填写，仍需核对行程',
             'ready': '准备信息已齐，行程检查已通过'}[state]
    summary = {'preview': '酒店或跨城交通尚未全部确定，下面仅作预览；补齐后按酒店位置、真实到离时间和费用重新核对。',
        'needs_review': '已填写的酒店和交通不会自动变成可执行行程；请先处理下方地点、接驳、时间或费用问题。',
        'ready': '按用户提供的住宿、交通与已保存路线完成核对；临时开放、寄存政策和未来路况仍需出发前确认。'}[state]
    return {'state': state, 'title': title, 'summary': summary, 'missing_count': missing,
            'hotels': hotels, 'transports': transports, 'blocking': blocking, 'unknowns': unknowns[:12],
            'issues': build_issues(report, blocking, plan.itinerary, meal_conflicts)}
