"""Offline usage accounting. Token shares are never substituted for monetary shares."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path


def audit(root):
    stages = defaultdict(Counter)
    ledger_total = 0
    files = {}
    unmatched = []
    def read(path):
        raw = path.read_bytes()
        files[str(path)] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)
    for path in sorted(root.glob('*/token_usage_audit.json')):
        saved = read(path)
        ledger = read(path.parent / 'request_ledger.json')
        trajectory = read(path.parent / 'trajectory.json')
        inherited = (trajectory.get('continuation') or {}).get('parent_requests', 0)
        new_http = ledger['requests_started'] - inherited
        ledger_total += new_http
        assigned_http = 0
        for row in saved['token_counts']:
            stage = stages[row['stage']]
            usage = row.get('new_response_usage') or {}
            attempts = row.get('http_attempts', 0)
            stage['http_attempts'] += attempts
            assigned_http += attempts
            stage['usage_records'] += bool(usage)
            stage['records_without_usage'] += not bool(usage)
            for key in ('prompt_tokens', 'completion_tokens', 'total_tokens',
                        'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens'):
                stage[key] += usage.get(key, 0)
            if usage and usage.get('prompt_tokens', 0) != (
                    usage.get('prompt_cache_hit_tokens', 0) + usage.get('prompt_cache_miss_tokens', 0)):
                stage['incomplete_cache_breakdown_records'] += 1
        if assigned_http != new_http:
            unmatched.append({'topic': path.parent.name, 'ledger_http': new_http,
                              'attributed_http': assigned_http, 'difference': new_http - assigned_http})
    totals = sum(stages.values(), Counter())
    return {'scope': str(root), 'new_http_ledger': ledger_total, 'recorded_usage_totals': dict(totals),
            'stages': {stage: {**counts, 'recorded_token_share': counts['total_tokens'] / totals['total_tokens'],
                              'http_share': counts['http_attempts'] / ledger_total,
                              'monetary_cost': None, 'monetary_share': None}
                       for stage, counts in sorted(stages.items(), key=lambda item: item[1]['total_tokens'], reverse=True)},
            'unattributed_http': unmatched,
            'price_model': {'status': 'historical_rates_and_invoice_not_available',
                            'unit': 'currency per million tokens',
                            'formula': '(cache_hit_input * hit_rate + cache_miss_input * miss_rate + output * output_rate) / 1000000',
                            'rates': None, 'currency': None,
                            'notes': ['Apply the actual model/date/discount rates to each usage record.',
                                      'Unknown usage on failed attempts is not assumed to be free.',
                                      'prompt_tokens already includes cache hits; do not count hits twice.',
                                      'Token shares do not establish monetary shares.']},
            'source_sha256': files}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.root)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'ledger_http': result['new_http_ledger'],
                      'usage_totals': result['recorded_usage_totals'],
                      'unattributed_http': result['unattributed_http'],
                      'monetary_cost': None}, ensure_ascii=False))
