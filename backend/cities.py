"""Public city suggestions from Amap administrative divisions, without key exposure."""
import asyncio
import time

from .amap import Amap


def city_entries(data: dict) -> list[dict[str, str]]:
    found = {}
    municipalities = {'北京市', '天津市', '上海市', '重庆市', '香港特别行政区', '澳门特别行政区'}
    for country in data.get('districts', []):
        for province in country.get('districts', []):
            province_name = str(province.get('name', ''))
            candidates = [province] if province_name in municipalities else province.get('districts', [])
            for city in candidates:
                name, code = str(city.get('name', '')), str(city.get('adcode', ''))
                if name and code and '行政区划' not in name:
                    found[code] = {'name': name, 'province': province_name, 'adcode': code}
    return sorted(found.values(), key=lambda entry: entry['name'])


class CityCatalog:
    def __init__(self):
        # Only public administrative names are cached; no itinerary or user state.
        self.entries = []
        self.expires_at = 0
        self.lock = asyncio.Lock()

    async def search(self, query: str) -> list[dict[str, str]]:
        query = query.strip()
        if not query:
            return []
        async with self.lock:
            if not self.entries or time.monotonic() >= self.expires_at:
                amap = Amap('中国')
                try:
                    data = await amap.request('/v3/config/district', {'keywords': '中国', 'subdistrict': 2, 'extensions': 'base'})
                    entries = city_entries(data)
                    if not entries:
                        raise RuntimeError('城市建议数据为空，可直接输入城市')
                    self.entries = entries
                    self.expires_at = time.monotonic() + 86400
                finally:
                    await amap.close()
        matches = [entry for entry in self.entries if query in entry['name'] or query in entry['province']]
        matches.sort(key=lambda entry: (not entry['name'].startswith(query), entry['name']))
        return matches[:8]


city_catalog = CityCatalog()
