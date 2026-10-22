"""Revise one DayPlan, rather than asking the model to regenerate the entire trip."""
import json

from openai import AsyncOpenAI
from pydantic import ValidationError

from .config import settings
from .models import DayPlan, cents, itinerary_expenses
from .journey import normalize_cities
from .feasibility import schedule_journey


def merge_day(content, previous, day_index):
    text = content.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    payload = json.loads(text)
    target = previous.itinerary.days[day_index].date
    # Accept common JSON wrappers, while validating the actual DayPlan strictly.
    if isinstance(payload, dict) and 'day' in payload:
        payload = payload['day']
    elif isinstance(payload, dict) and 'days' in payload:
        days = payload['days']
        matches = [day for day in days if isinstance(day, dict) and day.get('date') == str(target)] if isinstance(days, list) else []
        if len(matches) != 1:
            raise ValueError('单日修改必须返回所选日期，不能替换其他日期')
        payload = matches[0]
    day = DayPlan.model_validate(payload)
    if day.date != target:
        raise ValueError(f'只能修改{target}，不能改变出行日期')
    old_ids = [stop.id for stop in day.stops]
    if len(set(old_ids)) != len(old_ids):
        raise ValueError('同一天的活动id必须唯一')
    unchanged_ids = {stop.id for index, old in enumerate(previous.itinerary.days) if index != day_index for stop in old.stops}
    # Keep IDs referenced by the old day when possible; fix only cross-day collisions.
    replacements = {}
    for index, stop in enumerate(day.stops):
        if not stop.id or stop.id in unchanged_ids:
            new_id = f'revision-day{day_index + 1}-stop{index + 1}'
            while new_id in unchanged_ids or new_id in {s.id for s in day.stops}:
                new_id += '-new'
            replacements[stop.id] = new_id
            stop.id = new_id
    for expense in day.expenses:
        expense.stop_id = replacements.get(expense.stop_id, expense.stop_id)
    itinerary = previous.itinerary.model_copy(deep=True)
    itinerary.days[day_index] = day
    return itinerary


async def generate_day_itinerary(trip, amap, emit, previous, instruction, day_index):
    target = previous.itinerary.days[day_index]
    def cost(expenses):
        return sum(cents(e.unit_cost) * e.quantity * (trip.people if e.basis == 'person' else 1) for e in expenses)
    fixed_cost = cost(itinerary_expenses(previous.itinerary)) - cost(target.expenses)
    payload = {
        'date': str(target.date), 'instruction': instruction,
        'request': trip.model_dump(mode='json'), 'day': target.model_dump(mode='json'),
        'fixed_transfers': [t.model_dump(mode='json') for t in previous.itinerary.transfers],
        'remaining_day_budget_cents': cents(trip.budget) - fixed_cost,
        'other_restaurants': [s.matched_name or s.place_name or s.name for i, d in enumerate(previous.itinerary.days)
                              if i != day_index for s in d.stops if s.kind == 'meal'],
    }
    messages = [{'role': 'system', 'content':
        '只修改指定日期，输出一个DayPlan JSON对象，不输出整趟行程，不输出Markdown。'
        '保持日期、城市顺序、同行人数和固定跨城交通不变；先按到达后45分钟、离开前45分钟确定游玩窗口。'
        '使用具体真实景点和餐馆分店，餐费稍后由服务端按高德人均核算，不编造坐标或核实状态。'
        '分别保留午餐和晚餐，即使下午结束游览也要安排晚餐；在途用餐明确提前备餐。避免与其他日期重复同一品牌餐馆，不同分店仍属同品牌。'
        '保留合理游览时长和通勤时间，超出可用时间则减少可选活动；只推荐住宿片区，不安排虚构酒店。'
        '单次每人门票quantity=1，人数由服务端乘；费用用stop_id关联活动。'
        '不得改变或重复计算固定跨城交通，不输出其他日期。结构：' + json.dumps(DayPlan.model_json_schema(), ensure_ascii=False)},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]
    await emit('status', {'message': f'正在只调整{target.date}，保留其他日期和跨城交通'})
    async with AsyncOpenAI(api_key=settings.llm_key, base_url=settings.llm_url, timeout=60, max_retries=0) as client:
        for attempt in range(2):
            response = await client.chat.completions.create(model=settings.llm_model, messages=messages,
                response_format={'type': 'json_object'}, temperature=.2, max_tokens=5000, **settings.model_options())
            content = response.choices[0].message.content or ''
            try:
                itinerary = merge_day(content, previous, day_index)
                normalize_cities(trip, itinerary.model_copy(update={'days': [itinerary.days[day_index]]}))
                schedule_journey(trip, itinerary, only_day=day_index)
                return itinerary
            except (ValidationError, ValueError) as error:
                detail = error.json(include_input=False)[:2000] if isinstance(error, ValidationError) else str(error)
                if attempt:
                    raise ValueError(f'{target.date}的修改未通过检查，原行程保留：' + detail) from None
                amap.planning_repairs = 1
                messages.extend([{'role': 'assistant', 'content': content},
                    {'role': 'user', 'content': '仅修正所选日期的以下问题，返回DayPlan JSON：' + detail}])
