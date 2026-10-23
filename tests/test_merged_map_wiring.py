"""Offline HTTP regression for shared generation/map/bookings wiring."""
import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from backend.amap import Amap
from backend.models import Expense, PlanRecord, RouteLeg, Stop, ValidationReport
from backend.planning import recost_all
from backend.storage import Store
from backend.transfer_timeline import prepare_transfer_anchors
from backend.validation import Validator, normalized_input, validate_core
from test_merged_transfer_timeline import itinerary, trip


def fixture_plan():
    request, plan = trip(), itinerary()
    prepare_transfer_anchors(plan, request)
    plan.days[1].stops = [Stop(id='next-day', name='合成次日景点', city='北京', start='10:00', end='11:00')]
    for day in plan.days:
        for index, stop in enumerate(day.stops):
            stop.poi_id = 'synthetic-' + stop.id
            stop.location_status = 'verified'
            stop.location = (116.3 + index * .01, 39.9)
            stop.location_note = '离线合成POI，不代表地图核验'
    return PlanRecord(id='map-wiring', created_at=datetime.now(timezone.utc), request=request,
        itinerary=plan, routes=[], validation=ValidationReport(**validate_core(normalized_input(request, plan, []))))


def result(response):
    assert 'event: result' in response.text, response.text
    return json.loads(response.text.split('event: result\ndata: ')[1].split('\n\n')[0])


def isolate(monkeypatch, tmp_path):
    import backend.main as main
    store = Store(tmp_path / 'isolated-map-wiring.sqlite3')
    monkeypatch.setattr(main, 'store', store)
    monkeypatch.setattr(main, 'validator', Validator())
    return main, store


def test_partial_revise_preserves_other_day_verified_station_anchors(monkeypatch, tmp_path):
    main, store = isolate(monkeypatch, tmp_path)
    original = fixture_plan()
    store.save(original)
    unchanged = original.itinerary.days[0].model_dump(mode='json')
    generated = original.itinerary.model_copy(deep=True)
    generated.days[1].title = '只修改第二天'
    monkeypatch.setattr(main, 'generate_itinerary', AsyncMock(return_value=generated))
    enrich = AsyncMock(return_value=[])
    monkeypatch.setattr(Amap, 'enrich', enrich)
    with TestClient(main.app) as client:
        changed = result(client.post('/api/plans/map-wiring/revise', json={'instruction': '只改第二天', 'day_index': 1}))
    assert changed['itinerary']['days'][0] == unchanged
    assert changed['itinerary']['days'][1]['title'] == '只修改第二天'
    assert enrich.call_args.kwargs['day_indices'] == {1}
    assert enrich.call_args.kwargs['people'] == 2
    assert store.get(original.id).itinerary.days[0].model_dump(mode='json') == unchanged


def test_first_generation_passes_origin_and_people_and_counts_transfer_fare_once(monkeypatch, tmp_path):
    main, store = isolate(monkeypatch, tmp_path)
    original = fixture_plan()
    captured = []
    class CapturingMap(Amap):
        def __init__(self, cities, emit=None):
            captured.append(cities)
            super().__init__(cities, emit)
    monkeypatch.setattr(main, 'Amap', CapturingMap)
    enrich = AsyncMock(return_value=[])
    monkeypatch.setattr(CapturingMap, 'enrich', enrich)
    generated = original.itinerary.model_copy(deep=True)
    # A generation fixture contains model suggestions, not a saved record's
    # server-generated station anchors. Main will create those itself.
    for day in generated.days:
        day.stops = [stop for stop in day.stops if not stop.booking_anchor]
    monkeypatch.setattr(main, 'generate_itinerary', AsyncMock(return_value=generated))
    monkeypatch.setattr(main.Railway, 'enrich', AsyncMock())
    with TestClient(main.app) as client:
        created = result(client.post('/api/plans/generate', json=original.request.model_dump(mode='json')))
    assert captured == [original.request.journey_cities] and '长沙' in captured[0]
    assert enrich.call_args.kwargs['people'] == 2
    assert created['expense_totals']['transport'] == 20000  # ¥100/person * 2, no station duplicate.
    assert len(store.list()) == 1


