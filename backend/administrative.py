"""Resolve destination names through the provider's administrative identity."""
import asyncio
import re
import unicodedata


class AdministrativeMatchError(ValueError):
    pass


def name_key(value: str) -> str:
    return unicodedata.normalize('NFKC', value).strip()


def official_names(name: str) -> set[str]:
    names = {name_key(name)}
    # Only remove a suffix from an official API result, never guess a city table.
    for suffix in ('特别行政区', '特別行政區', '自治县', '自治旗', '市', '区', '县', '旗'):
        if name.endswith(suffix) and len(name) > len(suffix):
            names.add(name[:-len(suffix)])
            break
    return names


def select_division(city: str, data: dict) -> dict:
    rows = data.get('districts', [])
    if not isinstance(rows, list):
        raise AdministrativeMatchError('行政区查询未返回有效结果')
    matches = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name, code, level = row.get('name'), row.get('adcode'), row.get('level')
        if not isinstance(name, str) or not isinstance(code, str) or not re.fullmatch(r'\d{6}', code):
            continue
        allowed = level in ('city', 'district') or (level == 'province' and code.endswith('0000') and
            name.endswith(('市', '特别行政区', '特別行政區')))
        if allowed and name_key(city) in official_names(name):
            matches[code] = {key: row[key] for key in ('name', 'adcode', 'level', 'center') if key in row}
    try:
        complete = int(data.get('count', len(rows))) <= len(rows)
    except (TypeError, ValueError):
        complete = False
    if not complete:
        raise AdministrativeMatchError('目的地区划无法唯一确认，请填写明确的行政区全称')
    # A city name also matches its own same-named county (长沙市／长沙县). The
    # destination list is city-level, so that county is not a real ambiguity —
    # but a same-named district elsewhere (朝阳区 vs 朝阳市) still is.
    city_rows = [row for row in matches.values() if row.get('level') in ('city', 'province')]
    if len(city_rows) == 1:
        city = city_rows[0]
        width = 2 if city.get('level') == 'province' else 4
        others = [row for row in matches.values() if row is not city]
        if all(str(row['adcode'])[:width] == str(city['adcode'])[:width] for row in others):
            return city
    if len(matches) != 1:
        raise AdministrativeMatchError('目的地区划无法唯一确认，请填写明确的行政区全称')
    return next(iter(matches.values()))


def cached_division(client, city: str) -> dict | None:
    return getattr(client, 'administrative_divisions', {}).get(name_key(city))


async def resolve_division(client, city: str) -> dict:
    # Request-local only: weather and POI checks share identity, never Agent messages.
    if not hasattr(client, 'administrative_divisions'):
        client.administrative_divisions = {}
        client.administrative_locks = {}
    lock = client.administrative_locks.setdefault(name_key(city), asyncio.Lock())
    async with lock:
        found = cached_division(client, city)
        if found:
            return found
        data = await client.request('/v3/config/district', {'keywords': city,
            'subdistrict': 0, 'extensions': 'base', 'offset': 20, 'page': 1})
        found = select_division(city, data)
        client.administrative_divisions[name_key(city)] = found
        client.administrative_divisions[name_key(found['name'])] = found
        return found


def contains_place(division: dict, place: dict) -> bool:
    code = str(place.get('adcode', ''))
    expected = division['adcode']
    if re.fullmatch(r'\d{6}', code):
        length = 6 if division['level'] == 'district' else 4 if division['level'] == 'city' else 2
        return code[:length] == expected[:length]
    # Some detail responses omit codes; accept the resolved official full name only.
    return division['name'] in (place.get('cityname'), place.get('adname'))
