"""Intercity mode advice: compare rail and flight by distance, time and cost.

All numbers are transparent planning estimates, never provider quotes: rail
duration assumes 250 km/h plus station overhead, flight duration includes
airport access, and fares use public per-km envelopes. Nothing here claims a
queried timetable, a flight number or a real fare.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import math

from .models import city_key

TRAIN_SPEED_KMH = 250.0
TRAIN_STATION_HOURS = 0.6
TRAIN_FARE_PER_KM = 0.46

FLIGHT_SPEED_KMH = 750.0
FLIGHT_AIRPORT_HOURS = 1.6
FLIGHT_FARE_PER_KM = 0.55
FLIGHT_FARE_FIXED = 120.0

TRAIN_DISTANCE_KM = 600.0
FLIGHT_DISTANCE_KM = 1200.0


@dataclass(frozen=True)
class Advice:
    mode: str
    reason: str
    train_hours: float
    flight_hours: float
    train_fare: Decimal
    flight_fare: Decimal


def haversine_km(first: tuple[float, float], second: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(value))


def _hours(distance: float, speed: float, overhead: float) -> float:
    return round(distance / speed + overhead, 1)


def advise(distance_km: float, allowed) -> Advice:
    """Pick a mode from the user's allowed set using distance, time and cost."""
    allowed = {mode for mode in allowed if mode in ('train', 'flight')} or {'train'}
    rail_distance = distance_km * 1.15
    train_hours = _hours(rail_distance, TRAIN_SPEED_KMH, TRAIN_STATION_HOURS)
    flight_hours = _hours(distance_km, FLIGHT_SPEED_KMH, FLIGHT_AIRPORT_HOURS)
    train_fare = Decimal(str(round(rail_distance * TRAIN_FARE_PER_KM, -1)))
    flight_fare = Decimal(str(round(distance_km * FLIGHT_FARE_PER_KM + FLIGHT_FARE_FIXED, -1)))

    if allowed == {'train'}:
        return Advice('train', f'你只接受铁路；约 {distance_km:.0f} 公里，铁路估算 {train_hours:.1f} 小时',
                      train_hours, flight_hours, train_fare, flight_fare)
    if allowed == {'flight'}:
        return Advice('flight', f'你只接受飞机；约 {distance_km:.0f} 公里，飞行估算 {flight_hours:.1f} 小时（含进出机场）',
                      train_hours, flight_hours, train_fare, flight_fare)

    if distance_km <= TRAIN_DISTANCE_KM:
        mode = 'train'
        reason = (f'约 {distance_km:.0f} 公里，属于中短途：铁路估算 {train_hours:.1f} 小时、'
                  f'约 ¥{int(train_fare)}；飞机含机场接驳估算 {flight_hours:.1f} 小时、约 ¥{int(flight_fare)}，'
                  '铁路更省时省事')
    elif distance_km >= FLIGHT_DISTANCE_KM:
        mode = 'flight'
        reason = (f'约 {distance_km:.0f} 公里，属于长途：铁路估算 {train_hours:.1f} 小时、'
                  f'约 ¥{int(train_fare)}；飞机含机场接驳估算 {flight_hours:.1f} 小时、约 ¥{int(flight_fare)}，'
                  '飞行显著节省在途时间')
    else:
        time_gain = train_hours - flight_hours
        cost_gap = float(flight_fare - train_fare)
        if time_gain >= 1.5 and cost_gap <= float(train_fare) * 0.4:
            mode = 'flight'
            reason = (f'约 {distance_km:.0f} 公里：飞机估算比铁路快 {time_gain:.1f} 小时，'
                      f'票价估算高约 ¥{cost_gap:.0f}，综合时间与价格建议飞机')
        elif time_gain >= 3:
            mode = 'flight'
            reason = (f'约 {distance_km:.0f} 公里：火车在途时间过长（估算 {train_hours:.1f} 小时），'
                      f'即使票价便宜也建议飞机（估算 {flight_hours:.1f} 小时）')
        else:
            mode = 'train'
            reason = (f'约 {distance_km:.0f} 公里：铁路估算 {train_hours:.1f} 小时/约 ¥{int(train_fare)}，'
                      f'飞机估算 {flight_hours:.1f} 小时/约 ¥{int(flight_fare)}，时间差距不足以抵消机场接驳，建议铁路')
    return Advice(mode, reason, train_hours, flight_hours, train_fare, flight_fare)

