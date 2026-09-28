import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
r = importlib.import_module('retry_llm_tls_problem_events')


def test_retry_preserves_bad_response_and_replaces_only_clean_result(tmp_path):
    con = r.e.connect(tmp_path)
    old = r.e.dumps({'parse_status': 'parse_error', 'raw_response': 'bad'})
    con.execute("insert into jobs(job_id,dataset,topic,messages,input_meta,status,result) values ('j','t','x','[]','{}','done',?)", (old,))
    con.commit()
    original = dict(con.execute("select * from jobs where job_id='j'").fetchone())
    assert not r.apply_attempt(con, tmp_path, original, {'error_type': 'ContentRejectedError'})
    assert not r.apply_attempt(con, tmp_path, original, {'result': {'parse_status': 'parse_error'}})
    assert con.execute("select result from jobs where job_id='j'").fetchone()[0] == old
    result = {'job_id': 'j', 'parse_status': 'no_events', 'raw_response': 'NONE'}
    assert r.apply_attempt(con, tmp_path, original, {'result': result})
    assert r.apply_attempt(con, tmp_path, original, {'result': result})
    assert json.loads((tmp_path / 'responses/j/j.json').read_text()) == result
    with pytest.raises(RuntimeError, match='changed'):
        r.apply_attempt(con, tmp_path, original, {'result': {**result, 'raw_response': 'different'}})
    con.close()
