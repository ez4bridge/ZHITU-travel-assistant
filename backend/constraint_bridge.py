"""Engine2.2 process boundary, reused from the project Engine2.1 (Mulan PSL v2).

No Python repair fallback. Building and self-checking this independent binary
does not change the older optional validator's configuration or fallback.
"""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]


class ConstraintUnavailable(RuntimeError):
    pass


def strict_json(data):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('2.2原生输出包含重复字段')
            result[key] = value
        return result
    def forbidden(_):
        raise ValueError('2.2协议仅接受整数和有限JSON值')
    result = json.loads(data, object_pairs_hook=pairs, parse_constant=forbidden, parse_float=forbidden)
    def bounded(value, depth=0):
        if depth > 32:
            raise ValueError('2.2原生输出嵌套超限')
        if isinstance(value, dict):
            for child in value.values():
                bounded(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                bounded(child, depth + 1)
    bounded(result)
    return result


def validate_output(result, payload):
    def require(ok):
        if not ok:
            raise ValueError('2.2原生输出不符合冻结协议')
    def integer(value, low, high):
        return type(value) is int and low <= value <= high
    def strings(value, maximum=250):
        return (type(value) is list and len(value) <= maximum
                and all(type(item) is str and 0 < len(item) <= 1024 for item in value)
                and len(value) == len(set(value)))
    require(type(result) is dict and set(result) == {'schema_version', 'request_id', 'op', 'engine', 'outcome',
        'assignments', 'used_route_ids', 'charged_cost_ids', 'total_cents', 'unknown_cost_ids', 'checks',
        'unplaced', 'changes', 'objective', 'search'})
    require(result['schema_version'] == '2.2' and result['engine'] == 'cangjie-journey-planner')
    require(result['request_id'] == payload['request_id'] and result['op'] == payload['op'])
    require(result['outcome'] in ('feasible', 'provisional', 'unchanged', 'conflict', 'no_solution', 'completed'))
    assignments = result['assignments']
    if payload['op'] == 'repair':
        require((assignments is None) == (result['outcome'] in ('conflict', 'no_solution')))
    if assignments is not None:
        require(type(assignments) is list and len(assignments) <= 24)
        for assignment in assignments:
            require(type(assignment) is dict and set(assignment) == {'task_id', 'day_index', 'start_min', 'end_min'})
            require(type(assignment['task_id']) is str and 0 < len(assignment['task_id']) <= 160)
            require(integer(assignment['day_index'], 0, 2) and integer(assignment['start_min'], 0, 1439) and integer(assignment['end_min'], 0, 1439))
    require(strings(result['used_route_ids']) and strings(result['charged_cost_ids'], 100) and strings(result['unknown_cost_ids']))
    require(set(result['used_route_ids']) <= {route['route_id'] for route in payload['routes']})
    require(set(result['charged_cost_ids']) <= {cost['cost_id'] for cost in payload['cost_catalog']})
    require(integer(result['total_cents'], 0, 10**16))
    require(type(result['checks']) is list and len(result['checks']) <= 2000)
    for check in result['checks']:
        require(type(check) is dict and set(check) == {'constraint_id', 'kind', 'state', 'task_id', 'day_index', 'message', 'evidence_ids'})
        require(check['state'] in ('satisfied', 'violated', 'unknown'))
        require(all(type(check[key]) is str and len(check[key]) <= 2048 for key in ('constraint_id', 'kind', 'message')))
        require(check['task_id'] is None or (type(check['task_id']) is str and len(check['task_id']) <= 160))
        require(check['day_index'] is None or integer(check['day_index'], 0, 2))
        require(strings(check['evidence_ids'], 250))
    require(type(result['unplaced']) is list and len(result['unplaced']) <= 24)
    for item in result['unplaced']:
        require(type(item) is dict and set(item) == {'task_id', 'priority', 'reason_code', 'message'})
        require(item['priority'] in ('must', 'want', 'optional'))
        require(all(type(item[key]) is str and len(item[key]) <= 2048 for key in ('task_id', 'reason_code', 'message')))
    require(type(result['changes']) is list and len(result['changes']) <= 24)
    for change in result['changes']:
        require(type(change) is dict and set(change) == {'task_id', 'fields', 'from_day', 'to_day', 'from_start', 'to_start'})
        require(type(change['task_id']) is str and strings(change['fields'], 4))
        require(set(change['fields']) <= {'unplaced', 'day', 'time', 'placed_from_catalog'})
        for key in ('from_day', 'to_day'):
            require(change[key] is None or integer(change[key], -1, 2))
        for key in ('from_start', 'to_start'):
            require(change[key] is None or integer(change[key], 0, 1439))
    require(type(result['objective']) is list and len(result['objective']) == 8 and all(integer(value, 0, 10**16) for value in result['objective']))
    search = result['search']
    require(type(search) is dict and set(search) == {'expanded_states', 'evaluated_candidates', 'termination'})
    require(integer(search['expanded_states'], 0, payload['limits']['max_states']))
    require(integer(search['evaluated_candidates'], 0, search['expanded_states']))
    require(search['termination'] in ('completed', 'state_limit', 'time_limit'))
    if payload['op'] == 'repair' and assignments is not None:
        require(not any(check['state'] == 'violated' for check in result['checks']))
        require(not any(item['priority'] == 'must' for item in result['unplaced']))
    return result


class ConstraintBridge:
    def __init__(self, executable=None, timeout=5):
        self.executable = Path(executable or ROOT / 'cangjie/bin/journey_planner.exe')
        self.timeout = timeout
        self._slots = asyncio.Semaphore(2)
        self._lock = asyncio.Lock()
        self._approved_hash = ''
        self.diagnostic = '约束仓颉2.2引擎尚未自检'

    def digest(self):
        return hashlib.sha256(self.executable.read_bytes()).hexdigest()

    def _build_fingerprint(self):
        """A stale binary must not silently claim it is the current Cangjie source."""
        manifest_path = self.executable.with_name('journey_planner.build.json')
        source = ROOT / 'cangjie/journey_planner/src/main.cj'
        try:
            manifest = strict_json(manifest_path.read_text(encoding='utf-8-sig'))
            source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            binary_digest = self.digest()
        except (OSError, ValueError, TypeError):
            raise ConstraintUnavailable('约束2.2构建清单缺失或不可读，请重新构建') from None
        if (not isinstance(manifest, dict) or manifest.get('schema_version') != '2.2'
                or manifest.get('source_sha256') != source_digest
                or manifest.get('executable_sha256') != binary_digest):
            raise ConstraintUnavailable('约束2.2源码或二进制与构建清单不一致，请重新构建')
        return binary_digest + source_digest

    async def call(self, payload):
        if not isinstance(payload, dict) or payload.get('schema_version') != '2.2' or payload.get('op') not in ('repair', 'evaluate'):
            raise ValueError('请使用独立Engine2.2协议')
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(data) > 1000000:
            raise ValueError('约束规划输入超过1MB')
        if not self.executable.is_file():
            raise ConstraintUnavailable('请构建独立journey_planner.exe；新约束优化不会回退为旧引擎')
        async with self._slots:
            build_fingerprint = self._build_fingerprint()
            digest = self.digest()
            started = time.monotonic()
            process = await asyncio.create_subprocess_exec(str(self.executable), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            async def read_limited(stream, limit):
                buffer = bytearray()
                while chunk := await stream.read(65536):
                    buffer.extend(chunk)
                    if len(buffer) > limit:
                        raise RuntimeError('2.2原生输出超出大小限制')
                return bytes(buffer)
            async def write():
                try:
                    process.stdin.write(data)
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    process.stdin.close()
            tasks = [asyncio.create_task(write()), asyncio.create_task(read_limited(process.stdout, 1000000)),
                asyncio.create_task(read_limited(process.stderr, 65536)), asyncio.create_task(process.wait())]
            try:
                async with asyncio.timeout(self.timeout):
                    _, output, _, code = await asyncio.gather(*tasks)
                if code:
                    raise RuntimeError('2.2原生引擎拒绝输入或执行失败')
                result = validate_output(strict_json(output), payload)
                if self._build_fingerprint() != build_fingerprint:
                    raise RuntimeError('约束引擎在计算中改变，请重试')
                result['_engine'] = {'name': 'cangjie-journey-planner', 'version': '2.2',
                    'executable_sha256': digest, 'elapsed_ms': round((time.monotonic() - started) * 1000)}
                return result
            finally:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def evaluate(self, payload, candidate=None):
        body = copy.deepcopy(payload)
        body['op'] = 'evaluate'
        if candidate is not None:
            body['candidate'] = candidate
        return await self.call(body)

    async def repair(self, payload):
        body = copy.deepcopy(payload)
        body.update(op='repair', candidate=None)
        return await self.call(body)

    async def ready(self):
        if not self.executable.is_file():
            self.diagnostic = '约束2.2未构建，已有旅行功能保留'
            return False
        async with self._lock:
            try:
                fixture_bytes = (ROOT / 'cangjie/journey_planner/fixtures/cases.json').read_bytes()
                digest = self._build_fingerprint() + hashlib.sha256(fixture_bytes).hexdigest()
                if digest == self._approved_hash:
                    return True
                sample = json.loads(fixture_bytes)[0]['input']
                positive = await self.repair(sample)
                if positive['assignments'] is None or any(item['state'] == 'violated' for item in positive['checks']):
                    raise RuntimeError('原生正例失败')
                from .constraint_reference import evaluate
                reference = evaluate(sample, positive['assignments'])
                if (reference['status'] == 'conflict' or reference['total_cents'] != positive['total_cents']
                        or set(reference['charged_cost_ids']) != set(positive['charged_cost_ids'])
                        or set(reference['used_route_ids']) != set(positive['used_route_ids'])):
                    raise RuntimeError('原生正例独立复核失败')
                sample['budget_cents'] = 0
                negative = await self.repair(sample)
                if negative['assignments'] is not None or negative['outcome'] != 'no_solution':
                    raise RuntimeError('原生预算反例失败')
                self._approved_hash = digest
                self.diagnostic = '约束2.2真实独立进程正反自检通过'
                return True
            except Exception:
                self.diagnostic = '约束2.2自检失败，请运行tools/check_constraint_planner.py'
                return False


constraint_planner = ConstraintBridge()
planner = constraint_planner
