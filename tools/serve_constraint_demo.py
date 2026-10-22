"""Local synthetic UI acceptance server; real Cangjie, no keys or external HTTP.

Its temporary SQLite database is separate from normal history. Every location,
fee and 600-second road is authored test data, not a travel recommendation.
"""
import argparse
import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import hashlib
import os
import re
import socket
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def offline_process():
    import dotenv
    import dotenv.main
    dotenv.load_dotenv = dotenv.main.load_dotenv = lambda *args, **kwargs: False
    for name in list(os.environ):
        if name.upper().startswith(('LLM_', 'AMAP_', 'OPENAI_', 'LANGSMITH_', 'LANGCHAIN_')):
            os.environ.pop(name, None)
    os.environ.pop('TRAVEL_SERVER_PROCESS', None)
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    connect, connect_ex, resolve = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    resolve_name, sendto = socket.gethostbyname, socket.socket.sendto

    def local_address(address):
        return isinstance(address, str) or address[0] in ('127.0.0.1', '::1', 'localhost')

    def local_connect(self, address):
        if not local_address(address):
            raise OSError('synthetic acceptance: external network disabled')
        return connect(self, address)

    def local_connect_ex(self, address):
        if not local_address(address):
            raise OSError('synthetic acceptance: external network disabled')
        return connect_ex(self, address)

    def local_resolve(host, *args, **kwargs):
        if host not in ('127.0.0.1', '::1', 'localhost', None):
            raise OSError('synthetic acceptance: external DNS disabled')
        return resolve(host, *args, **kwargs)

    def local_name(host):
        if host not in ('127.0.0.1', '::1', 'localhost'):
            raise OSError('synthetic acceptance: external DNS disabled')
        return resolve_name(host)

    def local_sendto(self, *args):
        if not local_address(args[-1]):
            raise OSError('synthetic acceptance: external UDP disabled')
        return sendto(self, *args)

    socket.socket.connect, socket.socket.connect_ex = local_connect, local_connect_ex
    socket.getaddrinfo, socket.gethostbyname, socket.socket.sendto = local_resolve, local_name, local_sendto


class SyntheticRoads:
    def __init__(self, city):
        pass

    async def route(self, before, after, mode, city):
        return {'available': True, 'distance_m': 500, 'duration_s': 600,
                'polyline': [before.location, after.location],
                'note': '离线人工合成500米/600秒，并非高德查询', 'provider_endpoint': 'synthetic-fixture'}

    async def close(self):
        pass


def synthetic_cost(route, city='', stay_minutes=0):
    """Explicit fixture amounts, never supplier queries or real city pricing."""
    from backend.models import TransportCost
    if not route.available:
        return None
    amount = {'walking': '0', 'transit': '2', 'taxi': '15', 'driving': '8', 'bicycling': '2'}[route.mode]
    return TransportCost(low=amount, high=amount,
        basis='vehicle' if route.mode in ('taxi', 'driving') else 'person',
        method='离线人工合成费用', assumptions='只验证程序流程，非高德报价、票价或真实消费', source_url='')


