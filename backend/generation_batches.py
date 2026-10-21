"""Bound long model output while keeping one shared journey and transfer schedule."""
import asyncio
import json
import logging
from datetime import date, timedelta
from decimal import Decimal

from pydantic import Field, ValidationError

from .models import StrictModel, DayPlan, CityTransfer, GuideInsight, GuideDecision, Itinerary
from .model_stream import ModelOutputError


class OutlineDay(StrictModel):
    date: date
    cities: list[str] = Field(default_factory=list, max_length=5)
    title: str
    places: list[str] = Field(default_factory=list, max_length=6)
    group_budget: Decimal = Field(ge=0)


class JourneyOutline(StrictModel):
    title: str
    summary: str
    days: list[OutlineDay] = Field(min_length=1, max_length=10)
    transfers: list[CityTransfer] = Field(default_factory=list, max_length=14)
    tips: list[str] = Field(default_factory=list, max_length=10)
    guide_insights: list[GuideInsight] = Field(default_factory=list, max_length=12)


class DayBatch(StrictModel):
    days: list[DayPlan] = Field(min_length=1, max_length=2)
    guide_decisions: list[GuideDecision] = Field(default_factory=list, max_length=30)


class BatchStructureError(RuntimeError):
    code = 'model_structure'

    def __init__(self, phase, issues):
        self.phase = phase
        self.validation_issues = issues
        label = phase_label(phase)
        super().__init__(f'{label}未生成有效的行程结构，本次未完成；未保存不完整行程。')


def phase_label(phase):
    return '全程框架' if phase == 'outline' else ('第' + phase[5:].replace('_', '–') + '天行程' if phase.startswith('days_') else '行程')


def structure_issues(error):
    if isinstance(error, ValidationError):
        # No rejected input, field values, provider responses or credentials.
        return [{'path': '.'.join(map(str, e['loc'])), 'type': e['type']} for e in error.errors(include_input=False)[:12]]
    return [{'path': 'journey_constraints', 'type': str(error)[:180]}]


def task_limits(request, refresh=False):
    """Seconds, bounded even at the supported ten-day/five-city maximum."""
    cities = len(request.city_route)
    model = min(480, 100 + 30 * request.days + 20 * cities)
    rail = min(180, 30 + 25 * max(0, len(request.journey_cities) - 1))
    maps = min(240, 45 + 15 * request.days + 10 * cities)
    return {'model': model, 'rail': rail, 'map': maps,
            'total': maps + 30 if refresh else min(900, model + rail + maps + 30)}


