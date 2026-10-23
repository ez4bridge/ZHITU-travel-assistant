"""Synthetic expectations migrated from the project Engine2.1; five-mode extension for 2.2."""
import copy
import json
from pathlib import Path


def sample():
    tasks = [dict(task_id='F', original_day_index=1, original_start_min=630, duration_min=60,
        priority='want', backlog=False, protected=False, allowed_day_indexes=[1],
        opening_state='verified', windows=[dict(day_index=1, start_min=540, end_min=1200, last_entry_min=None)], cost_ids=['cost-F']),
        dict(task_id='M', original_day_index=0, original_start_min=600, duration_min=60,
        priority='must', backlog=True, protected=False, allowed_day_indexes=[1],
        opening_state='verified', windows=[dict(day_index=1, start_min=600, end_min=1020, last_entry_min=None)], cost_ids=['cost-M'])]
    nodes = ['F', 'M', 'hotel-start', 'hotel-end']
    routes = [dict(route_id=f'r-{a}-{b}', day_index=1, from_id=a, to_id=b, mode='walking',
        duration_s=600, cost_cents=0, time_state='verified', cost_state='verified', evidence_id=f'e-{a}-{b}')
        for a in nodes for b in nodes if a != b]
    return dict(schema_version='2.2', request_id='journey-native-fixture', op='repair',
        days=[dict(day_index=1, date='2026-10-18', start_min=540, end_min=1260,
                   start_anchor_id='hotel-start', end_anchor_id='hotel-end')], source_catalog=tasks,
        baseline=[dict(task_id='F', day_index=1, start_min=630, end_min=690)], candidate=None,
        completed_ids=['B'], cancelled_ids=['D'], budget_cents=10000,
        cost_catalog=[dict(cost_id=f'cost-{sid}', task_id=sid, remaining_cents=2000,
                           charge_on_omit=False, estimated=False) for sid in ('F', 'M')],
        routes=routes, limits=dict(max_states=200, max_search_ms=2000))


