from datetime import date, datetime
from collections.abc import Iterator
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING
from typing import Literal
import re
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

Mode = Literal['walking', 'driving', 'transit', 'bicycling', 'taxi']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class DestinationStay(StrictModel):
    city: str = Field(min_length=1, max_length=40)
    preferred_days: Decimal | None = Field(default=None, ge=Decimal('0.5'), le=10, multiple_of=Decimal('0.5'))

    @field_validator('city')
    @classmethod
    def single_city(cls, value: str) -> str:
        value = value.strip()
        if not value or split_cities(value) != [value]:
            raise ValueError('每一项请填写一个城市')
        return value


def split_cities(value: str) -> list[str]:
    return [city.strip() for city in re.split(r'\s*(?:→|->|、|，|,|；|;|\n)\s*', value) if city.strip()]




class GuideSource(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    url: str = Field(default='', max_length=1000)
    text: str = Field(min_length=1, max_length=6000)
    published_date: date | None = None

    @field_validator('title', 'text')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('攻略标题和正文不能为空')
        return value.strip()

    @field_validator('url')
    @classmethod
    def public_link(cls, value):
        from urllib.parse import urlsplit
        if value and (urlsplit(value).scheme not in ('https', 'http') or not urlsplit(value).hostname):
            raise ValueError('来源链接应为 http 或 https 网页')
        return value


class TripRequest(StrictModel):
    origin: str = Field(default='', max_length=80)
    destination: str = Field(default='', max_length=240)
    destinations: list[DestinationStay] = Field(default_factory=list, max_length=5)
    start_date: date
    start_time: str = '09:00'
    days: int = Field(default=3, ge=1, le=10)
    style: Literal['relaxed', 'balanced', 'compact'] = 'balanced'
    budget: Decimal = Field(default=Decimal('3000'), gt=0, le=10000000)
    people: int = Field(default=2, ge=1, le=50)
    mode: Literal['auto', 'walking', 'driving', 'transit', 'bicycling', 'taxi'] = 'auto'
    intercity_mode: Literal['train', 'flight', 'bus', 'driving'] = 'train'
    include_origin_transport: bool = False
    return_to_origin: bool = False
    preferences: str = Field(default='', max_length=2000)
    guides: list[GuideSource] = Field(default_factory=list, max_length=3)
    compare_modes: bool = True
    confirmed_hotel: str = Field(default='', max_length=120)
    guide_mode: Literal['prefer', 'inspiration'] = 'prefer'

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
    def destination_required(self, info: ValidationInfo):
        if self.destinations:
            names = [stop.city for stop in self.destinations]
            if self.destination and [city_key(c) for c in split_cities(self.destination)] != [city_key(c) for c in names]:
                raise ValueError('目的地文本与城市列表顺序不一致')
            self.destination = '→'.join(names)
        if not self.destination:
            raise ValueError('请输入目的地城市')
        cities = self.city_route
        # Previously saved six-city records remain readable; new requests allow five.
        limit = 6 if info.context and info.context.get('legacy_record') else 5
        if not cities or len(cities) > limit or any(len(city) > 40 for city in cities):
            raise ValueError('请按顺序填写 1–5 个城市，每个城市不超过 40 字')
        if any(city_key(a) == city_key(b) for a, b in zip(cities, cities[1:])):
            raise ValueError('相邻目的地不能是同一个城市')
        # New forms specify every city's duration; explicit totals in saved/legacy
        # requests keep their original meaning, including automatic city allocation.
        if 'days' not in self.model_fields_set and self.destinations and all(
                stay.preferred_days is not None for stay in self.destinations):
            total = sum((stay.preferred_days for stay in self.destinations), Decimal(0))
            derived_days = int(total.to_integral_value(rounding=ROUND_CEILING))
            if not 1 <= derived_days <= 10:
                raise ValueError('各站游玩天数合计最多 10 天，请调整各城市时长')
            self.days = derived_days
        if self.return_to_origin:
            if not self.origin:
                raise ValueError('返回出发地需要填写出发城市')
            self.include_origin_transport = True
        if self.include_origin_transport and not self.origin:
            raise ValueError('安排出发交通需要填写出发城市')
        return self

    @property
    def city_route(self) -> list[str]:
        return [stop.city for stop in self.destinations] if self.destinations else split_cities(self.destination)

    @property
    def journey_cities(self) -> list[str]:
        cities = list(self.city_route)
        if self.include_origin_transport and self.origin:
            cities.insert(0, self.origin)
        if self.return_to_origin:
            cities.append(self.origin)
        return [city for i, city in enumerate(cities) if not i or city_key(city) != city_key(cities[i - 1])]


def city_key(city: str) -> str:
    return city.strip().removesuffix('市')


class Stop(StrictModel):
    id: str = ''
    name: str = Field(min_length=1, max_length=120)
    city: str = Field(default='', max_length=40)
    kind: Literal['sight', 'meal', 'hotel', 'break'] = 'sight'
    start: str
    end: str
    description: str = Field(default='', max_length=1200)
    poi_id: str = ''
    address: str = ''
    location: tuple[float, float] | None = None
    matched_name: str = ''
    location_status: Literal['verified', 'unknown'] = 'unknown'
    opening_hours_status: Literal['unknown', 'map_reference'] = 'unknown'
    opening_hours: str = Field(default='', max_length=1200)
    opening_hours_today: str = Field(default='', max_length=500)
    opening_hours_scope: Literal['place', 'area'] = 'place'
    information_source: str = ''
    information_queried_at: datetime | None = None
    average_cost_yuan: Decimal | None = Field(default=None, ge=0, le=10000000)
    area_name: str = Field(default='', max_length=120)
    area_poi_id: str = ''
    anchor_note: str = Field(default='', max_length=500)
    food_recommendations: list[str] = Field(default_factory=list, max_length=6)
    food_reference: str = Field(default='', max_length=500)
    meal_period: Literal['', 'breakfast', 'lunch', 'dinner'] = ''
    location_note: str = ''
    place_name: str = Field(default='', max_length=120)
    location_behavior: Literal['poi', 'area', 'same_as_previous', 'unlocated'] = 'poi'

    @model_validator(mode='after')
    def stationary_is_break(self):
        if self.location_behavior == 'same_as_previous' and self.kind != 'break':
            raise ValueError('仅原地休息可继承前一地点；其他活动需指定真实地点')
        return self

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
    stop_id: str = ''
    note: str = Field(default='模型估算，未查询实时价格', max_length=500)


class DayPlan(StrictModel):
    date: date
    title: str = Field(max_length=120)
    stops: list[Stop] = Field(max_length=12)
    expenses: list[Expense] = Field(default_factory=list, max_length=20)
    accommodation: str = Field(default='', max_length=500)
    meal_note: str = Field(default='', max_length=1000)


class CityTransfer(StrictModel):
    from_city: str = Field(min_length=1, max_length=40)
    to_city: str = Field(min_length=1, max_length=40)
    departure_date: date
    departure_time: str
    arrival_date: date
    arrival_time: str
    mode: Literal['train', 'flight', 'bus', 'driving'] = 'train'
    unit_cost: Decimal = Field(ge=0, le=10000000)
    basis: Literal['person', 'group'] = 'person'
    estimated: Literal[True] = True
    note: str = Field(default='交通时间与费用为估算，班次、余票和票价需自行确认', max_length=500)
    rail_option_id: str = Field(default='', max_length=80)
    schedule_status: Literal['estimated', 'queried'] = 'estimated'
    price_status: Literal['estimated', 'reference'] = 'estimated'
    train_code: str = Field(default='', max_length=20)
    departure_station: str = Field(default='', max_length=60)
    arrival_station: str = Field(default='', max_length=60)
    seat_type: str = Field(default='', max_length=30)
    seat_availability: str = Field(default='', max_length=30)
    queried_at: datetime | None = None
    source: str = Field(default='', max_length=100)
    query_note: str = Field(default='', max_length=500)

    @field_validator('unit_cost')
    @classmethod
    def rounded_price(cls, value: Decimal) -> Decimal:
        return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @field_validator('departure_time', 'arrival_time')
    @classmethod
    def time_valid(cls, value):
        datetime.strptime(value, '%H:%M')
        if len(value) != 5:
            raise ValueError('时间应为 HH:MM')
        return value


class GuideInsight(StrictModel):
    source_index: int = Field(ge=0, le=2)
    text: str = Field(min_length=1, max_length=600)


class GuideDecision(StrictModel):
    source_index: int = Field(ge=0, le=2)
    excerpt: str = Field(min_length=1, max_length=300)
    place_names: list[str] = Field(default_factory=list, max_length=8)
    status: Literal['adopted', 'adjusted', 'not_used'] = 'not_used'
    stop_ids: list[str] = Field(default_factory=list, max_length=12)
    reason: str = Field(min_length=1, max_length=600)


class Itinerary(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(max_length=1500)
    days: list[DayPlan] = Field(min_length=1, max_length=10)
    tips: list[str] = Field(default_factory=list, max_length=10)
    transfers: list[CityTransfer] = Field(default_factory=list, max_length=14)
    guide_insights: list[GuideInsight] = Field(default_factory=list, max_length=12)
    guide_decisions: list[GuideDecision] = Field(default_factory=list, max_length=30)

def itinerary_expenses(itinerary: Itinerary) -> Iterator[Expense]:
    for day in itinerary.days:
        yield from day.expenses
    for transfer in itinerary.transfers:
        yield Expense(label=f'{transfer.from_city} → {transfer.to_city}', category='transport',
                      unit_cost=transfer.unit_cost, basis=transfer.basis, note=transfer.note)


class TransportCost(StrictModel):
    low: Decimal = Field(ge=0)
    high: Decimal = Field(ge=0)
    basis: Literal['person', 'vehicle']
    method: str
    assumptions: str
    source_url: str = ''


class RouteLeg(StrictModel):
    day_index: int
    from_id: str
    to_id: str
    mode: Mode
    scope: Literal['local', 'intercity'] = 'local'
    available: bool = False
    distance_m: int | None = None
    duration_s: int | None = None
    polyline: list[tuple[float, float]] = Field(default_factory=list)
    queried_at: datetime
    note: str = ''
    failure_code: str = ''
    stationary: bool = False
    fare_yuan: Decimal | None = Field(default=None, ge=0)
    instructions: list[str] = Field(default_factory=list)
    alternatives: list['RouteOption'] = Field(default_factory=list)
    provider_endpoint: str = ''
    taxi_cost_yuan: Decimal | None = Field(default=None, ge=0)
    tolls_yuan: Decimal | None = Field(default=None, ge=0)
    cost_estimate: TransportCost | None = None
    user_selected: bool = False
    selection_reason: str = ''


class RouteOption(StrictModel):
    mode: Mode
    available: bool = False
    distance_m: int | None = None
    duration_s: int | None = None
    polyline: list[tuple[float, float]] = Field(default_factory=list)
    fare_yuan: Decimal | None = Field(default=None, ge=0)
    instructions: list[str] = Field(default_factory=list)
    note: str = ''
    failure_code: str = ''
    provider_endpoint: str = ''
    taxi_cost_yuan: Decimal | None = Field(default=None, ge=0)
    tolls_yuan: Decimal | None = Field(default=None, ge=0)
    cost_estimate: TransportCost | None = None


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


class CostLine(StrictModel):
    label: str
    category: str
    unit_cost: Decimal
    quantity: int
    basis: Literal['person', 'group']
    people_multiplier: int
    subtotal_cents: int
    day_index: int | None = None
    stop_id: str = ''
    note: str = ''


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
    cost_breakdown: list[CostLine] = Field(default_factory=list)
    adjustments: list[str] = Field(default_factory=list)
    routing_revision: int = 0
    schedule_baseline: dict[str, tuple[str, str]] = Field(default_factory=dict)

    @model_validator(mode='after')
    def authoritative_cost_categories(self):
        totals = {}
        self.cost_breakdown = []
        daily = [(index, e) for index, day in enumerate(self.itinerary.days) for e in day.expenses]
        transfers = list(itinerary_expenses(self.itinerary))[len(daily):]
        for day_index, expense in [*daily, *((None, e) for e in transfers)]:
            amount = cents(expense.unit_cost) * expense.quantity * (self.request.people if expense.basis == 'person' else 1)
            totals[expense.category] = totals.get(expense.category, 0) + amount
            self.cost_breakdown.append(CostLine(label=expense.label, category=expense.category,
                unit_cost=Decimal(cents(expense.unit_cost)) / 100, quantity=expense.quantity, basis=expense.basis,
                people_multiplier=self.request.people if expense.basis == 'person' else 1,
                subtotal_cents=amount, day_index=day_index, stop_id=expense.stop_id, note=expense.note))
        self.expense_totals = totals
        return self


class RevisionRequest(StrictModel):
    instruction: str = Field(min_length=1, max_length=2000)
    day_index: int | None = Field(default=None, ge=0, le=9)
    trip: TripRequest | None = None

    @field_validator('instruction')
    @classmethod
    def real_instruction(cls, value):
        if not value.strip():
            raise ValueError('请输入修改要求')
        return value.strip()


class RouteSelection(StrictModel):
    day_index: int = Field(ge=0, le=9)
    from_id: str
    to_id: str
    mode: Mode
    expected_routing_revision: int | None = Field(default=None, ge=0)


def cents(value: Decimal | str | int):
    return int((Decimal(str(value)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
