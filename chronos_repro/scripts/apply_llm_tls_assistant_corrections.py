"""Apply the reviewed, versioned 24-record assistant correction manifest.

No provider API calls. Original rows and response caches are archived before
mutation. Resume the same batch safely after interruption. Export only affected
topic files, retaining all article mappings and marking assistant provenance.
"""
import copy
import json
import os
import re
import shutil
from collections import Counter
from pathlib import Path

import extract_llm_tls_events as e


def target_text(row):
    text = json.loads(row['messages'])[-1]['content'].split('### Content 2\n', 1)[1]
    return re.split(r'\n### Event(?: Summary)? Related', text, maxsplit=1)[0]


def normalized(text):
    return re.sub(r'\s+', ' ', text.replace('\u00ad', '')).strip()


def prepare_result(row, item, batch_name):
    if normalized(item['evidence']) not in normalized(target_text(row)):
        raise ValueError('Evidence missing from target article: ' + row['job_id'])
    events, errors, status = e.parse_events(item['response'], row['job_id'])
    if errors or status == 'parse_error':
        raise ValueError('Correction does not satisfy event schema')
    correction = {k: v for k, v in item.items() if k not in ('response', 'index', 'job_id')}
    correction.update(author='conversation_assistant', batch=batch_name,
                      scope='stored target article only', externally_fact_checked=False)
    for index, event in enumerate(events):
        event.update(event_id=e.digest(f"{row['job_id']}:{batch_name}:{index}")[:24],
                     origin='conversation_assistant', date_evidence=item.get('time_note'),
                     time_expression=item.get('time_expression'),
                     source_evidence=item['evidence'])
        if 'time_range' in item:
            event['time_range'] = item['time_range']
    return {'job_id': row['job_id'], 'raw_response': item['response'],
            'events': events, 'parse_errors': [], 'parse_status': status,
            'model': 'conversation_assistant', 'request_id': None, 'attempts': 0,
            'usage': {}, 'received_at_utc': e.now(), 'correction': correction}


def adjusted_report(base, originals, results, mappings):
    report = copy.deepcopy(base)
    for row in originals:
        old = json.loads(row['result'] or '{}')
        new = results[row['job_id']]
        report['job_counts'][row['status']] -= 1
        report['job_counts']['done'] = report['job_counts'].get('done', 0) + 1
        if row['status'] == 'done':
            report['parse_status_counts'][old['parse_status']] -= 1
        report['parse_status_counts'][new['parse_status']] = report['parse_status_counts'].get(new['parse_status'], 0) + 1
        report['unique_generated_events'] += len(new['events']) - len(old.get('events', []))
        for key in report['usage']:
            report['usage'][key] -= old.get('usage', {}).get(key, 0)
        for mapping in mappings[row['job_id']]:
            topic = next(t for t in report['topics'] if (t['dataset'], t['topic']) == tuple(mapping))
            topic['completed_articles'] += int(row['status'] != 'done')
            topic['error_articles'] -= int(row['status'] == 'error')
    for key in ('job_counts', 'parse_status_counts'):
        report[key] = {k: v for k, v in report[key].items() if v}
    report.update(updated_at_utc=e.now(), complete=True, all_outputs_parse_clean=True,
                  output_provenance='provider outputs plus explicitly marked conversation_assistant corrections',
                  usage_note='Current provider-result usage only; historical returned usage is preserved separately. Conversation assistant usage is not available here.')
    return report


