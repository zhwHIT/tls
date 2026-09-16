import json

import pytest

from chronos_repro.date_evidence import validate_date_evidence
from chronos_repro.llm import ChatResult, DeepSeekClient, InsufficientBalanceError
from chronos_repro.limited_llm import LimitedDeepSeekClient, RequestLimitError


def test_source_quote_can_cross_contiguous_context_boundary():
    doc = {'id': 'p', 'title': '', 'context_before': 'May 23, 2012 The ',
           'text': 'first round of voting begins.'}
    evidence = {'document_id': 'p', 'quote': 'May 23, 2012 The first round of voting begins.',
                'time_expression': 'May 23, 2012'}
    assert validate_date_evidence('2012-05-23', evidence, [doc], ['p'])['normalization'] == 'explicit'
    evidence['quote'] = 'May 23, 2012 The final round of voting begins.'
    with pytest.raises(ValueError, match='verbatim'):
        validate_date_evidence('2012-05-23', evidence, [doc], ['p'])


def test_context_only_quote_is_still_supplied_source():
    doc = {'id': 'p', 'context_before': 'April 20, 2012 The campaign begins. ', 'text': 'Later events follow.'}
    evidence = {'document_id': 'p', 'quote': 'April 20, 2012 The campaign begins.',
                'time_expression': 'April 20, 2012'}
    assert validate_date_evidence('2012-04-20', evidence, [doc], ['p'])


def test_request_ceiling_survives_restart_and_migrates_previous_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(DeepSeekClient, '_chat_once', lambda *args: ChatResult('{}', 'fake', {}, None))
    ledger = tmp_path / 'ledger.json'
    first = LimitedDeepSeekClient(request_limit=3, initial_requests=2, request_ledger_path=ledger)
    first.chat([])
    assert first.http_requests_started == 1 and first.http_requests_total == 3
    second = LimitedDeepSeekClient(request_limit=3, request_ledger_path=ledger)
    with pytest.raises(RequestLimitError):
        second.chat([])
    assert second.http_requests_started == 0
    assert json.loads(ledger.read_text())['requests_started'] == 3


def test_balance_stop_survives_restart(tmp_path, monkeypatch):
    calls = []
    def failed(*args):
        calls.append(1)
        raise InsufficientBalanceError('insufficient balance')
    monkeypatch.setattr(DeepSeekClient, '_chat_once', failed)
    ledger = tmp_path / 'ledger.json'
    for _ in range(2):
        client = LimitedDeepSeekClient(request_limit=30, request_ledger_path=ledger)
        with pytest.raises(InsufficientBalanceError):
            client.chat([])
    assert len(calls) == 1


def test_verify_repair_requests_are_distinct_and_identify_failed_candidate(monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    import run_full_timeline_api_agent as runner
    seen = []
    def fake_call(client, instruction, temperature):
        seen.append(json.loads(json.dumps(instruction)))
        return {'thought': 'Keep only events grounded in the supplied evidence.', 'candidates': [
            {'candidate_id': 'bad1', 'status': 'SUPPORTED',
             'event': {'time': '2020-01-01', 'summary': 'The mission began its initial launch.'},
             'evidence_ids': ['p'], 'confidence': .9, 'relevance_pass': True, 'contribution_pass': True,
             'date_evidence': {'document_id': 'p', 'quote': 'invented', 'time_expression': '2020-01-01'}}]}, {}
    monkeypatch.setattr(runner, 'teacher_call', fake_call)
    with pytest.raises(ValueError, match='bad1'):
        runner.verify_batch(None, {'model_visible_state': {'topic': 'mission', 'events': []}},
                            [{'id': 'p', 'text': '2020-01-01 Mission launch.'}],
                            {'max_candidates_per_round': 24, 'label_repair_attempts': 2, 'temperature': 0})
    assert len(seen) == 3
    assert seen[1]['repair'] != seen[2]['repair']
    assert 'bad1' in seen[1]['repair']['previous_error']
    assert seen[1]['repair']['candidate_errors'][0]['candidate_id'] == 'bad1'
    assert seen[1]['repair']['previous_response']['candidates'][0]['candidate_id'] == 'bad1'


def test_smoke_resume_preserves_committed_batches_and_rejects_unknown_passages(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    from run_coverage_api_smoke import restore_saved_progress
    manifest = tmp_path / 'evidence_reader_manifest.json'
    manifest.write_text(json.dumps({'processed_passage_ids': ['p1']}), encoding='utf-8')
    (tmp_path / 'candidate_pool.json').write_text(json.dumps([{'candidate_id': 'c1', 'merge_processed': True}]), encoding='utf-8')
    reader = SimpleNamespace(passages={'p1': {'id': 'p1'}, 'p2': {'id': 'p2'}}, processed=set())
    reader.mark_processed = lambda docs: reader.processed.update(d['id'] for d in docs)
    previous = {'final_events': [{'event_id': 'e1'}], 'steps': [{'action': 'MERGE'}],
                'audits': [{'checked': True}], 'usage': {'total_tokens': 123}}
    state, trace, usage = {}, {}, {}
    restore_saved_progress(tmp_path, previous, state, reader, trace, usage)
    assert reader.processed == {'p1'} and state['timeline_events'] == previous['final_events']
    assert state['candidate_pool'][0]['merge_processed']
    assert trace['resumed_processed_passage_count'] == 1 and trace['steps'] == previous['steps']
    assert usage['total_tokens'] == 123
    state['timeline_events'][0]['event_id'] = 'changed'
    assert previous['final_events'][0]['event_id'] == 'e1'
    manifest.write_text(json.dumps({'processed_passage_ids': ['not-approved']}), encoding='utf-8')
    with pytest.raises(ValueError, match='outside'):
        restore_saved_progress(tmp_path, previous, state, reader, trace, usage)