async def apply_advice(itinerary, trip, amap) -> list[str]:
    """Switch legs to the better of train/flight when the user accepts both."""
    allowed = set(trip.intercity_modes or []) or {trip.intercity_mode}
    if not {'train', 'flight'} <= allowed:
        return []
    locate = getattr(amap, 'city_point', None)
    if locate is None:
        return []
    booked = {(city_key(booking.from_city), city_key(booking.to_city))
              for booking in trip.booked_transports}
    notes = []
    for index, transfer in enumerate(itinerary.transfers):
        if transfer.booking_status == 'user_confirmed':
            continue
        if (city_key(transfer.from_city), city_key(transfer.to_city)) in booked:
            continue
        try:
            first = await locate(transfer.from_city)
            second = await locate(transfer.to_city)
        except Exception:
            continue
        if not first or not second:
            continue
        advice = advise(haversine_km(first, second), allowed)
        if advice.mode == transfer.mode:
            continue
        if advice.mode == 'flight':
            hours = max(1, math.ceil(advice.flight_hours))
            # Depart after that city's own activities plus airport access; otherwise
            # morning activities would overlap the flight window on the same day.
            day = next((item for item in itinerary.days if item.date == transfer.departure_date), None)
            last_end = 0
            if day is not None:
                for stop in day.stops:
                    if stop.booking_anchor or stop.kind in ('hotel', 'meal'):
                        continue
                    if city_key(stop.city) != city_key(transfer.from_city):
                        continue
                    last_end = max(last_end, int(stop.end[:2]) * 60 + int(stop.end[3:]))
            departure_minutes = int(transfer.departure_time[:2]) * 60 + int(transfer.departure_time[3:])
            latest = 24 * 60 - hours * 60 - 30
            departure_minutes = min(max(departure_minutes, last_end + 90), latest) if latest > 0 else departure_minutes
            base = datetime.combine(transfer.departure_date, datetime.min.time())
            departure = base + timedelta(minutes=departure_minutes)
            arrival = departure + timedelta(hours=hours)
            transfer.departure_date, transfer.departure_time = departure.date(), departure.strftime('%H:%M')
            transfer.mode = 'flight'
            transfer.arrival_date, transfer.arrival_time = arrival.date(), arrival.strftime('%H:%M')
            transfer.unit_cost = advice.flight_fare
            transfer.basis = 'person'
            transfer.price_status = 'estimated'
            transfer.train_code = transfer.rail_option_id = ''
            transfer.departure_station = transfer.arrival_station = ''
            transfer.seat_type = transfer.seat_availability = ''
            transfer.schedule_status = 'estimated'
            transfer.queried_at = None
            transfer.note = f'规划建议：{advice.reason}；航班未查询、未预订，机场与含税价请自行核对'
            transfer.query_note = '按距离、估算耗时与价格综合建议飞机；系统没有机票查询接口，时刻与票价均为估算'
        else:
            transfer.mode = 'train'
            transfer.train_code = transfer.rail_option_id = ''
            transfer.departure_station = transfer.arrival_station = ''
            transfer.note = f'规划建议：{advice.reason}；已改按铁路查询实际车次'
        notes.append(f'跨城方式建议（第{index + 1}段 {transfer.from_city}→{transfer.to_city}）：'
                     + advice.reason + '（按估算比较，未查机票）')
    return notes
