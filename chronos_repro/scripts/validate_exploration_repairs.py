"""Read-only historical regression checks; writes a separate report, never calls an API."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from chronos_repro.date_evidence import validate_date_evidence
from chronos_repro.tisa_rollout import next_open_gap
from analyze_exploration_rollout import audit_memory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    folder, output = Path(args.run_dir).resolve(), Path(args.output).resolve()
    if output == folder or folder in output.parents:
        raise ValueError('Write the repair report outside the preserved historical run directory')
    paths = [folder / name for name in ('trajectory.json', 'prediction.json', 'evaluation.json')]
    hashes = lambda: {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    before = hashes()
    trace = json.loads(paths[0].read_text(encoding='utf-8'))
    spotcheck = json.loads((folder / 'evidence_spotcheck.json').read_text(encoding='utf-8'))
    docs = {}
    for step in trace['steps']:
        for doc in step.get('observation', {}).get('documents', []):
            docs[str(doc['id'])] = doc
    results = []
    for case in spotcheck['cases']:
        doc = docs[case['document_id']]
        evidence = {'document_id': case['document_id'], 'quote': doc['text'],
                    'time_expression': case['source_time_expression']}
        try:
            validate_date_evidence(case['recorded_event_time'], evidence, [doc], [case['document_id']])
            accepted, reason = True, 'Literal date supported regardless of publication metadata'
        except ValueError as error:
            accepted, reason = False, str(error)
        expected = case['finding'] != 'unsupported_day_precision'
        results.append({'event_id': case['event_id'], 'accepted': accepted, 'expected_accepted': expected,
                        'passed': accepted == expected, 'reason': reason})
    gap_memory = trace['final_gap_memory']
    open_gaps = [g for g in gap_memory['gaps'] if g['status'] == 'OPEN']
    previous = max(open_gaps, key=lambda g: (g['priority'], g['gap_id']), default=None)
    selected = next_open_gap(gap_memory)
    report = {'schema': 'chronos-repro.exploration-repair-regression.v2', 'new_api_calls': 0,
              'historical_date_checks': results, 'historical_memory_audit': audit_memory(trace),
              'scheduler_on_same_final_state': {
                  'old_priority_only_next_gap': previous['gap_id'] if previous else None,
                  'new_attempt_balanced_next_gap': selected['gap_id'] if selected else None,
                  'selected_attempt_count': len(selected.get('attempted_queries', [])) if selected else None,
                  'note': 'Scheduling decision only; no hypothetical retrieval or coverage score'},
              'historical_hashes': before, 'historical_artifacts_unchanged': hashes() == before,
              'limitations': ['Two targeted date checks, not a full semantic audit',
                              'No real model validation or new coverage/cost result']}
    report['passed'] = (all(c['passed'] for c in results) and report['historical_artifacts_unchanged']
                        and report['historical_memory_audit']['memory_transitions_valid'])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
