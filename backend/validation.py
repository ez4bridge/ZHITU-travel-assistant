"""Deterministic validation; the normalized JSON contract can also run in Cangjie."""
import asyncio
import hashlib
import json
import math
from datetime import timedelta
from pathlib import Path

from .config import ROOT, settings
from .models import Issue, ValidationReport, cents, city_key, itinerary_expenses
from .journey import normalized_journey, validate_journey
from .transfer_buffers import arrival_buffer, departure_buffer


def minutes(time):
    hour, minute = map(int, time.split(':'))
    return hour * 60 + minute


def issue(code, message, day_index=None, stop_id=None):
    return {'code': code, 'message': message, 'day_index': day_index, 'stop_id': stop_id}


def normalized_input(request, itinerary, routes, study_context=None):
    errors, warnings, unknowns = [], [], []
    total = sum(cents(cost.unit_cost) * cost.quantity * (request.people if cost.basis == 'person' else 1)
                for cost in itinerary_expenses(itinerary))
    if len(itinerary.days) != request.days:
        errors.append(issue('day_count', f'需要 {request.days} 天，方案包含 {len(itinerary.days)} 天'))
    for index, day in enumerate(itinerary.days):
        if day.date != request.start_date + timedelta(days=index):
            errors.append(issue('date_mismatch', '行程日期与出发日期不一致', index))
        if index == 0 and day.stops and minutes(day.stops[0].start) < minutes(request.start_time):
            errors.append(issue('before_start', '首个活动早于设定的出发时间', index, day.stops[0].id))
        if not day.expenses:
            warnings.append(issue('missing_cost', '这一天没有费用明细，无法完整核对预算', index))
        categories = {cost.category for cost in day.expenses}
        if any(s.kind == 'meal' for s in day.stops) and 'food' not in categories:
            warnings.append(issue('missing_food_cost', '餐饮活动缺少费用明细，预算可能不完整', index))
        if index < request.days - 1 and 'hotel' not in categories:
            warnings.append(issue('missing_hotel_cost', '过夜行程未列住宿费用，请明确住宿或免费借宿安排', index))
        if request.confirmed_hotel and day.stops and index < request.days - 1 and day.stops[-1].kind != 'hotel' and city_key(day.stops[-1].city or request.city_route[0]) == city_key(request.city_route[0]):
            warnings.append(issue('hotel_arrival', '过夜当天末站未指定酒店，回酒店的接驳路线尚未核实', index))
        if request.confirmed_hotel and day.stops and index > 0 and day.stops[0].kind != 'hotel' and city_key(day.stops[0].city or request.city_route[0]) == city_key(request.city_route[0]):
            warnings.append(issue('hotel_departure', '当天首站未指定出发酒店，酒店至首站的接驳路线尚未核实', index))
        if any(r.day_index == index and r.scope == 'local' and r.mode != 'walking' and not r.stationary for r in routes) and 'transport' not in categories:
            warnings.append(issue('missing_transport_cost', '所选交通方式未列费用，预算可能不完整', index))
        if any(r.day_index == index and r.scope == 'local' and r.mode in ('transit', 'taxi', 'driving', 'bicycling')
               and not r.stationary and r.fare_yuan is None and r.cost_estimate is None for r in routes):
            unknowns.append(issue('transport_fare', '部分交通费用未查到，预算包含模型估算或仍有缺项，请核实', index))
        if any(s.kind == 'sight' for s in day.stops) and 'ticket' not in categories:
            unknowns.append(issue('ticket_coverage', '景点门票费用覆盖未确认；免费景点也应注明', index))
        walk_m = sum(r.distance_m or 0 for r in routes if r.day_index == index and r.mode == 'walking' and r.available)
        if walk_m > {'relaxed': 6000, 'balanced': 10000, 'compact': 15000}[request.style]:
            warnings.append(issue('walking_load', f'当天已知步行约 {walk_m / 1000:.1f} 公里，请结合体力选择其他交通方式', index))
        sight_count = sum(s.kind == 'sight' for s in day.stops)
        if sight_count > {'relaxed': 3, 'balanced': 4, 'compact': 5}[request.style]:
            warnings.append(issue('pace_limit', '当天主要景点数量超出所选节奏建议，请考虑减项', index))
        for stop in day.stops:
            if '餐饮绕路待调整' in stop.location_note:
                warnings.append(issue('meal_detour', stop.location_note, index, stop.id))
            if stop.kind != 'break':
                message = '已有地图营业时间参考，出行日开放与预约仍需官方核实' if stop.opening_hours_status == 'map_reference' else '营业时间、预约要求未验证'
                unknowns.append(issue('opening_hours', f'{stop.name}：{message}', index, stop.id))
            category = {'sight': 'ticket', 'meal': 'food'}.get(stop.kind)
            if category and not any(e.stop_id == stop.id and e.category == category for e in day.expenses):
                unknowns.append(issue('stop_cost', f'{stop.name}：尚无可对应的{"门票" if category == "ticket" else "餐饮"}费用参考；不视为免费', index, stop.id))
        from .meals import meal_notes, period, WINDOWS
        dining_note = meal_notes(day)
        if dining_note and not request.study:
            target = warnings if any(s.kind == 'meal' and not WINDOWS[period(s)][0] <= minutes(s.start) <= WINDOWS[period(s)][1] for s in day.stops) else unknowns
            target.append(issue('meal_arrangement', dining_note, index))
    if request.days > 1:
        from .bookings import confirmed_hotels
        hotels = confirmed_hotels(request)
        missing_nights = [d for d in itinerary.days[:-1] if not any(
            h.check_in <= d.date < h.check_out and (not d.stops or city_key(h.city) == city_key(d.stops[-1].city)) for h in hotels)]
        if missing_nights:
            unknowns.append(issue('accommodation_unconfirmed', '部分日期住宿尚未确认，仅推荐片区；相应酒店接驳未核实'))
    for booking in request.booked_transports:
        before = departure_buffer(booking.mode, booking.departure_buffer_min)
        after = arrival_buffer(booking.mode, booking.arrival_buffer_min)
        for index, day in enumerate(itinerary.days):
            for stop in day.stops:
                if (day.date == booking.departure_date and city_key(stop.city) == city_key(booking.from_city)
                        and stop.booking_anchor != 'transport_departure'
                        and minutes(stop.end) > minutes(booking.departure_time) - before):
                    errors.append(issue('booking_departure_buffer', '活动未留足用户已订交通的进站／安检时间', index, stop.id))
                if (day.date == booking.arrival_date and city_key(stop.city) == city_key(booking.to_city)
                        and minutes(stop.start) < minutes(booking.arrival_time) + after):
                    errors.append(issue('booking_arrival_buffer', '活动早于已订交通到达加出站预留时间', index, stop.id))
    if request.guides and (not itinerary.guide_decisions or any(d.status == 'not_used' for d in itinerary.guide_decisions)):
        unknowns.append(issue('guide_adoption', '部分攻略未能落实到活动，请查看攻略采纳与调整说明'))
    unknowns.insert(0, issue('estimated_prices', '费用为参考估算；交通区分地图估价、公开费率和预算假设，非实时订单报价'))
    if request.study:
        from .study_rules import study_issues
        study_errors, study_unknowns = study_issues(request, itinerary, routes, study_context)
        errors.extend(study_errors)
        unknowns.extend(study_unknowns)
    data = {
        'budget_cents': cents(request.budget), 'total_cents': total,
        'pre_errors': errors, 'pre_warnings': warnings, 'unknowns': unknowns,
        'days': [{'stops': [{'id': stop.id, 'name': stop.name, 'identity': stop.poi_id or f'{city_key(stop.city)}:{stop.name}',
                            'start_min': minutes(stop.start), 'end_min': minutes(stop.end),
                            'poi_ok': bool(stop.location and stop.location_status == 'verified'),
                            'needs_poi': True, 'check_duplicate': stop.kind == 'sight'} for stop in day.stops]} for day in itinerary.days],
        'legs': [{'day_index': r.day_index, 'from_id': r.from_id, 'to_id': r.to_id,
                  'available': r.available and r.duration_s is not None and r.distance_m is not None
                               and (len(r.polyline) >= 2 or r.stationary),
                  'duration_min': math.ceil((r.duration_s or 0) / 60)} for r in routes],
    }
    # Preserve one leg per adjacent pair, including an explicitly non-local boundary.
    for index, day in enumerate(itinerary.days):
        for before, after in zip(day.stops, day.stops[1:]):
            if before.city and after.city and city_key(before.city) != city_key(after.city):
                data['legs'] = [leg for leg in data['legs'] if (leg['day_index'], leg['from_id'], leg['to_id']) != (index, before.id, after.id)]
                data['legs'].append({'day_index': index, 'from_id': before.id, 'to_id': after.id,
                                     'available': False, 'duration_min': 0, 'intercity': True})
    journey = normalized_journey(request, itinerary)
    if journey is not None:
        data['journey'] = journey
    return data


