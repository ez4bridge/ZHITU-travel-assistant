"""Launch the isolated, pinned community MCP with verified TLS and city metadata."""
import json
import os
import sys
from pathlib import Path

VENDORED_SOURCE = Path(__file__).resolve().parents[1] / 'third_party' / 'mcp-server-12306' / 'src'
if not (VENDORED_SOURCE / 'mcp_12306' / 'server.py').is_file():
    raise RuntimeError('仓库内缺少 12306 MCP 源码，请检查 third_party/mcp-server-12306')
sys.path.insert(0, str(VENDORED_SOURCE))

os.environ['DEBUG'] = 'false'
os.environ['LOG_LEVEL'] = 'ERROR'

from mcp_12306.services import ticket_service
from mcp_12306 import server
from mcp_12306.stdio_server import main


def verified_client():
    return ticket_service.httpx.AsyncClient(follow_redirects=True, timeout=8, verify=True)


ticket_service.create_12306_client = verified_client
original_station_search = server.TOOL_HANDLERS['search-stations']


async def station_search_with_city(arguments):
    contents = await original_station_search(arguments)
    for content in contents:
        if content.get('type') != 'text':
            continue
        data = json.loads(content['text'])
        for station in data.get('stations', []):
            record = await ticket_service.station_service.get_station_by_code(station['code'])
            station['city'] = record.city if record else ''
        content['text'] = json.dumps(data, ensure_ascii=False)
    return contents


server.TOOL_HANDLERS['search-stations'] = station_search_with_city

if __name__ == '__main__':
    if sys.argv[1:] == ['--check-install']:
        from mcp_12306 import __version__
        print(json.dumps({'source': str(Path(server.__file__).resolve()), 'version': __version__}))
    else:
        main()
