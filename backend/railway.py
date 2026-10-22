"""Request-local railway options, with provider facts kept separate from model estimates."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4
import asyncio
from copy import deepcopy
from time import monotonic

from .config import settings
from .models import TripRequest, Itinerary, CityTransfer, city_key
from .rail_mcp import RailMcpClient

# Current railway queries use China's UTC+8 clock without requiring Windows tzdata.
CHINA_TIMEZONE = timezone(timedelta(hours=8))
SOURCE = '12306 / drfccv MCP'
PUBLIC_SNAPSHOTS = {}
SNAPSHOT_TTL_SECONDS = 300
SEATS = [('second_class', '二等座'), ('hard_seat', '硬座'), ('first_class', '一等座'),
         ('soft_seat', '软座'), ('hard_sleeper', '硬卧'), ('soft_sleeper', '软卧'),
         ('business', '商务座'), ('no_seat', '无座')]


def sample_options(options, preferred_time='00:00', limit=8):
    """Small, deterministic evidence covering the day, rather than its first trains."""
    if len(options) <= limit:
        return list(options)
    chosen = []
    def add(option):
        if option not in chosen and len(chosen) < limit:
            chosen.append(option)
    def minute(value):
        hour, minute = map(int, value.split(':'))
        return hour * 60 + minute
    add(options[0])
    add(options[-1])
    add(min(options, key=lambda o: abs(minute(o['departure_time']) - minute(preferred_time))))
    add(min(options, key=lambda o: o['duration_minutes']))
    priced = [o for o in options if o.get('reference_price') is not None]
    if priced:
        add(min(priced, key=lambda o: Decimal(o['reference_price'])))
    for i in range(limit):
        add(options[round(i * (len(options) - 1) / (limit - 1))])
    return sorted(chosen, key=lambda o: o['departure_time'])


def reference_price(value) -> str | None:
    try:
        amount = Decimal(str(value))
        if amount.is_finite() and 0 <= amount <= 10000000:
            return str(amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        pass
    return None


def train_options(tickets: list, prices: list, stations_from: list, stations_to: list,
                  travel_date: date, people: int, from_city: str, to_city: str) -> list[dict]:
    from_codes = {station['code']: station['name'] for station in stations_from}
    to_codes = {station['code']: station['name'] for station in stations_to}
    price_index = {(row.get('train_code'), row.get('from_station'), row.get('to_station'), row.get('start_time'), row.get('arrive_time')): row
                   for row in prices[:2000] if isinstance(row, dict)}
    options = []
    observed = datetime.now(timezone.utc).isoformat()
    for ticket in tickets[:2000]:
        if not isinstance(ticket, dict):
            continue
        start_station = from_codes.get(ticket.get('from_station_code'))
        end_station = to_codes.get(ticket.get('to_station_code'))
        if not start_station or not end_station:
            continue
        try:
            start = datetime.combine(travel_date, time.fromisoformat(ticket['start_time']))
            hours, minutes = map(int, ticket['duration'].split(':'))
            duration = hours * 60 + minutes
            if not 0 <= minutes < 60 or not 0 < duration <= 14400:
                continue
            arrival = start + timedelta(minutes=duration)
            if arrival.strftime('%H:%M') != ticket['arrive_time']:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        code = str(ticket.get('train_no', ''))
        if not code or len(code) > 20:
            continue
        quoted = price_index.get((code, start_station, end_station, ticket['start_time'], ticket['arrive_time']), {}).get('prices', {})
        quoted = quoted if isinstance(quoted, dict) else {}
        seats = ticket.get('seats', {})
        if not isinstance(seats, dict):
            continue
        available = [(seat, str(seats.get(key, ''))) for key, seat in SEATS
                     if str(seats.get(key, '')) == '有' or (str(seats.get(key, '')).isdigit() and int(seats[key]) >= people)]
        if not available:
            continue
        seat, count = next(((seat, count) for seat, count in available if reference_price(quoted.get(seat)) is not None), available[0])
        options.append({'option_id': str(uuid4()), 'from_city': from_city, 'to_city': to_city,
                        'train_code': code, 'departure_station': start_station, 'arrival_station': end_station,
                        'departure_date': start.date().isoformat(), 'departure_time': start.strftime('%H:%M'),
                        'arrival_date': arrival.date().isoformat(), 'arrival_time': arrival.strftime('%H:%M'),
                        'duration_minutes': duration, 'seat_type': seat, 'seat_availability': count,
                        'reference_price': reference_price(quoted.get(seat)), 'queried_at': observed,
                        'source': SOURCE})
    return sorted(options, key=lambda option: (option['departure_time'], option['duration_minutes']))


class Railway:
    def __init__(self, request: TripRequest, emit=None, share_snapshots=False) -> None:
        self.request = request
        self.emit = emit
        self.enabled = settings.rail_mcp_enabled and settings.rail_mcp_python.is_file()
        self.client = RailMcpClient(settings.rail_mcp_python)
        self.station_cache = {}
        self.query_cache = {}
        self.options = {}
        self._query_lock = asyncio.Lock()
        self.prepared_transfers = None
        self.share_snapshots = share_snapshots

    async def close(self) -> None:
        await self.client.close()

    def require_verified(self, itinerary):
        if self.enabled and any(t.mode == 'train' and t.schedule_status != 'queried' for t in itinerary.transfers):
            raise ValueError('铁路时刻尚未核实，不能将模型估计时刻作为可执行车次发布；旧估算方案请使用整体重新规划，单日修改不能锁定未核实的跨城交通')

    async def prepare_plan(self):
        """Own the required transport edges instead of asking a model to remember them.

        Dates follow requested destination stays. Every time, station and price is
        taken from the request-local provider cache, never a synthetic placeholder.
        """
        if self.prepared_transfers is not None:
            return [t.model_copy(deep=True) for t in self.prepared_transfers]
        if not self.enabled or self.request.intercity_mode != 'train':
            return None
        trip = self.request
        cities = trip.journey_cities
        if len(cities) < 2:
            self.prepared_transfers = []
            return []
        durations = [stay.preferred_days for stay in trip.destinations]
        if not durations:
            durations = [None] * len(trip.city_route)
        known = sum((d for d in durations if d is not None), Decimal(0))
        automatic = max(Decimal('0.5'), (Decimal(trip.days) - known) / max(1, durations.count(None)))
        durations = [d if d is not None else automatic for d in durations]
        edges = []
        if trip.include_origin_transport:
            edges.append((trip.origin, trip.city_route[0], 0, trip.start_time))
        elapsed = Decimal(0)
        for index, (origin, destination) in enumerate(zip(trip.city_route, trip.city_route[1:])):
            elapsed += durations[index]
            edges.append((origin, destination, min(trip.days - 1, int(elapsed)), '08:00'))
        if trip.return_to_origin:
            edges.append((trip.city_route[-1], trip.origin, trip.days - 1, '17:00'))
        prepared = []
        previous_end = datetime.combine(trip.start_date, time.fromisoformat(trip.start_time))
        trip_end = datetime.combine(trip.start_date + timedelta(days=trip.days), time())
        for origin, destination, offset, preferred_time in edges:
            day = trip.start_date + timedelta(days=offset)
            result = await self.query(origin, destination, day.isoformat(), preferred_time)
            key = (city_key(origin), city_key(destination), day.isoformat())
            if result.get('retryable'):
                if self.emit:
                    await self.emit('status', {'message': f'{origin}→{destination}铁路查询暂未完成，保留其他段结果，仅重查本段一次'})
                self.query_cache.pop(key, None)
                result = await self.query(origin, destination, day.isoformat(), preferred_time)
            rows = self.query_cache.get(key, result)['options']
            candidates = [o for o in rows
                if datetime.fromisoformat(o['departure_date'] + 'T' + o['departure_time']) >= previous_end
                and datetime.fromisoformat(o['arrival_date'] + 'T' + o['arrival_time']) < trip_end
                ]
            if not candidates:
                raise ValueError(f'{origin}→{destination}（{day}）尚未取得可衔接的铁路选项：' + result['message'])
            allowance = trip.budget / max(1, trip.people * len(edges))
            preferred = datetime.combine(day, time.fromisoformat(preferred_time))
            def rank(option):
                departure = datetime.fromisoformat(option['departure_date'] + 'T' + option['departure_time'])
                return (option['seat_type'] == '无座', option.get('reference_price') is None,
                        option.get('reference_price') is not None and Decimal(option['reference_price']) > allowance,
                        option['arrival_date'] != option['departure_date'],
                        abs((departure - preferred).total_seconds()) / 60 + option['duration_minutes'] / 2,
                        Decimal(option['reference_price']) if option.get('reference_price') is not None else allowance)
            option = min(candidates, key=rank)
            transfer = CityTransfer(from_city=origin, to_city=destination,
                departure_date=day, arrival_date=day, departure_time='00:00', arrival_time='00:01', unit_cost=allowance)
            self.apply_verified_option(transfer, option)
            prepared.append(transfer)
            previous_end = datetime.fromisoformat(option['arrival_date'] + 'T' + option['arrival_time']) + timedelta(minutes=45)
        self.prepared_transfers = prepared
        if self.emit:
            await self.emit('status', {'message': f'已由实际查询建立完整的{len(prepared)}段跨城交通，正在按真实到离时间安排各城市活动'})
        return [t.model_copy(deep=True) for t in prepared]

    async def stations(self, city: str) -> list:
        key = city_key(city)
        if key not in self.station_cache:
            data = await self.client.call('search-stations', {'query': key, 'limit': 50})
            if not data.get('success'):
                raise RuntimeError('未找到该城市的铁路车站')
            self.station_cache[key] = [station for station in data.get('stations', [])
                                       if city_key(str(station.get('city') or '')) == key]
        if not self.station_cache[key]:
            raise RuntimeError('无法核验车站所属城市，保留估算安排')
        return self.station_cache[key]

    async def query(self, from_city: str, to_city: str, travel_date: str, earliest_time: str = '00:00') -> dict:
        # Do not expose a pending cache entry as an empty result to another tool call.
        async with self._query_lock:
            return await self._query(from_city, to_city, travel_date, earliest_time)

    async def _query(self, from_city: str, to_city: str, travel_date: str, earliest_time: str = '00:00') -> dict:
        cities = self.request.journey_cities
        edges = {(city_key(a), city_key(b)) for a, b in zip(cities, cities[1:])}
        if (city_key(from_city), city_key(to_city)) not in edges:
            raise ValueError('铁路查询必须属于本次城市顺序中的相邻跨城段')
        selected_date = date.fromisoformat(travel_date)
        earliest = time.fromisoformat(earliest_time)
        today = datetime.now(CHINA_TIMEZONE).date()
        if not today <= selected_date <= today + timedelta(days=14):
            return {'status': 'unavailable', 'message': '日期不在当前可查询预售范围内，交通仍为估算', 'options': []}
        if not self.request.start_date <= selected_date < self.request.start_date + timedelta(days=self.request.days):
            raise ValueError('铁路查询日期必须在本次旅行日期内')
        if not self.enabled:
            return {'status': 'unavailable', 'message': '铁路查询未启用，交通仍为估算', 'options': []}
        key = (city_key(from_city), city_key(to_city), travel_date)
        snapshot_key = (*key, self.request.people)
        snapshot = PUBLIC_SNAPSHOTS.get(snapshot_key) if self.share_snapshots else None
        if key not in self.query_cache and snapshot and monotonic() - snapshot[0] <= SNAPSHOT_TTL_SECONDS:
            cached = deepcopy(snapshot[1])
            cached['snapshot_age_s'] = round(monotonic() - snapshot[0])
            for option in cached['options']:
                option['option_id'] = str(uuid4())  # Provider facts may be shared; selection IDs may not.
                self.options[option['option_id']] = option
            self.query_cache[key] = cached
            if self.emit:
                await self.emit('tool', {'name': '12306 车次与参考票价',
                    'arguments': {'from_city': from_city, 'to_city': to_city, 'date': travel_date},
                    'summary': f'复用{cached["snapshot_age_s"]}秒前的公开车次席位快照，保留原查询时间；购票前仍需复核'})
        if key not in self.query_cache:
            if len(self.query_cache) >= 6:
                return {'status': 'unavailable', 'message': '本次铁路查询次数已达上限，未查询部分保留估算', 'options': []}
            self.query_cache[key] = {'status': 'unavailable', 'message': '铁路查询暂不可用，交通仍为估算', 'options': []}
            try:
                origin, destination = await self.stations(from_city), await self.stations(to_city)
                origin_station = next((station for station in origin if city_key(station['name']) == city_key(from_city)), origin[0])
                destination_station = next((station for station in destination if city_key(station['name']) == city_key(to_city)), destination[0])
                args = {'from_station': origin_station['code'], 'to_station': destination_station['code'], 'train_date': travel_date}
                ticket_data = await self.client.call('query-tickets', args)
                if not ticket_data.get('success'):
                    raise RuntimeError('铁路接口查询失败，尚不能判断是否有车或有票')
                try:
                    prices = await self.client.call('query-ticket-price', args)
                except Exception:
                    prices = {}
                rows = train_options(ticket_data.get('trains', []), prices.get('data', []) if prices.get('success') else [],
                                     origin, destination, selected_date, self.request.people, from_city, to_city)
                if rows and not any(option['reference_price'] is not None for option in rows):
                    if self.emit:
                        await self.emit('status', {'message': f'{from_city}→{to_city}时刻与席位已取得，正在仅补查一次参考票价'})
                    try:
                        retry_prices = await self.client.call('query-ticket-price', args)
                        if retry_prices.get('success'):
                            rows = train_options(ticket_data.get('trains', []), retry_prices.get('data', []),
                                origin, destination, selected_date, self.request.people, from_city, to_city)
                    except Exception:
                        pass
                for option in rows:
                    self.options[option['option_id']] = option
                raw_count = len(ticket_data.get('trains', []))
                self.query_cache[key] = {'status': 'queried', 'reason': 'available' if rows else 'no_matching_seats' if raw_count else 'no_direct_trains',
                    'raw_train_count': raw_count, 'available_option_count': len(rows),
                    'message': ('时刻和席位为查询时快照；参考票价非锁价，不含站外接驳' if rows else
                        '本次接口返回车次，但未找到满足同行人数且车站、时刻均可核验的席位' if raw_count else '本次接口未返回直达车次，不代表其他日期或中转无票'), 'options': rows}
                if rows and self.share_snapshots:
                    if len(PUBLIC_SNAPSHOTS) >= 64:
                        PUBLIC_SNAPSHOTS.pop(min(PUBLIC_SNAPSHOTS, key=lambda k: PUBLIC_SNAPSHOTS[k][0]))
                    PUBLIC_SNAPSHOTS[snapshot_key] = (monotonic(), deepcopy(self.query_cache[key]))
            except Exception as error:
                detail = settings.safe_error(error)[:160] or ('接口连接超时' if isinstance(error, TimeoutError) else type(error).__name__)
                self.query_cache[key]['message'] = '铁路查询未完成，尚不能判断有无车票：' + detail
                self.query_cache[key]['retryable'] = True
            if self.emit:
                count = len(self.query_cache[key]['options'])
                await self.emit('tool', {'name': '12306 车次与参考票价', 'arguments': {'from_city': from_city, 'to_city': to_city, 'date': travel_date},
                                        'summary': f'查到 {count} 个可核验的直达席位选项' if count else self.query_cache[key]['message']})
        result = self.query_cache[key]
        if not result['options']:
            # Keep unavailable/network/limit diagnostics; never turn these into "sold out".
            return {**result, 'options': []}
        hard_start = time.fromisoformat(self.request.start_time) if selected_date == self.request.start_date else time()
        trip_end = datetime.combine(self.request.start_date + timedelta(days=self.request.days), time())
        available = [option for option in result['options'] if time.fromisoformat(option['departure_time']) >= hard_start
                     and datetime.fromisoformat(option['arrival_date'] + 'T' + option['arrival_time']) < trip_end]
        preferred = [option for option in available if time.fromisoformat(option['departure_time']) >= earliest]
        if not available:
            return {**result, 'reason': 'trip_window', 'message': '查到可用直达席位，但不在用户的旅行起止时间内；不能据此声称全日无票', 'options': []}
        candidates = sample_options(available, earliest_time)
        message = result['message'] if preferred else f'查到{len(available)}个旅行时间内的可用直达席位，但均早于偏好时刻{earliest_time}；已返回更早车次，请按真实车次调整活动，不是无票'
        return {**result, 'reason': 'available' if preferred else 'preferred_window', 'message': message,
                'preferred_time_matched': bool(preferred), 'options': candidates}

    async def enrich(self, itinerary: Itinerary) -> None:
        from .feasibility import schedule_journey
        for transfer_index, transfer in enumerate(itinerary.transfers):
            selected_id = transfer.rail_option_id
            # Model assertions, including assertions copied from an old version, are not evidence.
            transfer.schedule_status = 'estimated'
            transfer.price_status = 'estimated'
            transfer.train_code = ''
            transfer.departure_station = ''
            transfer.arrival_station = ''
            transfer.seat_type = ''
            transfer.seat_availability = ''
            transfer.queried_at = None
            transfer.source = ''
            transfer.query_note = ''
            transfer.rail_option_id = ''
            if transfer.mode == 'train':
                transfer.note = '交通时间与费用为估算；车票未预订，班次、余票及进出站接驳需确认'
            if transfer.mode != 'train' or not self.enabled:
                if transfer.mode == 'train':
                    transfer.query_note = '铁路查询未启用或服务环境未安装，交通仍为估算'
                continue
            try:
                # The model's provisional departure time is a preference, never a user constraint.
                result = await self.query(transfer.from_city, transfer.to_city, transfer.departure_date.isoformat())
                key = (city_key(transfer.from_city), city_key(transfer.to_city), transfer.departure_date.isoformat())
                candidates = self.query_cache.get(key, result)['options']
                def ranking(candidate):
                    departure = datetime.fromisoformat(candidate['departure_date'] + 'T' + candidate['departure_time'])
                    preferred = datetime.combine(transfer.departure_date, time.fromisoformat(transfer.departure_time))
                    per_person_allowance = self.request.budget / max(1, self.request.people * len(itinerary.transfers))
                    costly = candidate.get('reference_price') is not None and Decimal(candidate['reference_price']) > per_person_allowance
                    return (costly, not self.fits(candidate, itinerary), candidate['option_id'] != selected_id,
                            abs((departure - preferred).total_seconds()), candidate['duration_minutes'])
                option = None
                for candidate in sorted(candidates, key=ranking):
                    departure = datetime.fromisoformat(candidate['departure_date'] + 'T' + candidate['departure_time'])
                    arrival = datetime.fromisoformat(candidate['arrival_date'] + 'T' + candidate['arrival_time'])
                    trip_start = datetime.combine(self.request.start_date, time.fromisoformat(self.request.start_time))
                    trip_end = datetime.combine(self.request.start_date + timedelta(days=self.request.days), time())
                    if departure < trip_start or arrival >= trip_end:
                        continue
                    trial = itinerary.model_copy(deep=True)
                    trial_transfer = trial.transfers[transfer_index]
                    self.apply_schedule(trial_transfer, candidate)
                    try:
                        schedule_journey(self.request, trial)
                    except ValueError:
                        continue
                    # Only accept a train after adapting activities to its real timetable.
                    for original_day, trial_day in zip(itinerary.days, trial.days):
                        for original_stop, trial_stop in zip(original_day.stops, trial_day.stops):
                            original_stop.start, original_stop.end = trial_stop.start, trial_stop.end
                    option = candidate
                    break
                if not option:
                    transfer.query_note = result['message'] if not candidates else f'查到{len(candidates)}个可用直达席位，但当前活动或其他交通无法衔接；应调整活动或交通日期，不是无票'
                    raise ValueError(f'{transfer.from_city}→{transfer.to_city}（{transfer.departure_date}）：' + transfer.query_note)
                self.apply_verified_option(transfer, option)
            except ValueError:
                raise  # A provisional model timetable must not be published as usable rail transport.
            except Exception as error:
                transfer.query_note = '铁路查询未完成，交通仍为估算：' + settings.safe_error(error)[:160]
                raise ValueError(transfer.query_note) from None

    @staticmethod
    def apply_verified_option(transfer, option):
        Railway.apply_schedule(transfer, option)
        transfer.rail_option_id = option['option_id']
        transfer.schedule_status = 'queried'
        for name in ('train_code', 'departure_station', 'arrival_station', 'seat_type', 'seat_availability', 'source'):
            setattr(transfer, name, option[name])
        transfer.queried_at = datetime.fromisoformat(option['queried_at'])
        if option['reference_price'] is not None:
            transfer.unit_cost = Decimal(option['reference_price'])
            transfer.basis = 'person'
            transfer.price_status = 'reference'
        else:
            transfer.price_status = 'estimated'
        transfer.query_note = ''
        transfer.note = '时刻与席位为查询时快照；票价为公布参考价或估算，未预订，进出站接驳需确认'
        if option['reference_price'] is None:
            transfer.note = f'时刻和席位已查询；参考票价接口暂未完成，本项每人{transfer.unit_cost}元仅为预算预留，不是车票报价，实际票价需补核。未预订，进出站接驳需确认。'

    @staticmethod
    def apply_schedule(transfer, option):
        transfer.departure_date = date.fromisoformat(option['departure_date'])
        transfer.arrival_date = date.fromisoformat(option['arrival_date'])
        transfer.departure_time = option['departure_time']
        transfer.arrival_time = option['arrival_time']

    def fits(self, option: dict, itinerary: Itinerary) -> bool:
        start = datetime.fromisoformat(option['departure_date'] + 'T' + option['departure_time'])
        end = datetime.fromisoformat(option['arrival_date'] + 'T' + option['arrival_time'])
        trip_start = datetime.combine(self.request.start_date, time.fromisoformat(self.request.start_time))
        trip_end = datetime.combine(self.request.start_date + timedelta(days=self.request.days), time())
        if start < trip_start or end >= trip_end:
            return False
        for day in itinerary.days:
            for stop in day.stops:
                before = datetime.combine(day.date, time.fromisoformat(stop.start))
                after = datetime.combine(day.date, time.fromisoformat(stop.end))
                if before < end + timedelta(minutes=45) and after > start - timedelta(minutes=45):
                    return False
        return True
