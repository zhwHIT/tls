import json
from pathlib import Path

import pytest


@pytest.mark.parametrize('balance,limit,expected', [(False, 400, 323), (True, 400, None), (False, 401, None)])
def test_batch_plan_preserves_authorized_counter(tmp_path, monkeypatch, balance, limit, expected):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    from run_guarded_diagnostic_suite import plan
    config = {'dataset': 'fixture', 'topic': 'topic', 'phase2_teacher_guidance': False, 'max_api_requests': 400}
    (tmp_path / 'config.json').write_text(json.dumps(config), encoding='utf-8')
    output = tmp_path / 'output'
    output.mkdir()
    ledger = {'requests_started': 77, 'request_limit': limit, 'balance_stop': balance}
    (output / 'request_ledger.json').write_text(json.dumps(ledger), encoding='utf-8')
    suite = {'topics': [{'dataset': 'fixture', 'topic': 'topic', 'config': 'config.json', 'output_dir': 'output'}]}
    if expected is None:
        with pytest.raises(ValueError):
            plan(tmp_path, suite)
    else:
        assert plan(tmp_path, suite)[0]['remaining_http_requests'] == expected
    assert json.loads((output / 'request_ledger.json').read_text()) == ledger


def test_exhausted_topic_does_not_block_other_topics(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    from run_guarded_diagnostic_suite import plan
    config = {'dataset': 'fixture', 'topic': 'topic', 'phase2_teacher_guidance': False, 'max_api_requests': 2}
    (tmp_path / 'config.json').write_text(json.dumps(config))
    (tmp_path / 'output').mkdir()
    (tmp_path / 'output/request_ledger.json').write_text(json.dumps(
        {'requests_started': 2, 'request_limit': 2, 'balance_stop': False}))
    suite = {'topics': [{'dataset': 'fixture', 'topic': 'topic', 'config': 'config.json', 'output_dir': 'output'}]}
    assert plan(tmp_path, suite, allow_exhausted=True)[0]['remaining_http_requests'] == 0
    with pytest.raises(ValueError):
        plan(tmp_path, suite)
