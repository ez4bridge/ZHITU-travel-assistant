"""Intercity mode advice: compare rail vs flight instead of always defaulting to rail."""
import asyncio
from datetime import date

from backend.intercity_choice import apply_advice, advise, haversine_km
from backend.models import CityTransfer, DayPlan, Itinerary, TripRequest


def trip(modes=('train', 'flight')):
    return TripRequest(origin='广州', destination='北京', start_date=date(2026, 11, 1),
                       start_time='09:00', days=2, intercity_mode=modes[0],
                       intercity_modes=list(modes), include_origin_transport=True)


class StubMap:
    def __init__(self, points):
        self.points = points

    async def city_point(self, city):
        return self.points.get(city)


def itinerary(departure='08:00'):
    transfer = CityTransfer(from_city='广州', to_city='北京', departure_date=date(2026, 11, 1),
                            departure_time=departure, arrival_date=date(2026, 11, 1),
                            arrival_time='15:00', mode='train', unit_cost=800, basis='person')
    return Itinerary(title='合成跨城建议', summary='', transfers=[transfer],
                     days=[DayPlan(date=date(2026, 11, 1), title='第一天', stops=[]),
                           DayPlan(date=date(2026, 11, 2), title='第二天', stops=[])])


def test_short_trip_prefers_train_and_long_trip_prefers_flight():
    assert advise(300, {'train', 'flight'}).mode == 'train'
    assert advise(1500, {'train', 'flight'}).mode == 'flight'
    assert '飞' in advise(1500, {'train', 'flight'}).reason


def test_single_allowed_mode_is_never_switched():
    assert advise(1500, {'train'}).mode == 'train'
    assert advise(300, {'flight'}).mode == 'flight'


def test_mid_distance_compares_time_and_price():
    # ~900 km: flying saves about 2 hours for a modest fare gap.
    assert advise(900, {'train', 'flight'}).mode == 'flight'
    # ~650 km: rail is close enough in time, keep rail.
    assert advise(650, {'train', 'flight'}).mode == 'train'


def test_haversine_guangzhou_beijing_is_about_1900km():
    distance = haversine_km((23.13, 113.26), (39.90, 116.41))
    assert 1800 < distance < 1950


def test_long_leg_is_switched_to_flight_with_estimate_and_note():
    points = {'广州': (23.13, 113.26), '北京': (39.90, 116.41)}
    plan = itinerary()
    notes = asyncio.run(apply_advice(plan, trip(), StubMap(points)))
    transfer = plan.transfers[0]
    assert transfer.mode == 'flight'
    assert transfer.price_status == 'estimated'
    assert transfer.unit_cost > 0
    assert transfer.schedule_status == 'estimated'
    assert '规划建议' in transfer.note and '未查询' in transfer.note
    assert notes and '跨城方式建议' in notes[0]


def test_short_leg_keeps_rail_and_booked_leg_never_changes():
    points = {'广州': (23.13, 113.26), '近城': (23.20, 113.30)}
    plan = itinerary()
    plan.transfers[0].from_city, plan.transfers[0].to_city = '广州', '近城'
    asyncio.run(apply_advice(plan, trip(), StubMap(points)))
    assert plan.transfers[0].mode == 'train'

    booked = itinerary()
    booked.transfers[0].booking_status = 'user_confirmed'
    asyncio.run(apply_advice(booked, trip(), StubMap(points)))
    assert booked.transfers[0].mode == 'train'
