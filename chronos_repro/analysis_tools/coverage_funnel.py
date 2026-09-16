"""Read-only rollout analysis; Gold is loaded only here, never sent to a model."""
import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path

from chronos_repro.data import iter_topics
from chronos_repro.date_evidence import explicit_dates


def analyze(project, config_path, trajectory_path):
    config = json.loads(config_path.read_text(encoding='utf-8'))
    topic = next(t for t in iter_topics(project / config['data']) if t.topic_id == config['topic'])
    gold = {d.isoformat() for timeline in topic.timelines for d in timeline}
    trace = json.loads(trajectory_path.read_text(encoding='utf-8'))
    steps = trace.get('steps', [])
    verified = [row for step in steps if step['action'] == 'VERIFY'
                for row in step.get('model_output', {}).get('candidates', [])]
    selected = {}
    for step in steps:
        if step['action'] == 'SEARCH':
            for doc in step.get('observation', {}).get('documents', []):
                selected[doc['id']] = doc
    literal = set()
    publication = set()
    annotated = set()
    for doc in selected.values():
        literal |= {d for d in explicit_dates(str(doc.get('title', '')) + ' ' +
                    str(doc.get('context_before', '')) + str(doc.get('text', ''))) if len(d) == 10}
        publication.add(str(doc.get('publication_date', ''))[:10])
        annotated.update(a['date'] for a in doc.get('temporal_annotations', []))
    all_dates = {r['event'].get('time') for r in verified} - {None}
    passed = [r for r in verified if r['status'] in {'SUPPORTED', 'CONFLICTED'}
              and r.get('relevance_pass') and r.get('contribution_pass')]
    accepted_dates = {r['event'].get('time') for r in passed} - {None}
    final = {e['time'] for e in trace.get('final_events', [])}
    def counts(values):
        values = set(values)
        hits = len(values & gold)
        precision = hits / len(values) if values else 0.0
        recall = hits / len(gold) if gold else 0.0
        return {'count': len(values), 'gold_date_hits': len(values & gold),
                'gold_date_count': len(gold), 'gold_date_recall': recall,
                'gold_date_precision': precision,
                'gold_date_f1': 2 * precision * recall / (precision + recall) if precision + recall else 0.0}
    reasons = Counter(str(r.get('reason', r.get('verification_reason', ''))) for r in verified if r['status'] == 'INSUFFICIENT')
    queries = [{'phase': s['phase'], 'query': s.get('model_output', {}).get('query'),
                'passages': len(s.get('observation', {}).get('documents', []))}
               for s in steps if s['action'] == 'SEARCH']
    merges = Counter(op['operation'] for s in steps if s['action'] == 'MERGE'
                     for op in s.get('model_output', {}).get('operations', []))
    return {'dataset': config['dataset'], 'topic': config['topic'], 'trace_path': str(trajectory_path),
        'status': trace['status'], 'steps': len(steps), 'source_sha256': trace.get('source_sha256'),
        'phase_actions': dict(Counter(s['phase'] + ':' + s['action'] for s in steps)),
        'selected_passages': len(selected), 'selected_literal_dates': counts(literal),
        'selected_annotation_dates': counts(annotated),
        'selected_publication_dates': counts(publication), 'candidate_dates': counts(all_dates),
        'verified_dates': counts(accepted_dates), 'final_dates': counts(final),
        'candidate_status_counts': dict(Counter(r['status'] for r in verified)),
        'candidate_date_precision': dict(Counter('none' if not r['event'].get('time') else
            'day' if len(r['event']['time']) == 10 else 'partial' for r in verified)),
        'insufficient_reason_counts': dict(reasons.most_common(15)), 'merge_operations': dict(merges),
        'verified_gold_dates_missing_from_final': sorted((gold & accepted_dates) - final),
        'gold_dates_literally_seen_not_verified': sorted((gold & literal) - accepted_dates),
        'gold_dates_annotated_not_verified': sorted((gold & annotated) - accepted_dates),
        'queries': queries, 'usage': trace.get('usage'), 'error': trace.get('error'),
        'limits': ['Literal date occurrence is not evidence of an event or its relevance',
                   'Publication dates are diagnostic only, not event dates',
                   'Incomplete trajectories must not be reported as completed runs',
                   'This offline diagnostic uses inspected Gold and is not an untouched-test evaluation'],
        'api_calls': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--trajectory', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = analyze(args.project_root, args.config, args.trajectory)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        with args.output.open('x', encoding='utf-8') as target:
            target.write(text + '\n')
    print(text)
