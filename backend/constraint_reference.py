"""Independent Engine2.2 wire checks; contains no search or scheduling fallback.

Pure-wire evaluation extracted from the earlier project journey_reference.py
(Engine2.1, Mulan PSL v2). Domain and payment projection stay in the new adapter.
"""
from collections import Counter


def evaluate(payload: dict, assignments: list) -> dict:
    """Evaluate identity, timing and future costs against a frozen native wire."""
    values = [dict(a) for a in assignments]
    checks, used, charged, unknown = [], set(), set(), set()

    def add(kind, state, message, task_id=None, day_index=None, evidence_ids=None):
        checks.append({'constraint_id': f'oracle:{len(checks)}', 'kind': kind, 'state': state,
                       'task_id': task_id, 'day_index': day_index, 'message': message,
                       'evidence_ids': evidence_ids or []})

    catalog = {t['task_id']: t for t in payload['source_catalog']}
    days = {d['day_index']: d for d in payload['days']}
    baseline = {a['task_id']: a for a in payload['baseline']}
    forbidden = set(payload['completed_ids']) | set(payload['cancelled_ids'])
    by_id = {a['task_id']: a for a in values}
    for task_id, count in Counter(a['task_id'] for a in values).items():
        add('identity', 'satisfied' if count == 1 and task_id in catalog and task_id not in forbidden else 'violated',
            '活动必须属于唯一的授权未完成身份', task_id)
    for assignment in values:
        sid, index = assignment['task_id'], assignment['day_index']
        task, day = catalog.get(sid), days.get(index)
        if task is None or day is None:
            add('allowed_day', 'violated', '活动或日期不在授权目录', sid, index)
            continue
        start, end = assignment['start_min'], assignment['end_min']
        allowed = index in task['allowed_day_indexes'] and (task['backlog'] or index == task['original_day_index'])
        add('allowed_day', 'satisfied' if allowed else 'violated', '只有明确未去的活动可以迁移日期', sid, index)
        add('duration', 'satisfied' if end > start and end - start == task['duration_min'] else 'violated',
            '停留时长必须保持', sid, index)
        add('day_window', 'satisfied' if day['start_min'] <= start < end <= day['end_min'] else 'violated',
            '遵守完整日期投影的每日可用时窗', sid, index)
        if task['opening_state'] == 'unknown':
            add('opening_hours', 'unknown', '营业资料未核实，不能按全天营业声称通过', sid, index)
        else:
            fits = any(w['day_index'] == index and w['start_min'] <= start and end <= w['end_min']
                       and (w['last_entry_min'] is None or start <= w['last_entry_min']) for w in task['windows'])
            add('opening_hours', 'satisfied' if fits else 'violated', '检查开放和停止入场时刻', sid, index)
            if task['opening_state'] == 'user_confirmed':
                add('opening_source', 'unknown', '用户给定营业区间尚非运营方实时核验', sid, index)
    for sid, task in catalog.items():
        if sid in forbidden:
            continue
        if task['priority'] == 'must':
            add('must_placed', 'satisfied' if sid in by_id else 'violated', '全部必去活动必须安置', sid)
        if task['protected']:
            add('booking_protection', 'satisfied' if sid in baseline and by_id.get(sid) == baseline[sid] else 'violated',
                '固定预约和进行中活动保持日时', sid)
    routes, total = {}, 0
    for route in payload['routes']:
        key = (route['day_index'], route['from_id'], route['to_id'])
        if key in routes:
            add('route_identity', 'violated', '同一接驳边不能有多个竞争报价')
        routes[key] = route
    for index, day in days.items():
        ordered = sorted((a for a in values if a['day_index'] == index), key=lambda a: (a['start_min'], a['task_id']))
        add('daily_capacity', 'satisfied' if len(ordered) <= 8 else 'violated', '同日最多8项市内活动', day_index=index)
        for n, left in enumerate(ordered):
            for right in ordered[n + 1:]:
                add('time_overlap', 'satisfied' if left['end_min'] <= right['start_min'] else 'violated',
                    '全部活动区间两两检查', right['task_id'], index)
        previous, finish = day['start_anchor_id'], day['start_min']
        edges = [*( (a['task_id'], a['start_min'], a['end_min']) for a in ordered),
                 (day['end_anchor_id'], day['end_min'], day['end_min'])]
        for target, start, end in edges:
            add('time_order', 'satisfied' if start >= finish else 'violated', '包括日首末的时间顺序', target, index)
            if previous is None or target is None:
                add('boundary_location', 'unknown', '日首末实际地点未确认', day_index=index)
                unknown.add(f'boundary:{index}:{previous}:{target}')
            elif previous != target:
                route = routes.get((index, previous, target))
                if route is None:
                    add('transfer_time', 'unknown', '缺少实际使用接驳边时长', target, index)
                    add('cost_unknown', 'unknown', '缺少交通方式/价格资料，不视为免费', target, index)
                    unknown.add(f'route:{index}:{previous}:{target}')
                else:
                    used.add(route['route_id'])
                    evidence = [route['evidence_id']] if route['evidence_id'] else []
                    known_time = route['time_state'] != 'unknown' and route['duration_s'] is not None
                    add('transfer_time', ('satisfied' if (start - finish) * 60 >= route['duration_s'] else 'violated')
                        if known_time else 'unknown', '仅依据该接驳边冻结时长', target, index, evidence)
                    if route['time_state'] == 'estimated':
                        add('route_estimate', 'unknown', '交通时长为估算', target, index, evidence)
                    if route['cost_state'] != 'unknown' and route['cost_cents'] is not None:
                        total += route['cost_cents']
                    else:
                        unknown.add('route:' + route['route_id'])
                        add('cost_unknown', 'unknown', '路段费用未定', target, index, evidence)
                    if route['cost_state'] == 'estimated':
                        add('estimated_price', 'unknown', '路段价格为估算', target, index, evidence)
            previous, finish = target, end
    for cost in payload['cost_catalog']:
        if cost['task_id'] is None or cost['task_id'] in by_id or cost['charge_on_omit']:
            if cost['cost_id'] in charged:
                add('cost_identity', 'violated', '费用标识重复计入')
                continue
            charged.add(cost['cost_id'])
            if cost['remaining_cents'] is None:
                unknown.add(cost['cost_id'])
                add('cost_unknown', 'unknown', '未来应付金额未定')
            else:
                total += cost['remaining_cents']
            if cost['estimated']:
                add('estimated_price', 'unknown', '活动费用仍为估算')
    add('remaining_budget', 'satisfied' if total <= payload['budget_cents'] else 'violated', '城市未来应付与同一快照额度比较')
    status = 'conflict' if any(c['state'] == 'violated' for c in checks) else (
        'provisional' if any(c['state'] == 'unknown' for c in checks) else 'passed')
    return {'checks': checks, 'total_cents': total, 'unknown_cost_ids': sorted(unknown),
            'used_route_ids': sorted(used), 'charged_cost_ids': sorted(charged), 'status': status}

