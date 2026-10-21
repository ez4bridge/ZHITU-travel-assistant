"""Request-local railway options, with provider facts kept separate from model estimates."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from .config import settings
from .models import TripRequest, Itinerary, city_key
from .rail_mcp import RailMcpClient

# Current railway queries use China's UTC+8 clock without requiring Windows tzdata.
CHINA_TIMEZONE = timezone(timedelta(hours=8))
SOURCE = '12306 / drfccv MCP'
SEATS = [('second_class', '二等座'), ('hard_seat', '硬座'), ('first_class', '一等座'),
         ('soft_seat', '软座'), ('hard_sleeper', '硬卧'), ('soft_sleeper', '软卧'),
         ('business', '商务座'), ('no_seat', '无座')]


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
    def __init__(self, request: TripRequest, emit=None) -> None:
        self.request = request
        self.emit = emit
        self.enabled = settings.rail_mcp_enabled and settings.rail_mcp_python.is_file()
        self.client = RailMcpClient(settings.rail_mcp_python)
        self.station_cache = {}
        self.query_cache = {}
        self.options = {}

    async def close(self) -> None:
        await self.client.close()

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
                    raise RuntimeError('未查到可用铁路班次，可能无直达车、未开售或接口暂不可用')
                try:
                    prices = await self.client.call('query-ticket-price', args)
                except Exception:
                    prices = {}
                rows = train_options(ticket_data.get('trains', []), prices.get('data', []) if prices.get('success') else [],
                                     origin, destination, selected_date, self.request.people, from_city, to_city)
                for option in rows:
                    self.options[option['option_id']] = option
                self.query_cache[key] = {'status': 'queried', 'message': ('时刻和席位为查询时快照；参考票价非锁价，不含站外接驳' if rows else '未找到满足人数且可核验的直达席位，交通仍为估算'), 'options': rows}
            except Exception as error:
                self.query_cache[key]['message'] = '铁路查询未完成，交通仍为估算：' + settings.safe_error(error)[:160]
            if self.emit:
                count = len(self.query_cache[key]['options'])
                await self.emit('tool', {'name': '12306 车次与参考票价', 'arguments': {'from_city': from_city, 'to_city': to_city, 'date': travel_date},
                                        'summary': f'查到 {count} 个可核验的直达席位选项' if count else self.query_cache[key]['message']})
        result = self.query_cache[key]
        candidates = [option for option in result['options'] if time.fromisoformat(option['departure_time']) >= earliest][:8]
        return {**result, 'message': result['message'] if candidates else '该时段无可用直达席位，交通仍为估算', 'options': candidates}

    async def enrich(self, itinerary: Itinerary) -> None:
        for transfer in itinerary.transfers:
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
                result = await self.query(transfer.from_city, transfer.to_city, transfer.departure_date.isoformat(), transfer.departure_time)
                option = self.options.get(selected_id)
                if (not option or (city_key(option['from_city']), city_key(option['to_city']), option['departure_date']) != (city_key(transfer.from_city), city_key(transfer.to_city), transfer.departure_date.isoformat())
                        or option['departure_time'] < transfer.departure_time or not self.fits(option, itinerary, transfer)):
                    key = (city_key(transfer.from_city), city_key(transfer.to_city), transfer.departure_date.isoformat())
                    candidates = self.query_cache.get(key, result)['options']
                    option = next((candidate for candidate in candidates
                                   if candidate['departure_time'] >= transfer.departure_time and self.fits(candidate, itinerary, transfer)), None)
                if not option:
                    transfer.query_note = result['message'] if not result['options'] else '未找到符合当前活动时间窗口的车次，请调整出行时段'
                    continue
                transfer.departure_date = date.fromisoformat(option['departure_date'])
                transfer.arrival_date = date.fromisoformat(option['arrival_date'])
                transfer.departure_time = option['departure_time']
                transfer.arrival_time = option['arrival_time']
                transfer.rail_option_id = option['option_id']
                transfer.schedule_status = 'queried'
                for name in ('train_code', 'departure_station', 'arrival_station', 'seat_type', 'seat_availability', 'source'):
                    setattr(transfer, name, option[name])
                transfer.queried_at = datetime.fromisoformat(option['queried_at'])
                if option['reference_price'] is not None:
                    transfer.unit_cost = Decimal(option['reference_price'])
                    transfer.basis = 'person'
                    transfer.price_status = 'reference'
                transfer.note = '时刻与席位为查询时快照；票价为公布参考价或估算，未预订，进出站接驳需确认'
            except Exception as error:
                transfer.query_note = '铁路查询未完成，交通仍为估算：' + settings.safe_error(error)[:160]

    def fits(self, option: dict, itinerary: Itinerary, transfer=None) -> bool:
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
                if transfer is not None:
                    planned_start = datetime.combine(transfer.departure_date, time.fromisoformat(transfer.departure_time))
                    planned_end = datetime.combine(transfer.arrival_date, time.fromisoformat(transfer.arrival_time))
                    # A late train can miss daytime activities without overlapping
                    # any of them. Check city arrival/departure order as well.
                    if city_key(stop.city) == city_key(transfer.to_city) and planned_start <= before < end + timedelta(minutes=45):
                        return False
                    if city_key(stop.city) == city_key(transfer.from_city) and start - timedelta(minutes=45) < after <= planned_end:
                        return False
        return True
