from datetime import date, datetime, timezone, timedelta
from backend.models import TripRequest, Itinerary, DayPlan, Stop, Expense, RouteLeg, PlanRecord, ValidationReport, CityTransfer, DestinationStay
from backend.planning import recost_all

def sample_plan(days=2):
    trip = TripRequest(destination='北京', start_date='2026-11-02', days=days, budget='1000', people=2, mode='walking')
    plan_days, routes = [], []
    for index in range(days):
        stops = [Stop(id=f's{index}-{n}', name=f'合成地点{index}-{n}', city='北京', start=f'{9+n*2:02d}:00', end=f'{10+n*2:02d}:00',
                      location=(116 + n * .01, 39 + index * .01), poi_id=f'poi{index}-{n}', location_status='verified') for n in range(2)]
        plan_days.append(DayPlan(date=date(2026, 11, 2)+timedelta(days=index), title='合成测试日', stops=stops,
                          expenses=[Expense(label=s.name+'门票', category='ticket', unit_cost='10', basis='group', stop_id=s.id) for s in stops]))
        routes.append(RouteLeg(day_index=index, from_id=stops[0].id, to_id=stops[1].id, mode='walking', available=True,
                               distance_m=1000, duration_s=300, polyline=[stops[0].location, stops[1].location], queried_at=datetime.now(timezone.utc)))
    return PlanRecord(id='base', created_at=datetime.now(timezone.utc), request=trip,
                      itinerary=Itinerary(title='合成行程', summary='仅离线测试', days=plan_days), routes=routes,
                      validation=ValidationReport(status='passed_with_unknowns', errors=[], warnings=[], unknowns=[], total_cents=days*2000, budget_cents=100000))


def mixed_plan():
    plan = sample_plan()
    plan.request.destinations = [DestinationStay(city='北京', preferred_days=1), DestinationStay(city='天津', preferred_days=1)]
    plan.request.destination = '北京→天津'
    first = plan.itinerary.days[0]
    first.stops[1].city = '天津'
    first.stops[1].start, first.stops[1].end = '14:00', '15:00'
    extra = first.stops[1].model_copy(deep=True)
    extra.id, extra.name = 'tj-extra', '合成天津地点'
    extra.start, extra.end = '16:00', '17:00'
    extra.location = (116.02, 39)
    first.stops.append(extra)
    first.expenses.append(Expense(label='合成天津门票', category='ticket', unit_cost=10, basis='group', stop_id=extra.id))
    for stop in plan.itinerary.days[1].stops:
        stop.city = '天津'
    plan.itinerary.transfers = [CityTransfer(from_city='北京', to_city='天津', departure_date=first.date,
        arrival_date=first.date, departure_time='11:00', arrival_time='12:00', mode='train', unit_cost=20, basis='group')]
    plan.routes[0] = RouteLeg(day_index=0, from_id=first.stops[1].id, to_id=extra.id, mode='taxi', available=True,
        distance_m=600, duration_s=180, taxi_cost_yuan=15, polyline=[first.stops[1].location, extra.location], queried_at=datetime.now(timezone.utc))
    recost_all(plan.request, plan.itinerary, plan.routes)
    return plan
