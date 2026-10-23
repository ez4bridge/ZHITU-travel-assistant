"""Recover flexible booking suggestions using day-scoped activity identities.

The validator identifies a missing edge by its destination. That destination may
be an immovable station: in that case the removable suggestion is its predecessor.
No display-name match is allowed to delete another day's activity.
"""
import re

from .models import city_key
from .validation import minutes


def protected_activity_ids(itinerary, state):
    protected = {sid for sid, fact in state.get('activities', {}).items()
                 if fact.get('locked') or fact.get('status') == 'completed'}
    protected.update(s.id for day in itinerary.days for s in day.stops
                     if s.booking_anchor or s.booking_ref or s.kind == 'hotel')
    protected.update(e.stop_id for day in itinerary.days for e in day.expenses
                     if e.stop_id and (e.commitment != 'planned' or e.booking_ref))
    counts = {}
    for day in itinerary.days:
        for stop in day.stops:
            counts[stop.id] = counts.get(stop.id, 0) + 1
    protected.update(sid for sid, count in counts.items() if not sid or count != 1)
    return protected


def recovery_target(plan, state, affected, errors, schedule_conflicts=(), meal_conflicts=()):
    """Return one (day, id, reason), then require the caller to requery the graph."""
    protected = protected_activity_ids(plan.itinerary, state)

    def choose(index, stop, reason):
        if index in affected and stop is not None and stop.id not in protected:
            return index, stop.id, reason
        return None

    # Location and edge evidence is stronger than downstream time symptoms.
    for code in ('missing_location', 'missing_route'):
        for error in errors:
            index = error.day_index
            if error.code != code or index not in affected:
                continue
            stops = plan.itinerary.days[index].stops
            pos = next((i for i, s in enumerate(stops) if s.id == error.stop_id), None)
            if pos is None:
                continue
            if code == 'missing_route' and pos and any(
                    stop.booking_anchor and (not stop.location or stop.location_status != 'verified')
                    for stop in (stops[pos - 1], stops[pos])):
                # Removing a perfectly valid restaurant cannot resolve an
                # unknown user hotel/station coordinate. Retain the suggestion
                # and flag that edge as unknown instead of emptying the day.
                continue
            target = choose(index, stops[pos], '地点未能唯一定位' if code == 'missing_location' else '相邻路线未查到')
            if target:
                return target
            if code == 'missing_route' and pos:
                target = choose(index, stops[pos - 1], '无法核实前往固定地点的路线')
                if target:
                    return target

    # Schedule messages are scoped to an explicit date. Resolve their named
    # activity only on that day; if it is fixed, trim a preceding suggestion.
    for message in schedule_conflicts:
        found = re.match(r'第(\d+)天', message)
        if not found or int(found.group(1)) - 1 not in affected:
            continue
        index = int(found.group(1)) - 1
        stops = plan.itinerary.days[index].stops
        names = re.findall(r'「(.+?)」', message)
        positions = [i for i, stop in enumerate(stops) if stop.name in names]
        if len(positions) == 1:
            pos = positions[0]
            target = choose(index, stops[pos], '与固定交通或当天可用时间冲突')
            if target:
                return target
            stops = stops[:pos]
        elif names:
            continue  # ambiguous names never guess
        for stop in reversed(stops):
            target = choose(index, stop, '当天可用时间不足')
            if target:
                return target

    for index, message in meal_conflicts:
        stops = plan.itinerary.days[index].stops
        names = re.findall(r'「(.+?)」', message)
        meals = [s for s in stops if s.kind == 'meal' and
                 (s.name in names if names else any(label in message and s.meal_period == key
                    for key, label in [('breakfast', '早餐'), ('lunch', '午餐'), ('dinner', '晚餐')]))]
        for stop in meals:
            target = choose(index, stop, '用餐时段或餐馆营业参考不合适')
            if target:
                return target

    for error in errors:
        index = error.day_index
        if index not in affected or error.code not in (
                'invalid_duration', 'before_start', 'time_overlap', 'transfer_time',
                'booking_arrival_buffer', 'booking_departure_buffer'):
            continue
        stops = plan.itinerary.days[index].stops
        pos = next((i for i, s in enumerate(stops) if s.id == error.stop_id), None)
        if pos is not None:
            for stop in reversed(stops[:pos + 1]):
                target = choose(index, stop, '与真实路线或固定交通的时间冲突')
                if target:
                    return target
    return None


def preserved_fact_conflicts(original, candidate, state, affected):
    """Run after *every* repair, including the final one before saving."""
    before = {s.id: s for d in original.itinerary.days for s in d.stops}
    after = {s.id: s for d in candidate.itinerary.days for s in d.stops}
    conflicts = []
    for sid, fact in state.get('activities', {}).items():
        if (fact.get('status') == 'completed' or fact.get('locked')) and sid in before and after.get(sid) != before[sid]:
            conflicts.append(f'「{before[sid].name}」已有完成／锁定事实；本次补录影响该项，请只修改未开始的日期')
    for window in state.get('day_windows', []):
        index = window['day_index']
        if index not in affected:
            continue
        for stop in candidate.itinerary.days[index].stops:
            if window.get('scope_city') and city_key(stop.city) != city_key(window['scope_city']):
                continue
            if minutes(stop.start) < window['start_min'] or minutes(stop.end) > window['end_min']:
                conflicts.append(f'第{index + 1}天「{stop.name}」超出已确认的每日活动时段，请先调整时段')
    return conflicts


def fixed_unknown_errors(plan, state):
    """Keep unknown fixed endpoints visible in validation without inventing roads."""
    protected = protected_activity_ids(plan.itinerary, state)
    result = set()
    for error in plan.validation.errors:
        if error.day_index is None:
            continue
        stops = plan.itinerary.days[error.day_index].stops
        pos = next((i for i, s in enumerate(stops) if s.id == error.stop_id), None)
        if pos is None:
            continue
        current = stops[pos]
        if error.code == 'missing_location' and current.booking_anchor:
            result.add((error.code, error.day_index, error.stop_id))
        elif error.code == 'missing_route' and pos and any(
                stop.booking_anchor and (not stop.location or stop.location_status != 'verified')
                for stop in (stops[pos - 1], current)):
            result.add((error.code, error.day_index, error.stop_id))
        elif error.code == 'missing_route' and pos and current.id in protected and stops[pos - 1].id in protected:
            # At least one user booking anchor is required: an ordinary protected
            # activity is never silently declared feasible by this exception.
            if current.booking_anchor or stops[pos - 1].booking_anchor:
                result.add((error.code, error.day_index, error.stop_id))
    return result
