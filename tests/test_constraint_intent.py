"""Interpret wishes without keys, writes, or an AI-generated plan."""
import asyncio

import pytest

from backend import constraint_intent
from backend.constraint_models import ConstraintIntentRequest
from backend.constraint_storage import ConstraintStore, ConstraintStale, fingerprint
from backend.storage import Store
from test_constraint_replan import sample_plan


@pytest.fixture
def setup_intent(tmp_path):
    plan = sample_plan()
    store = Store(tmp_path / 'intent.sqlite3')
    store.save(plan)
    state = ConstraintStore(store.path).state(plan)
    return plan, store, state


def interpret(setup, instruction, **kwargs):
    plan, store, state = setup
    request = ConstraintIntentRequest(base_fingerprint=fingerprint(plan), expected_routing_revision=0,
        expected_state_revision=0, day_indexes=[0, 1], instruction=instruction, **kwargs)
    return asyncio.run(constraint_intent.parse_intent(plan, state, request))


def test_rules_budget_day_window_priority_and_lock_have_visible_patch(setup_intent):
    plan, store, state = setup_intent
    result = interpret(setup_intent, '预算改为800.50元，第2天上午十点出发，晚上八点结束，合成地点1-0必去，锁定合成地点1-1')
    assert result['source'] == 'rules' and not result['unresolved'], result
    assert result['patch']['budget_cents'] == 80050
    assert result['patch']['day_windows'] == [{'day_index': 1, 'start_min': 600, 'end_min': 1200}]
    assert {a['stop_id']: a for a in result['patch']['activities']} == {
        's1-0': {'stop_id': 's1-0', 'priority': 'must'}, 's1-1': {'stop_id': 's1-1', 'locked': True}}
    assert result['summary'] and len(store.list()) == 1
    assert ConstraintStore(store.path).state(plan) == state


def test_partial_understanding_does_not_hide_food_or_new_place_request(setup_intent):
    result = interpret(setup_intent, '预算900元，想吃地道火锅，还要去一个新景点')
    assert result['patch']['budget_cents'] == 90000
    assert len(result['unresolved']) == 2
    assert len(setup_intent[1].list()) == 1


def test_clock_only_updates_named_day_and_keeps_other_endpoint(setup_intent):
    result = interpret(setup_intent, '第2天10:30出门')
    assert not result['unresolved']
    assert result['patch']['day_windows'] == [{'day_index': 1, 'start_min': 630, 'end_min': 720}]


def test_words_and_manual_day_window_share_one_draft_without_losing_end(setup_intent):
    result = interpret(setup_intent, '第2天10:00出发', current_day_windows=[
        {'day_index': 1, 'start_min': 540, 'end_min': 1260}])
    assert not result['unresolved']
    assert result['patch']['day_windows'] == [{'day_index': 1, 'start_min': 600, 'end_min': 1260}]


def test_progress_needs_during_mode_and_exact_day_for_tomorrow(setup_intent):
    assert interpret(setup_intent, '合成地点0-0已完成')['unresolved']
    assert interpret(setup_intent, '明天晚点出发')['unresolved']
    result = interpret(setup_intent, '合成地点0-0已完成', mode='during', as_of='2026-11-02T10:30:00+08:00')
    assert not result['unresolved']
    assert result['patch']['activities'][0]['status'] == 'completed'


def test_cancellation_is_explicit_but_never_implies_free_or_refund(setup_intent):
    result = interpret(setup_intent, '取消合成地点0-0')
    assert not result['unresolved']
    assert result['patch']['activities'] == [{'stop_id': 's0-0', 'status': 'cancelled'}]


@pytest.mark.parametrize('instruction', ['取消合成地点0-0但已付款', '合成地点0-0不去但不可退', '取消合成地点0-0，保留费用'])
def test_cost_retention_words_are_user_budget_choice_not_payment_verification(setup_intent, instruction):
    result = interpret(setup_intent, instruction)
    assert not result['unresolved'], result
    assert result['patch']['activities'] == [{'stop_id': 's0-0', 'status': 'cancelled', 'retain_cost': True}]
    assert any('付款未核验' in text for text in result['summary'])
    assert setup_intent[0].itinerary.days[0].expenses[0].commitment == 'planned'


