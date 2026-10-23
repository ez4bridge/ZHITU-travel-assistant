"""Offline regressions for integrating travel constraints with the study edition."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.journey import ensure_transfer_coverage, normalized_journey, validate_journey
from backend.models import (BookedTransport, ConfirmedHotel, DayPlan, Expense, Itinerary,
                            PlanRecord, StudyContext, StudyOptions, TripRequest, WeatherSnapshot)
from backend.storage import Store
from backend.validation import Validator, normalized_input, validate_core


def record(**updates):
    trip = TripRequest(destination='上海', start_date='2026-10-23', days=1)
    itinerary = Itinerary(title='合并兼容', summary='原始合成测试', days=[
        DayPlan(date=trip.start_date, title='第一天', stops=[], expenses=[
            Expense(label='预算预留', category='other', unit_cost=100)])])
    values = dict(id='original', created_at=datetime.now(timezone.utc), request=trip,
                  itinerary=itinerary, routes=[], validation=validate_core(normalized_input(trip, itinerary, [])))
    values.update(updates)
    return PlanRecord.model_validate(values)


def test_legacy_records_defaults_do_not_drop_study_weather_or_guides(tmp_path):
    source = record()
    data = source.model_dump(mode='json')
    data['request']['study'] = StudyOptions().model_dump(mode='json')
    data['request']['mode'] = 'walking'
    data['request']['guides'] = [{'title': '用户攻略', 'text': '用户自己的路线线索'}]
    for key in ('intercity_modes', 'confirmed_hotels', 'booked_transports'):
        data['request'].pop(key, None)
    data['study_context'] = StudyContext(corpus_version='synthetic-merge').model_dump(mode='json')
    snapshot = WeatherSnapshot(requested_city='上海', fetched_at=datetime.now(timezone.utc))
    data['weather'] = snapshot.model_dump(mode='json')
    data['weather_by_city'] = {'上海': data['weather']}
    restored = PlanRecord.model_validate(data)
    assert restored.request.intercity_modes == ['train']
    assert restored.request.guides[0].text == '用户自己的路线线索'
    assert restored.study_context.corpus_version == 'synthetic-merge'
    assert restored.weather_by_city['上海'].provider == 'amap'
    store = Store(tmp_path / 'merge.sqlite3')
    store.save(restored)
    assert store.get(restored.id).model_dump(mode='json') == restored.model_dump(mode='json')


def test_bookings_daily_tips_and_paid_expenses_roundtrip(tmp_path):
    plan = record()
    plan.request.intercity_modes = ['flight', 'train']
    plan.request.confirmed_hotels = [ConfirmedHotel(city='上海', name='合成酒店',
        check_in='2026-10-23', check_out='2026-10-24', total_cost='500.50')]
    plan.request.booked_transports = [BookedTransport(from_city='长沙', to_city='上海',
        departure_date='2026-10-23', departure_time='06:00', arrival_date='2026-10-23',
        arrival_time='08:00', total_cost='600', departure_buffer_min=0)]
    plan.itinerary.days[0].tips = ['当天自己的提醒']
    plan.itinerary.days[0].expenses[0].commitment = 'paid'
    store = Store(tmp_path / 'bookings.sqlite3')
    store.save(plan)
    restored = store.get(plan.id)
    assert restored.request.intercity_modes == ['flight', 'train']
    assert restored.request.confirmed_hotels[0].total_cost == plan.request.confirmed_hotels[0].total_cost
    assert restored.request.booked_transports[0].departure_buffer_min == 0
    assert restored.itinerary.days[0].expenses[0].commitment == 'paid'
    assert restored.itinerary.days[0].tips == ['当天自己的提醒']


def test_study_constraints_still_reject_invalid_city_and_missing_evidence():
    with pytest.raises(ValidationError, match='上海单日'):
        TripRequest(destination='北京', start_date='2026-10-23', days=1, study=StudyOptions())
    plan = record()
    plan.request = TripRequest(destination='上海', start_date='2026-10-23', days=1, study=StudyOptions())
    result = validate_core(normalized_input(plan.request, plan.itinerary, [], None))
    assert 'study_missing_evidence' in {entry['code'] for entry in result['errors']}


@pytest.mark.asyncio
async def test_python_validator_keeps_study_prevalidation():
    plan = record()
    plan.request = TripRequest(destination='上海', start_date='2026-10-23', days=1, study=StudyOptions())
    result = await Validator().validate(plan.request, plan.itinerary, [], None)
    assert result.backend == 'python'
    assert 'study_missing_evidence' in {entry.code for entry in result.errors}


def test_delete_preserves_fact_ledger_guard_and_cleans_guide_cache(tmp_path):
    store = Store(tmp_path / 'protected.sqlite3')
    first = record()
    second = record(id='protected')
    store.save(first)
    store.save(second)
    with store.connect() as db:
        db.execute('CREATE TABLE place_guides(plan_id TEXT, value TEXT)')
        db.executemany('INSERT INTO place_guides VALUES (?,?)', [('original', 'first'), ('protected', 'second')])
        db.execute('CREATE TABLE constraint_members(plan_id TEXT, root_id TEXT)')
        db.execute('CREATE TABLE constraint_chains(root_id TEXT, state_revision INTEGER)')
        db.execute("INSERT INTO constraint_members VALUES ('protected', 'root')")
        db.execute("INSERT INTO constraint_chains VALUES ('root', 1)")
    store.delete('original')
    with pytest.raises(ValueError, match='已确认约束'):
        store.delete('protected')
    with store.connect() as db:
        assert db.execute('SELECT plan_id FROM place_guides').fetchall() == [('protected',)]
    assert store.get('original') is None and store.get('protected') is not None


def test_origin_station_anchor_does_not_become_destination_but_still_checks_time():
    trip = TripRequest(origin='长沙', destination='上海', include_origin_transport=True,
                       start_date='2026-10-23', start_time='07:00', days=1)
    itinerary = Itinerary.model_validate(dict(title='合成往返', summary='', days=[dict(
        date=trip.start_date, title='上海', stops=[
            dict(id='station', name='长沙南站安检', city='长沙', kind='break',
                 start='07:30', end='07:35', booking_anchor='transport_departure'),
            dict(id='sight', name='外滩', city='上海', start='12:00', end='13:00')])],
        transfers=[dict(from_city='长沙', to_city='上海', departure_date=trip.start_date,
                        arrival_date=trip.start_date, departure_time='08:00', arrival_time='11:00', unit_cost=100)]))
    ensure_transfer_coverage(trip, itinerary)
    data = normalized_journey(trip, itinerary)
    assert data['stops'][0]['transport_only'] is True
    errors = validate_journey(data)[0]
    assert not {'city_sequence', 'intercity_activity_overlap'} & {entry['code'] for entry in errors}
    itinerary.days[0].stops[0].end = '08:10'
    assert 'intercity_activity_overlap' in {entry['code'] for entry in validate_journey(normalized_journey(trip, itinerary))[0]}
