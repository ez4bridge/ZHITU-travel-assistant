import copy
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from backend.budget import reconcile_expenses
from backend.models import Expense, PlanRecord, Stop, CityTransfer
from backend.validation import Validator
from backend.storage import Store
from test_core import sample


def test_ticket_party_count_is_applied_once_and_explicit_repeat_preserved():
    trip, itinerary, _ = sample()
    trip.people = 2
    day = itinerary.days[0]
    day.expenses = [Expense(label='景点门票', category='ticket', unit_cost=40, quantity=2, basis='person', stop_id='a')]
    reconcile_expenses(trip, itinerary)
    assert day.expenses[0].quantity == 1
    assert not reconcile_expenses(trip, itinerary)
    day.expenses[0].quantity = 2
    day.expenses[0].note = '每人2次入场'
    reconcile_expenses(trip, itinerary)
    assert day.expenses[0].quantity == 2


def test_map_meal_average_replaces_party_estimate_once_without_overwriting_snacks():
    trip, itinerary, _ = sample()
    trip.people = 2
    day = itinerary.days[0]
    day.stops.append(Stop(id='lunch', name='午餐', kind='meal', start='12:00', end='13:00', average_cost_yuan=86))
    day.expenses = [Expense(label='午餐', category='food', unit_cost=70, basis='group', stop_id='lunch'),
                    Expense(label='小吃', category='food', unit_cost=10, stop_id='lunch', note='按当天合并预算分摊')]
    reconcile_expenses(trip, itinerary)
    meal, snack = day.expenses
    assert (meal.unit_cost, meal.quantity, meal.basis) == (Decimal(86), 1, 'person')
    assert snack.unit_cost == 10
    snapshot = itinerary.model_dump()
    assert not reconcile_expenses(trip, itinerary)
    assert itinerary.model_dump() == snapshot


def test_multiple_dish_costs_are_not_each_replaced_with_full_meal_average():
    trip, itinerary, _ = sample()
    day = itinerary.days[0]
    day.stops.append(Stop(id='lunch', name='午餐', kind='meal', start='12:00', end='13:00', average_cost_yuan=86))
    day.expenses = [Expense(label=name, category='food', unit_cost=20, stop_id='lunch') for name in ('菜一', '菜二')]
    reconcile_expenses(trip, itinerary)
    assert [e.unit_cost for e in day.expenses] == [20, 20]


@pytest.mark.asyncio
async def test_authoritative_breakdown_includes_transfers_and_rounds_per_unit():
    from datetime import datetime, timezone
    trip, itinerary, routes = sample()
    itinerary.days[0].expenses[0].unit_cost = Decimal('50.255')
    itinerary.transfers = [CityTransfer(from_city='上海', to_city='杭州', departure_date=trip.start_date,
        departure_time='07:00', arrival_date=trip.start_date, arrival_time='08:00',
        mode='train', unit_cost=110, basis='person')]
    report = await Validator(preferred='python').validate(trip, itinerary, routes)
    plan = PlanRecord(id='budget', created_at=datetime.now(timezone.utc), request=trip,
                      itinerary=itinerary, routes=routes, validation=report)
    assert sum(row.subtotal_cents for row in plan.cost_breakdown) == report.total_cents
    assert sum(plan.expense_totals.values()) == report.total_cents
    assert plan.cost_breakdown[0].unit_cost == Decimal('50.26')
    assert plan.cost_breakdown[0].subtotal_cents == 5026 * 2 * 3
    assert plan.cost_breakdown[-1].day_index is None
    assert plan.cost_breakdown[-1].subtotal_cents == 11000 * trip.people
    assert PlanRecord.model_validate(plan.model_dump()).cost_breakdown == plan.cost_breakdown


@pytest.mark.asyncio
async def test_reconcile_endpoint_keeps_id_history_and_routes_and_is_idempotent(monkeypatch, tmp_path):
    import backend.main as main
    from datetime import datetime, timezone
    trip, itinerary, routes = sample()
    trip.people = 2
    itinerary.days[0].expenses = [Expense(label='外滩门票', category='ticket', unit_cost=40,
        quantity=2, basis='person', stop_id='a')]
    validator = Validator(preferred='python')
    report = await validator.validate(trip, itinerary, routes)
    plan = PlanRecord(id='budget', created_at=datetime.now(timezone.utc), request=trip,
        itinerary=itinerary, routes=routes, validation=report)
    store = Store(tmp_path / 'cost.sqlite3')
    store.save(plan)
    monkeypatch.setattr(main, 'store', store)
    monkeypatch.setattr(main, 'validator', validator)
    with TestClient(main.app) as client:
        response = client.post('/api/plans/budget/costs/reconcile')
        assert response.status_code == 200
        first = response.json()
        assert first['id'] == 'budget' and first['revision'] == 1
        assert first['itinerary']['days'][0]['expenses'][0]['quantity'] == 1
        assert [{k:v for k,v in r.items() if k != 'cost_estimate'} for r in first['routes']] == [{k:v for k,v in r.items() if k != 'cost_estimate'} for r in plan.model_dump(mode='json')['routes']]
        assert len(client.get('/api/plans').json()) == 1
        second = client.post('/api/plans/budget/costs/reconcile').json()
        assert first == second


def test_new_map_reference_updates_ledger_and_missing_reference_does_not_make_free():
    trip, itinerary, _ = sample()
    day = itinerary.days[0]
    meal = Stop(id='lunch', name='午餐', kind='meal', start='12:00', end='13:00', average_cost_yuan=86)
    day.stops.append(meal)
    day.expenses = [Expense(label='午餐', category='food', unit_cost=70, stop_id='lunch')]
    reconcile_expenses(trip, itinerary)
    meal.average_cost_yuan = Decimal(126)
    reconcile_expenses(trip, itinerary)
    assert day.expenses[0].unit_cost == 126
    meal.average_cost_yuan = None
    reconcile_expenses(trip, itinerary)
    assert day.expenses[0].unit_cost == 126


def test_budget_instruction_respects_new_budget_and_does_not_promise_affordability():
    from types import SimpleNamespace
    from backend.budget import budget_instruction
    plan = SimpleNamespace(validation=SimpleNamespace(total_cents=137700, budget_cents=100000), expense_totals={'transport': 52700})
    text = budget_instruction(plan, Decimal(1200))
    assert '预算1200元' in text and '至少需节省177元' in text
    assert '不得仅下调' in text and '无法满足' in text
