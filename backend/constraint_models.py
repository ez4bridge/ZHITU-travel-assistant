"""Compatible preview identities, route choices and atomic apply requests."""
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


class ConstraintModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ConstraintActivity(ConstraintModel):
    stop_id: str = Field(min_length=1, max_length=100)
    status: Literal['planned', 'completed', 'missed', 'cancelled'] = 'planned'
    priority: Literal['must', 'want', 'optional'] = 'want'
    locked: StrictBool = False
    removable_cost: StrictBool = False
    retain_cost: StrictBool = False


class ConstraintDayWindow(ConstraintModel):
    day_index: StrictInt = Field(ge=0, le=9)
    start_min: StrictInt = Field(ge=0, le=1438)
    end_min: StrictInt = Field(ge=1, le=1439)

    @model_validator(mode='after')
    def ordered(self):
        if self.start_min >= self.end_min:
            raise ValueError('当天结束时间须晚于开始时间')
        return self


class ConstraintRouteChoice(ConstraintModel):
    day_index: StrictInt = Field(ge=0, le=9)
    from_id: str = Field(min_length=1, max_length=100)
    to_id: str = Field(min_length=1, max_length=100)
    mode: Literal['walking', 'taxi', 'driving', 'transit', 'bicycling']


class ConstraintIntentRequest(ConstraintModel):
    schema_version: Literal['1.0'] = '1.0'
    base_fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    expected_routing_revision: StrictInt = Field(ge=0)
    expected_state_revision: StrictInt = Field(ge=0)
    day_indexes: list[StrictInt] = Field(min_length=1, max_length=3)
    scope_city: str | None = Field(default=None, min_length=1, max_length=100)
    mode: Literal['requirements', 'during'] = 'requirements'
    as_of: datetime | None = None
    instruction: str = Field(min_length=1, max_length=2000)
    current_day_windows: list[ConstraintDayWindow] = Field(default_factory=list, max_length=3)

    @model_validator(mode='after')
    def coherent(self):
        if self.day_indexes != list(range(self.day_indexes[0], self.day_indexes[-1] + 1)) or self.day_indexes[0] < 0:
            raise ValueError('请选择连续且不重复的1—3天')
        if not self.instruction.strip():
            raise ValueError('请输入具体修改要求')
        if (len({w.day_index for w in self.current_day_windows}) != len(self.current_day_windows)
                or any(w.day_index not in self.day_indexes for w in self.current_day_windows)):
            raise ValueError('当前逐日草稿只能包含所选日期，且同一天不能重复')
        if self.mode == 'during' and self.as_of is None:
            raise ValueError('途中调整须明确确认当前北京时间')
        if self.as_of is not None and self.as_of.utcoffset() != timedelta(hours=8):
            raise ValueError('当前时间须为带+08:00时区的北京时间')
        return self


class ConstraintPreviewRequest(ConstraintModel):
    schema_version: Literal['1.0'] = '1.0'
    base_fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    expected_routing_revision: StrictInt = Field(ge=0)
    expected_state_revision: StrictInt = Field(ge=0)
    day_indexes: list[StrictInt] = Field(min_length=1, max_length=3)
    scope_city: str | None = Field(default=None, min_length=1, max_length=100)
    mode: Literal['requirements', 'during'] = 'requirements'
    as_of: datetime | None = None
    current_stop_id: str | None = Field(default=None, max_length=100)
    budget_cents: StrictInt | None = Field(default=None, gt=0, le=1000000000)
    daily_start_min: StrictInt | None = Field(default=None, ge=0, le=1438)
    daily_end_min: StrictInt | None = Field(default=None, ge=1, le=1439)
    day_windows: list[ConstraintDayWindow] = Field(default_factory=list, max_length=3)
    route_choices: list[ConstraintRouteChoice] = Field(default_factory=list, max_length=30)
    activities: list[ConstraintActivity] = Field(default_factory=list, max_length=120)

    @model_validator(mode='after')
    def coherent(self):
        if self.day_indexes != list(range(self.day_indexes[0], self.day_indexes[-1] + 1)) or self.day_indexes[0] < 0:
            raise ValueError('请选择连续且不重复的1—3天')
        if self.daily_start_min is not None and self.daily_end_min is not None and self.daily_start_min >= self.daily_end_min:
            raise ValueError('每日结束时间须晚于开始时间')
        if len({w.day_index for w in self.day_windows}) != len(self.day_windows) or any(w.day_index not in self.day_indexes for w in self.day_windows):
            raise ValueError('逐日时间只能修改所选日期，且同一天不能重复')
        if len({(r.day_index, r.from_id, r.to_id) for r in self.route_choices}) != len(self.route_choices):
            raise ValueError('同一路段不能重复选择交通方式')
        if any(r.day_index not in self.day_indexes or r.from_id == r.to_id for r in self.route_choices):
            raise ValueError('交通选择只能针对所选日期的不同地点')
        if len({a.stop_id for a in self.activities}) != len(self.activities):
            raise ValueError('同一活动不能重复提交')
        if self.mode == 'during' and self.as_of is None:
            raise ValueError('途中调整须明确确认当前北京时间')
        if self.as_of is not None and self.as_of.utcoffset() != timedelta(hours=8):
            raise ValueError('当前时间须为带+08:00时区的北京时间')
        return self


class ConstraintApplyRequest(ConstraintModel):
    preview_id: str = Field(min_length=1, max_length=100)
