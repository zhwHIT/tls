"""Read-only stage reconstruction for the authorized Beckham diagnostic."""
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'artifacts/tisa_v10_lowest_phase1_api'
OUT = RUN / 'entities_David_Beckham'

def read(path):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()

def main():
    path = OUT / 'trajectory.json'
    if not path.exists():
        path = OUT / 'checkpoint.json'
    trace, sha = read(path)
    baseline, base_sha = read(RUN / 'David_Beckham_phase1_baseline.json')
    reference, _ = read(ROOT / 'artifacts/phase1_span_audit_20260917/report.json')
    gold = set(next(r for r in reference['rows'] if r['topic'] == 'David_Beckham')['gold_dates'])
    def metric(events):
        dates = {e['time'] for e in events}
        hits = sorted(dates & gold)
        return {'events': len(events), 'gold_dates': len(gold), 'hit_dates': hits,
                'hits': len(hits), 'date_recall': len(hits)/len(gold),
                'missing_dates': sorted(gold-dates)}
    events = {e['event_id']: copy.deepcopy(e) for e in baseline['events']}
    supplement = None
    for step in trace['steps']:
        if step['phase'] == 'GAP_FILLING':
            break
        if step['action'] == 'MERGE':
            candidates = {c['candidate_id']: c for c in step['model_input'].get('tool_observation', {}).get('verified_candidates', [])}
            obs = step.get('observation', {})
            fusions = {f['merged_event']['event_id']: f['merged_event'] for f in obs.get('update_fusions', [])}
            for op in obs.get('applied', []):
                action = op['applied_operation']
                if action == 'APPEND':
                    events[op['event_id']] = {**copy.deepcopy(candidates[op['candidate_id']]['event']), 'event_id': op['event_id']}
                elif action == 'UPDATE':
                    events[op['event_id']] = copy.deepcopy(fusions[op['event_id']])
        if step['phase'] == 'PHASE1_SUPPLEMENT' and step['action'] == 'COMPLETE':
            supplement = metric(list(events.values()))
            assert supplement['events'] == step['model_output']['event_count']
            break
    scope_errors = []
    children = []
    record = trace.get('phase1_supplement', {})
    intervals = {r['interval_id']: r for r in record.get('intervals', [])}
    for key, child in record.get('children', {}).items():
        scope = intervals[key]
        local_events = child['state']['timeline_events']
        scope_errors.extend({'interval': key, 'event_id': e['event_id'], 'time': e['time']} for e in local_events if not scope['start'] <= e['time'] <= scope['end'])
        children.append({'interval': key, 'start': scope['start'], 'end': scope['end'],
                         'complete': child.get('complete', False), 'events': len(local_events),
                         'batches': len(child['state'].get('query_batches', []))})
    ledger, _ = read(OUT / 'request_ledger.json')
    report = {'observed_at_utc': datetime.now(timezone.utc).isoformat(),
              'source': str(path), 'source_sha256': sha, 'baseline_sha256': base_sha,
              'status': trace['status'], 'phase1_baseline': metric(baseline['events']),
              'phase1_supplement_end': supplement,
              'latest_root_timeline': metric(trace['final_events']),
              'children': children, 'interval_event_scope_violations': scope_errors,
              'ledger': ledger, 'training_ready': False,
              'interpretation': 'Exact Gold date overlap only; single-topic micro equals macro. Gold-selected diagnostic, not a paired no-regression test. Running snapshot is not final.'}
    dest = RUN / ('stage_audit_final.json' if path.name == 'trajectory.json' else 'stage_audit_live.json')
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))

if __name__ == '__main__':
    main()
