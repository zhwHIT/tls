"""Offline request-length audit; character counts are not student-token counts."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics


def summarize(output):
    trace = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    groups = defaultdict(list)
    for audit in trace.get('audits', []):
        for attempt in audit.get('attempts', []):
            if attempt.get('usage', {}).get('prompt_tokens') is not None:
                groups[audit['stage']].append(attempt['usage'])
    stages = {}
    for stage, rows in groups.items():
        prompts = [r['prompt_tokens'] for r in rows]
        stages[stage] = {'returned_responses_with_usage': len(rows), 'prompt_tokens_max': max(prompts),
            'prompt_tokens_median': statistics.median(prompts),
            'completion_tokens_total': sum(r.get('completion_tokens', 0) for r in rows),
            'reasoning_tokens_total': sum(r.get('completion_tokens_details', {}).get('reasoning_tokens', 0) for r in rows)}
    projection_path = output / 'context_projections.json'
    projections = json.loads(projection_path.read_text(encoding='utf-8')) if projection_path.exists() else []
    policy = [p for p in projections if any(label in p['stage'] for label in ('SEARCH', 'POLICY', 'MEMORY', 'STOP'))]
    return {'topic': trace['topic'], 'status': trace['status'], 'source_sha256': trace.get('source_sha256'),
        'stages': stages,
        'all_request_chars_max': max((r['after_chars'] for r in projections), default=None),
        'controller_request_chars_max': max((r['after_chars'] for r in policy), default=None),
        'context_request_count': len(projections), 'api_calls': 0,
        'notes': ['Usage tokens use the API tokenizer, not the future student tokenizer',
                  'Cached responses have no new usage and are excluded from token aggregates',
                  'Long-context training feasibility requires a separate tokenizer and hardware test']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = summarize(args.run)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    with args.output.open('x', encoding='utf-8') as target:
        target.write(text + '\n')
    print(text)