class SyntheticBookingMap:
    """Only the separate demo process may turn fixture names into fixture POIs."""
    def __init__(self, cities):
        self.cities = cities

    async def enrich(self, itinerary, mode, compare_modes=False, day_indices=None, **kwargs):
        from backend.models import RouteLeg, RouteOption, city_key
        routes = []
        for day_index, day in enumerate(itinerary.days):
            if day_indices is not None and day_index not in day_indices:
                continue
            for stop in day.stops:
                if stop.location_status == 'verified' and stop.location:
                    continue
                token = hashlib.sha256(f'{city_key(stop.city)}|{stop.place_name or stop.name}'.encode()).hexdigest()
                base = (117.1, 39.1) if city_key(stop.city) == city_key('天津市') else (116.3, 39.8)
                stop.location = (base[0] + int(token[:4], 16) % 20 / 1000,
                                 base[1] + int(token[4:8], 16) % 20 / 1000)
                stop.poi_id = 'synthetic-booking-' + token[:16]
                stop.matched_name = stop.place_name or stop.name
                stop.address = getattr(stop, 'booking_address', '') or '离线合成地址，不能用于真实导航'
                stop.location_status = 'verified'
                stop.location_note = '离线人工合成地点，未调用高德；只验证补录与接驳程序流程。'
            for left, right in zip(day.stops, day.stops[1:]):
                selected = 'walking' if mode == 'auto' else mode
                leg = RouteLeg(day_index=day_index, from_id=left.id, to_id=right.id, mode=selected,
                    queried_at=datetime.now(timezone.utc), provider_endpoint='synthetic-booking-fixture',
                    note='离线人工合成路线，非高德实查，不可用于出行')
                if city_key(left.city) != city_key(right.city):
                    leg.scope = 'intercity'
                    leg.note = '离线合成跨城边界；使用独立合成跨城段，不绘制市内导航'
                    routes.append(leg)
                    continue
                values = {'walking': (500, 600), 'transit': (650, 420),
                          'taxi': (600, 180), 'driving': (600, 180), 'bicycling': (550, 300)}
                options = []
                for candidate_mode, (distance, duration) in values.items():
                    option = RouteOption(mode=candidate_mode, available=True,
                        distance_m=distance, duration_s=duration, polyline=[left.location, right.location],
                        provider_endpoint='synthetic-booking-fixture',
                        instructions=['人工合成公交直达线路'] if candidate_mode == 'transit' else [],
                        fare_yuan='2' if candidate_mode == 'transit' else None,
                        taxi_cost_yuan='15' if candidate_mode == 'taxi' else None,
                        note='离线人工合成距离／耗时／费用，不代表实际道路和报价')
                    option.cost_estimate = synthetic_cost(option)
                    options.append(option)
                active = next(option for option in options if option.mode == selected)
                for field, value in active.model_dump().items():
                    setattr(leg, field, value)
                leg.alternatives = [option for option in options if option.mode != selected]
                routes.append(leg)
        return routes

    async def close(self):
        pass


class SyntheticConversationMap:
    """Fixture geocoding only: no arbitrary real place is claimed verified."""
    def __init__(self, cities):
        self.cities = cities

    async def resolve(self, stop):
        from backend.models import city_key
        if not stop.name.startswith('合成') or city_key(stop.city) not in {city_key(c) for c in self.cities}:
            stop.location, stop.location_status = None, 'unknown'
            stop.location_note = '离线演示只定位合成名称；真实地点须在正常服务调用高德核实。'
            return
        token = hashlib.sha256(f'{city_key(stop.city)}|{stop.name}'.encode()).hexdigest()
        stop.location = (116.325, 39.805) if city_key(stop.city) == city_key('北京市') else (117.125, 39.105)
        stop.location_status, stop.poi_id = 'verified', 'synthetic-conversation-' + token[:16]
        stop.matched_name, stop.address = stop.name, '离线人工合成地址，不可用于出行'
        stop.location_note = '离线合成地点定位；未调用高德，不代表真实地点或营业。'

    async def close(self):
        pass


