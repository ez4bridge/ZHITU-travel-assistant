"""Per-request usage accounting; never records prompts, images or credentials."""
from collections import Counter
from types import SimpleNamespace


def summarize_calls(calls: list[dict]) -> dict:
    totals, reported = Counter(), 0
    for call in calls:
        usage = call.get('usage')
        if usage is None:
            continue
        reported += 1
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens',
                    'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens'):
            if isinstance(usage.get(key), int):
                totals[key] += usage[key]
    result = {'attempted_calls': len(calls), 'usage_reported_calls': reported,
              'usage_missing_calls': len(calls) - reported, **dict(totals)}
    if not calls:
        result.update(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    if len(calls) > reported:
        result['reported_total_tokens'] = totals.get('total_tokens')
        result['total_tokens'] = None
    return result


class UsageCollector:
    def __init__(self):
        self.calls: list[dict] = []

    def track(self, client):
        async def create(**kwargs):
            entry = {'usage': None}
            self.calls.append(entry)
            response = await client.chat.completions.create(**kwargs)
            usage = getattr(response, 'usage', None)
            if usage is not None:
                entry['usage'] = usage.model_dump(exclude_none=True)
            return response
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    def result(self) -> dict:
        return summarize_calls(self.calls)
