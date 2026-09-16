"""Offline, loss-checked duplicate removal proposal; not deployed into an active run."""
import argparse
import copy
import json
from pathlib import Path

from chronos_repro.compact_context import gap_view, serialize


def propose(payload):
    result = copy.deepcopy(payload)
    if 'update GAP_MEMORY' not in result.get('stage', ''):
        return result
    if isinstance(result.get('active_gap'), dict):
        result['active_gap'] = gap_view(result['active_gap'])
    observation = result.get('cycle_observation', {})
    extracted = observation.get('all_extracted_candidates', [])
    verified = observation.get('verified_candidates', [])
    by_id = {r['candidate_id']: r for r in extracted}
    if all(r['candidate_id'] in by_id and by_id[r['candidate_id']] == r for r in verified):
        observation['verified_candidate_ids'] = [r['candidate_id'] for r in verified]
        observation.pop('verified_candidates', None)
    applied = observation.get('applied', [])
    operations = observation.get('merge_operations', [])
    if operations and all(any(all(a.get(k) == v for k, v in op.items()) for a in applied) for op in operations):
        observation.pop('merge_operations')
        observation['merge_operations_recorded_in_applied'] = True
    # Never alter event IDs, dates, summaries, the active gap's factual description,
    # or any nonduplicate extracted candidate.
    assert result.get('student_visible_state') == payload.get('student_visible_state')
    assert result.get('cycle_observation', {}).get('all_extracted_candidates') == payload.get('cycle_observation', {}).get('all_extracted_candidates')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--samples', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    rows = []
    for line in args.samples.read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        payload = json.loads(row['messages'][1]['content'])
        if 'update GAP_MEMORY' not in payload.get('stage', ''):
            continue
        proposed = propose(payload)
        before, after = len(serialize(payload)), len(serialize(proposed))
        rows.append({'before_chars': before, 'after_chars': after,
                     'reduction': 1 - after / before})
    report = {'role': 'offline_proposal_not_deployed_or_api_tested', 'samples': rows, 'api_calls': 0,
        'event_views_unchanged': True, 'extracted_candidates_unchanged': True,
        'input_chars_before_total': sum(r['before_chars'] for r in rows),
        'input_chars_after_total': sum(r['after_chars'] for r in rows)}
    with args.output.open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, indent=2))
