"""One auditable expense ledger; provider averages are references, never menu quotes."""
import re
from decimal import Decimal
from .models import cents


def reconcile_expenses(trip, itinerary, only_day=None):
    changes = []
    for index, day in enumerate(itinerary.days):
        if only_day is not None and index != only_day:
            continue
        for expense in day.expenses:
            # A per-person ticket already multiplies the party size. Only repair
            # the common party-size duplication; explicit repeat admission survives.
            if (expense.category == 'ticket' and expense.basis == 'person'
                    and trip.people > 1 and expense.quantity == trip.people
                    and not re.search(r'联票|套票|多日|每人.*[多两二三四五六七八九十0-9].*(张|次)|重复|多次|二次|两次', expense.note + expense.label)):
                expense.quantity = 1
                expense.note = ('按每人一次入场计费，人数仅乘一次；' + expense.note)[:500]
                changes.append(f'{expense.label}：已取消重复计算的同行人数。')
        for stop in day.stops:
            if stop.kind != 'meal' or stop.average_cost_yuan is None or stop.average_cost_yuan <= 0:
                continue
            primary = [e for e in day.expenses if e.category == 'food' and e.stop_id == stop.id and '分摊' not in e.note]
            # Several dish-level rows need review, not multiple copies of a meal average.
            if len(primary) != 1:
                continue
            expense = primary[0]
            if re.search(r'已支付|实付|收据|用户确认|已核实菜单', expense.note):
                continue
            if ('高德人均消费参考' in expense.note and expense.unit_cost == stop.average_cost_yuan
                    and expense.basis == 'person' and expense.quantity == 1):
                continue
            original = cents(expense.unit_cost) * expense.quantity * (trip.people if expense.basis == 'person' else 1)
            expense.label = f'{stop.name} · 餐饮参考'[:160]
            expense.unit_cost = stop.average_cost_yuan
            expense.basis, expense.quantity = 'person', 1
            expense.note = f'高德人均消费参考，非菜单报价；此前全员记账{Decimal(original) / 100}元已替换，实际消费以商家为准'[:500]
            changes.append(f'{stop.name}：餐饮账本已采用地图人均参考{stop.average_cost_yuan}元，而非旧模型估算。')
    return changes


def budget_instruction(plan, budget=None):
    report = plan.validation
    limit = report.budget_cents if budget is None else cents(budget)
    gap = max(0, report.total_cents - limit)
    categories = '、'.join(f'{key}={Decimal(value) / 100}元' for key, value in plan.expense_totals.items())
    return (f'当前全员核对后估算{Decimal(report.total_cents) / 100}元，预算{Decimal(limit) / 100}元，'
            f'至少需节省{Decimal(gap) / 100}元。分类：{categories}。'
            '请在预算内重排行程，优先减少可选付费项目、选择更经济且真实可用的餐馆和交通；'
            '不得仅下调同一餐馆的人均参考或已查询票价凑预算，不得漏算住宿、往返交通和用餐。'
            '若必须调整景点或交通，明确说明取舍；无法满足时说明真实差额，不声称达标。')