def cases():
    result = []
    def add(name, data, outcomes, **expected):
        data['request_id'] = name
        result.append(dict(name=name, input=data, expected_outcomes=outcomes, **expected))
    add('missed_original_id_cross_day', sample(), ['feasible'], required_ids=['F', 'M'], expected_total=4000)
    p=sample(); p['source_catalog'][0]['protected']=True
    add('booked_future_keeps_exact_time',p,['feasible'],required_ids=['F','M'],protected_ids=['F'])
    p=sample(); p['source_catalog'][0]['protected']=True
    p['source_catalog'][1]['windows']=[dict(day_index=1,start_min=600,end_min=660,last_entry_min=600)]
    add('must_cannot_move_booking',p,['no_solution'])
    p=sample(); p['days'][0]['start_min']=570
    p['source_catalog'][1]['windows']=[dict(day_index=1,start_min=570,end_min=700,last_entry_min=600)]
    for r in p['routes']:
        if r['from_id']=='hotel-start': r['duration_s']=2700
        if r['from_id']=='F': r['duration_s']=2700
    add('last_entry_after_anchor_transfer',p,['no_solution'])
    p=sample(); p['source_catalog'][1]['opening_state']='unknown'; p['source_catalog'][1]['windows']=[]
    add('unknown_hours_not_verified',p,['provisional'],required_ids=['M'],required_unknown_kinds=['opening_hours'])
    p=sample(); p['routes']=[]
    add('missing_first_last_routes_unknown',p,['provisional'],required_ids=['M'],required_unknown_kinds=['transfer_time'])
    p=sample(); p['budget_cents']=3000
    add('budget_cannot_drop_must_but_can_leave_want',p,['feasible'],required_ids=['M'],expected_total=2000)
    p=sample(); p['budget_cents']=1000
    add('must_over_remaining_budget_fails',p,['no_solution'])
    p=sample(); p['cost_catalog'][1]['remaining_cents']=None
    add('unknown_cost_not_zero',p,['provisional'],required_ids=['M'],required_unknown_kinds=['cost_unknown'])
    p=sample(); p['source_catalog'][1]['priority']='optional'; p['cancelled_ids'].append('M')
    p['cost_catalog'][1]['charge_on_omit']=True
    add('cancelled_unpaid_commitment_remains',p,['feasible','unchanged'],forbidden_ids=['M'],expected_total=4000)
    p=sample(); p['source_catalog'][1]['priority']='want'; p['completed_ids'].append('M')
    add('completed_nonprefix_not_reexecuted',p,['feasible','unchanged'],forbidden_ids=['B','M'])
    p=sample(); p['op']='evaluate'; p['candidate']=[dict(task_id='B',day_index=1,start_min=600,end_min=660)]
    add('malicious_completed_assignment_rejected',p,['conflict'])
    p=sample(); p['op']='evaluate'; p['candidate']=[dict(task_id='M',day_index=1,start_min=600,end_min=659)]
    add('stay_shortening_rejected',p,['conflict'])
    p=sample(); p['source_catalog'][0]['priority']='optional'; p['source_catalog'][1]['priority']='want'
    p['budget_cents']=2000
    add('want_coverage_precedes_optional',p,['feasible'],required_ids=['M'],forbidden_ids=['F'])
    p=sample(); p['days']=[]; p['baseline']=[];p['source_catalog']=[];p['cost_catalog']=[];p['routes']=[]
    add('all_completed_no_fake_activity',p,['completed'],required_ids=[],expected_total=0)
    p=sample();p['days']=[];p['baseline']=[];p['routes']=[]
    for task in p['source_catalog']: task['allowed_day_indexes']=[];task['windows']=[]
    add('no_remaining_day_must_not_success',p,['no_solution'])
    p=sample();p['limits']['max_states']=1
    add('state_limit_is_honest',p,['no_solution'],expected_termination='state_limit')
    p=sample()
    for route in p['routes']:
        route.update(mode='taxi',cost_cents=None,cost_state='unknown')
    add('taxi_time_without_price',p,['provisional'],required_unknown_kinds=['cost_unknown'])
    p=sample();p['days'][0]['start_anchor_id']=None;p['days'][0]['end_anchor_id']=None
    p['routes']=[r for r in p['routes'] if r['from_id'] in ('F','M') and r['to_id'] in ('F','M')]
    add('missing_boundary_is_unknown_not_fake_hotel',p,['provisional'],required_ids=['M'],required_unknown_kinds=['boundary_location'])
    p=sample();p['baseline']=[];p['source_catalog']=[];p['cost_catalog']=[];p['routes']=[]
    for index in (1,2):
        for sid in ('M','N'):
            if sid=='N' and index==2: continue
            if not any(t['task_id']==sid for t in p['source_catalog']):
                p['source_catalog'].append(dict(task_id=sid,original_day_index=0,original_start_min=540,duration_min=60,
                    priority='must',backlog=True,protected=False,allowed_day_indexes=[1,2] if sid=='M' else [1],opening_state='verified',windows=[],cost_ids=[]))
            next(t for t in p['source_catalog'] if t['task_id']==sid)['windows'].append(dict(day_index=index,start_min=600,end_min=680,last_entry_min=None))
    p['days']=[dict(day_index=i,date=f'2026-10-{17+i}',start_min=600,end_min=680,start_anchor_id=f's{i}',end_anchor_id=f'e{i}') for i in (1,2)]
    for index in (1,2):
        nodes=['M','N',f's{index}',f'e{index}']
        p['routes'] += [dict(route_id=f'r{index}-{a}-{b}',day_index=index,from_id=a,to_id=b,mode='walking',duration_s=600,cost_cents=0,time_state='verified',cost_state='verified',evidence_id=f'ev{index}-{a}-{b}') for a in nodes for b in nodes if a!=b]
    add('two_must_need_different_future_days',p,['feasible'],required_ids=['M','N'],expected_total=0)
    p=sample()
    for sid in ('N','O'):
        task=copy.deepcopy(p['source_catalog'][1]); task['task_id']=sid;task['cost_ids']=[f'cost-{sid}'];p['source_catalog'].append(task)
        p['cost_catalog'].append(dict(cost_id=f'cost-{sid}',task_id=sid,remaining_cents=2000,charge_on_omit=False,estimated=False))
    nodes=['F','M','N','O','hotel-start','hotel-end']
    p['routes']=[dict(route_id=f'r-{a}-{b}',day_index=1,from_id=a,to_id=b,mode='walking',duration_s=600,cost_cents=0,time_state='verified',cost_state='verified',evidence_id=f'ev-{a}-{b}') for a in nodes for b in nodes if a!=b]
    add('three_missed_must_are_not_product_capped_at_two',p,['feasible'],required_ids=['F','M','N','O'],expected_total=8000)
    p=sample();unicode_id='景'*90+'🏞'
    for task in p['source_catalog']:
        if task['task_id']=='M':task['task_id']=unicode_id
    for cost in p['cost_catalog']:
        if cost['task_id']=='M':cost['task_id']=unicode_id
    for route in p['routes']:
        for key in ('from_id','to_id'):
            if route[key]=='M':route[key]=unicode_id
        route['route_id']='route:'+route['from_id']+':'+route['to_id']
        route['evidence_id']=route['route_id']
    add('unicode_stable_identity_and_long_composite_route_id',p,['feasible'],required_ids=['F',unicode_id])
    # A mode is carried honestly: native constraints use that mode's frozen facts.
    # Switching a mode never substitutes walking or taxi assumptions.
    for mode in ('walking', 'taxi', 'driving', 'transit', 'bicycling'):
        p = sample()
        for route in p['routes']:
            route.update(mode=mode, duration_s=900, cost_cents=125, cost_state='estimated')
        add('actual_mode_' + mode, p, ['provisional'], required_ids=['F', 'M'],
            expected_total=4375, required_unknown_kinds=['estimated_price'])
    return result


if __name__=='__main__':
    Path(__file__).with_name('cases.json').write_text(json.dumps(cases(),ensure_ascii=False,indent=2),encoding='utf-8')