async def generate_batches(request, messages, call, emit, day_schema):
    """call(messages, phase, max_tokens) uses the caller's request-local client."""
    async def parse(model, prompt_messages, phase, tokens, check):
        attempt_messages = list(prompt_messages)
        for attempt in range(2):
            try:
                response = await call(attempt_messages, phase, tokens)
            except ModelOutputError as error:
                if error.reason != 'length':
                    raise
                raise BatchStructureError(phase, [{'path': 'model_output', 'type': 'length'}]) from None
            content = response.choices[0].message.content or ''
            try:
                value = model.model_validate_json(content)
                check(value)
                return value
            except (ValidationError, ValueError) as error:
                issues = structure_issues(error)
                logging.getLogger(__name__).warning('model_structure phase=%s attempt=%s issues=%s', phase, attempt + 1, json.dumps(issues, ensure_ascii=False))
                if attempt:
                    raise BatchStructureError(phase, issues) from None
                detail = error.json(include_input=False)[:2000] if isinstance(error, ValidationError) else str(error)
                await emit('status', {'message': f'{phase_label(phase)}格式有误，正在修复一次'})
                attempt_messages.extend([{'role': 'assistant', 'content': content},
                    {'role': 'user', 'content': '仅修复结构并保持约束：' + detail}])

    dates = [request.start_date + timedelta(days=i) for i in range(request.days)]
    decision_limit = 30 // ((request.days + 1) // 2)
    def check_outline(value):
        if [d.date for d in value.days] != dates:
            raise ValueError('框架必须覆盖全部连续旅行日期')
        from .models import city_key
        keys = [city_key(c) for c in request.city_route]
        origin = city_key(request.origin) if request.include_origin_transport else ''
        route = []
        for day in value.days:
            for city in day.cities:
                key = city_key(city)
                if key == origin and key not in keys:
                    continue  # Origin/return travel is not a destination activity.
                if key not in keys:
                    raise ValueError('框架包含目的地之外的游览城市')
                if not route or route[-1] != key:
                    route.append(key)
        if route != keys:
            raise ValueError('框架必须按用户顺序覆盖目的地，允许中途纯交通日及返回出发地')

    await emit('status', {'message': '正在确定全程城市顺序、跨城交通和每日预算框架', 'phase': 'outline'})
    outline_schema = JourneyOutline.model_json_schema()
    # Provider proof belongs to Railway.enrich, not to an outline. Listing all
    # optional proof fields here encourages the model to fill verbose defaults.
    transfer_fields = outline_schema['$defs']['CityTransfer']['properties']
    for field in ('schedule_status', 'price_status', 'train_code', 'departure_station', 'arrival_station',
            'seat_type', 'seat_availability', 'queried_at', 'source', 'query_note'):
        transfer_fields.pop(field, None)
    outline = await parse(JourneyOutline, [*messages, {'role': 'user', 'content':
        '先仅输出全程框架JSON，不写每日活动细节。每天指定主要城市、主要地点和全员日预算。先预留全部跨城费用，再分配每日餐饮、门票、市内交通及必要住宿预算；不可漏计费用伪装满足预算。'
        'transfers必须按完整交通顺序一次列全，并预留进出站时间；后续每日细化不能改变这些交通。纯交通日cities和places可以为空；出发地和返程不是额外游览目的地。不要输出days[].stops或expenses，细节在后续步骤生成。仅写必要字段，省略默认空值和查询元数据，每日places最多3处、title最多20字，summary最多120字。summary说明不能满足的预算或游玩时长要求。遵循schema：'
        + json.dumps(outline_schema, ensure_ascii=False)}], 'outline', min(6000, 2000 + request.days * 300 + len(request.city_route) * 150), check_outline)
    frozen = outline.model_dump_json()
    semaphore = asyncio.Semaphore(2)

    async def batch(start):
        wanted = dates[start:start + 2]
        def check(value):
            if [d.date for d in value.days] != wanted:
                raise ValueError('只能输出指定日期，不能遗漏或增加日期')
            ids = [s.id for d in value.days for s in d.stops]
            if len(ids) != len(set(ids)):
                raise ValueError('批次活动id必须唯一')
            if len(value.guide_decisions) > decision_limit:
                raise ValueError(f'本批次攻略决定最多{decision_limit}条')
            from .models import city_key
            for day in value.days:
                destinations = {city_key(c) for c in request.city_route}
                allowed = {city_key(c) for c in outline.days[dates.index(day.date)].cities} & destinations
                if any(city_key(s.city) not in allowed for s in day.stops):
                    raise ValueError('活动城市必须遵守对应日期的全程框架')
        async with semaphore:
            phase = f'days_{start + 1}_{start + len(wanted)}'
            await emit('status', {'message': f'正在细化第{start + 1}–{start + len(wanted)}天（共{request.days}天）', 'phase': phase})
            prompt = ('依据固定全程框架，仅输出日期' + ','.join(map(str, wanted)) + '的每日细节JSON。'
                '严格使用框架城市、地点和日预算；跨城和进出站时段不得安排活动，不改变或重复输出transfers。跨午夜交通也要避让。'
                '不要增加其他日期，不重复其他天景点；活动使用全程唯一id例如d3s1。费用quantity是每人次数不是人数；每顿餐及景点费用以stop_id关联，酒店仅推荐片区，除最后一天预留住宿。'
                '框架cities为空或仅有出发地时是纯交通日，stops必须为空；用meal_note提示途中用餐并预留费用，不在出发地添加活动。'
                '沿用用户节奏、餐饮饭点、区域选点和攻略要求。攻略decision仅关联本批次活动，不声称采纳其他日期活动；不要输出整篇攻略未采纳结论。'
                f'guide_decisions最多{decision_limit}条，每个stop.city必须写框架中当日所属城市。'
                '省略默认空字段和地图元数据，每活动description至多60字。固定框架：' + frozen + '\n输出schema：' + json.dumps(day_schema, ensure_ascii=False))
            return await parse(DayBatch, [*messages, {'role': 'user', 'content': prompt}], phase, 6500, check)

    # TaskGroup cancels sibling streams on failure/cancellation; no orphan paid calls.
    try:
        async with asyncio.TaskGroup() as group:
            jobs = [group.create_task(batch(i)) for i in range(0, request.days, 2)]
    except ExceptionGroup as errors:
        error = errors.exceptions[0]
        while isinstance(error, ExceptionGroup):
            error = error.exceptions[0]
        raise error from None
    days, decisions = [], []
    for job in jobs:
        value = job.result()
        remap = {}
        for day in value.days:
            day_index = dates.index(day.date)
            for i, stop in enumerate(day.stops):
                remap[stop.id] = f'd{day_index + 1}s{i + 1}'
            for expense in day.expenses:
                if expense.stop_id in remap:
                    expense.stop_id = remap[expense.stop_id]
            for stop in day.stops:
                stop.id = remap[stop.id]
            days.append(day)
        for decision in value.guide_decisions:
            decision.stop_ids = [remap[s] for s in decision.stop_ids if s in remap]
            decisions.append(decision)
    return Itinerary(title=outline.title, summary=outline.summary, days=days, transfers=outline.transfers,
        tips=outline.tips, guide_insights=outline.guide_insights, guide_decisions=decisions)
