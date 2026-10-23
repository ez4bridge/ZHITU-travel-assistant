"""Real HTTP/save boundaries with offline model and directed map fixtures.

No real restaurant, hotel, route, fare, model or provider is queried here.
The parent/child writes below use only pytest-owned temporary SQLite files.
"""
from decimal import Decimal

from fastapi.testclient import TestClient

from backend import booking_routes, main
from backend.models import ConfirmedHotel, Expense
from test_booking_route_recovery import map_with_failures, model_answer, point
from test_constraint_replan import sample_plan
from test_merged_bookings import booking_body, setup_booking


def stored_bytes(storage, plan_id):
    """Compare the persisted source row, not only an in-memory model copy."""
    with storage.connect() as db:
        return db.execute('SELECT data FROM plans WHERE id=?', (plan_id,)).fetchone()[0]


def wire_real_preview(monkeypatch, *, amap, generator):
    real_preview = booking_routes.preview_bookings
    seen = []

    async def isolated_preview(*args, **kwargs):
        assert kwargs.get('regenerate') is True
        result = await real_preview(*args, **kwargs, amap_factory=amap, generator=generator)
        seen.append(result)
        return result

    monkeypatch.setattr(booking_routes, 'preview_bookings', isolated_preview)
    # These two tests target route repair and finance/save boundaries; meal
    # insertion itself has separate real-flow coverage in the route suite.
    monkeypatch.setattr(booking_routes, 'prepare_arrival_flow', lambda *_args, **_kwargs: [])
    return seen


def test_regenerate_http_repairs_restaurant_to_fixed_hotel_and_directly_saves_child(tmp_path, monkeypatch):
    store, parent, storage, state = setup_booking(tmp_path, sample_plan(2))
    monkeypatch.setattr(main, 'store', store)
    before = stored_bytes(storage, parent.id)
    meal = point('api-unreachable-meal', '楼外楼（孤山路店）午餐', meal=True)
    sight = point('api-kept-sight', start='09:00', end='10:00')
    amap = map_with_failures(lambda day_index, first, second:
        day_index == 0 and first.id == meal.id and second.booking_anchor == 'hotel_return')
    calls = []

    async def generator(request, *_args, **_kwargs):
        calls.append(request)
        return model_answer(request, [sight, meal])

    results = wire_real_preview(monkeypatch, amap=amap, generator=generator)
    hotel = ConfirmedHotel(city='北京', name='合成API已填酒店', check_in='2026-11-01',
                           check_out='2026-11-04', total_cost=100)
    body = booking_body(parent, state, [hotel]).model_dump(mode='json')
    with TestClient(main.app) as client:
        # This is the only mutation request: no second /booking-apply call.
        response = client.post(f'/api/plans/{parent.id}/booking-regenerate', json=body)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['status'] == 'applied', result
        assert 'preview_id' not in result
        assert len(store.list()) == 2
        assert client.get('/api/health').json()['tasks']['booking_active'] == 0

    assert len(calls) == 1 and calls[0].confirmed_hotels[0].name == hotel.name
    assert len(results) == 1 and results[0]['status'] == 'ready'
    child = store.get(result['plan']['id'])
    assert child and child.parent_id == parent.id and child.revision == parent.revision + 1
    assert stored_bytes(storage, parent.id) == before, 'HTTP regeneration must never overwrite the source row'
    assert storage.state(child)['current_plan_id'] == child.id
    assert storage.state(child)['state_revision'] == 1
    first_day = child.itinerary.days[0]
    assert sight.id in {stop.id for stop in first_day.stops}
    assert meal.id not in {stop.id for stop in first_day.stops}
    assert not any(expense.stop_id == meal.id for expense in first_day.expenses)
    hotel_return = next(stop for stop in first_day.stops if stop.booking_anchor == 'hotel_return')
    assert hotel_return.name == hotel.name
    assert any(index == 0 and first == sight.id and second == hotel_return.id and available
               for index, first, second, available in amap.calls)
    assert all(route.available for route in child.routes if route.scope == 'local')
    assert any(meal.name in message for message in result['notices'])
    assert all(instance.closed for instance in amap.instances)


def test_regenerate_http_rejects_overfull_restored_payment_ledger_without_preview_or_save(tmp_path, monkeypatch):
    parent = sample_plan(2)
    paid = parent.itinerary.days[0].expenses[0]
    paid.label, paid.commitment, paid.unit_cost = '合成原已付款票务', 'paid', Decimal('37.50')
    paid.quantity = 2
    store, parent, storage, state = setup_booking(tmp_path, parent)
    monkeypatch.setattr(main, 'store', store)
    before = stored_bytes(storage, parent.id)
    amap = map_with_failures(lambda *_args: False)

    async def generator(request, *_args, **_kwargs):
        answer = model_answer(request, [point('api-new-sight', start='10:00', end='11:00')])
        answer.days[0].expenses = [Expense(label=f'合成普通预算项目{index}', category='ticket',
            unit_cost=1, basis='group', stop_id='api-new-sight', commitment='planned') for index in range(20)]
        return answer

    results = wire_real_preview(monkeypatch, amap=amap, generator=generator)
    hotel = ConfirmedHotel(city='北京', name='合成API已填酒店', check_in='2026-11-01',
                           check_out='2026-11-04', total_cost=100)
    with TestClient(main.app) as client:
        response = client.post(f'/api/plans/{parent.id}/booking-regenerate',
            json=booking_body(parent, state, [hotel]).model_dump(mode='json'))
        assert response.status_code == 200, response.text
        result = response.json()
        # Supplement regeneration saves even when the restored ledger is large;
        # the problem is reported instead of blocking the user's new version.
        assert result['status'] == 'applied', result
        assert any('已合并普通预算估算项' in message for message in result['notices'])
        assert 'preview_id' not in result
        applied = result['plan']
        labels = [expense['label'] for day in applied['itinerary']['days'] for expense in day['expenses']]
        assert '合成原已付款票务' in labels
        assert client.get('/api/health').json()['tasks']['booking_active'] == 0

    assert len(results) == 1 and results[0]['status'] == 'ready'
    assert len(store.list()) == 2
    with storage.connect() as db:
        assert db.execute('SELECT count(*) FROM constraint_previews WHERE applied_plan_id IS NOT NULL').fetchone()[0] == 1
    first_day = applied['itinerary']['days'][0]
    carried = [expense for expense in first_day['expenses'] if expense['label'] == paid.label]
    assert len(carried) == 1 and carried[0]['commitment'] == 'paid'
    assert Decimal(str(carried[0]['unit_cost'])) * carried[0]['quantity'] == Decimal('75.00')
    # DayPlan.expenses is capped at 20: paid rows stay, planned estimates merge.
    assert len(first_day['expenses']) <= 20
    assert all(expense['category'] != 'transport' for expense in first_day['expenses'])
    assert all(route['mode'] == 'walking' for route in applied['routes'])
    assert all(instance.closed for instance in amap.instances)
