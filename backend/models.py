from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class TripRequest(StrictModel):
    origin: str = Field(default='', max_length=80)
    destination: str = Field(min_length=1, max_length=80)
    start_date: date
    start_time: str = '09:00'
    days: int = Field(default=3, ge=1, le=10)
    style: Literal['relaxed', 'balanced', 'compact'] = 'balanced'
    budget: Decimal = Field(default=3000, gt=0, le=10000000)
    people: int = Field(default=2, ge=1, le=50)
    mode: Literal['walking', 'driving'] = 'walking'
    preferences: str = Field(default='', max_length=2000)

    @field_validator('start_time')
    @classmethod
    def time_valid(cls, value):
        datetime.strptime(value, '%H:%M')
        if len(value) != 5:
            raise ValueError('时间应为 HH:MM')
        return value

    @field_validator('destination', 'origin')
    @classmethod
    def strip_text(cls, value):
        return value.strip()

    @model_validator(mode='after')
    def destination_required(self):
        if not self.destination:
            raise ValueError('请输入目的地城市')
        return self


class Stop(StrictModel):
    id: str = ''
    name: str = Field(min_length=1, max_length=120)
    kind: Literal['sight', 'meal', 'hotel', 'break'] = 'sight'
    start: str
    end: str
    description: str = Field(default='', max_length=1200)
    poi_id: str = ''
    address: str = ''
    location: tuple[float, float] | None = None
    matched_name: str = ''
    location_status: Literal['verified', 'unknown'] = 'unknown'
    opening_hours_status: Literal['unknown'] = 'unknown'
    location_note: str = ''

    @field_validator('start', 'end')
    @classmethod
    def times(cls, value):
        datetime.strptime(value, '%H:%M')
        if len(value) != 5:
            raise ValueError('时间应为 HH:MM')
        return value


class Expense(StrictModel):
    label: str = Field(min_length=1, max_length=160)
    category: Literal['ticket', 'food', 'hotel', 'transport', 'other']
    unit_cost: Decimal = Field(ge=0, le=10000000)
    quantity: int = Field(default=1, ge=1, le=1000)
    basis: Literal['person', 'group'] = 'group'
    estimated: Literal[True] = True
    note: str = Field(default='模型估算，未查询实时价格', max_length=500)


class DayPlan(StrictModel):
    date: date
    title: str = Field(max_length=120)
    stops: list[Stop] = Field(min_length=1, max_length=12)
    expenses: list[Expense] = Field(default_factory=list, max_length=20)
    accommodation: str = Field(default='', max_length=500)


class Itinerary(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(max_length=1500)
    days: list[DayPlan] = Field(min_length=1, max_length=10)
    tips: list[str] = Field(default_factory=list, max_length=10)


class RouteLeg(StrictModel):
    day_index: int
    from_id: str
    to_id: str
    mode: Literal['walking', 'driving']
    available: bool = False
    distance_m: int | None = None
    duration_s: int | None = None
    polyline: list[tuple[float, float]] = Field(default_factory=list)
    queried_at: datetime
    note: str = ''


class Issue(StrictModel):
    code: str
    message: str
    day_index: int | None = None
    stop_id: str | None = None


class ValidationReport(StrictModel):
    status: Literal['issues', 'passed_with_unknowns', 'passed']
    errors: list[Issue]
    warnings: list[Issue]
    unknowns: list[Issue]
    total_cents: int
    budget_cents: int
    backend: str = 'python'
    diagnostic: str = ''


class PlanRecord(StrictModel):
    id: str
    parent_id: str | None = None
    revision: int = 1
    created_at: datetime
    request: TripRequest
    itinerary: Itinerary
    routes: list[RouteLeg]
    validation: ValidationReport
    revision_instruction: str = ''
    expense_totals: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode='after')
    def authoritative_cost_categories(self):
        totals = {}
        for day in self.itinerary.days:
            for expense in day.expenses:
                amount = cents(expense.unit_cost) * expense.quantity * (self.request.people if expense.basis == 'person' else 1)
                totals[expense.category] = totals.get(expense.category, 0) + amount
        self.expense_totals = totals
        return self


class RevisionRequest(StrictModel):
    instruction: str = Field(min_length=1, max_length=2000)

    @field_validator('instruction')
    @classmethod
    def real_instruction(cls, value):
        if not value.strip():
            raise ValueError('请输入修改要求')
        return value.strip()


def cents(value: Decimal | str | int):
    return int((Decimal(str(value)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