async def synthetic_interpreter(plan, state, body):
    """Explicit deterministic fixture parser; never pretends to use DeepSeek."""
    from backend.planning_conversation import Proposal, NewPlace, interpret
    text = body.instruction.strip()
    followup = text.startswith('保留本窗口尚未应用的对话变更')
    if followup:
        # Form recalculation sends the current structured choices separately.
        # Recover only the explicit fixture request, not an assistant's prose.
        text = next((message.content for message in reversed(body.messages)
                     if message.role == 'user' and '取消合成活动A' in message.content
                     and '新增合成活动Z' in message.content), '')
        if not text:
            return Proposal(unresolved=['离线演示未找到尚未应用的A→Z原始要求，请重新输入完整合成测试句。'],
                            reply='【离线合成解析器】没有猜测此前文字变更，也没有修改行程。')
    if any(word in text for word in ('改签', '自动退款', '跨城搬移', '不支持测试', '别的城市')):
        return Proposal(unresolved=['离线演示不执行改签、退款或跨城修改。请明确所选城市内的活动及时间，或使用A→Z合成测试句。'],
                        reply='【离线合成澄清】这条要求尚不能执行，当前方案保持；不会转用真实模型或静默丢弃要求。')
    if '取消合成活动A' in text and '新增合成活动Z' in text:
        matches = [(i, s) for i, d in enumerate(plan.itinerary.days) for s in d.stops
                   if s.name == '合成活动A' and i in body.command.day_indexes]
        existing = [(i, s) for i, d in enumerate(plan.itinerary.days) for s in d.stops
                    if s.name == '合成活动Z' and i in body.command.day_indexes]
        if not matches and len(existing) == 1:
            return Proposal(reply='【离线合成解析器】A→Z已在当前版本中，无需再次新增；继续结合当前表单活动、时间和预算重新计算。')
        if len(matches) != 1:
            return Proposal(unresolved=['离线样例的合成活动A不在本次所选日期，请打开对话样例第1天。'],
                            reply='【离线合成解析器】没有修改行程。')
        index, stop = matches[0]
        price = re.search(r'费用\s*(\d+(?:\.\d{1,2})?)\s*元', text)
        if not price:
            return Proposal(unresolved=['请给合成活动Z填写全员费用，例如费用80元。'])
        additions = [] if existing else [NewPlace(name='合成活动Z', city=stop.city, day_index=index, duration_min=30,
                                                  estimated_total_yuan=Decimal(price.group(1)), priority='must')]
        return Proposal(patch={'activities': [{'stop_id': stop.id, 'status': 'cancelled'}]}, additions=additions,
            reply='【离线合成解析器】取消A、加入Z，30分钟、全员预留' + price.group(1) +
                  '元，并结合当前表单选择。地点和道路均为合成；下方规划确实执行仓颉内核，请查看差异再保存。')
    # Rules remain useful for budget/time forms. The offline process has no key,
    # so unsupported free text returns an honest unresolved response, not a call.
    return await interpret(plan, state, body)


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
    flight_id = 'constraint-demo-flight'
    if not store.get(flight_id):
        request = trip.model_copy(deep=True)
        request.origin, request.include_origin_transport, request.budget = '长沙市', True, Decimal('4000')
        request.start_time = '05:00'
        request.intercity_modes = ['train', 'flight']
        data = itinerary.model_copy(deep=True)
        data.title += ' · 机票外跳与混合交通'
        data.transfers.insert(0, CityTransfer(from_city='长沙市', to_city='北京市', departure_date=start,
            arrival_date=start, departure_time='05:00', arrival_time='08:00', mode='flight', unit_cost='700',
            note='合成航班时段/金额，非真实航班；请使用外部查询'))
        validation = validate_core(normalized_input(request, data, routes))
        store.save(PlanRecord(id=flight_id, created_at=datetime.now(timezone.utc), request=request,
            itinerary=data, routes=routes, validation=validation))
    mixed_id = 'constraint-demo-mixed'
    if not store.get(mixed_id):
        request = trip.model_copy(deep=True)
        request.destinations[0].preferred_days = Decimal('1.5')
        request.destinations[1].preferred_days = Decimal('1.5')
        data = itinerary.model_copy(deep=True)
        data.title = '【离线合成】跨城日期分城市调整'
        data.summary = '第二天上午北京、下午天津；跨城11:00—12:00，其余道路和价格全部人工合成，不能用于出行。'
        mixed_day = data.days[1]
        mixed_day.title = '离线合成北京上午＋天津下午'
        mixed_day.stops[0].start, mixed_day.stops[0].end = '09:00', '10:00'
        mixed_day.stops[1].city = mixed_day.stops[2].city = '天津市'
        mixed_day.stops[1].start, mixed_day.stops[1].end = '14:00', '15:00'
        mixed_day.stops[2].start, mixed_day.stops[2].end = '16:00', '16:30'
        data.transfers[0].departure_time, data.transfers[0].arrival_time = '11:00', '12:00'
        mixed_routes = [route.model_copy(deep=True) for route in routes if route.day_index != 1]
        for left, right in zip(mixed_day.stops, mixed_day.stops[1:]):
            same_city = left.city == right.city
            mixed_routes.append(RouteLeg(day_index=1, from_id=left.id, to_id=right.id, mode='walking',
                available=same_city, scope='local' if same_city else 'intercity',
                distance_m=500 if same_city else None, duration_s=600 if same_city else None,
                polyline=[left.location, right.location] if same_city else [],
                queried_at=datetime.now(timezone.utc), provider_endpoint='synthetic-fixture',
                note='离线人工合成跨城边界或市内500米/600秒，不可用于出行'))
        mixed_routes.sort(key=lambda route: route.day_index)
        validation = validate_core(normalized_input(request, data, mixed_routes))
        store.save(PlanRecord(id=mixed_id, created_at=datetime.now(timezone.utc), request=request,
            itinerary=data, routes=mixed_routes, validation=validation))


