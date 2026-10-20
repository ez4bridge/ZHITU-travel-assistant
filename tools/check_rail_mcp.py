"""Opt-in real railway probe; does not call the LLM or book tickets."""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import settings
from backend.models import TripRequest
from backend.railway import Railway, CHINA_TIMEZONE


async def main() -> None:
    parser = argparse.ArgumentParser(description='测试已配置的铁路 MCP，不调用模型或购票')
    parser.add_argument('--from-city', required=True)
    parser.add_argument('--to-city', required=True)
    parser.add_argument('--date', default=str(datetime.now(CHINA_TIMEZONE).date() + timedelta(days=3)))
    parser.add_argument('--earliest-time', default='09:00')
    args = parser.parse_args()
    request = TripRequest(destinations=[{'city': args.from_city}, {'city': args.to_city}],
                          start_date=args.date, days=2)
    rail = Railway(request)
    try:
        async with asyncio.timeout(140):
            result = await rail.query(args.from_city, args.to_city, args.date, args.earliest_time)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result['options']:
            raise SystemExit(1)
    except (RuntimeError, ValueError, TimeoutError) as error:
        print('铁路测试未通过：' + settings.safe_error(error), file=sys.stderr)
        raise SystemExit(1)
    finally:
        await rail.close()


if __name__ == '__main__':
    asyncio.run(main())
