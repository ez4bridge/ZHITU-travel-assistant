"""Explainable local-mode preference over real provider routes, without queries.

These are adjustable product heuristics, not an optimal routing algorithm.
Durations exclude provider-unknown waiting, parking and vehicle availability.
"""
from decimal import Decimal
import math

from .transport_cost import estimate_cost


def group_cost(option, city: str, people: int) -> Decimal | None:
    estimate = option.cost_estimate or estimate_cost(option, city)
    if estimate is None:
        return None
    multiplier = people if estimate.basis == 'person' else math.ceil(people / 4)
    return estimate.high * multiplier


def transit_changes(option) -> int | None:
    """Amap stores one line name for each boarded public-transport segment."""
    lines = [name for name in option.instructions if name.strip()]
    return max(0, len(lines) - 1) if lines else None


def recommend_local(options, city: str = '', people: int = 1):
    """Return (one actual option or None, reason), without altering mode facts."""
    known = {option.mode: option for option in options if option.available
             and option.duration_s is not None and option.duration_s >= 0
             and option.distance_m is not None and option.distance_m >= 0
             and (len(option.polyline) >= 2 or getattr(option, 'stationary', False))}
    walk, transit, taxi = (known.get(mode) for mode in ('walking', 'transit', 'taxi'))
    suffix = '地图路耗不含额外候车、安检或停车取车；费用为估算，可逐段更换。'
    if walk and walk.distance_m <= 1500 and walk.duration_s <= 20 * 60:
        return walk, '推荐步行：已查道路不超过1.5公里且20分钟，免交通费用，适合短途。' + suffix
    if transit and taxi:
        transit_cost, taxi_cost = group_cost(transit, city, people), group_cost(taxi, city, people)
        changes = transit_changes(transit)
        times = f'打车约{math.ceil(taxi.duration_s / 60)}分钟，公交／地铁约{math.ceil(transit.duration_s / 60)}分钟'
        connection = f'，公交需换乘{changes}次' if changes is not None else '，公交换乘细节待核实'
        if (transit_cost is not None and taxi_cost is not None and taxi_cost <= transit_cost
                and taxi.duration_s <= transit.duration_s + 5 * 60):
            return taxi, (f'推荐打车：{times}；全员费用参考不高于公交，约少花{transit_cost - taxi_cost:.2f}元'
                + connection + '；默认综合全员费用、时间和换乘取舍，可改选。' + suffix)
        if (changes is not None and changes >= 2 and transit_cost is not None and taxi_cost is not None
                and taxi_cost - transit_cost <= Decimal(10) * people
                and taxi.duration_s <= transit.duration_s):
            return taxi, (f'推荐打车：{times}，公交需换乘{changes}次；全员增加约{taxi_cost - transit_cost:.2f}元，'
                '地图路耗不更长；默认在每人多付不超过10元时优先减少多次换乘，可改选。' + suffix)
        if (transit.duration_s <= taxi.duration_s + 15 * 60
                and transit_cost is not None and taxi_cost is not None and transit_cost < taxi_cost):
            return transit, (f'推荐公交／地铁：{times}；全员费用更低，约少花{taxi_cost - transit_cost:.2f}元'
                + connection + '；默认在比打车慢不超过15分钟时优先控制全员费用，可改选。' + suffix)
        if taxi.duration_s + 15 * 60 <= transit.duration_s:
            saving = Decimal(transit.duration_s - taxi.duration_s) / 60
            if (transit_cost is not None and taxi_cost is not None
                    and taxi_cost - transit_cost > saving * people):
                return transit, (f'推荐公交／地铁：{times}，比打车约多花{math.ceil(saving)}分钟，但全员约少花'
                    f'{taxi_cost - transit_cost:.2f}元' + connection +
                    '；按默认每人每省1分钟最多增加1元的产品规则取舍，可自行改选。' + suffix)
            if transit_cost is None or taxi_cost is None:
                return taxi, (f'耗时参考打车：{times}，约节省{math.ceil(saving)}分钟' + connection +
                    '；费用未齐，仅耗时参考需确认，不能据此判断全员花费更合适。' + suffix)
            return taxi, (f'推荐打车：{times}，约节省{math.ceil(saving)}分钟，全员多约{taxi_cost - transit_cost:.2f}元'
                + connection + '；按默认每人每省1分钟最多增加1元的产品规则综合取舍，可改选。' + suffix)
    if taxi and not transit and (walk is None or walk.duration_s - taxi.duration_s >= 15 * 60):
        return taxi, '推荐打车：未取得可用公交方案，且步行较远或至少多花15分钟；不代表公交实际停运。' + suffix
    primary = [option for mode, option in known.items() if mode in ('walking', 'transit', 'taxi')]
    if primary:
        order = {'walking': 0, 'transit': 1, 'taxi': 2}
        selected = min(primary, key=lambda option: (option.duration_s, order[option.mode]))
        return selected, '参考推荐：常用方式中已查地图通行时间最短；费用或比较依据不足，不声称最便宜。' + suffix
    if known:
        selected = min(known.values(), key=lambda option: option.duration_s)
        return selected, '仅取得自驾或骑行路线参考；车辆、自行车及取还车条件未确认，请确认后选用。' + suffix
    return None, '未取得完整可用道路，暂不推荐；缺少数据不表示无需交通。'
