"""Offline audit of the immutable, user-stopped first-phase checkpoint. No API."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from chronos_repro.date_evidence import validate_date_evidence


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    folder = ROOT / 'artifacts/beckham_joint_evidence_stopped_audit'
    manifest = read(folder / 'snapshot_manifest.json')
    assert all(digest(folder / name) == value for name, value in manifest['sha256'].items())
    trace = read(folder / 'checkpoint.json')
    config = read(folder / 'run_config.json')
    assert config['first_phase_only'] and not config.get('phase1_baseline')
    assert not config.get('phase1_supplement', {}).get('enabled')
    assert {s['phase'] for s in trace['steps']} == {'SKELETON_EXPLORATION'}
    gold_path = ROOT / config['data'] / config['topic'] / 'timelines.jsonl'
    gold = {d[:10]: summaries for d, summaries in read(gold_path)}
    assert len(gold) == 16
    events = trace['final_events']
    pool = trace['candidate_pool']
    leads = trace['exploration_memory']['lead_queue']
    steps = {s['step_id']: s for s in trace['steps']}
    verifies = [s for s in steps.values() if s['action'] == 'VERIFY']
    searches = [s for s in steps.values() if s['action'] == 'SEARCH']
    queries = [q for s in steps.values() if s['action'] == 'BATCH_RETRIEVAL'
               for q in s['observation']['queries']]
    passages = {p['id']: p for s in verifies for p in s['model_input']['tool_observation']['retrieved_documents']}
    passage_uses = Counter(p['id'] for s in verifies for p in s['model_input']['tool_observation']['retrieved_documents'])
    exposed = {l['lead_id'] for s in searches for l in s['model_input']['state'].get('pending_leads', [])}
    hits = sorted(set(gold) & {e['time'] for e in events})
    errors = []
    for event in events:
        try:
            validate_date_evidence(event['time'], event.get('date_evidence'), list(passages.values()), event['evidence_ids'])
        except (ValueError, KeyError) as error:
            errors.append({'event_id': event['event_id'], 'error': str(error)})

    definitions = [
        ('F01', ['event-005'], ['step-093'], 'different_occurrences_combined',
         '2001-02-27 captaincy report and 2004-11-15 Spain-friendly report were declared the same match. The Wednesday annotation also points backward to 2004-11-10 despite a forthcoming match.'),
        ('F02', ['event-020'], ['step-227'], 'future_weekday_and_preview_as_outcome',
         '2008-03-22 next-week preview was grounded to 2008-03-19; 2008-03-25 will-grant preview became won his 100th cap. Gold is 2008-03-26. No result evidence in this VERIFY input establishes occurrence.'),
        ('F03', ['event-010', 'event-011'], ['step-132'], 'partial_expression_and_wrong_temporal_role',
         'Wednesday week was reduced to Wednesday and normalized to 2008-01-23. It dates the Switzerland match, not the squad announcement. A separate saved event-011 has explicit today evidence for the omission on 2008-01-31, leaving inconsistent nodes.'),
        ('F04', ['event-019'], ['step-219'], 'future_weekday_backward',
         '2007-07-17 plans for a Saturday debut were dated 2007-07-14. The summary retains planned status, so this is not a proven completed appearance and must not be counted as the Gold 2007-07-21 appearance.'),
        ('F05', ['event-027'], ['step-336', 'step-338'], 'preview_modality_lost',
         'will not feature in today match was rewritten as did not feature. The date is grounded, but the cited preview does not establish the retrospective result.'),
        ('F06', ['event-015', 'event-022'], ['step-180', 'step-260', 'step-297'], 'date_conflict_split_then_duplicate',
         'MERGE first treated the 2001-10-05 and 2001-10-06 Greece qualification accounts as different developments. A later UPDATE corrected event-015 to 2001-10-06 without consolidating event-022. Semantically overlapping OPEN leads also survive.'),
    ]
    findings = []
    for fid, event_ids, step_ids, kind, assessment in definitions:
        linked = [l for l in leads if set(l.get('resolution_event_ids', [])) & set(event_ids)]
        findings.append({'id': fid, 'kind': kind, 'assessment': assessment,
                         'events': [e for e in events if e['event_id'] in event_ids],
                         'linked_resolved_leads': linked,
                         'trace_steps': [steps[i] for i in step_ids],
                         'source_passages': [p for pid, p in passages.items()
                             if any(pid in e['evidence_ids'] for e in events if e['event_id'] in event_ids)]})
    false_closed_events = {'event-005', 'event-010', 'event-020'}
    false_closed = [l for l in leads if l['status'] == 'RESOLVED'
                    and set(l.get('resolution_event_ids', [])) & false_closed_events]
    code = read(folder / 'code_binding.json')
    changed = [name for name, value in code['files'].items() if digest(ROOT / name) != value]
    report = {
        'status': 'USER_STOPPED', 'completed_first_phase': False,
        'stopped_at_utc': manifest['stopped_at_utc'], 'source_checkpoint_sha256': digest(folder / 'checkpoint.json'),
        'metric': 'exact_gold_date_coverage', 'gold_count': 16, 'matched_count': len(hits),
        'gold_date_recall': len(hits) / 16, 'matched_dates': hits,
        'per_gold_date': [{'date': d, 'hit': d in hits, 'gold_summaries': summaries,
                          'events': [e for e in events if e['time'] == d]} for d, summaries in sorted(gold.items())],
        'counts': {'completed_batches': len(trace['query_batches']), 'started_batches': len(searches),
                   'queries': len(queries), 'events': len(events), 'unique_event_dates': len({e['time'] for e in events}),
                   'verify_steps': len(verifies), 'joint_verify_steps': sum(len(s['model_input']['tool_observation']['retrieved_documents']) > 1 for s in verifies),
                   'candidate_status': dict(Counter(c['status'] for c in pool)),
                   'lead_status': dict(Counter(l['status'] for l in leads)), 'distinct_exposed_leads': len(exposed),
                   'unexposed_final_leads': sum(l['lead_id'] not in exposed for l in leads),
                   'leads_with_attempts': sum(bool(l['attempted_queries']) for l in leads),
                   'queries_bound_to_leads': sum(bool(q.get('target_lead_ids')) for q in queries),
                   'supported_reassessments': sum(c['status'] == 'SUPPORTED' and bool(c.get('revises_candidate_ids')) for c in pool),
                   'identified_false_closed_leads': len(false_closed),
                   'quarantined_candidates': len(trace['verification_quarantine']),
                   'incomplete_extraction_records': len(trace['incomplete_extraction'])},
        'findings': findings, 'identified_false_closed_leads': false_closed,
        'retirement_leads': [l for l in leads if 'retir' in l['summary'].lower()],
        'greece_leads': [l for l in leads if 'greece' in l['summary'].lower()],
        'most_repeated_verify_passages': [{'passage': passages[pid], 'verify_inputs': n} for pid, n in passage_uses.most_common(5)],
        'query_history': queries, 'evidence_progress': trace['evidence_progress'],
        'verification_quarantine': trace['verification_quarantine'],
        'checks': {'snapshot_hashes_unchanged': True, 'runtime_code_unchanged': not changed,
                   'changed_runtime_files': changed, 'date_structure_errors': errors,
                   'all_events_have_time_expression': all(bool(e.get('date_evidence', {}).get('time_expression')) for e in events),
                   'all_search_leads_have_reason': all(bool(l.get('reason')) for s in searches for l in s['model_input']['state'].get('pending_leads', [])),
                   'candidate_missing_fields_count': sum('missing_fields' in c for c in pool)},
        'usage': trace['usage'], 'request_ledger': read(folder / 'request_ledger.json'),
        'limitations': ['User-stopped partial result, not a completed first-phase evaluation.',
                       'Structural validation does not establish semantic correctness.',
                       'Findings are evidence-backed examples, not an exhaustive error rate.',
                       'Incomplete extraction records can overlap and are not necessarily outstanding failures.',
                       'No event dates, candidates, or source artifacts were corrected by this audit.']}
    (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ['status', 'matched_count', 'gold_date_recall', 'matched_dates', 'counts', 'checks', 'usage', 'request_ledger']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
