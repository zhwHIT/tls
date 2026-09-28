"""Durable all-dataset first-phase scheduler; policy stays in the existing runner."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from chronos_repro.atomic_io import atomic_write_json
from chronos_repro.preextracted_events import ExtractedEventStore
from chronos_repro.retrieval import _bm25_path
from chronos_repro.run_guard import exclusive_run, source_binding
from chronos_repro.snapshot import sha256


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def preflight(project, suite):
    rows, seen = [], set()
    for item in suite['topics']:
        key = (item['dataset'], item['topic'])
        if key in seen:
            raise ValueError(f'Duplicate topic: {key}')
        seen.add(key)
        config_path = project / item['config']
        cfg = read(config_path)
        if not cfg['first_phase_only'] or not cfg['preextracted_events']['enabled']:
            raise ValueError('All topics must use pre-extracted first phase only')
        if (cfg['dataset'], cfg['topic']) != key:
            raise ValueError('Topic/config mismatch')
        if cfg.get('phase1_baseline') or cfg.get('phase1_supplement', {}).get('enabled'):
            raise ValueError('Baseline/supplement not allowed')
        event_path = project / cfg['preextracted_events']['event_root'] / key[0] / (key[1] + '_events.jsonl')
        store = ExtractedEventStore(event_path, *key, Path(cfg['data']).name)
        index = project / cfg['index']
        with sqlite3.connect(_bm25_path(index).as_uri() + '?mode=ro', uri=True) as con:
            article_rows = con.execute('select doc_id, timestamp from documents where topic=?', (key[1],)).fetchall()
        store.validate_index_inventory(article_rows)
        if not (project / cfg['data'] / key[1] / 'timelines.jsonl').is_file():
            raise ValueError(f'Missing post-run evaluation file: {key}')
        rows.append({**item, 'articles': store.source_record_count,
                     'returned_article_keys': len(store.articles),
                     'identical_output_aliases': dict(store.identical_output_aliases),
                     'config_sha256': sha256(config_path),
                     'event_store_sha256': store.sha256, 'index_manifest_sha256': sha256(index)})
    return {'api_calls': 0, 'topics': rows, 'topic_count': len(rows),
            'article_count': sum(r['articles'] for r in rows),
            'datasets': dict(Counter(r['dataset'] for r in rows))}


def summarize(project, suite):
    rows, requests, tokens = [], 0, 0
    for item in suite['topics']:
        output = project / item['output_dir']
        ledger = read(output / 'request_ledger.json', {})
        requests += ledger.get('requests_started', 0)
        evaluation = read(output / 'gold_date_coverage.json')
        if evaluation is None:
            continue
        trajectory = read(output / 'trajectory.json', {})
        tokens += evaluation.get('returned_response_usage_cumulative', {}).get('total_tokens', 0)
        rows.append({**{k: item[k] for k in ('dataset', 'topic', 'output_dir')},
            **{k: evaluation[k] for k in ('runtime_status', 'phase1_termination', 'gold_date_count',
                'matched_gold_date_count', 'gold_date_recall', 'completed_batches', 'query_count',
                'accepted_event_count', 'retrieved_extracted_date_hits', 'all_topic_extracted_date_hits',
                'deferred_verification_count', 'gold_start_exactly_matched', 'gold_end_exactly_matched')},
            'error': trajectory.get('error'), 'requests': ledger.get('requests_started', 0)})
    aggregates = {}
    for ds in sorted({r['dataset'] for r in suite['topics']}):
        valid = [r for r in rows if r['dataset'] == ds and r['runtime_status'] == 'ok']
        failed = [r for r in rows if r['dataset'] == ds and r['runtime_status'] != 'ok']
        denominator = sum(r['gold_date_count'] for r in valid)
        aggregates[ds] = {'planned_topics': sum(r['dataset'] == ds for r in suite['topics']),
            'finished_without_runtime_error': len(valid), 'partial_error_results': len(failed),
            'macro_gold_date_recall': sum(r['gold_date_recall'] for r in valid) / len(valid) if valid else None,
            'micro_gold_date_recall': sum(r['matched_gold_date_count'] for r in valid) / denominator if denominator else None,
            'termination_counts': dict(Counter(r['phase1_termination'] for r in valid))}
    return {'updated_at_utc': now(), 'scope': 'First phase only; original article ranking, extracted event results.',
        'metric': 'exact Gold date recall per topic (union of reference dates)',
        'aggregation': 'Macro averages topic recalls; micro divides summed hits by summed topic Gold counts. '
                       'Only runtime_status=ok enters aggregates; error results remain separate partial rows. '
                       'Runtime success does not establish autonomous search sufficiency.',
        'requests_started': requests, 'returned_tokens_finished_topics': tokens,
        'datasets': aggregates, 'rows': rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite-config', required=True)
    parser.add_argument('--output-root', required=True)
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--allow-api', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.workers <= 3:
        raise ValueError('Use 1-3 concurrent topic processes')
    project = Path(__file__).resolve().parents[1]
    suite_path = project / args.suite_config
    suite = read(suite_path)
    root = project / args.output_root
    with exclusive_run(root):
        audit = preflight(project, suite)
        atomic_write_json(root / 'preflight.json', audit)
        binding = {'source': source_binding(project), 'suite_sha256': sha256(suite_path), 'topics': audit['topics']}
        previous = read(root / 'suite_binding.json')
        if previous and previous != binding:
            raise ValueError('Suite inputs or runtime changed; use a new output directory')
        atomic_write_json(root / 'suite_binding.json', binding)
        if not args.allow_api:
            print(json.dumps({k: audit[k] for k in ('api_calls', 'topic_count', 'article_count', 'datasets')}), flush=True)
            return 0
        prior = read(root / 'suite_status.json', {})
        state = {'stage': 'running', 'pid': os.getpid(), 'started_at_utc': prior.get('started_at_utc', now()),
                 'resumed_at_utc': now() if prior else None, 'parallel_topics': args.workers,
                 'planned_topics': len(suite['topics']), 'completed': [], 'active': [], 'stop_reason': None}
        pending = list(suite['topics'])
        active = []
        stop_reason = None
        while pending or active:
            if (root / 'STOP').exists():
                stop_reason = stop_reason or 'user_stop'
            # Existing workers own their network gates. Avoid starting more topics
            # while a worker reports a transport outage.
            paused = any(read(r['output'] / 'network_status.json', {}).get('stage') == 'paused_network'
                         for r in active)
            while pending and len(active) < args.workers and not stop_reason and not paused:
                item = pending.pop(0)
                output = project / item['output_dir']
                trajectory = read(output / 'trajectory.json', {})
                if trajectory.get('status') == 'ok':
                    state['completed'].append({**item, 'returncode': 0, 'reused_completed_run': True})
                    continue
                ledger = read(output / 'request_ledger.json', {})
                if ledger.get('balance_stop'):
                    pending.insert(0, item)
                    stop_reason = 'insufficient_balance'
                    break
                output.mkdir(parents=True, exist_ok=True)
                if (output / 'STOP').exists():
                    state['completed'].append({**item, 'returncode': None, 'status': 'blocked_by_topic_stop'})
                    continue
                log = (output / 'run.log').open('a', encoding='utf-8')
                command = [sys.executable, '-u', str(project / 'scripts/run_tisa_two_phase_annotation.py'),
                    '--project-root', str(project), '--config', str(project / item['config']),
                    '--env-file', str(project / '.env'), '--output-dir', str(output), '--allow-api']
                process = subprocess.Popen(command, cwd=project, stdout=log, stderr=subprocess.STDOUT)
                active.append({'item': item, 'output': output, 'process': process, 'log': log})
                print(json.dumps({'started': item['topic'], 'dataset': item['dataset'], 'pid': process.pid}), flush=True)
            for row in list(active):
                code = row['process'].poll()
                if code is not None:
                    row['log'].close()
                    result = {**row['item'], 'returncode': code, 'finished_at_utc': now()}
                    state['completed'].append(result)
                    active.remove(row)
                    print(json.dumps(result), flush=True)
                    if code == 3:
                        stop_reason = 'insufficient_balance'
            if stop_reason:
                for row in active:
                    (row['output'] / 'STOP').touch()
            state['active'] = []
            for row in active:
                checkpoint = read(row['output'] / 'checkpoint.json', {}).get('state', {})
                state['active'].append({**row['item'], 'pid': row['process'].pid,
                    'completed_batches': len(checkpoint.get('query_batches', [])),
                    'queries': len(checkpoint.get('search_history', [])),
                    'events': len(checkpoint.get('timeline_events', [])),
                    'network': read(row['output'] / 'network_status.json', {}).get('stage')})
            state.update(updated_at_utc=now(), pending_topics=len(pending), stop_reason=stop_reason,
                         stage='stopping' if stop_reason else 'paused_network' if paused else 'running')
            atomic_write_json(root / 'suite_status.json', state)
            atomic_write_json(root / 'summary.json', summarize(project, suite))
            if stop_reason and not active:
                break
            if active:
                time.sleep(5)
        state.update(stage='stopped' if stop_reason else 'completed' if all(
            r['returncode'] == 0 for r in state['completed']) else 'completed_with_errors',
            finished_at_utc=now(), pending_topics=len(pending), stop_reason=stop_reason)
        atomic_write_json(root / 'suite_status.json', state)
        atomic_write_json(root / 'summary.json', summarize(project, suite))
        return 0 if state['stage'] == 'completed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
