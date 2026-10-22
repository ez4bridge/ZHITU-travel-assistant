"""Verify every requested city before weather, model calls or railway queries."""
from .administrative import AdministrativeMatchError, resolve_division

# Province-level prefixes in China's six-digit administrative division codes.
DOMESTIC_PREFIXES = frozenset('11 12 13 14 15 21 22 23 31 32 33 34 35 36 37 41 42 43 44 45 46 50 51 52 53 54 61 62 63 64 65 71 81 82'.split())


class DomesticScopeError(ValueError):
    def __init__(self, message, city='', code='domestic_scope_unconfirmed'):
        super().__init__(message)
        self.city = city
        self.code = code


async def ensure_domestic_trip(trip, amap):
    cities = list(dict.fromkeys(([trip.origin] if trip.origin else []) + trip.city_route))
    for city in cities:
        try:
            division = await resolve_division(amap, city)
        except AdministrativeMatchError:
            raise DomesticScopeError(
                f'无法将“{city}”唯一确认为中国境内的城市或区县。仅支持国内旅行，请填写明确的行政区全称；不支持境外或虚构地点。', city=city) from None
        except Exception:
            # Verification must fail closed, without exposing signed provider URLs.
            raise DomesticScopeError('国内城市核验暂不可用，请检查高德 Web 服务配置或稍后重试。核验成功后才能生成行程。', code='domestic_scope_unavailable') from None
        if division['adcode'][:2] not in DOMESTIC_PREFIXES:
            raise DomesticScopeError(f'“{city}”不在支持的国内旅行范围内，请选择中国境内的城市或区县。', city=city, code='domestic_scope_outside')