def test_refresh_does_not_call_model_or_duplicate_cross_city_price(monkeypatch, tmp_path):
    main, store = isolate(monkeypatch, tmp_path)
    original = fixture_plan()
    original.itinerary.days[0].expenses = [Expense(label='已付本地接驳', category='transport',
        unit_cost=50, basis='group', commitment='paid')]
    store.save(original)
    generate = AsyncMock()
    rail = AsyncMock()
    monkeypatch.setattr(main, 'generate_itinerary', generate)
    monkeypatch.setattr(main.Railway, 'enrich', rail)
    async def synthetic_enrich(self, plan, mode, **kwargs):
        for day in plan.days:
            for index, stop in enumerate(day.stops):
                stop.poi_id = 'synthetic-' + stop.id
                stop.location_status = 'verified'
                stop.location = (116.3 + index * .01, 39.9)
                stop.location_note = '离线合成POI，不代表地图核验'
        return []
    monkeypatch.setattr(Amap, 'enrich', synthetic_enrich)
    with TestClient(main.app) as client:
        refreshed = result(client.post('/api/plans/map-wiring/refresh'))
    generate.assert_not_awaited()
    rail.assert_not_awaited()
    assert refreshed['expense_totals']['transport'] == 25000
    assert refreshed['itinerary']['days'][0]['expenses'][0]['commitment'] == 'paid'
    assert len(store.list()) == 1 and refreshed['id'] == original.id


def test_route_recost_retains_user_paid_or_committed_transport_expense():
    record = fixture_plan()
    paid = Expense(label='用户已付接驳', category='transport', unit_cost='50', basis='group', commitment='paid')
    committed = Expense(label='已承诺交通', category='transport', unit_cost='70', basis='group', commitment='committed')
    booked = Expense(label='已订接驳', category='transport', unit_cost='30', basis='group', booking_ref='user-booked-shuttle')
    unspent = Expense(label='旧交通估算', category='transport', unit_cost='40', basis='group')
    record.itinerary.days[0].expenses = [paid, committed, booked, unspent]
    recost_all(record.request, record.itinerary, [])
    assert record.itinerary.days[0].expenses == [paid, committed, booked]
    assert sum(e.unit_cost for e in record.itinerary.days[0].expenses) == Decimal('150')


def test_repeated_recost_replaces_old_estimate_and_keeps_paid_cost_separate():
    record = fixture_plan()
    paid = Expense(label='已付原接驳', category='transport', unit_cost='50', basis='group', commitment='paid')
    record.itinerary.days[0].expenses = [paid]
    route = RouteLeg(day_index=0, from_id='sight', to_id='new-destination', mode='taxi', available=True,
        distance_m=3000, duration_s=600, taxi_cost_yuan=20,
        polyline=[(116.3, 39.9), (116.31, 39.9)], queried_at=datetime.now(timezone.utc))
    recost_all(record.request, record.itinerary, [route])
    costs = record.itinerary.days[0].expenses
    assert len(costs) == 2 and costs[0] == paid
    assert costs[1].unit_cost == Decimal('20') and costs[1].commitment == 'planned'
    assert '未假定退款或抵扣' in costs[1].note
    snapshot = [e.model_dump() for e in costs]
    recost_all(record.request, record.itinerary, [route])
    assert [e.model_dump() for e in record.itinerary.days[0].expenses] == snapshot


def test_booking_preview_does_not_drop_paid_local_transport(tmp_path):
    from backend.booking_routes import preview_bookings
    from test_merged_bookings import setup_booking, booking_body, hotel, SyntheticBookingMap
    store, plan, storage, state = setup_booking(tmp_path)
    paid = Expense(label='用户已付接驳', category='transport', unit_cost=50, basis='group', commitment='paid')
    plan.itinerary.days[0].expenses.append(paid)
    store.delete(plan.id)
    store.save(plan)
    body = booking_body(plan, state, [hotel()])
    preview = asyncio.run(preview_bookings(plan, state, body, storage, amap_factory=SyntheticBookingMap))
    assert preview['status'] == 'ready', preview['conflicts']
    child = storage.apply(plan.id, preview['preview_id'])
    assert paid in child.itinerary.days[0].expenses
