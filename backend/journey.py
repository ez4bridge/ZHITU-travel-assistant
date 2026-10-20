"""Normalize nationwide journeys to integer minutes for both rule engines."""
from datetime import datetime, time

from .models import Itinerary, TripRequest, city_key


def normalize_cities(request: TripRequest, itinerary: Itinerary) -> None:
    cities = {city_key(city): city for city in request.city_route}
    for day in itinerary.days:
        for stop in day.stops:
            if not stop.city and len(cities) == 1:
                stop.city = request.city_route[0]
            canonical = cities.get(city_key(stop.city))
            if canonical:
                stop.city = canonical


def normalized_journey(request: TripRequest, itinerary: Itinerary) -> dict | None:
    if (len(request.city_route) == 1 and not request.include_origin_transport and not itinerary.transfers
            and not any(stay.preferred_days is not None for stay in request.destinations)):
        return None
    midnight = datetime.combine(request.start_date, time())

    def moment(day, clock):
        return int((datetime.combine(day, time.fromisoformat(clock)) - midnight).total_seconds() // 60)

    stops = [{'id': stop.id, 'city': city_key(stop.city or (request.city_route[0] if len(request.city_route) == 1 else '')),
              'start_min': moment(day.date, stop.start), 'end_min': moment(day.date, stop.end), 'day_index': index}
             for index, day in enumerate(itinerary.days) for stop in day.stops]
    # A preference is measured in occupied sightseeing half-days, not hotel nights
    # or raw date counts. Transit, meals and overnight stays do not fill a half-day.
    occupied = {}
    visit_index, previous_city = -1, None
    for index, day in enumerate(itinerary.days):
        for stop in day.stops:
            city = city_key(stop.city or (request.city_route[0] if len(request.city_route) == 1 else ''))
            if city != previous_city:
                visit_index += 1
                previous_city = city
            if stop.kind not in ('sight', 'break'):
                continue
            start, end = time.fromisoformat(stop.start), time.fromisoformat(stop.end)
            if end <= start:
                continue
            blocks = occupied.setdefault((visit_index, city), set())
            if start < time(12):
                blocks.add((index, 'am'))
            if end > time(12):
                blocks.add((index, 'pm'))
    return {
        'cities': [city_key(city) for city in request.journey_cities],
        'destinations': [city_key(city) for city in request.city_route],
        'start_min': moment(request.start_date, request.start_time), 'end_min': request.days * 1440,
        'stops': stops,
        'stays': [{'city': stay.city, 'expected_halves': int(stay.preferred_days * 2),
                   'actual_halves': len(occupied.get((index, city_key(stay.city)), set()))}
                  for index, stay in enumerate(request.destinations) if stay.preferred_days is not None],
        'transfers': [{'from_city': city_key(transfer.from_city), 'to_city': city_key(transfer.to_city),
                       'start_min': moment(transfer.departure_date, transfer.departure_time),
                       'end_min': moment(transfer.arrival_date, transfer.arrival_time),
                       'day_index': max(0, min(request.days - 1, (transfer.departure_date - request.start_date).days))}
                      for transfer in itinerary.transfers],
    }


def validate_journey(journey: dict) -> tuple[list[dict], list[dict], list[dict]]:
    # Imported lazily to keep normalization independent of engine initialization.
    from .validation import issue

    errors, warnings, unknowns = [], [], []
    transfers = journey['transfers']
    expected = list(zip(journey['cities'], journey['cities'][1:]))
    actual = [(transfer['from_city'], transfer['to_city']) for transfer in transfers]
    if actual != expected:
        errors.append(issue('intercity_route', '跨城交通必须按顺序连接出发地、全部目的地及所选返程'))
    visited = []
    for stop in journey['stops']:
        if not visited or visited[-1] != stop['city']:
            visited.append(stop['city'])
    if visited != journey['destinations']:
        errors.append(issue('city_sequence', '每日活动必须按目的地顺序覆盖全部城市，且注明所属城市'))
    for stay in journey.get('stays', []):
        expected_halves, actual_halves = stay['expected_halves'], stay['actual_halves']
        if expected_halves != actual_halves:
            def days_text(halves):
                return str(halves // 2) + ('.5' if halves % 2 else '')
            warnings.append(issue('stay_preference', f'{stay["city"]}：期望游玩 {days_text(expected_halves)} 天，当前约 {days_text(actual_halves)} 天，请调整城市时长或总天数'))
    previous_end = journey['start_min']
    for transfer in transfers:
        index = transfer['day_index']
        if transfer['end_min'] <= transfer['start_min']:
            errors.append(issue('intercity_duration', '跨城交通到达时间必须晚于出发时间', index))
        if transfer['start_min'] < journey['start_min'] or transfer['end_min'] >= journey['end_min']:
            errors.append(issue('intercity_dates', '跨城交通必须安排在所选旅行日期与首日开始时间内', index))
        if transfer['start_min'] < previous_end:
            errors.append(issue('intercity_overlap', '跨城交通时间重叠或早于上一段到达时间', index))
        previous_end = transfer['end_min']
    for stop in journey['stops']:
        city = journey['cities'][0]
        overlap = False
        for transfer in transfers:
            if transfer['start_min'] < stop['end_min'] and transfer['end_min'] > stop['start_min']:
                overlap = True
            if transfer['end_min'] <= stop['start_min']:
                city = transfer['to_city']
        if overlap:
            errors.append(issue('intercity_activity_overlap', '活动时间与跨城交通重叠，请预留进出站或机场接驳时间', stop['day_index'], stop['id']))
        elif city != stop['city']:
            errors.append(issue('city_arrival', '活动所在城市与交通到达安排不一致', stop['day_index'], stop['id']))
    if expected or transfers:
        unknowns.append(issue('intercity_estimates', '跨城车票未预订；票价、余票和进出站接驳请在出行前确认'))
    return errors, warnings, unknowns
