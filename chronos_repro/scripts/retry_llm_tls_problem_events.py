"""One audited retry of explicitly selected rejected/parse-error requests.

Prompts and model settings stay unchanged. Only parse-clean responses replace
existing results. Rejected requests are never rewritten to evade rejection.
Pass the same --batch path to recover saved attempts without calling again.
"""
import argparse
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import extract_llm_tls_events as e
import llm_tls_network_pause as n


def selected(row):
    return (row['status'] == 'error' and row['error'] == 'ContentRejectedError') or (
        row['status'] == 'done' and
        json.loads(row['result'] or '{}').get('parse_status') == 'parse_error')


def apply_attempt(con, output, original, attempt):
    result = attempt.get('result')
    if not result or result['parse_status'] not in ('extracted', 'no_events'):
        return False
    job_id = original['job_id']
    current = con.execute('select status,result,error from jobs where job_id=?', (job_id,)).fetchone()
    if current['result'] == e.dumps(result) and current['status'] == 'done':
        return True  # Already committed by this batch before a restart.
    if any(current[k] != original[k] for k in ('status', 'result', 'error')):
        raise RuntimeError('Selected job changed since retry snapshot')
    cache = output / 'responses' / job_id[:2] / (job_id + '.json')
    cache.parent.mkdir(parents=True, exist_ok=True)
    e.atomic_write_json(cache, result)
    con.execute("update jobs set status='done',result=?,error=null where job_id=?",
                (e.dumps(result), job_id))
    con.commit()
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', required=True, type=Path)
    args = parser.parse_args()
    cfg = e.read_config(e.PROJECT / 'configs/llm_tls_extraction_v1.json')
    output = Path(cfg['output'])
    batch = args.batch.resolve()
    batch.mkdir(parents=True, exist_ok=True)
    if (output / 'suite.lock').exists() or (output / 'STOP').exists():
        raise RuntimeError('Active supervisor or STOP file: do not start a retry batch')
    lock = output / 'runner.lock'
    with lock.open('x', encoding='utf-8') as f:
        f.write(e.dumps({'pid': os.getpid(), 'batch': str(batch), 'started_at_utc': e.now()}))
    con, gate = None, None
    try:
        con = e.connect(output)
        _, _, _, upstream = e.load_upstream(cfg['upstream'])
        e.check_identity(con, e.identity(cfg, upstream))
        manifest = batch / 'original_jobs.json'
        if manifest.exists():
            rows = json.loads(manifest.read_text(encoding='utf-8'))
        else:
            rows = [dict(r) for r in con.execute("""select * from jobs where
                (status='error' and error='ContentRejectedError') or
                (status='done' and json_extract(result,'$.parse_status')='parse_error')""")]
            for row in rows:
                cache = output / 'responses' / row['job_id'][:2] / (row['job_id'] + '.json')
                if cache.exists():
                    shutil.copy2(cache, batch / (row['job_id'] + '.original.json'))
            e.atomic_write_json(manifest, rows)
        print(e.dumps({'selected': len(rows), 'batch': str(batch)}), flush=True)
        n.configure_direct_transport()
        e.load_env_file(cfg['env_file'])
        gate = n.NetworkGate(output, cfg['base_url'])
        client = n.guarded_client_class(gate)(
            model=cfg['model'], base_url=cfg['base_url'], api_key_env=cfg['api_key_env'],
            timeout=cfg['timeout'], max_retries=cfg['max_retries'],
            request_options={'thinking': {'type': 'disabled'}, 'max_tokens': cfg['max_tokens']})

        def request(row):
            path = batch / (row['job_id'] + '.attempt.json')
            if path.exists():
                return json.loads(path.read_text(encoding='utf-8'))
            attempt = {'job_id': row['job_id'], 'started_at_utc': e.now(),
                       'messages_sha256': e.digest(row['messages']), 'request_unchanged': True}
            try:
                response = client.chat(json.loads(row['messages']), temperature=cfg['temperature'])
                events, errors, status = e.parse_events(response.text, row['job_id'])
                attempt['result'] = {
                    'job_id': row['job_id'], 'raw_response': response.text, 'events': events,
                    'parse_errors': errors, 'parse_status': status, 'usage': response.usage,
                    'model': response.model, 'request_id': response.request_id,
                    'attempts': response.attempts, 'received_at_utc': e.now()}
            except Exception as error:
                attempt['error_type'] = type(error).__name__
            attempt['finished_at_utc'] = e.now()
            e.atomic_write_json(path, attempt)
            return attempt

        outcomes = []
        with ThreadPoolExecutor(max_workers=min(cfg['workers'], max(1, len(rows)))) as pool:
            futures = {pool.submit(request, r): r for r in rows}
            for future in as_completed(futures):
                row = futures[future]
                attempt = future.result()
                applied = apply_attempt(con, output, row, attempt)
                outcome = {'job_id': row['job_id'], 'dataset': row['dataset'], 'topic': row['topic'],
                           'previous_error': row['error'] or 'parse_error', 'applied': applied,
                           'parse_status': attempt.get('result', {}).get('parse_status'),
                           'error_type': attempt.get('error_type')}
                outcomes.append(outcome)
                print(e.dumps(outcome), flush=True)
        usage = {k: 0 for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
        for row in rows:
            attempt = json.loads((batch / (row['job_id'] + '.attempt.json')).read_text(encoding='utf-8'))
            for key in usage:
                usage[key] += attempt.get('result', {}).get('usage', {}).get(key, 0)
        report = {'updated_at_utc': e.now(), 'selected': len(rows),
                  'repaired': sum(r['applied'] for r in outcomes), 'outcomes': outcomes,
                  'retry_returned_usage': usage, 'original_results_preserved_in': str(batch),
                  'usage_note': 'Retry usage is incremental; current-result totals are not historical billing totals.',
                  'stage': 'exporting'}
        e.atomic_write_json(batch / 'report.json', report)
        e.export(cfg, con)
        report.update(stage='finished', updated_at_utc=e.now())
        e.atomic_write_json(batch / 'report.json', report)
        print(e.dumps({k: v for k, v in report.items() if k != 'outcomes'}), flush=True)
    finally:
        if gate:
            gate.cancel('retry_batch_exited')
        if con:
            con.close()
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
