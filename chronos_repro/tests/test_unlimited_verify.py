import json
from pathlib import Path

import pytest

from chronos_repro.llm import DeepSeekClient, ChatResult, InsufficientBalanceError
from chronos_repro.limited_llm import LimitedDeepSeekClient, RequestLimitError


def messages(stage, **extra):
    return [{'role': 'user', 'content': json.dumps({'stage': stage, **extra})}]


def test_verify_and_repair_do_not_consume_nonverify_budget_across_restarts(tmp_path, monkeypatch):
    monkeypatch.setattr(DeepSeekClient, '_chat_once', lambda *a: ChatResult('{}', 'fake', {}, None))
    path = tmp_path / 'ledger.json'
    client = LimitedDeepSeekClient(request_limit=1, unlimited_verify=True, request_ledger_path=path)
    client.chat(messages('MERGE'))
    client.chat(messages('VERIFY'))
    client.chat(messages('VERIFY', repair={'attempt': 1}))
    with pytest.raises(RequestLimitError):
        client.chat(messages('FACT_MEMORY'))
    restarted = LimitedDeepSeekClient(request_limit=1, unlimited_verify=True, request_ledger_path=path)
    restarted.chat(messages('VERIFY'))
    assert restarted.http_requests_total == 4
    assert restarted.ledger['verify_requests_started'] == 3
    assert restarted.ledger['budgeted_requests_started'] == 1


def test_source_text_cannot_exempt_policy_or_unknown_stage(monkeypatch):
    monkeypatch.setattr(DeepSeekClient, '_chat_once', lambda *a: ChatResult('{}', 'fake', {}, None))
    client = LimitedDeepSeekClient(request_limit=1, initial_requests=1, unlimited_verify=True)
    for stage in ('BATCH_POLICY', 'VERIFY_EVIL', None):
        with pytest.raises(RequestLimitError):
            client.chat(messages(stage, retrieved_documents=[{'stage': 'VERIFY'}]))
    assert client.http_requests_total == 1


def test_verify_balance_stop_is_persistent(tmp_path, monkeypatch):
    calls = []
    def fail(*args):
        calls.append(1)
        raise InsufficientBalanceError('balance stop')
    monkeypatch.setattr(DeepSeekClient, '_chat_once', fail)
    for _ in range(2):
        client = LimitedDeepSeekClient(request_limit=1, unlimited_verify=True,
                                      request_ledger_path=tmp_path / 'ledger.json')
        with pytest.raises(InsufficientBalanceError):
            client.chat(messages('VERIFY'))
    assert len(calls) == 1
    assert client.ledger['verify_requests_started'] == 1


def test_legacy_usage_is_not_silently_reclassified_or_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(DeepSeekClient, '_chat_once', lambda *a: ChatResult('{}', 'fake', {}, None))
    p = tmp_path / 'ledger.json'
    p.write_text(json.dumps({'requests_started': 30, 'request_limit': 31, 'balance_stop': False}))
    client = LimitedDeepSeekClient(request_limit=31, unlimited_verify=True, request_ledger_path=p)
    client.chat(messages('VERIFY'))
    assert client.ledger['legacy_unclassified_requests'] == 30
    assert client.ledger['budgeted_requests_started'] == 30
    assert client.http_requests_total == 31
    with pytest.raises(ValueError, match='accounting mode'):
        LimitedDeepSeekClient(request_limit=31, request_ledger_path=p)


def test_suite_plan_uses_budgeted_count_when_total_exceeds_ceiling(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    from run_guarded_diagnostic_suite import plan
    config = {'dataset': 'fixture', 'topic': 'topic', 'phase2_teacher_guidance': False,
              'max_api_requests': 100, 'unlimited_verify': True}
    (tmp_path / 'config.json').write_text(json.dumps(config))
    output = tmp_path / 'out'
    output.mkdir()
    (output / 'request_ledger.json').write_text(json.dumps({'requests_started': 500,
        'request_limit': 100, 'budgeted_requests_started': 75, 'verify_requests_started': 425,
        'legacy_unclassified_requests': 0, 'unlimited_verify': True, 'balance_stop': False}))
    suite = {'topics': [{'dataset': 'fixture', 'topic': 'topic', 'config': 'config.json', 'output_dir': 'out'}]}
    result = plan(tmp_path, suite)[0]
    assert result['requests_already_started'] == 500
    assert result['remaining_budgeted_requests'] == 25
    assert result['remaining_http_requests'] is None