def test_retained_cost_requires_identified_activity_and_strict_boolean(setup_intent):
    assert interpret(setup_intent, '已付款')['unresolved']
    with pytest.raises(ValueError):
        constraint_intent.ActivityPatch(stop_id='s0-0', retain_cost='true')
    raw = {'patch': {'activities': [{'stop_id': 's0-0', 'retain_cost': True}]}, 'unresolved': []}
    plan, store, state = setup_intent
    body = ConstraintIntentRequest(base_fingerprint=fingerprint(plan), expected_routing_revision=0,
        expected_state_revision=0, day_indexes=[0], instruction='保留这项费用')
    result = constraint_intent.validate_interpretation(raw, plan, state, body, constraint_intent.stop_catalog(plan, state))
    assert result['patch']['activities'][0]['retain_cost'] is True


def test_route_mode_patch_and_ambiguous_wish_stay_distinct(setup_intent):
    result = interpret(setup_intent, '合成地点0-0到合成地点0-1打车')
    assert not result['unresolved']
    assert result['patch']['route_choices'][0]['mode'] == 'taxi'
    assert interpret(setup_intent, '早上晚点起床')['unresolved']


def test_stale_plan_rejected_before_model_or_writes(setup_intent):
    plan, store, state = setup_intent
    state['state_revision'] += 1
    with pytest.raises(ConstraintStale):
        interpret(setup_intent, '预算900元')
    assert len(store.list()) == 1


@pytest.mark.parametrize('patch', [
    {'activities': [{'stop_id': 'model-invented', 'priority': 'must'}]},
    {'day_windows': [{'day_index': 8, 'start_min': 540, 'end_min': 1200}]},
    {'activities': [{'stop_id': 's0-0', 'removable_cost': True}]},
    {'transfer': {'mode': 'flight'}},
])
def test_model_unsupported_fields_ids_or_free_cancellation_cannot_execute(setup_intent, monkeypatch, patch):
    monkeypatch.setattr(constraint_intent.settings, 'llm_key', 'offline-placeholder')
    async def fake_model(*args):
        return {'patch': patch, 'unresolved': []}
    monkeypatch.setattr(constraint_intent, 'model_interpret', fake_model)
    result = interpret(setup_intent, '请理解这个不明确的要求')
    assert result['unresolved'] and result['source'] == 'rules'
    assert len(setup_intent[1].list()) == 1


def test_one_model_patch_is_validated_and_revealed_without_applying(setup_intent, monkeypatch):
    monkeypatch.setattr(constraint_intent.settings, 'llm_key', 'offline-placeholder')
    calls = []
    async def fake_model(*args):
        calls.append(True)
        return {'patch': {'budget_cents': 70000}, 'unresolved': ['新增餐馆需要原AI内容调整并查询地点。']}
    monkeypatch.setattr(constraint_intent, 'model_interpret', fake_model)
    result = interpret(setup_intent, '七百块预算并加个餐馆')
    assert calls == [True] and result['source'] == 'llm'
    assert result['patch']['budget_cents'] == 70000 and result['summary'] and result['unresolved']
    assert len(setup_intent[1].list()) == 1


def test_model_cannot_silently_drop_unrepresentable_clause(setup_intent, monkeypatch):
    monkeypatch.setattr(constraint_intent.settings, 'llm_key', 'offline-placeholder')
    async def incomplete_model(*args):
        return {'patch': {'budget_cents': 90000}, 'unresolved': []}
    monkeypatch.setattr(constraint_intent, 'model_interpret', incomplete_model)
    result = interpret(setup_intent, '预算900元，想吃地道火锅，还要去一个新景点')
    assert result['source'] == 'llm' and len(result['unresolved']) == 2


@pytest.mark.parametrize('instruction', [
    '第2天合成地点0-0必去', '第2天合成地点0-0到合成地点0-1打车',
])
def test_explicit_day_is_not_discarded_for_activity_or_route(setup_intent, instruction):
    result = interpret(setup_intent, instruction)
    assert result['unresolved'] and '日期限定' in result['unresolved'][0]
    assert not result['patch'].get('activities') and not result['patch'].get('route_choices')
