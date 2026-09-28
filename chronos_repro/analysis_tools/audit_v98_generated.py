"""Read-only rollout review; save exact observed inputs, never change a live run."""
import collections
import datetime
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'artifacts/tisa_v98_resumed_and_unstarted'
DEST = ROOT / 'artifacts' / ('v98_review_' + datetime.datetime.now().strftime('%Y%m%dT%H%M%S'))
DEST.mkdir()
hashes = {}

def read(path):
    raw = path.read_bytes()
    relative = path.relative_to(ROOT)
    target = DEST / 'observed' / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    hashes[relative.as_posix()] = hashlib.sha256(raw).hexdigest()
    return json.loads(raw)

def stats(t):
    steps = t['steps']; events = t['final_events']
    verify = [s for s in steps if s['action'] == 'VERIFY']
    merges = [s for s in steps if s['action'] == 'MERGE']
    passages = [d['id'] for s in verify for d in s['model_input'].get('tool_observation', {}).get('retrieved_documents', [])]
    gaps = (t.get('final_gap_memory') or t.get('gap_memory') or {}).get('gaps', [])
    pair_examples = []
    for i, a in enumerate(events):
        for b in events[i + 1:]:
            if a['time'] == b['time']:
                score = difflib.SequenceMatcher(None, a['summary'].lower(), b['summary'].lower()).ratio()
                if score >= .78:
                    pair_examples.append({'a': a['event_id'], 'b': b['event_id'], 'date': a['time'],
                        'similarity': round(score, 3), 'summary_a': a['summary'], 'summary_b': b['summary']})
    batches = t.get('query_batches', [])
    memory = t.get('controller_memory') or {}
    return {'status': t['status'], 'events': len(events), 'dates': len({e['time'] for e in events}),
        'steps_by_action': dict(collections.Counter(s['action'] for s in steps)),
        'phase1_termination': t.get('phase1_termination'), 'phase2_termination': t.get('phase2_termination'),
        'verify': {'steps': len(verify), 'passage_visits': len(passages), 'unique_passages': len(set(passages)),
            'cache_hits': sum(bool(s['observation'].get('cache_reused')) for s in verify),
            'partial_repair_steps': sum(bool(s['model_output'].get('partial_repair_applied')) for s in verify),
            'repair_exhausted_steps': sum(bool(s['model_output'].get('repair_exhausted')) for s in verify),
            'candidates': sum(len(s['model_output'].get('candidates', [])) for s in verify),
            'passed_candidates': sum(len(s['observation'].get('passed_candidate_ids', [])) for s in verify)},
        'merge': {'steps':len(merges), 'local_duplicate_drops':sum(len(s['observation'].get('local_duplicate_filter', [])) for s in merges),
            'operations':dict(collections.Counter(o['operation'] for s in merges for o in s['model_output'].get('operations', [])))},
        'quarantine_count_including_inherited':len(t.get('verification_quarantine', [])),
        'incomplete_count_including_inherited':len(t.get('incomplete_extraction', [])),
        'gaps_by_status':dict(collections.Counter(g['status'] for g in gaps)),
        'gap_status_reasons':dict(collections.Counter(g.get('status_reason', '') for g in gaps)),
        'query_count_including_inherited':len(memory.get('query_history', [])),
        'batch_count_including_inherited':len(batches),
        'zero_change_batches':sum(b.get('event_change_count') == 0 for b in batches),
        'summary_facts':len(memory.get('facts', [])),
        'summary_distinct_support_events':len({e for f in memory.get('facts', []) for e in f.get('event_ids', [])}),
        'evidence_progress':t.get('evidence_progress'), 'similar_same_date_pairs_for_review':pair_examples,
        'usage':t.get('usage')}

status = read(RUN / 'batch/batch_status.json')
report = {'observed_at':datetime.datetime.now().astimezone().isoformat(),
          'batch_status':{k:status.get(k) for k in ['status','active_topic','completed_processes']}, 'topics':[]}
for row in status['plan']:
    folder = ROOT / row['output_dir']
    path = folder / 'trajectory.json'
    if not path.exists(): path = folder / 'checkpoint.json'
    if not path.exists(): continue
    t = read(path); result = {'dataset':row['dataset'], 'topic':row['topic'], 'snapshot_kind':path.name, **stats(t)}
    config = read(ROOT / row['config'])
    if config.get('continuation'):
        parent = ROOT / config['continuation']['snapshot']
        old = read(parent); result['parent'] = stats(old)
        old_ids = {e['event_id'] for e in old['final_events']}
        result['new_event_ids'] = [e['event_id'] for e in t['final_events'] if e['event_id'] not in old_ids]
        old_ledger = read(parent.parent / 'request_ledger.json')
        if (parent.parent / 'evaluation.json').exists(): result['parent_evaluation'] = read(parent.parent / 'evaluation.json')
    else: old_ledger = {}
    ledger = read(folder / 'request_ledger.json')
    result['new_http_attempts_at_ledger_read'] = {k:ledger.get(k,0)-old_ledger.get(k,0)
        for k in ['requests_started','verify_requests_started','budgeted_requests_started']}
    for name in ['evaluation.json','training_gate.json']:
        if (folder / name).exists():result[name] = read(folder / name)
    if (folder / 'token_usage_audit.json').exists():
        tokens = read(folder / 'token_usage_audit.json')['token_counts']; stages=collections.defaultdict(collections.Counter)
        for r in tokens:
            c=stages[r['stage']];c['http_attempts']+=r.get('http_attempts',0)
            for k in ['prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens']:
                c[k]+=(r.get('new_response_usage') or {}).get(k,0)
        result['token_stages'] = {k:dict(v) for k,v in stages.items()}
        result['max_estimated_input_tokens']=max((r.get('estimated_prompt_tokens',0) for r in tokens),default=0)
        result['max_recorded_input_tokens']=max((r.get('prompt_tokens') or 0 for r in tokens),default=0)
    report['topics'].append(result)
report['input_sha256']=hashes
report['limitations']=['Live files were read sequentially, not as one atomic batch snapshot.',
    'Parent state is included in event/gap counts but not in child step counters.',
    'Lexical similarity is a review lead, not proof of semantic duplication.',
    'No API calls were made by this audit; the authorized background runner is separate.']
(DEST/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(DEST)
for r in report['topics']:
    print(json.dumps({k:v for k,v in r.items() if k not in ['parent','parent_evaluation','evaluation.json','token_stages','similar_same_date_pairs_for_review']},ensure_ascii=False))
    print('similar pairs',len(r['similar_same_date_pairs_for_review']))
    if r.get('evaluation.json'):print('date_score',r['evaluation.json']['date_score'],'parent_date_score',r['parent_evaluation']['date_score'])
