"""Offline continuation-aware cost and date-loss diagnostic; no API calls."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from chronos_repro.data import iter_topics
from chronos_repro.date_evidence import explicit_dates


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def analyze(project):
    root = project / 'artifacts/tisa_v95_continuation'
    evaluation = read(root / 'batch/evaluation_after_09.json')
    config = read(root / 'haiti/run_config.json')
    gold = {t.topic_id: {d.isoformat() for timeline in t.timelines for d in timeline}
            for t in iter_topics(project / config['data'])}
    rows = []
    for result in evaluation['topics']:
        topic = result['topic']
        current = read(root / topic / 'trajectory.json')
        parent = read(Path(current['continuation']['parent_snapshot']))
        steps = parent['steps'] + current['steps']
        passages = {}
        candidates = []
        for step in steps:
            if step['action'] == 'VERIFY':
                docs = step['model_input'].get('tool_observation', {}).get('retrieved_documents', [])
                passages.update({p['id']: p for p in docs})
                candidates.extend(step.get('model_output', {}).get('candidates', []))
        dates = set()
        for passage in passages.values():
            dates.update(explicit_dates(' '.join(str(passage.get(k, '')) for k in
                                                ('title', 'context_before', 'text'))))
            dates.update(a['date'] for a in passage.get('temporal_annotations', []))
        accepted = {c['event'].get('time') for c in candidates
                    if c['status'] in {'SUPPORTED', 'CONFLICTED'}
                    and c.get('relevance_pass') and c.get('contribution_pass')}
        final = {e['time'] for e in current['final_events']}
        old_dates = {e['time'] for e in parent['final_events']}
        reference = gold[topic]
        attempts, logical, retries = Counter(), Counter(), Counter()
        for audit in current['audits']:
            stage = audit['stage']
            logical[stage] += 1
            attempts[stage] += sum(a.get('attempts', 0) for a in audit.get('attempts', []))
            retries[stage] += max(0, sum(bool(a.get('attempts', 0)) for a in audit.get('attempts', [])) - 1)
        batches = current.get('query_batches', [])
        gap_memory = current.get('final_gap_memory') or {}
        gaps = gap_memory.get('gaps', [])
        searches = [s for s in current['steps'] if s['action'] == 'BATCH_RETRIEVAL']
        statuses = Counter(c['status'] for c in candidates)
        missing_reasons = Counter(c.get('reason', '') for c in candidates if c['status'] == 'INSUFFICIENT')
        no_gain = [b['batch_id'] for b in batches if b['event_change_count'] == 0]
        ledger = read(root / topic / 'request_ledger.json')
        known_attempts = sum(attempts.values())
        new_attempts = ledger['requests_started'] - current['continuation']['parent_requests']
        rows.append({
            'topic': topic, 'status': current['status'], 'new_requests': new_attempts,
            'audited_http_attempts_by_stage': dict(attempts),
            'requests_without_returned_audit': new_attempts - known_attempts,
            'logical_calls_by_stage': dict(logical), 'label_repair_responses_by_stage': dict(retries),
            'gold_dates': len(reference), 'parent_gold_hits': len(old_dates & reference),
            'current_gold_hits': len(final & reference),
            'gold_hits_added': sorted((final - old_dates) & reference),
            'gold_hits_removed': sorted((old_dates - final) & reference),
            'unique_verified_passages_across_parent_and_child': len(passages),
            'visible_date_signals_gold_hits': len(dates & reference),
            'accepted_candidate_gold_hits': len(accepted & reference),
            'date_signals_not_accepted': sorted((dates & reference) - accepted),
            'accepted_dates_not_in_final': sorted((accepted & reference) - final),
            'missing_dates_without_visible_signal': sorted(reference - final - dates),
            'candidate_status_occurrences': dict(statuses),
            'insufficient_reason_examples': missing_reasons.most_common(8),
            'completed_batches': len(batches), 'zero_event_change_batches': no_gain,
            'last_six_batch_changes': [b['event_change_count'] for b in batches[-6:]],
            'continuation_selected_passages_per_batch': [s['observation']['distinct_passages'] for s in searches],
            'gap_status_counts': dict(Counter(g['status'] for g in gaps)),
            'completed_gap_search_batches': sum(g.get('search_batches', 0) for g in gaps),
            'gap_attempts_over_configured_limit': [g['gap_id'] for g in gaps if len(g.get('attempted_queries', [])) > 3],
            'gap_status_reasons': dict(Counter(g.get('status_reason', '') for g in gaps)),
            'phase1_termination': current.get('phase1_termination'),
            'phase2_termination': current.get('phase2_termination'),
            'gap_questions': [{'id': g['gap_id'], 'status': g['status'],
                               'question': g['retrieval_target']['question']} for g in gaps],
        })
    return {'api_calls': 0, 'topics': rows, 'limitations': [
        'Date expressions are diagnostic signals, not proof of a relevant event.',
        'Parent plus continuation audits are combined for date loss, child audits only for new cost.',
        'Haiti parent uses checkpoint; uncommitted work may be replayed and candidate occurrences can repeat.',
        'Missing visible signals do not establish corpus absence or retrieval impossibility.',
        'Gold is read only for this offline diagnostic; never fed to policy or gap discovery.',
    ]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.project_root)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    for row in report['topics']:
        print(json.dumps({k: row[k] for k in (
            'topic', 'new_requests', 'parent_gold_hits', 'current_gold_hits',
            'visible_date_signals_gold_hits', 'accepted_candidate_gold_hits',
            'accepted_dates_not_in_final', 'last_six_batch_changes',
            'completed_gap_search_batches', 'gap_attempts_over_configured_limit',
            'audited_http_attempts_by_stage', 'requests_without_returned_audit')}, ensure_ascii=False))