async def seed_stations(store):
    """Actual station names; every schedule, fare, POI and road is synthetic."""
    from backend.bookings import apply_hotel_anchors, repair_booking_schedule
    from backend.transfer_timeline import prepare_transfer_anchors
    from backend.planning import recost_all
    from backend.models import TripRequest, Stop, DayPlan, Expense, Itinerary, CityTransfer, PlanRecord
    from backend.validation import normalized_input, validate_core
    identifier = 'constraint-demo-stations'
    if store.get(identifier):
        return
    start = date(2026, 10, 23)
    request = TripRequest(origin='北京市', destination='天津市', include_origin_transport=True, return_to_origin=True,
        start_date=start, start_time='09:00', days=2, people=2, budget='1500', mode='walking',
        confirmed_hotels=[{'city': '天津市', 'name': '合成天津海河酒店', 'check_in': start,
                           'check_out': start + timedelta(days=1), 'total_cost': '300'}],
        preferences='纯离线合成验收：站名为真实名称，但时刻、价格、地点坐标与道路全部人工构造，不可用于出行。')
    days = []
    for index in range(2):
        stops = [Stop(id=f'station-sight-{index}', city='天津市', name='合成天津游览点' + str(index + 1),
            kind='sight', start='12:00' if index == 0 else '10:00', end='13:00' if index == 0 else '11:00'),
            Stop(id=f'station-meal-{index}', city='天津市', name='合成天津餐厅' + str(index + 1),
                 kind='meal', start='13:15' if index == 0 else '11:30', end='13:45' if index == 0 else '12:00')]
        days.append(DayPlan(date=start + timedelta(days=index), title='【合成】北京往返天津·第' + str(index + 1) + '天',
            stops=stops, expenses=[Expense(label=s.name + '合成费用', category='food' if s.kind == 'meal' else 'ticket',
                                          stop_id=s.id, unit_cost='50' if s.kind == 'meal' else '20', basis='group',
                                          note='离线人工合成预算，不是真实票价或消费') for s in stops],
            tips=['站名真实；班次、时刻、费用、地点和道路为人工合成，不是铁路／地图查询结果。']))
    transfers = [CityTransfer(from_city='北京市', to_city='天津市', departure_date=start, arrival_date=start,
        departure_time='10:30', arrival_time='11:15', mode='train', unit_cost='60', departure_station='北京南',
        arrival_station='天津', schedule_status='queried', train_code='合成C001', source='离线合成，非铁路实查',
        queried_at=datetime.now(timezone.utc), note='合成往程45分钟、60元/人，不是真实车次／票价'),
        CityTransfer(from_city='天津市', to_city='北京市', departure_date=start + timedelta(days=1),
        arrival_date=start + timedelta(days=1), departure_time='17:00', arrival_time='17:45', mode='train',
        unit_cost='60', departure_station='天津', arrival_station='北京南', schedule_status='queried',
        train_code='合成C002', source='离线合成，非铁路实查', queried_at=datetime.now(timezone.utc),
        note='合成返程45分钟、60元/人，不是真实车次／票价')]
    data = Itinerary(title='【离线合成】北京南站↔天津站·铁路、酒店与每日接驳',
        summary='真实站名仅用于检查界面衔接；全部时刻、价格、地点和道路均为离线合成，不可用于出行或购票。',
        days=days, transfers=transfers)
    prepare_transfer_anchors(data, request)
    apply_hotel_anchors(data, request)
    mapping = SyntheticBookingMap(request.journey_cities)
    routes = await mapping.enrich(data, request.mode, compare_modes=True)
    await mapping.close()
    recost_all(request, data, routes)
    from types import SimpleNamespace
    conflicts, adjustments = repair_booking_schedule(SimpleNamespace(request=request, itinerary=data, routes=routes))
    if conflicts:
        raise ValueError('离线站点样例仍有时间冲突：' + '；'.join(conflicts))
    validation = validate_core(normalized_input(request, data, routes))
    store.save(PlanRecord(id=identifier, created_at=datetime.now(timezone.utc), request=request,
                         itinerary=data, routes=routes, validation=validation, adjustments=adjustments))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=7867)
    args = parser.parse_args()
    offline_process()
    from backend.config import settings
    area = ROOT / '.runtime'
    area.mkdir(exist_ok=True)
    runtime = Path(tempfile.mkdtemp(prefix='constraint-demo-', dir=area))
    settings.runtime, settings.port, settings.rail_mcp_enabled = runtime, args.port, False
    for field in ('llm_key', 'amap_key', 'amap_web_key', 'amap_js_sec', 'amap_sign_secret'):
        setattr(settings, field, '')
    from backend import main as server, constraint_replan, booking_routes, planning_conversation_routes as conversation_routes, transport_cost, local_transport_policy
    original_preview = constraint_replan.preview
    async def synthetic_preview(*args, **kwargs):
        # Explicit provider injection bypasses the real-key availability gate
        # only in this separate demo process; no pretend API key is configured.
        kwargs['provider_factory'] = SyntheticRoads
        return await original_preview(*args, **kwargs)
    constraint_replan.preview = synthetic_preview
    original_booking_preview = booking_routes.preview_bookings
    async def synthetic_booking_preview(*args, **kwargs):
        kwargs['amap_factory'] = SyntheticBookingMap
        return await original_booking_preview(*args, **kwargs)
    booking_routes.preview_bookings = synthetic_booking_preview
    original_conversation_preview = conversation_routes.conversation_preview
    async def synthetic_conversation_preview(*args, **kwargs):
        kwargs.update(interpreter=synthetic_interpreter, amap_factory=SyntheticConversationMap,
                      provider_factory=SyntheticRoads)
        return await original_conversation_preview(*args, **kwargs)
    conversation_routes.conversation_preview = synthetic_conversation_preview
    original_estimate = transport_cost.estimate_cost
    def fixture_estimate(route, city, stay_minutes=0):
        if route.provider_endpoint.startswith('synthetic'):
            return synthetic_cost(route, city, stay_minutes)
        return original_estimate(route, city, stay_minutes)
    transport_cost.estimate_cost = fixture_estimate
    constraint_replan.estimate_cost = fixture_estimate
    local_transport_policy.estimate_cost = fixture_estimate
    seed(server.store)
    asyncio.run(seed_stations(server.store))
    # Deliberately invalid historical clocks exercise the UI's conflict display.
    # This is an isolated synthetic record, never written to the normal history.
    from backend.models import Stop, DayPlan, Itinerary, TripRequest, PlanRecord, RouteLeg
    from backend.validation import normalized_input, validate_core
    trip = TripRequest(destination='上海', start_date='2026-10-23', days=1)
    points = [Stop(id='overlap-bund', name='合成外滩', city='上海', kind='sight',
        start='21:51', end='23:01', location=(121.49,31.24), location_status='verified'),
        Stop(id='overlap-nanjing', name='合成南京路', city='上海', kind='sight',
        start='21:40', end='22:30', location=(121.48,31.23), location_status='verified')]
    data = Itinerary(title='【离线合成】历史重叠时间不可重排掩盖',
        summary='人工构造的错误时段，只测试明确冲突与原顺序保留；不能用于出行。',
        days=[DayPlan(date=trip.start_date, title='错误历史回归样例', stops=points)])
    roads = [RouteLeg(day_index=0, from_id=points[0].id, to_id=points[1].id, mode='walking',
        available=True, distance_m=500, duration_s=600, polyline=[p.location for p in points],
        queried_at=datetime.now(timezone.utc), note='合成500米/600秒，非真实地图查询')]
    server.store.save(PlanRecord(id='schedule-overlap-regression', created_at=datetime.now(timezone.utc),
        request=trip, itinerary=data, routes=roads, validation=validate_core(normalized_input(trip,data,roads))))
    import uvicorn
    print(f'Offline synthetic acceptance: http://127.0.0.1:{args.port}; runtime={runtime}', flush=True)
    uvicorn.run(server.app, host='127.0.0.1', port=args.port, log_level='warning', access_log=False)


if __name__ == '__main__':
    main()
