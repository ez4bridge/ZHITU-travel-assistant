"""Cost references never substitute for provider road geometry or travel times."""
import math
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

from .models import TransportCost

AMAP_SOURCE = 'https://lbs.amap.com/api/webservice/guide/api/direction'
BEIJING_TAXI = 'https://jtw.beijing.gov.cn/czqc/zxts/202303/t20230310_2933641.html'
SHANGHAI_TAXI = 'https://jtw.sh.gov.cn/czqcyj/index.html'
BEIJING_PARKING = 'https://jtw.beijing.gov.cn/qaknowledge/202204/t20220426_2692780.html'
BEIJING_BIKE = 'https://item.btime.com/f32s19nb9ns8n19b9rshfrkqlgj'


def money(value: Decimal) -> Decimal:
    # Provider/stored values occasionally arrive as int; never crash a preview.
    return Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def estimate_cost(route, city: str, stay_minutes: int = 0) -> TransportCost | None:
    if not route.available or route.distance_m is None or route.duration_s is None:
        return None
    km = Decimal(route.distance_m) / 1000
    minutes = math.ceil(route.duration_s / 60)
    city = city.rstrip('市')
    if route.mode == 'walking' or getattr(route, 'stationary', False):
        return TransportCost(low=0, high=0, basis='person', method='无需交通付费', assumptions='不含途中消费')
    if route.mode == 'transit':
        if route.fare_yuan is None:
            return None
        return TransportCost(low=route.fare_yuan, high=route.fare_yuan, basis='person',
            method='高德公共交通票价参考', assumptions='查询时接口票价，不保证未来票制或优惠', source_url=AMAP_SOURCE)
    if route.mode == 'taxi':
        if route.taxi_cost_yuan is not None:
            low = high = route.taxi_cost_yuan
            method, source = '高德出租车估价', AMAP_SOURCE
            assumptions = '每辆普通车按最多4位乘客预算；非网约车实时报价；不含候车、动态加价和额外费用'
        else:
            if city == '北京':
                low = Decimal(14) + max(km - 3, Decimal(0)) * Decimal('2.3') + max(km - 15, Decimal(0)) * Decimal('1.15')
                high = low * Decimal('1.2')
                method, source = '北京巡游出租车公开费率参考', BEIJING_TAXI
                assumptions = '2023公开标准：3公里13元，2.3元/公里，15公里以上加50%，燃油附加1元；上限留20%夜间余量'
            elif city == '上海':
                low = Decimal(14) + max(km - 3, Decimal(0)) * Decimal('2.7') + max(km - 15, Decimal(0)) * Decimal('1.35')
                high = low * Decimal('1.3')
                method, source = '上海普通巡游出租车公开费率参考', SHANGHAI_TAXI
                assumptions = '2023公开标准：3公里14元，2.7元/公里，15公里以上加50%；上限留30%夜间余量；特殊车型另计'
            else:
                low = Decimal(10) + max(km - 3, Decimal(0)) * 2
                high = Decimal(16) + max(km - 3, Decimal(0)) * 4
                method, source = '通用打车预算假设', ''
                assumptions = '当地公开费率尚未收录；按起步10–16元/3公里、续程2–4元/公里预留，非当地报价'
            assumptions += '；每车最多4位乘客；未含低速等候、网约车动态加价、过路费，以平台报价为准'
        return TransportCost(low=money(low), high=money(high), basis='vehicle', method=method,
                             assumptions=assumptions, source_url=source)
    if route.mode == 'driving':
        # A planning envelope, explicitly not a current fuel quotation or a booked parking lot.
        fuel_low, fuel_high = km * Decimal('.06') * 8, km * Decimal('.10') * Decimal('9.5')
        hours = Decimal(max(stay_minutes, 0)) / 60
        parking_low, parking_high = (Decimal(2), Decimal(15)) if city == '北京' else (Decimal(3), Decimal(15))
        toll = route.tolls_yuan or Decimal(0)
        assumptions = f'每车最多4人；燃油预算假设6–10L/百公里、8–9.5元/L（非实时油价）；目的地停车按停留{stay_minutes}分钟'
        assumptions += '、北京路侧公开标准2–15元/小时作参考' if city == '北京' else '、预留3–15元/小时（当地停车预算假设）'
        assumptions += f'；高德过路费{toll}元' if route.tolls_yuan is not None else '；接口未给过路费，未计入'
        assumptions += '；不含租车、押金和异地还车；实际停车场另核实'
        return TransportCost(low=money(fuel_low + parking_low * hours + toll),
            high=money(fuel_high + parking_high * hours + toll), basis='vehicle', method='道路里程＋燃油与停车预算',
            assumptions=assumptions, source_url=BEIJING_PARKING if city == '北京' else '')
    if route.mode == 'bicycling':
        if city == '北京':
            extra = Decimal(max(minutes - 60, 0)) * Decimal('.1')
            low, high = Decimal('1.88') + extra, Decimal('2.5') + extra
            method, source = '北京共享单车公开报道费率参考', BEIJING_BIKE
            assumptions = '2026年6月调价报道：首60分钟约1.88–2.5元，超时0.1元/分钟；车型、运营区、节假日以扫码页为准'
        else:
            blocks = (Decimal(minutes) / 30).to_integral_value(rounding=ROUND_CEILING)
            low, high = max(blocks, 1) * Decimal('1.5'), max(blocks, 1) * 3
            method, source = '共享单车预算假设', ''
            assumptions = '当地运营商实时价未接入；按每30分钟1.5–3元、每段重新起租预留，非运营商报价'
        return TransportCost(low=money(low), high=money(high), basis='person', method=method,
            assumptions=assumptions + '；自带自行车使用费为0，不含调度费、押金和景区租车', source_url=source)
    return None


def price_routes(trip, itinerary, routes, only_day=None):
    for route in routes:
        if only_day is not None and route.day_index != only_day:
            continue
        stop = next((s for s in itinerary.days[route.day_index].stops if s.id == route.to_id), None)
        if route.scope == 'intercity':
            continue
        stay = 0
        if stop:
            from .validation import minutes
            stay = max(0, minutes(stop.end) - minutes(stop.start))
        for option in [route, *route.alternatives]:
            option.cost_estimate = estimate_cost(option, stop.city if stop and stop.city else trip.destination, stay)
