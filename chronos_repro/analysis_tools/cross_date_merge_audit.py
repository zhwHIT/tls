"""Offline audit of date changes/removals; never supplies private references to API."""
import argparse
import json
from pathlib import Path


def analyze(trace):
    candidates, rows = {}, []
    for step in trace['steps']:
        if step['action'] == 'VERIFY':
            candidates = {r['candidate_id']: r for r in step['model_output']['candidates']}
        if step['action'] != 'MERGE':
            continue
        for op in step['model_output'].get('operations', []):
            if op['operation'] not in {'UPDATE', 'DROP'}:
                continue
            candidate = candidates.get(op['candidate_id'])
            if candidate:
                rows.append({'phase': step['phase'], 'operation': op,
                    'candidate_event': candidate['event'], 'date_evidence': candidate.get('date_evidence'),
                    'review_required': True})
    return {'topic': trace['topic'], 'status': trace['status'], 'source_sha256': trace.get('source_sha256'),
            'operations_for_review': rows, 'api_calls': 0,
            'caution': 'These are review candidates, not automatic merge errors. Planned dates, true duplicates and contradictory reports require semantic inspection.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trajectory', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    report = analyze(json.loads(args.trajectory.read_text(encoding='utf-8')))
    with args.output.open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(args.output), 'operations_for_review': len(report['operations_for_review']), 'api_calls': 0}))
