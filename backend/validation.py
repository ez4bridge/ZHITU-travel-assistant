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


def minutes(time):
    hour, minute = map(int, time.split(':'))
    return hour * 60 + minute


def issue(code, message, day_index=None, stop_id=None):
    return {'code': code, 'message': message, 'day_index': day_index, 'stop_id': stop_id}


def normalized_input(request, itinerary, routes):
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
        for stop in day.stops:
            if stop.kind != 'break':
                unknowns.append(issue('opening_hours', f'{stop.name}：营业时间、预约要求未验证', index, stop.id))
    unknowns.insert(0, issue('estimated_prices', '门票、餐饮、住宿和交通费用均为估算，未查询实时价格'))
    data = {
        'budget_cents': cents(request.budget), 'total_cents': total,
        'pre_errors': errors, 'pre_warnings': warnings, 'unknowns': unknowns,
        'days': [{'stops': [{'id': stop.id, 'name': stop.name, 'identity': stop.poi_id or f'{city_key(stop.city)}:{stop.name}',
                            'start_min': minutes(stop.start), 'end_min': minutes(stop.end),
                            'poi_ok': bool(stop.location and stop.location_status == 'verified'),
                            'needs_poi': stop.kind != 'break', 'check_duplicate': stop.kind == 'sight'} for stop in day.stops]} for day in itinerary.days],
        'legs': [{'day_index': r.day_index, 'from_id': r.from_id, 'to_id': r.to_id,
                  'available': r.available and r.duration_s is not None,
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
                unknowns.append(issue('missing_location', '地点未定位，路线和通行时间无法验证', index, stop['id']))
        for before, after in zip(stops, stops[1:]):
            leg = next((r for r in data['legs'] if r['day_index'] == index and r['from_id'] == before['id'] and r['to_id'] == after['id']), None)
            if leg and leg.get('intercity'):
                continue
            if not leg or not leg['available']:
                unknowns.append(issue('missing_route', '相邻地点没有可用的真实路线', index, after['id']))
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
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode or len(output) > 1000000:
            raise RuntimeError('仓颉模块退出异常或输出过大')
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

    async def validate(self, request, itinerary, routes):
        data = normalized_input(request, itinerary, routes)
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
