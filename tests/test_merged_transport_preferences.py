"""Offline multi-mode input and explainable real-route preference checks."""
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.local_transport_policy import group_cost, recommend_local
from backend.models import DayPlan, RouteOption, TripRequest
from backend.railway import Railway


def request(**fields):
    return TripRequest(destination='北京', start_date=date(2026, 11, 1), **fields)


def route(mode, seconds=1800, meters=5000, **fields):
    return RouteOption(mode=mode, available=True, duration_s=seconds, distance_m=meters,
                       polyline=[(116.1, 39.1), (116.2, 39.2)], **fields)


def test_old_single_preference_is_preserved_and_missing_list_is_filled():
    trip = request(intercity_mode='flight')
    assert trip.intercity_modes == ['flight']
    assert TripRequest.model_validate_json(trip.model_dump_json()).intercity_modes == ['flight']


def test_multiple_preferences_keep_order_and_legacy_default():
    trip = request(intercity_modes=['flight', 'train', 'driving'])
    assert trip.intercity_modes == ['flight', 'train', 'driving']
    assert trip.intercity_mode == 'flight'


@pytest.mark.parametrize('modes', [[], ['train', 'train'], ['taxi'], ['train', 'flight', 'bus', 'driving', 'train']])
def test_invalid_or_explicit_empty_preferences_are_rejected(modes):
    with pytest.raises(ValidationError):
        request(intercity_modes=modes)


def test_daily_tips_are_additive_and_bounded():
    assert DayPlan(date='2026-11-01', title='旧日程', stops=[]).tips == []
    assert DayPlan(date='2026-11-01', title='日程', stops=[], tips=[' 核实当天开放 ']).tips == ['核实当天开放']
    for tips in ([''], ['x' * 501], ['提醒'] * 9):
        with pytest.raises(ValidationError):
            DayPlan(date='2026-11-01', title='日程', stops=[], tips=tips)


def test_short_walk_is_preferred_without_claiming_fastest():
    walk = route('walking', 1000, 1000)
    selected, reason = recommend_local([walk, route('taxi', 300, 1500)], people=2)
    assert selected.mode == 'walking' and '短途' in reason


def test_transit_uses_group_cost_and_tolerates_fifteen_minutes_more():
    public = route('transit', 1800, fare_yuan='5')
    taxi = route('taxi', 1200, taxi_cost_yuan='30')
    selected, reason = recommend_local([public, taxi], city='北京', people=2)
    assert selected.mode == 'transit' and '全员费用更低' in reason
    assert group_cost(public, '北京', 2) == Decimal('10')
    assert group_cost(taxi, '北京', 5) == Decimal('60')


def test_group_cost_does_not_assume_public_is_always_cheaper():
    selected, reason = recommend_local([route('transit', 1800, fare_yuan='20'),
                                       route('taxi', 1200, taxi_cost_yuan='20')], people=8)
    assert selected.mode == 'taxi' and '全员费用更低' not in reason


def test_large_real_time_saving_can_recommend_taxi():
    selected, reason = recommend_local([route('transit', 2700, fare_yuan='5'), route('taxi', 1200, taxi_cost_yuan='30')])
    assert selected.mode == 'taxi' and '节省25分钟' in reason
    assert '打车约20分钟' in reason and '公交／地铁约45分钟' in reason and '全员多约25.00元' in reason
    assert '产品规则' in reason and '预算偏好' not in reason


def test_large_time_saving_with_unknown_fare_is_only_time_reference():
    selected, reason = recommend_local([route('transit', 2700), route('taxi', 1200, taxi_cost_yuan='30')])
    assert selected.mode == 'taxi' and '费用未齐，仅耗时参考需确认' in reason


def test_taxi_recommendation_accounts_for_many_transfers_and_group_cost():
    public = route('transit', 1800, fare_yuan='5', instructions=['1号线', '公交2路', '公交3路'])
    selected, reason = recommend_local([public, route('taxi', 1400, taxi_cost_yuan='25')], people=2)
    assert selected.mode == 'taxi' and '换乘2次' in reason and '增加约15.00元' in reason
    selected, reason = recommend_local([public, route('taxi', 1400, taxi_cost_yuan='100')], people=2)
    assert selected.mode == 'transit' and '换乘2次' in reason


def test_group_taxi_can_cost_less_than_individual_public_fares():
    public = route('transit', 1200, fare_yuan='10', instructions=['地铁1号线'])
    selected, reason = recommend_local([public, route('taxi', 1250, taxi_cost_yuan='35')], people=4)
    assert selected.mode == 'taxi' and '费用参考不高于公交' in reason


def test_small_time_saving_does_not_recommend_extremely_expensive_taxi():
    selected, reason = recommend_local([route('transit', 2700, fare_yuan='5'),
        route('taxi', 1500, taxi_cost_yuan='200')], people=2)
    assert selected.mode == 'transit' and '默认每人每省1分钟最多增加1元' in reason


def test_unknown_price_and_incomplete_geometry_are_not_fake_evidence():
    public = route('transit', 1800)
    selected, reason = recommend_local([public, route('taxi', 1200, taxi_cost_yuan='30')])
    assert selected.mode == 'taxi' and '依据不足' in reason
    public.polyline = []
    selected, reason = recommend_local([public])
    assert selected is None and '暂不推荐' in reason


def test_auto_common_modes_do_not_assume_own_car_or_bicycle():
    selected, _ = recommend_local([route('transit', 600, fare_yuan='4'), route('bicycling', 300), route('driving', 200)])
    assert selected.mode == 'transit'
    selected, reason = recommend_local([route('driving', 200)])
    assert selected.mode == 'driving' and '车辆' in reason and '确认' in reason


def test_flight_only_does_not_enable_or_start_rail_query(monkeypatch, tmp_path):
    from backend.config import settings
    executable = tmp_path / 'python.exe'
    executable.write_bytes(b'not executed')
    monkeypatch.setattr(settings, 'rail_mcp_enabled', True)
    monkeypatch.setattr(settings, 'rail_mcp_python', executable)
    assert not Railway(request(intercity_modes=['flight'])).enabled
    assert Railway(request(intercity_modes=['flight', 'train'])).enabled
