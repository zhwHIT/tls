"""Read-only status of the authorized first-phase run; Gold never sent to policy."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'artifacts/beckham_joint_evidence_phase1_api'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    file = RUN / 'trajectory.json'
    if not file.exists():
        file = RUN / 'checkpoint.json'
    ledger = RUN / 'request_ledger.json'
    result = {'saved_state_exists': file.exists()}
    if ledger.exists():
        result['ledger'] = json.loads(ledger.read_text(encoding='utf-8'))
    if file.exists():
        s = json.loads(file.read_text(encoding='utf-8'))
        pool_file = RUN / 'candidate_pool.json'
        pool = s.get('candidate_pool', [])
        if file.name == 'trajectory.json' and pool_file.exists():
            pool = json.loads(pool_file.read_text(encoding='utf-8'))
        steps = s['steps']; verifies = [t for t in steps if t['action'] == 'VERIFY']
        queries = [q for t in steps if t['action'] == 'BATCH_RETRIEVAL' for q in t['observation']['queries']]
        gold = {d[:10] for d, _ in json.loads((ROOT / 'snapshots/entities/25ac73e52bc93b3b/David_Beckham/timelines.jsonl').read_text(encoding='utf-8'))}
        hits = sorted({e['time'] for e in s['final_events']} & gold)
        leads = s.get('final_exploration_memory', s.get('exploration_memory', {})).get('lead_queue', [])
        last_policy = next((t for t in reversed(steps) if t['action'] == 'SEARCH'), None)
        result.update(file=file.name, status=s['status'], error=s.get('error'), phase1_termination=s.get('phase1_termination'),
            completed_batches=len(s.get('query_batches', [])), queries=len(queries), events=len(s['final_events']),
            gold_hits=hits, gold_recall=len(hits)/len(gold), verify_calls=len(verifies),
            joint_verify_calls=sum(len(t['model_input']['tool_observation']['retrieved_documents'])>1 for t in verifies),
            targeted_queries=sum(bool(q.get('target_lead_ids')) for q in queries), leads=len(leads),
            leads_with_attempts=sum(bool(l.get('attempted_queries')) for l in leads),
            resolved_leads=sum(l['status']=='RESOLVED' for l in leads),
            supported_reassessments=sum(c['status']=='SUPPORTED' and bool(c.get('revises_candidate_ids')) for c in pool),
            last_step=steps[-1]['step_id'] if steps else None,
            last_queries=[q['query'] for q in queries[-3:]],
            last_policy_reason=last_policy['model_output']['reason'] if last_policy else None,
            usage=s['usage'])
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
