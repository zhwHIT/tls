"""Small authorized API pilot; responses are shared with the full extraction run."""
import gzip
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import extract_llm_tls_events as e


def main():
    from tokenizers import Tokenizer
    cfg = e.read_config(e.PROJECT / 'configs/llm_tls_extraction_v1.json')
    existing_report = Path(cfg['output']) / 'pilot_report.json'
    if existing_report.exists():
        report = json.loads(existing_report.read_text(encoding='utf-8'))
        print(e.dumps({'pilot_already_recorded': True, 'passed': report['passed'],
                       'articles': report['articles']}), flush=True)
        return
    templates, get_time, get_format, upstream = e.load_upstream(cfg['upstream'])
    run_identity = e.identity(cfg, upstream)
    tokenizer = Tokenizer.from_file(cfg['tokenizer'])
    jobs = []
    for ds, root in cfg['datasets'].items():
        root = Path(root)
        topic_dir = sorted(p for p in root.iterdir() if p.is_dir())[0]
        keywords = json.loads((topic_dir / 'keywords.json').read_text(encoding='utf-8'))
        with gzip.open(topic_dir / 'articles.preprocessed.jsonl.gz', 'rt', encoding='utf-8') as f:
            for index, line in enumerate(f):
                if index >= 3:
                    break
                item = e.prepare_article(json.loads(line), ds, topic_dir.name, index, keywords, (get_time, get_format))
                messages, input_meta = e.build_messages(item, templates, tokenizer, cfg['context_length'])
                job_id = e.digest(e.dumps({'identity': e.digest(e.dumps(run_identity)), 'messages': messages}))
                jobs.append({'job_id': job_id, 'dataset': ds, 'topic': topic_dir.name,
                             'source_id': item['source_id'], 'messages': messages, 'input_meta': input_meta})
    e.load_env_file(cfg['env_file'])
    if not os.environ.get(cfg['api_key_env']):
        raise ValueError('Configured API key is missing')
    client = e.DeepSeekClient(model=cfg['model'], base_url=cfg['base_url'], api_key_env=cfg['api_key_env'],
                             timeout=cfg['timeout'], max_retries=cfg['max_retries'],
                             request_options={'thinking': {'type': 'disabled'}, 'max_tokens': cfg['max_tokens']})
    output = Path(cfg['output'])

    def extract(job):
        path = output / 'responses' / job['job_id'][:2] / (job['job_id'] + '.json')
        replay = path.exists()
        if replay:
            value = json.loads(path.read_text(encoding='utf-8'))
        else:
            result = client.chat(job['messages'], temperature=cfg['temperature'])
            events, errors, parse_status = e.parse_events(result.text, job['job_id'])
            value = {'job_id': job['job_id'], 'raw_response': result.text, 'events': events,
                     'parse_errors': errors, 'parse_status': parse_status, 'usage': result.usage,
                     'model': result.model, 'request_id': result.request_id, 'attempts': result.attempts,
                     'received_at_utc': e.now()}
            path.parent.mkdir(parents=True, exist_ok=True)
            e.atomic_write_json(path, value)
        row = {k: v for k, v in job.items() if k != 'messages'}
        row.update({'response': value, 'cache_replay': replay})
        print(e.dumps({'dataset': job['dataset'], 'source_id': job['source_id'],
                       'parse_status': value['parse_status'], 'usage': value['usage']}), flush=True)
        return row

    # Deduplicate before concurrency; identical source articles must not race
    # on a paid request or on the same response cache file.
    unique_jobs = {j['job_id']: j for j in jobs}
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(extract, unique_jobs.values()))
    report = {'completed_at_utc': e.now(), 'identity': run_identity,
              'passed': all(r['response']['parse_status'] in ('extracted', 'no_events') for r in rows),
              'articles': len(jobs), 'unique_requests': len(rows), 'rows': rows,
              'fact_verification': 'not_performed',
              'usage': {key: sum(r['response']['usage'].get(key, 0) for r in rows)
                        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}}
    e.atomic_write_json(output / 'pilot_report.json', report)
    print(e.dumps({k: v for k, v in report.items() if k not in ('rows', 'identity')}), flush=True)
    if not report['passed']:
        raise SystemExit('Pilot output format requires inspection')


if __name__ == '__main__':
    main()
