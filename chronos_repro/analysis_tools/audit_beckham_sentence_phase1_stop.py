"""Read-only diagnosis of a user-stopped first-phase run; no search or API calls."""
import hashlib
import json
import re
import shutil
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'artifacts/beckham_sentence_queue_phase1_api'
OUT = ROOT / 'artifacts/beckham_sentence_queue_stopped_audit'
sys.path.insert(0, str(ROOT / 'src'))
from chronos_repro.evaluate import evaluate_dates

# Previously located corpus evidence/lead documents, NOT exhaustive event qrels.
KNOWN = {
    '1995-04-02': ['20843'], '2000-11-15': ['20522', '19699'],
    '2003-11-27': ['20143'], '2005-01-10': ['19578', '18282'],
    '2005-08-03': ['19767', '19763'], '2006-03-09': ['19634'],
    '2007-07-21': ['19197'], '2008-03-26': ['19367', '19391', '19069'],
    '2010-03-14': ['18743'], '2012-12-01': ['18460', '18474', '18462', '18422'],
    '2013-01-31': ['18446', '18442'], '2013-05-16': ['18406', '18348', '18349'],
    '2014-02-05': ['18307', '18342'], '2015-02-09': ['18282'],
    '2015-11-18': [], '2018-01-29': [],
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(exist_ok=True)
    for name in ['checkpoint.json', 'request_ledger.json', 'run_config.json']:
        target = OUT / ('stopped_' + name)
        if target.exists():
            assert sha(target) == sha(RUN / name), 'Stopped source changed; do not overwrite the audit snapshot'
        else:
            shutil.copy2(RUN / name, target)
    checkpoint = OUT / 'stopped_checkpoint.json'
    state = json.loads(checkpoint.read_text(encoding='utf-8'))
    ledger = json.loads((OUT / 'stopped_request_ledger.json').read_text(encoding='utf-8'))
    gold_path = ROOT / 'snapshots/entities/25ac73e52bc93b3b/David_Beckham/timelines.jsonl'
    gold = json.loads(gold_path.read_text(encoding='utf-8'))
    refs = {date.fromisoformat(d[:10]): tuple(s) for d, s in gold}
    events = state['final_events']
    pred = {date.fromisoformat(e['time']): (e['summary'],) for e in events}
    metric = evaluate_dates(pred, [refs])
    assert len(refs) == 16
    steps = state['steps']
    assert {s['phase'] for s in steps} == {'SKELETON_EXPLORATION'}
    queries = [dict(q, query_number=i) for i, q in enumerate(
        [q for s in steps if s['action'] == 'BATCH_RETRIEVAL' for q in s['observation']['queries']], 1)]
    verifies = [s for s in steps if s['action'] == 'VERIFY']
    passages = [p for s in verifies for p in s['model_input']['tool_observation']['retrieved_documents']]
    seen = {p['id']: p for p in passages}
    selected_ids = {i for q in queries for i in q['passage_ids']}
    candidates = state['candidate_pool']
    with sqlite3.connect((ROOT / 'artifacts/entities_25ac73e52bc93b3b.sqlite3').as_uri() + '?mode=ro', uri=True) as con:
        docs = {str(i): {'id': str(i), 'title': t, 'text': s, 'publication_date': dt}
                for i, t, s, dt in con.execute('SELECT doc_id,title,text,timestamp FROM documents WHERE topic=?', ('David_Beckham',))}
    for p in passages:
        assert docs[p['document_id']]['text'][p['source_start']:p['source_end']] == p['text']
        assert p.get('context_before') == ''
    rows = []
    for day, summaries in sorted(refs.items()):
        day = day.isoformat()
        sources = []
        for did in KNOWN[day]:
            sources.append({'document_id': did, 'title': docs[did]['title'], 'publication_date': docs[did]['publication_date'],
                            'retrieved_at_queries': [q['query_number'] for q in queries if did in map(str, q['result_ids'])],
                            'selected_at_queries': [q['query_number'] for q in queries if any(i.startswith(did+'@') for i in q['passage_ids'])],
                            'verify_steps': [s['step_id'] for s in verifies if any(p['document_id'] == did for p in s['model_input']['tool_observation']['retrieved_documents'])]})
        rows.append({'date': day, 'gold_summaries': list(summaries), 'hit': any(e['time'] == day for e in events),
                     'predicted_events': [e for e in events if e['time'] == day],
                     'exact_date_candidates': [c for c in candidates if c['event']['time'] == day],
                     'checked_reference_documents': sources})
    policies = [s for s in steps if s['action'] == 'SEARCH']
    leads = state['exploration_memory']['lead_queue']
    retirement = [{'queue_position_1based': i, **l} for i, l in enumerate(leads, 1) if 'retir' in l['summary'].lower()]
    lead_exposures = [{'step_id': s['step_id'], 'lead_ids': [l['lead_id'] for l in s['model_input']['state']['pending_leads']],
                      'leads': s['model_input']['state']['pending_leads']} for s in policies]
    query_counts = {name: sum(bool(re.search(pattern, q['query'], re.I)) for q in queries) for name, pattern in {
        'galaxy': r'Galaxy', 'real_madrid': r'Real Madrid|Mijatovic',
        'galaxy_final_or_last': r'(?:final|last).*(?:Galaxy|MLS)|Galaxy.*(?:final|last)',
        'galaxy_final_with_2011': r'(?=.*(?:last|final))(?=.*Galaxy)(?=.*2011)',
        'retirement': r'retir', 'hundredth': r'100th|hundredth', 'unicef': r'unicef',
        'obe': r'\bobe\b', 'achilles': r'achilles', 'psg': r'paris|psg', 'miami': r'miami',
    }.items()}
    examples = []
    for s in verifies:
        ps = s['model_input']['tool_observation']['retrieved_documents']
        if any(p['document_id'] in {'18349', '18460', '18474', '20843'} for p in ps):
            examples.append({'step_id': s['step_id'], 'passages': ps, 'output': s['model_output']})
    source_binding = json.loads((RUN / 'code_binding.json').read_text(encoding='utf-8'))
    code_unchanged = all(sha(ROOT / rel) == digest for rel, digest in source_binding['files'].items())
    report = {'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'status': 'stopped_by_user',
              'audit_api_calls': 0, 'completed_first_phase': False, 'source_checkpoint_sha256': sha(checkpoint),
              'source_gold_sha256': sha(gold_path), 'metric': 'gold_date_recall',
              'gold_date_count': len(refs), 'matched_gold_dates': [r['date'] for r in rows if r['hit']],
              'matched_gold_count': sum(r['hit'] for r in rows), 'gold_date_recall': metric.recall,
              'per_gold_date': rows, 'counts': {
                  'recorded_search_batches': sum(s['action'] == 'BATCH_RETRIEVAL' for s in steps),
                  'completed_search_batches': len(state.get('query_batches', [])), 'queries': len(queries),
                  'retrieved_documents': len({str(i) for q in queries for i in q['result_ids']}),
                  'selected_unique_passages': len(selected_ids), 'verified_unique_passages': len(seen),
                  'selected_not_yet_verified': len(selected_ids-set(seen)),
                  'verified_documents': len({p['document_id'] for p in passages}),
                  'verify_steps': len(verifies), 'candidates': len(candidates),
                  'candidate_status': dict(Counter(c['status'] for c in candidates)),
                  'events': len(events), 'unique_event_dates': len(pred),
                  'verify_empty_candidates': sum(not s['model_output']['candidates'] for s in verifies),
                  'passages_without_annotations': sum(not p.get('temporal_annotations') for p in passages),
                  'passages_under_80_characters': sum(len(p['text']) < 80 for p in passages),
                  'median_passage_characters': statistics.median(len(p['text']) for p in passages),
                  'lead_queue_size': len(leads), 'leads_with_attempted_queries': sum(bool(l.get('attempted_queries')) for l in leads)},
              'query_keyword_counts_nonexclusive': query_counts, 'retirement_leads': retirement,
              'retirement_policy_exposure_steps': [x['step_id'] for x in lead_exposures if any('retir' in l['summary'].lower() for l in x['leads'])],
              'lead_exposures': lead_exposures, 'usage_completed_in_checkpoint': state['usage'],
              'http_requests_started': ledger['requests_started'],
              'verify_requests_started': ledger['verify_requests_started'],
              'nonverify_requests_started': ledger['budgeted_requests_started'],
              'requests_started_not_accounted_in_checkpoint': ledger['requests_started']-state['usage']['http_attempts'],
              'checks': {'source_offsets_equal_frozen_body': True, 'runtime_source_unchanged': code_unchanged,
                         'single_passage_per_verify': all(len(s['model_input']['tool_observation']['retrieved_documents']) == 1 for s in verifies),
                         'no_supplement_or_second_phase': True},
              'limitations': ['Stopped midway through batch 12; not a completed phase or autonomous STOP.',
                  'Known reference documents are diagnostic leads, not exhaustive qrels; missing those documents does not prove no equivalent evidence exists.',
                  'One HTTP request started beyond the last committed checkpoint; cost/response is not inferred.',
                  'The online in-memory selection history and unread queue were not written before the forced stop. Selected and read intervals are reconstructed from saved query and VERIFY steps.',
                  'Query-group counts are non-exclusive lexical diagnostics, not a gold-based retrieval policy.']}
    assert report['matched_gold_dates'] == ['1995-04-02']
    (OUT / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / 'evidence_examples.json').write_text(json.dumps(examples, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / 'queries.json').write_text(json.dumps(queries, ensure_ascii=False, indent=2), encoding='utf-8')
    stop = {'status': 'stopped_by_user', 'process_id': 42480, 'source_checkpoint_sha256': sha(checkpoint),
            'recorded_at_utc': report['observed_at_utc'], 'requests_started': ledger['requests_started'],
            'gold_date_recall_at_saved_checkpoint': metric.recall, 'matched_gold_dates': report['matched_gold_dates'],
            'note': 'Process was explicitly stopped at user request; source checkpoint/ledger are preserved, not relabelled as a completed trajectory.'}
    (RUN / 'user_stop.json').write_text(json.dumps(stop, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ['status','matched_gold_dates','gold_date_recall','counts','query_keyword_counts_nonexclusive','retirement_policy_exposure_steps','checks']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
