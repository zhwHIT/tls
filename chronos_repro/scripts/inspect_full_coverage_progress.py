"""Read-only progress inspection, including persisted request counts (no API)."""
import argparse
import json
from collections import Counter
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', required=True)
    args = parser.parse_args()
    folder = Path(args.run_dir)
    def read(name, default):
        path = folder / name
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default
    final = read('trajectory.json', {})
    checkpoint = read('checkpoint.json', {})
    # A restart may leave a historical terminal trace while a newer checkpoint advances.
    final_path, checkpoint_path = folder / 'trajectory.json', folder / 'checkpoint.json'
    newer_checkpoint = checkpoint and (not final or checkpoint_path.stat().st_mtime > final_path.stat().st_mtime)
    trace = checkpoint if newer_checkpoint else final
    events = trace.get('final_events', [])
    steps = trace.get('steps', [])
    print(json.dumps({'status': trace.get('status', 'preflight_or_starting'),
        'error': trace.get('error'), 'steps': len(steps), 'events': len(events),
        'distinct_dates': len({e['time'] for e in events}),
        'last_step': {'phase': steps[-1]['phase'], 'action': steps[-1]['action']} if steps else None,
        'searches_by_phase': dict(Counter(s['phase'] for s in steps if s['action'] == 'SEARCH')),
        'ledger': read('request_ledger.json', {}),
        'cached_responses': len(list((folder / 'api_cache').glob('*.json'))),
        'usage': trace.get('usage'), 'evidence_progress': trace.get('evidence_progress'),
        'unresolved_leads': trace.get('exploration_memory', trace.get('final_exploration_memory', {})).get('unresolved_lead_count'),
        'note': 'Checkpoint may lag the in-flight batch; status does not establish semantic or date coverage.'},
        ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
