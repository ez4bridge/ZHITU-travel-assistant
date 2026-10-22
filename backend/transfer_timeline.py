"""Put actual selected stations in the day; intercity fares remain in transfers."""
from datetime import datetime, timedelta
import re

from .models import city_key
from .validation import minutes
from .transfer_buffers import arrival_buffer


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


def prepare_transfer_anchors(itinerary, trip):
    from .bookings import _anchor
    for day in itinerary.days:
        day.stops = [s for s in day.stops if not s.booking_anchor.startswith('transport_')]
    for index, transfer in enumerate(itinerary.transfers):
        known = transfer.booking_status == 'user_confirmed' or transfer.schedule_status == 'queried'
        if not known:
            continue
        booking = _matching_booking(trip, transfer, index)
        before = booking.departure_buffer_min if booking and booking.departure_buffer_min is not None else (120 if transfer.mode == 'flight' else 60)
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
                        and stop.location_status != 'verified' and re.search(r'到达|抵达', text)):
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
