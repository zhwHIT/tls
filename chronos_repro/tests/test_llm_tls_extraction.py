"""Extraction contracts: no API needed, no reference timeline data."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('extract_llm_tls_events', ROOT / 'scripts/extract_llm_tls_events.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def article(times):
    return {'id': '42', 'title': 'Title', 'time': '2013-05-17T12:00:00Z', 'text': 'Original text',
            'sentences': [{'raw': 'On May 16 he announced plans for May 18.',
                           'tokens': {'keys': ['raw', 'time', 'time_format'], 'data': times}}]}


def test_first_time_only_and_all_mentions_retained():
    _, t, f, _ = m.load_upstream(ROOT.parent / 'llm_tls_upstream')
    obj = article([['May 16', '2013-05-16T00:00:00', '%Y-%m-%d'],
                   ['May 18', '2013-05-18T00:00:00', '%Y-%m-%d']])
    item = m.prepare_article(obj, 'entities', 'David_Beckham', 0, ['David Beckham'], (t, f))
    assert item['date'] == '2013-05-17'
    assert len(item['sentence_with_time']) == 1
    assert item['sentence_with_time'][0].startswith('2013-05-16:')
    assert len(item['time_mentions']) == 2
    assert item['source_id'] == '42'


def test_first_partial_time_does_not_promote_later_day():
    _, t, f, _ = m.load_upstream(ROOT.parent / 'llm_tls_upstream')
    obj = article([['2013', '2013-01-01T00:00:00', '%Y'],
                   ['May 18', '2013-05-18T00:00:00', '%Y-%m-%d']])
    item = m.prepare_article(obj, 'entities', 'David_Beckham', 0, ['David Beckham'], (t, f))
    assert not item['sentence_with_time']
    assert '(2013-05-18)' not in item['content']
    assert len(item['time_mentions']) == 2


def test_model_date_precision_not_publication_fallback():
    events, errors, status = m.parse_events('2013-05: Announced plans.\nUNKNOWN: Discussed retirement.', 'job')
    assert status == 'extracted' and not errors
    assert [e['date_precision'] for e in events] == ['month', 'unknown']
    assert events[1]['event_date'] is None
    assert all(e['verification_status'] == 'not_fact_checked' for e in events)
    assert m.parse_events('2013-02-30: Impossible date.', 'job')[2] == 'parse_error'
    assert m.parse_events('NONE', 'job')[2] == 'no_events'


def test_resume_identity_rejects_changed_model(tmp_path):
    con = m.connect(tmp_path)
    m.check_identity(con, {'model': 'a'}, create=True)
    import pytest
    with pytest.raises(ValueError, match='identity changed'):
        m.check_identity(con, {'model': 'b'})
    con.close()


def test_duplicate_requests_preserve_both_article_mappings(tmp_path):
    cfg = {'output': str(tmp_path)}
    con = m.connect(tmp_path)
    events, errors, ps = m.parse_events('2013-05-16: Announced retirement.', 'j')
    result = {'events': events, 'raw_response': '2013-05-16: Announced retirement.',
              'parse_errors': errors, 'parse_status': ps, 'usage': {'total_tokens': 20}}
    con.execute("insert into jobs(job_id,dataset,topic,messages,input_meta,status,result) values ('j','entities','David_Beckham','[]','{}','done',?)", (json.dumps(result),))
    for i in range(2):
        item = {'source_id': str(i), 'index': i, 'content': 'raw', 'date': '2013-05-17'}
        con.execute('insert into articles values (?,?,?,?,?,?)', ('entities', 'David_Beckham', i, str(i), 'j', json.dumps(item)))
    con.execute("insert into topics values ('entities','David_Beckham','hash',2)")
    con.commit()
    m.export(cfg, con)
    rows = [json.loads(line) for line in (tmp_path / 'events/entities/David_Beckham_events.jsonl').read_text().splitlines()]
    assert len(rows) == 2 and {r['source_id'] for r in rows} == {'0', '1'}
    assert rows[0]['events'][0]['event_date'] == '2013-05-16'
    assert rows[0]['date'] == '2013-05-17'
    assert m.status(con)['unique_generated_events'] == 1
    con.close()


def test_paid_response_cache_recovers_without_second_api_call(tmp_path, monkeypatch):
    from types import SimpleNamespace
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def chat(self, messages, temperature):
            calls.append(messages)
            return SimpleNamespace(text='2013-05-16: Announced retirement.',
                                   model='fake', usage={'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
                                   request_id='test-request', attempts=1)

    monkeypatch.setattr(m, 'DeepSeekClient', FakeClient)
    monkeypatch.setattr(m, 'load_upstream', lambda _: (None, None, None, {}))
    monkeypatch.setattr(m, 'identity', lambda *_: {'test': 1})
    monkeypatch.setenv('TLS_TEST_ONLY_KEY', 'not-a-real-api-key')
    env = tmp_path / 'test.env'
    env.write_text('', encoding='utf-8')
    cfg = {'output': str(tmp_path), 'upstream': '.', 'env_file': str(env), 'api_key_env': 'TLS_TEST_ONLY_KEY',
           'model': 'fake', 'base_url': 'https://invalid.example', 'timeout': 1, 'max_retries': 0,
           'max_tokens': 100, 'workers': 1, 'temperature': 0, 'datasets': {'entities': '.'}}
    con = m.connect(tmp_path)
    m.check_identity(con, {'test': 1}, create=True)
    m.meta_set(con, 'prepared', True)
    con.execute("insert into jobs(job_id,dataset,topic,messages,input_meta) values ('j','entities','David_Beckham','[]','{}')")
    con.commit(); con.close()
    m.run(cfg)
    assert len(calls) == 1
    # Simulate crash between durable paid-response save and SQLite result commit.
    con = m.connect(tmp_path)
    con.execute("update jobs set status='running',result=null where job_id='j'")
    con.commit(); con.close()
    m.run(cfg)
    assert len(calls) == 1
    con = m.connect(tmp_path)
    assert m.status(con)['job_counts'] == {'done': 1}
    assert m.status(con)['usage']['total_tokens'] == 15
    con.close()
