"""Regression for headcount being charged twice on admission estimates."""
from decimal import Decimal
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.itinerary_evidence import normalize_ticket_quantities
from backend.models import Expense, Itinerary, PlanRecord, TripRequest, cents, itinerary_expenses
from backend.validation import normalized_input


def fixture(people=2, label=None, quantity=None, basis='person', note='模型估算，未查询实时价格'):
    trip = TripRequest(destination='上海', start_date='2026-10-28', days=1, people=people, budget=5000)
    itinerary = Itinerary.model_validate({'title': '门票计费测试', 'summary': '独立人数与消费份数', 'days': [{
        'date': '2026-10-28', 'title': '参观',
        'stops': [{'id': 's1', 'name': '景点', 'kind': 'sight', 'start': '09:00', 'end': '10:00'}],
        'expenses': [{'label': label or f'景点门票（{people}人）', 'category': 'ticket', 'unit_cost': '40',
                      'quantity': quantity if quantity is not None else people, 'basis': basis, 'stop_id': 's1', 'note': note}],
    }]})
    return trip, itinerary


def total(trip, itinerary):
    return sum(cents(e.unit_cost) * e.quantity * (trip.people if e.basis == 'person' else 1)
               for e in itinerary_expenses(itinerary))


@pytest.mark.parametrize('people', [2, 3, 10])
def test_explicit_headcount_is_not_charged_twice_and_correction_is_idempotent(people):
    trip, itinerary = fixture(people)
    assert total(trip, itinerary) == 4000 * people * people
    assert len(normalize_ticket_quantities(trip, itinerary)) == 1
    assert total(trip, itinerary) == 4000 * people
    assert itinerary.days[0].expenses[0].unit_cost == Decimal('40')
    assert '已纠正重复人数' in itinerary.days[0].expenses[0].note
    assert normalize_ticket_quantities(trip, itinerary) == []


@pytest.mark.parametrize('options', [
    {'basis': 'group'},
    {'label': '景点门票'},  # No explicit evidence that quantity means headcount.
    {'quantity': 1},
    {'label': '景点门票（2人，各参观2次）'},
    {'note': '每人2张票，分别体验两个项目'},
    {'quantity': 3},
])
def test_group_prices_and_independent_or_ambiguous_quantities_are_preserved(options):
    trip, itinerary = fixture(**options)
    before = itinerary.model_dump()
    assert normalize_ticket_quantities(trip, itinerary) == []
    assert itinerary.model_dump() == before


def test_only_requested_day_changes_and_unlinked_or_food_costs_are_untouched():
    trip, itinerary = fixture()
    itinerary.days.append(itinerary.days[0].model_copy(deep=True))
    itinerary.days[1].expenses.extend([
        Expense(label='午餐（2人）', category='food', unit_cost=50, quantity=2, basis='person', stop_id='s1'),
        Expense(label='未关联门票（2人）', category='ticket', unit_cost=40, quantity=2, basis='person'),
    ])
    assert len(normalize_ticket_quantities(trip, itinerary, only_day=1)) == 1
    assert [e.quantity for e in itinerary.days[0].expenses] == [2]
    assert [e.quantity for e in itinerary.days[1].expenses] == [1, 2, 2]


def test_reported_nine_admissions_and_total_remain_consistent_after_correction():
    trip, itinerary = fixture()
    prices = [40, 180, 50, 180, 55, 100, 90, 120, 150]
    day = itinerary.days[0]
    day.expenses = [Expense(label=f'门票{i}（2人）', category='ticket', unit_cost=price,
                            quantity=2, basis='person', stop_id='s1') for i, price in enumerate(prices)]
    day.expenses.extend([Expense(label='其他全程费用', category='other', unit_cost=6457)])
    assert total(trip, itinerary) == 1_031_700
    assert len(normalize_ticket_quantities(trip, itinerary)) == 9
    assert total(trip, itinerary) == 838_700
    report = normalized_input(trip, itinerary, [])
    assert report['total_cents'] == 838_700 and report['budget_cents'] == 500_000
    plan = PlanRecord(id='test', created_at='2026-10-21T00:00:00Z', request=trip,
        itinerary=itinerary, routes=[], validation={
            'status': 'issues', 'errors': [], 'warnings': [], 'unknowns': [],
            'total_cents': report['total_cents'], 'budget_cents': report['budget_cents'],
        })
    assert plan.expense_totals['ticket'] == 193_000
    assert sum(plan.expense_totals.values()) == plan.validation.total_cents


def test_loading_legacy_history_does_not_silently_change_quantities():
    trip, itinerary = fixture()
    plan = PlanRecord(id='old', created_at='2026-10-21T00:00:00Z', request=trip,
        itinerary=itinerary, routes=[], validation={
            'status': 'issues', 'errors': [], 'warnings': [], 'unknowns': [],
            'total_cents': 16000, 'budget_cents': 500000,
        })
    loaded = PlanRecord.model_validate_json(plan.model_dump_json(), context={'legacy_record': True})
    assert loaded.itinerary.days[0].expenses[0].quantity == 2
    assert loaded.expense_totals['ticket'] == loaded.validation.total_cents == 16000


@pytest.mark.asyncio
async def test_generation_and_revision_correct_before_saving_but_refresh_does_not_rewrite_old_costs(monkeypatch, tmp_path):
    import backend.main as main
    from backend.amap import Amap
    from backend.storage import Store
    from backend.validation import Validator

    trip, itinerary = fixture()
    trip.start_date = (datetime.now(timezone(timedelta(hours=8))) + timedelta(days=2)).date()
    itinerary.days[0].date = trip.start_date
    itinerary.days[0].stops[0].city = '上海'
    itinerary.days[0].stops[0].location = (121.49, 31.23)
    itinerary.days[0].stops[0].location_status = 'verified'
    store = Store(tmp_path / 'plans.sqlite3')
    monkeypatch.setattr(main, 'store', store)
    monkeypatch.setattr(main, 'validator', Validator())
    monkeypatch.setattr(main, 'generate_itinerary', AsyncMock(side_effect=lambda *args, **kwargs: itinerary.model_copy(deep=True)))
    monkeypatch.setattr(Amap, 'enrich', AsyncMock(return_value=[]))

    def result(response):
        assert response.status_code == 200 and 'event: error' not in response.text, response.text
        return json.loads(response.text.split('event: result\ndata: ')[1].split('\n\n')[0])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        first = result(await client.post('/api/plans/generate', json=trip.model_dump(mode='json')))
        second = result(await client.post(f'/api/plans/{first["id"]}/revise', json={'instruction': '保持景点'}))
        for plan in [first, second]:
            assert plan['itinerary']['days'][0]['expenses'][0]['quantity'] == 1
            assert plan['expense_totals']['ticket'] == plan['validation']['total_cents'] == 8000
            assert any('纠正门票重复计人数' in change for change in plan['adjustments'])
        assert second['parent_id'] == first['id'] and second['revision'] == 2
        assert (await client.get(f'/api/plans/{first["id"]}')).json() == first
        legacy = PlanRecord.model_validate({**first, 'id': 'legacy', 'itinerary': itinerary.model_dump(),
            'validation': {**first['validation'], 'total_cents': 16000}})
        store.save(legacy)
        refreshed = result(await client.post('/api/plans/legacy/refresh'))
        assert refreshed['itinerary']['days'][0]['expenses'][0]['quantity'] == 2
        assert refreshed['expense_totals']['ticket'] == refreshed['validation']['total_cents'] == 16000
