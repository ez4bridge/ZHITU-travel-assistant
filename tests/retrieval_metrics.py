def metrics(question: dict, hits: list[dict]) -> dict:
    groups = question['evidence_groups']
    if not groups:
        return {'recall': None, 'mrr': None, 'returned_count': len(hits)}
    ranks = []
    for group in groups:
        ranks.append(next((i for i, hit in enumerate(hits[:5], 1) if any(
            span['source_id'] in hit['source_ids'] and hit['start_char'] <= span['start_char']
            and hit['end_char'] >= span['end_char'] for span in group)), None))
    present = [rank for rank in ranks if rank is not None]
    return {'recall': len(present) / len(groups), 'mrr': 1 / min(present) if present else 0,
            'returned_count': len(hits)}


def choose(result: dict) -> dict:
    methods = result['methods']
    baseline, hybrid, rerank = [methods[m]['test'] for m in ('bm25', 'hybrid', 'hybrid_rerank')]
    base_rows = {r['id']: r for r in methods['bm25']['rows']}
    improved = any(r['split'] == 'test' and r['kind'] == 'paraphrase' and r['recall'] is not None
        and (r['recall'] > base_rows[r['id']]['recall'] or r['mrr'] > base_rows[r['id']]['mrr'])
        for r in methods['hybrid']['rows'])
    hybrid_ok = (hybrid['recall_at_5'] >= baseline['recall_at_5'] - 1e-9
                 and hybrid['mrr_at_5'] >= baseline['mrr_at_5'] - 1e-9 and improved)
    rerank_ok = (hybrid_ok and rerank['recall_at_5'] >= hybrid['recall_at_5'] - 1e-9
                 and rerank['mrr_at_5'] - hybrid['mrr_at_5'] >= .02 - 1e-9
                 and rerank['p95_seconds'] <= 3)
    return {'mode': 'hybrid_rerank' if rerank_ok else 'hybrid' if hybrid_ok else 'bm25',
            'hybrid_passed': hybrid_ok, 'rerank_passed': rerank_ok,
            'paraphrase_improved': improved, 'fingerprint': result['fingerprint'],
            'question_set_sha256': result['question_set_sha256'], 'measured_at': result['measured_at']}
