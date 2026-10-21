"""Display-only administrative centers; never activity or routing coordinates."""
import asyncio
import math

from .administrative import resolve_division
from .amap import Amap


async def resolve_map_region(city: str) -> dict:
    client = Amap(city)
    try:
        async with asyncio.timeout(5):
            division = await resolve_division(client, city)
            center = [float(part) for part in division.get('center', '').split(',')]
            if len(center) != 2 or not all(math.isfinite(value) for value in center) or not (-180 <= center[0] <= 180 and -90 <= center[1] <= 90):
                raise ValueError('行政区中心坐标不可用')
            return {'status': 'available', 'requested_city': city, 'city': division['name'],
                'adcode': division['adcode'], 'level': division['level'], 'center': center,
                'approximate': True, 'source': '高德行政区中心'}
    except Exception:
        return {'status': 'unavailable', 'requested_city': city, 'city': '', 'center': None,
            'approximate': True, 'message': '暂未取得目的地区域位置，仅显示全国概览'}
    finally:
        await client.close()