def validate_core(data):
    errors = list(data.get('pre_errors', []))
    warnings = list(data.get('pre_warnings', []))
    unknowns = list(data.get('unknowns', []))
    if data.get('journey'):
        journey_errors, journey_warnings, journey_unknowns = validate_journey(data['journey'])
        errors.extend(journey_errors)
        warnings.extend(journey_warnings)
        unknowns.extend(journey_unknowns)
    if data['total_cents'] > data['budget_cents']:
        errors.append(issue('over_budget', '估算总费用超过全员预算'))
    seen = set()
    for index, day in enumerate(data['days']):
        stops = day['stops']
        for i, stop in enumerate(stops):
            if stop['end_min'] <= stop['start_min']:
                errors.append(issue('invalid_duration', '活动结束时间必须晚于开始时间', index, stop['id']))
            if i and stop['start_min'] < stops[i - 1]['end_min']:
                errors.append(issue('time_overlap', '与上一个活动的时间重叠', index, stop['id']))
            identity = stop['identity']
            if stop['check_duplicate']:
                if identity in seen:
                    warnings.append(issue('duplicate_stop', '行程中出现重复地点，请确认是否有意安排', index, stop['id']))
                seen.add(identity)
            if stop['needs_poi'] and not stop['poi_ok']:
                errors.append(issue('missing_location', '地点未定位，行程尚不可执行，请选择具体地点', index, stop['id']))
        for before, after in zip(stops, stops[1:]):
            leg = next((r for r in data['legs'] if r['day_index'] == index and r['from_id'] == before['id'] and r['to_id'] == after['id']), None)
            if leg and leg.get('intercity'):
                continue
            if not leg or not leg['available']:
                errors.append(issue('missing_route', '相邻地点没有可用的真实路线，行程尚不可执行', index, after['id']))
            elif after['start_min'] - before['end_min'] < leg['duration_min']:
                errors.append(issue('transfer_time', '活动之间预留的时间不足以完成实际路线', index, after['id']))
    return {'status': 'issues' if errors or warnings else ('passed_with_unknowns' if unknowns else 'passed'),
            'errors': errors, 'warnings': warnings, 'unknowns': unknowns,
            'total_cents': data['total_cents'], 'budget_cents': data['budget_cents']}


