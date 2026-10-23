"""Synthetic itinerary and road fixtures for constraint regression tests."""
from datetime import date, datetime, timedelta, timezone


class SyntheticRoads:
    def __init__(self, city):
        pass

    async def route(self, before, after, mode, city):
        return {'available': True, 'distance_m': 500, 'duration_s': 600,
                'polyline': [before.location, after.location],
                'note': '离线人工合成500米/600秒，并非高德查询', 'provider_endpoint': 'synthetic-fixture'}

    async def close(self):
        pass


def seed(store):
    from backend.models import TripRequest, Stop, DayPlan, Expense, Itinerary, CityTransfer, RouteLeg, RouteOption, PlanRecord
    from backend.transport_cost import estimate_cost
    from backend.validation import normalized_input, validate_core

    start = date(2026, 10, 23)
    rows = [[('A', '09:00', '10:00', '100'), ('B', '10:20', '11:20', '200'), ('午餐1', '11:30', '12:00', '50')],
            [('C', '09:00', '10:00', '150'), ('D', '10:20', '11:20', '100'), ('午餐2', '11:30', '12:00', '50')],
            [('E', '09:00', '10:00', '50'), ('午餐3', '11:30', '12:00', '50')]]
    days, routes = [], []
    for index, items in enumerate(rows):
        city = '北京市' if index < 2 else '天津市'
        stops = [Stop(id=f'd{index + 1}s{n + 1}', name='合成活动' + name, city=city,
                      kind='meal' if name.startswith('午餐') else 'sight', start=begin, end=end,
                      poi_id=f'synthetic-{index}-{n}', location=(116.3 + n * .01, 39.8 + index * .01),
                      location_status='verified', matched_name='合成地点' + name,
                      location_note='离线人工合成地点，未调用地图；只验证程序流程。')
                 for n, (name, begin, end, amount) in enumerate(items)]
        expenses = [Expense(label=stop.name + '合成费用', category='food' if stop.kind == 'meal' else 'ticket',
                            unit_cost=items[n][3], basis='group', stop_id=stop.id,
                            note='人工合成估算，非真实票价或消费') for n, stop in enumerate(stops)]
        if index == 0:
            expenses.append(Expense(label='固定合成住宿费用', category='hotel', unit_cost='250', basis='group'))
        days.append(DayPlan(date=start + timedelta(days=index), title=f'离线合成第{index + 1}天', stops=stops, expenses=expenses,
                            tips=[f'第{index + 1}天合成提醒：核对预约和开放；这不是实际景点公告。']))
        for left, right in zip(stops, stops[1:]):
            alternatives = [RouteOption(mode='transit', available=True, distance_m=650, duration_s=420, fare_yuan='2',
                polyline=[left.location, right.location], instructions=['合成公交站A', '合成公交站B'],
                provider_endpoint='synthetic-fixture', note='合成公交2元/人，非真实查询'),
                RouteOption(mode='taxi', available=True, distance_m=600, duration_s=180, taxi_cost_yuan='15',
                polyline=[left.location, right.location], provider_endpoint='synthetic-fixture', note='合成打车15元/辆，非实际报价')]
            for option in alternatives:
                option.cost_estimate = estimate_cost(option, city)
            routes.append(RouteLeg(day_index=index, from_id=left.id, to_id=right.id, mode='walking',
                                   available=True, distance_m=500, duration_s=600,
                                   polyline=[left.location, right.location], queried_at=datetime.now(timezone.utc),
                                   note='人工合成500米/600秒；未查询高德', alternatives=alternatives))
    trip = TripRequest(destinations=[{'city': '北京市', 'preferred_days': 2}, {'city': '天津市', 'preferred_days': 1}],
                       start_date=start, people=2, budget='2000', mode='walking',
                       preferences='离线合成验收；所有地点、道路和价格均为人工构造，不能用于出行。')
    transfer = CityTransfer(from_city='北京市', to_city='天津市', departure_date=start + timedelta(days=1),
                            arrival_date=start + timedelta(days=1), departure_time='16:00', arrival_time='18:00',
                            unit_cost='100', basis='group', note='离线合成跨城时间与价格，非真实车次')
    itinerary = Itinerary(title='【离线合成】北京两日＋天津一日', summary='真实仓颉内核验收；其余地点、道路和费用为合成数据，不调用模型或地图。',
                          days=days, transfers=[transfer], tips=['全程合成数据不可用于真实出行。', '北京市的合成地点没有真实营业资料。'])
    report = validate_core(normalized_input(trip, itinerary, routes))
    for identifier, label in [('constraint-demo-budget', '预算与锁定'), ('constraint-demo-progress', '途中与漏游'),
                              ('constraint-demo-conversation', '对话新增与取消')]:
        if store.get(identifier):
            continue
        data = itinerary.model_copy(deep=True)
        data.title += ' · ' + label
        store.save(PlanRecord(id=identifier, created_at=datetime.now(timezone.utc), request=trip,
                              itinerary=data, routes=routes, validation=report))