def main():
    cfg = e.read_config(e.PROJECT / 'configs/llm_tls_extraction_v1.json')
    output = Path(cfg['output'])
    batch = output / 'assistant_corrections_20260920'
    source = Path(__file__).with_name('llm_tls_assistant_corrections_20260920.json')
    manifest = json.loads(source.read_text(encoding='utf-8'))
    items = manifest['records']
    scope = json.loads((output / 'retry_20260920_01/original_jobs.json').read_text(encoding='utf-8'))
    if len(items) != len(scope) or {r['job_id'] for r in items} != {r['job_id'] for r in scope}:
        raise ValueError('Correction scope must match all 24 problem records')
    if (output / 'suite.lock').exists() or (output / 'STOP').exists():
        raise RuntimeError('Active supervisor or STOP file')
    lock = output / 'runner.lock'
    with lock.open('x', encoding='utf-8') as f:
        f.write(e.dumps({'pid': os.getpid(), 'batch': str(batch)}))
    con = None
    try:
        con = e.connect(output)
        if con.execute("select count(*) from jobs where status in ('running','pending')").fetchone()[0]:
            raise RuntimeError('Unfinished extraction jobs exist')
        batch.mkdir(exist_ok=True)
        saved_manifest = batch / 'corrections.json'
        if saved_manifest.exists() and e.file_hash(saved_manifest) != e.file_hash(source):
            raise ValueError('Correction manifest changed within an existing batch')
        shutil.copy2(source, saved_manifest)
        archive = batch / 'before_jobs.json'
        if not archive.exists():
            originals = [dict(con.execute('select * from jobs where job_id=?', (i['job_id'],)).fetchone()) for i in items]
            # Validate every response and evidence anchor before archiving or mutation.
            for row, item in zip(originals, items):
                prepare_result(row, item, batch.name)
            for row in originals:
                cached = output / 'responses' / row['job_id'][:2] / (row['job_id'] + '.json')
                if cached.exists():
                    shutil.copy2(cached, batch / (row['job_id'] + '.before.json'))
            for name in ('export_report.json', 'suite_status.json', 'progress.json'):
                shutil.copy2(output / name, batch / ('before_' + name))
            e.atomic_write_json(archive, originals)
        originals = json.loads(archive.read_text(encoding='utf-8'))
        results = {}
        for row, item in zip(originals, items):
            assert row['job_id'] == item['job_id']
            saved = batch / (row['job_id'] + '.result.json')
            if not saved.exists():
                e.atomic_write_json(saved, prepare_result(row, item, batch.name))
            results[row['job_id']] = json.loads(saved.read_text(encoding='utf-8'))
        with con:
            for row in originals:
                result = results[row['job_id']]
                current = con.execute('select status,error,result from jobs where job_id=?', (row['job_id'],)).fetchone()
                if current['result'] != e.dumps(result) and any(current[k] != row[k] for k in ('status', 'error', 'result')):
                    raise RuntimeError('Current job differs from archived baseline')
                con.execute("update jobs set status='done',error=null,result=? where job_id=?", (e.dumps(result), row['job_id']))
        for job_id, result in results.items():
            e.atomic_write_json(output / 'responses' / job_id[:2] / (job_id + '.json'), result)
        mappings = {job_id: [tuple(r) for r in con.execute('select dataset,topic from articles where job_id=?', (job_id,))] for job_id in results}
        topics = sorted({t for ts in mappings.values() for t in ts})
        counts = Counter()
        for dataset, topic in topics:
            target = output / 'events' / dataset / (topic + '_events.jsonl')
            temporary = target.with_suffix('.jsonl.correction.tmp')
            with target.open(encoding='utf-8') as inp, temporary.open('w', encoding='utf-8') as out:
                for line in inp:
                    record = json.loads(line)
                    job_id = record['job_id']
                    if job_id in results:
                        result = results[job_id]
                        record.update(status='done', error=None, llm=result['raw_response'],
                                      events=result['events'], parse_status=result['parse_status'],
                                      parse_errors=[], correction=result['correction'])
                        line = e.dumps(record) + '\n'
                        counts[job_id] += 1
                    out.write(line)
            e.replace_with_retry(temporary, target)
        if any(counts[job_id] != len(mappings[job_id]) for job_id in results):
            raise RuntimeError('Article mapping/export count mismatch')
        baseline = json.loads((batch / 'before_export_report.json').read_text(encoding='utf-8'))
        report = adjusted_report(baseline, originals, results, mappings)
        actual_counts = dict(con.execute('select status,count(*) from jobs group by status').fetchall())
        assert actual_counts == report['job_counts']
        report['latest_assistant_correction'] = str(batch / 'report.json')
        e.atomic_write_json(output / 'export_report.json', report)
        e.atomic_write_json(output / 'progress.json', {**report, 'stage': 'finished'})
        audit = {'finished_at_utc': e.now(), 'records_updated': len(results),
                 'article_mappings_updated': sum(counts.values()), 'topic_files_updated': len(topics),
                 'parse_status_counts': dict(Counter(r['parse_status'] for r in results.values())),
                 'provider_api_calls': 0, 'author': 'conversation_assistant',
                 'unknown_single_dates': [job_id for job_id, r in results.items() if any(v['event_date'] is None for v in r['events'])],
                 'reason': 'User requested assistant-authored replacements for all known problem records.',
                 'baseline_scope': str(output / 'retry_20260920_01/original_jobs.json')}
        e.atomic_write_json(batch / 'report.json', audit)
        suite = json.loads((output / 'suite_status.json').read_text(encoding='utf-8'))
        suite.update(stage='completed_with_assistant_corrections', completed=True,
                     all_outputs_parse_clean=True, latest_assistant_correction=str(batch / 'report.json'),
                     latest_job_counts=report['job_counts'], latest_parse_status_counts=report['parse_status_counts'],
                     updated_at_utc=e.now())
        e.atomic_write_json(output / 'suite_status.json', suite)
        print(e.dumps(audit), flush=True)
        print(e.dumps({k: v for k, v in report.items() if k != 'topics'}), flush=True)
    finally:
        if con:
            con.close()
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