class Validator:
    def __init__(self, executable=None, fixtures=None, preferred=None):
        self.executable = Path(executable or ROOT / 'cangjie' / 'bin' / 'validator.exe')
        self.fixtures = Path(fixtures or ROOT / 'cangjie' / 'fixtures.json')
        self.preferred = preferred or settings.validator
        self._verified_hash = ''
        self._failed_hash = ''
        self._lock = asyncio.Lock()
        self.diagnostic = '仓颉模块未启用，使用 Python 校验'

    async def call_cangjie(self, data):
        process = await asyncio.create_subprocess_exec(str(self.executable), stdin=asyncio.subprocess.PIPE,
                                                      stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            output, _ = await asyncio.wait_for(process.communicate(json.dumps(data, ensure_ascii=False).encode('utf-8')), timeout=3)
        except BaseException as error:
            if process.returncode is None:
                process.kill()
            await process.wait()
            if isinstance(error, asyncio.TimeoutError):
                raise RuntimeError('仓颉原生程序超过 3 秒运行上限') from None
            raise
        if process.returncode or len(output) > 1000000:
            raise RuntimeError(f'仓颉模块退出异常或输出过大（退出码 {process.returncode}）')
        result = json.loads(output)
        report = ValidationReport.model_validate({**result, 'backend': 'cangjie'})
        if report.total_cents != data['total_cents'] or report.budget_cents != data['budget_cents']:
            raise RuntimeError('仓颉模块费用输出与输入不一致')
        expected_status = 'issues' if report.errors or report.warnings else ('passed_with_unknowns' if report.unknowns else 'passed')
        if report.status != expected_status:
            raise RuntimeError('仓颉模块状态格式不一致')
        return result

    async def ready(self):
        if self.preferred == 'python' or not self.executable.is_file():
            return False
        async with self._lock:
            try:
                fixture_bytes = self.fixtures.read_bytes()
                digest = hashlib.sha256(self.executable.read_bytes() + b'\0' + fixture_bytes).hexdigest()
            except OSError as error:
                self.diagnostic = '仓颉文件不可访问，已回退 Python：' + settings.safe_error(error)
                return False
            if digest == self._verified_hash:
                self.diagnostic = '仓颉编译程序已通过固定样例一致性检查'
                return True
            if digest == self._failed_hash:
                return False
            try:
                cases = json.loads(fixture_bytes.decode('utf-8-sig'))
                if not cases:
                    raise RuntimeError('仓颉固定样例不能为空')
                for case in cases:
                    if await self.call_cangjie(case['input']) != validate_core(case['input']):
                        raise RuntimeError('仓颉与 Python 固定样例结果不一致：' + case['name'])
                self._verified_hash = digest
                self.diagnostic = '仓颉编译程序已通过固定样例一致性检查'
                return True
            except Exception as error:
                self._failed_hash = digest
                self.diagnostic = '仓颉不可用，已回退 Python：' + settings.safe_error(error)
                return False

    async def validate(self, request, itinerary, routes, study_context=None):
        # Study evidence, policy and walking checks run in Python normalization;
        # the optional native program checks the shared schedule and cost contract.
        data = normalized_input(request, itinerary, routes, study_context)
        if await self.ready():
            try:
                result = await self.call_cangjie(data)
                return ValidationReport(**result, backend='cangjie', diagnostic=self.diagnostic)
            except Exception as error:
                self._failed_hash = self._verified_hash
                self._verified_hash = ''
                self.diagnostic = '仓颉运行失败，已回退 Python：' + settings.safe_error(error)
        return ValidationReport(**validate_core(data), backend='python', diagnostic=self.diagnostic)


validator = Validator()
