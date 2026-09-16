"""Observe current-source checkpoints without mistaking old trajectories for progress."""
from collections import Counter
import json
from pathlib import Path
import sys

from chronos_repro.data import iter_topics

root = Path(__file__).resolve().parents[1]
batch = json.loads((root / sys.argv[1] / 'batch_status.json').read_text(encoding='utf-8'))
rows = []
for item in batch['plan']:
    output = root / item['output_dir']
    ledger_path = output / 'request_ledger.json'
    ledger = json.loads(ledger_path.read_text(encoding='utf-8')) if ledger_path.exists() else {}
    row = {'topic': item['topic'], 'http_requests_total': ledger.get('requests_started', 0),
           'balance_stop': ledger.get('balance_stop', False)}
    matched = []
    for name in ('trajectory.json', 'checkpoint.json'):
        path = output / name
        if path.exists():
            trace = json.loads(path.read_text(encoding='utf-8'))
            if trace.get('source_sha256') == batch['source_sha256']:
                matched.append((path.stat().st_mtime, name, trace))
    if matched:
        _, name, trace = max(matched)
        config = json.loads((root / item['config']).read_text(encoding='utf-8'))
        topic = next(t for t in iter_topics(root / config['data']) if t.topic_id == item['topic'])
        gold = {d.isoformat() for reference in topic.timelines for d in reference}
        predicted = {e['time'] for e in trace['final_events']}
        row.update(state_file=name, status=trace['status'], steps=len(trace['steps']),
            last_action=trace['steps'][-1]['phase'] + ':' + trace['steps'][-1]['action'] if trace['steps'] else None,
            searches=sum(s['action'] == 'SEARCH' for s in trace['steps']), events=len(trace['final_events']),
            predicted_dates=len(predicted), gold_hits=len(gold & predicted), gold_count=len(gold),
            recall=len(gold & predicted)/len(gold), error=trace.get('error'),
            date_grounding=dict(Counter(e.get('date_evidence', {}).get('normalization', 'other') for e in trace['final_events'])))
    else:
        row['status'] = 'no_checkpoint_for_current_source_yet'
    rows.append(row)
print(json.dumps({'batch_status': batch['status'], 'active_topic': batch.get('active_topic'), 'topics': rows,
                  'api_calls_by_this_observer': 0}, ensure_ascii=False, indent=2))
