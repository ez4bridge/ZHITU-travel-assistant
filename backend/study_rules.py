"""Study evidence and schedule normalization."""
from .models import Itinerary, RouteLeg, StudyContext, TripRequest


def minutes(value: str) -> int:
    hour, minute = map(int, value.split(':'))
    return hour * 60 + minute


def study_issues(request: TripRequest, itinerary: Itinerary, routes: list[RouteLeg], context: StudyContext | None):
    errors, unknowns = [], []
    if not request.study:
        return errors, unknowns

    def add(code, message, stop=None):
        errors.append({'code': code, 'message': message, 'day_index': 0, 'stop_id': stop.id if stop else None})

    stops = [stop for day in itinerary.days for stop in day.stops if stop.kind == 'sight']
    if len(stops) > request.study.max_stops:
        add('study_stop_limit', '研学安排过于密集，请减少参观地点')
    place_ids = [stop.study.place_id for stop in stops if stop.study]
    if set(request.study.required_place_ids) - set(place_ids):
        add('study_required_place', '尚未保留你指定的必去地点')
    if len(place_ids) != len(set(place_ids)):
        add('study_duplicate_place', '研学安排重复了同一历史地点')
    facts = {fact.id: fact for fact in context.facts} if context else {}
    if not context:
        add('study_missing_evidence', '研学方案缺少可核对的资料快照')
    for day in itinerary.days:
        for stop in day.stops:
            if minutes(stop.end) > minutes(request.study.end_time):
                add('study_end_time', '活动超过本次研学的结束时间', stop)
            if stop.kind != 'sight':
                continue
            if not stop.study or not stop.study.fact_ids or set(stop.study.fact_ids) - set(facts):
                add('study_citation', '研学地点缺少有效的本次资料引用', stop)
                continue
    if request.mode == 'walking':
        known_distance = sum(leg.distance_m or 0 for leg in routes if leg.available)
        if known_distance > request.study.max_walking_m:
            add('study_walking_limit', '已查到的站间步行距离超过你的上限')
        if any(not leg.available or leg.distance_m is None for leg in routes):
            unknowns.append({'code': 'study_walking_unknown', 'message': '部分道路数据缺失，尚不能完整核对站间步行上限', 'day_index': 0, 'stop_id': None})
    return errors, unknowns
