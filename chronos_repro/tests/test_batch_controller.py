import copy
import json
from pathlib import Path

import pytest

from chronos_repro import batch_memory as memory
from chronos_repro.batch_policy import validate_action
from chronos_repro.batch_policy import instruction, POLICY_SYSTEM
from chronos_repro.compact_context import compact_instruction, ContextLimitError
from chronos_repro.gap_contract import gap_signature
from chronos_repro.llm import ChatResult
from chronos_repro.token_budget import TokenBudgetClient


def event(i, summary='The committee approved the acquisition.'):
    return {'event_id': i, 'time': '2020-01-02', 'summary': summary, 'evidence_ids': ['d1']}


def state():
    return {'dataset': 'fixture', 'topic': 'Aurora', 'timeline_events': [], 'search_history': []}


def query(text='Aurora acquisition approval date', mode='none', start=None, end=None):
    return {'query': text, 'time_filter': {'mode': mode, 'start': start, 'end': end}}


def action(queries=None):
    return {'reason': 'Find the approval required by the agreement.', 'action': 'SEARCH',
            'queries': queries or [query()], 'stop_reason': None}


def stop():
    return {'reason': 'No worthwhile supported query remains.', 'action': 'STOP',
            'queries': [], 'stop_reason': 'NO_ACTIONABLE_QUERY'}


def test_delayed_summary_replaces_and_deletes_existing_facts():
    s = state()
    s['timeline_events'] = [event('e1'), event('e2')]
    memory.sync_events(s)
    memory.commit_summary(s, [{'text': 'Old facts.', 'event_ids': ['e1', 'e2']}], 2)
    s['timeline_events'] = [event('e1', 'Approval was revoked.'), event('e3')]
    memory.sync_events(s)
    view = memory.policy_view(s, 'SKELETON_EXPLORATION')
    assert view['memory']['through_revision'] == 2
    assert {r['event_id']: r['operation'] for r in view['event_delta']} == {
        'e1': 'UPDATE', 'e2': 'DELETE', 'e3': 'APPEND'}
    assert view['current_revision'] == 5
    with pytest.raises(ValueError, match='stale'):
        memory.commit_summary(s, [], 2)


def test_summary_is_not_updated_every_search_and_no_gain_does_not_refresh():
    s = state()
    for i in range(3):
        s['timeline_events'].append(event(f'e{i}'))
        memory.sync_events(s)
        memory.initialize(s)['batches_since_summary'] += 1
        assert memory.summary_due(s) == (i == 2)
    memory.commit_summary(s, [], 3)
    memory.initialize(s)['batches_since_summary'] = 99
    assert not memory.summary_due(s)


def test_batch_queries_validate_time_filters_and_history():
    rows = [query(), query('Aurora acquisition regulatory decision', 'soft', '2020-01-01', '2020-02-01')]
    assert validate_action(action(rows), [])['queries'] == rows
    with pytest.raises(ValueError, match='already executed'):
        validate_action(action(rows), [rows[0]])
    with pytest.raises(ValueError, match='duplicated'):
        validate_action(action([rows[0], rows[0]]), [])
    assert validate_action(stop(), []) == stop()


@pytest.mark.parametrize('row', [query(mode='none', start='2020-01-01'),
    query(mode='hard', start='2021-01-01', end='2020-01-01'),
    query(mode='soft', start='2020', end='2020-02-01'), query(mode='soft')])
def test_invalid_temporal_contract_fails(row):
    with pytest.raises((ValueError, TypeError)):
        validate_action(action([row]), [])


def test_same_anchor_different_questions_are_not_duplicates():
    g = {'type': 'MISSING_FACTOR', 'left_event_id': 'e1', 'right_event_id': None}
    assert gap_signature({**g, 'retrieval_target': {'question': 'Who approved it?'}}) != gap_signature(
        {**g, 'retrieval_target': {'question': 'When was it approved?'}})


def test_projection_preserves_v9_protocol_and_tombstones():
    payload = {'protocol': 'batch-v9', 'stage': 'BATCH_POLICY',
               'state': {'event_delta': [{'operation': 'DELETE', 'event_id': 'e1'}]}}
    assert compact_instruction(payload) == payload


class FakeTransport:
    model = 'offline'
    request_options = {'max_tokens': 8192}

    def __init__(self, usage=None):
        self.calls = []
        self.usage = usage or {'prompt_tokens': 100, 'completion_tokens': 20}

    def chat(self, messages, temperature=0):
        self.calls.append(copy.deepcopy(self.request_options))
        return ChatResult(json.dumps(stop()), self.model, self.usage, None)


def budget_client(transport, estimate=100):
    c = TokenBudgetClient.__new__(TokenBudgetClient)
    c.client, c.model, c.settings = transport, transport.model, {'input_limit': 4096, 'preflight_limit': 3800}
    c.records, c.accepted, c.last_exchange = [], [], None
    c.estimate = lambda messages: estimate
    return c


def messages():
    return [{'role': 'system', 'content': 'policy'}, {'role': 'user', 'content': json.dumps({
        'stage': 'BATCH_POLICY', 'state': {'phase': 'SKELETON_EXPLORATION'}})}]


def test_token_preflight_blocks_transport_and_server_overflow_is_quarantined():
    raw = FakeTransport()
    with pytest.raises(ContextLimitError, match='preflight'):
        budget_client(raw, 4000).chat(messages())
    assert not raw.calls
    raw = FakeTransport({'prompt_tokens': 4200, 'completion_tokens': 20})
    client = budget_client(raw)
    with pytest.raises(ContextLimitError, match='quarantined'):
        client.chat(messages())
    assert not client.accepted
    assert raw.request_options['max_tokens'] == 8192


def test_policy_output_limit_and_exact_training_scope():
    raw = FakeTransport()
    client = budget_client(raw)
    client.chat(messages())
    client.accept_last_response(stop())
    assert raw.calls == [{'max_tokens': 512}]
    assert len(client.accepted) == 1
    other = messages()
    other[1]['content'] = json.dumps({'stage': 'FACT_MEMORY'})
    client.chat(other)
    client.accept_last_response({'action': 'MEMORY_UPDATE'})
    assert len(client.accepted) == 1


def test_cached_overlong_response_cannot_bypass_token_gate():
    class Cached(FakeTransport):
        last_response_usage = {'prompt_tokens': 5000, 'completion_tokens': 20}
        def chat(self, messages, temperature=0):
            return ChatResult(json.dumps(stop()), self.model, {}, None, attempts=0)
    with pytest.raises(ContextLimitError, match='quarantined'):
        budget_client(Cached()).chat(messages())


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    import batched_search_phase
    return batched_search_phase


def test_batch_executes_independent_queries_deduplicates_passages_and_records_delta(runtime, monkeypatch):
    s = state()
    calls, processed = [], []
    def fake_search(index, queries, k, engine, **kwargs):
        calls.append((queries, kwargs))
        return [{'id': 'd1'}]
    monkeypatch.setattr(runtime, 'search', fake_search)
    monkeypatch.setattr(runtime.coverage_pipeline, 'retrieve_passages', lambda *a, **k: [{'id': 'p1'}])
    def process(client, state, docs, *args):
        processed.extend(docs)
        state['timeline_events'] = [event('e1')]
    monkeypatch.setattr(runtime.coverage_pipeline, 'process_passages', process)
    monkeypatch.setattr(runtime.coverage_pipeline, 'checkpoint', lambda *a: None)
    trace = {'steps': []}
    rows = [query(), query('Aurora approval final decision', 'hard', '2020-01-01', '2020-02-01')]
    runtime.execute_batch(None, s, {'top_k': 5}, 'index', trace, {}, 'SKELETON_EXPLORATION', action(rows), {})
    assert len(calls) == 2 and len(processed) == 1
    assert calls[1][1]['date_filter_mode'] == 'hard'
    assert s['controller_memory']['version'] == 0
    assert s['controller_memory']['pending_changes'][0]['event_id'] == 'e1'
    assert s['query_batches'][0]['query_count'] == 2


def test_phase1_allows_early_stop_without_minimum_events(runtime, monkeypatch):
    s = state()
    monkeypatch.setattr(runtime, 'decide', lambda *a: (stop(), {}))
    monkeypatch.setattr(runtime, 'refresh_summary', lambda *a, **kw: False)
    trace = {'steps': [{'observation': {}}]}
    runtime.run_phase1(None, s, {'phase1_max_rounds': 10}, None, trace, {})
    assert s['phase1_termination'] == 'autonomous_stop'
    assert trace['steps'][-1]['observation']['completion_established'] is False


def test_real_phase1_keeps_summary_old_and_passes_verified_deltas(runtime, monkeypatch):
    class Client:
        def __init__(self):
            self.policies, self.summaries = [], []
        def chat(self, messages, temperature=0):
            p = json.loads(messages[-1]['content'])
            if p['stage'] == 'BATCH_POLICY':
                self.policies.append(p)
                n = len(self.policies)
                result = action([query(f'Aurora approval report number {n}')]) if n < 3 else stop()
            else:
                assert p['stage'] == 'FACT_MEMORY'
                self.summaries.append(p)
                result = {'action': 'MEMORY_UPDATE', 'facts': [
                    {'text': 'The committee approved the acquisition.',
                     'event_ids': [r['event_id'] for r in p['event_changes'] if r['event']]}]}
            return ChatResult(json.dumps(result), 'fixture', {'prompt_tokens': 100}, None)
    monkeypatch.setattr(runtime, 'search', lambda *a, **k: [{'id': 'd1'}])
    monkeypatch.setattr(runtime.coverage_pipeline, 'retrieve_passages', lambda *a, **k: [{'id': 'p1'}])
    def process(client, s, *args):
        s['timeline_events'].append(event(f"e{len(s['timeline_events']) + 1}"))
    monkeypatch.setattr(runtime.coverage_pipeline, 'process_passages', process)
    monkeypatch.setattr(runtime.coverage_pipeline, 'checkpoint', lambda *a: None)
    config = {'batch_controller': {'summary_interval_batches': 3, 'max_queries': 3},
              'phase1_max_rounds': 5, 'label_repair_attempts': 0, 'temperature': 0, 'top_k': 5}
    s, trace, client = state(), {'steps': [], 'audits': []}, Client()
    usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'logical_calls': 0, 'http_attempts': 0}
    runtime.run_phase1(client, s, config, None, trace, usage)
    assert [p['state']['memory']['version'] for p in client.policies] == [0, 0, 0]
    assert [len(p['state']['event_delta']) for p in client.policies] == [0, 1, 2]
    assert len(client.summaries) == 1
    assert s['controller_memory']['summary_revision'] == 2
    assert s['phase1_termination'] == 'autonomous_stop'


def test_v9_projection_still_rejects_private_reference():
    with pytest.raises(ValueError, match='private'):
        compact_instruction({'protocol': 'batch-v9', 'state': {'gold_events': [event('e1')]}})


def test_search_examples_use_json_null_not_null_string():
    prompt = instruction({}, 2)
    assert prompt['output']['stop_reason'] is None
    assert prompt['search_example']['stop_reason'] is None
    assert validate_action(prompt['search_example'], [], maximum_queries=2)['stop_reason'] is None
    invalid = copy.deepcopy(prompt['search_example'])
    invalid['stop_reason'] = 'null'
    with pytest.raises(ValueError, match='JSON null'):
        validate_action(invalid, [], maximum_queries=2)
    assert 'JSON literal null' in POLICY_SYSTEM


def test_gap_discovery_prompt_states_the_actual_character_limit(runtime, monkeypatch):
    captured = []
    def call(client, s, config, trace, usage, stage, payload, validator, **kwargs):
        captured.append(payload)
        return validator({'thought': 'The anchored uncertainty is worth checking.', 'action': 'GAP_MEMORY', 'gaps': []})
    monkeypatch.setattr(runtime, '_call', call)
    s = state()
    s['timeline_events'] = [event('e1')]
    runtime.discover_gaps(None, s, {}, {}, {})
    assert '6-240' in captured[0]['output']['thought']
    assert '<=160' in captured[0]['output']['thought']
    assert '6-240' in runtime.API_SYSTEM


def test_empty_gap_discovery_stops_without_manufacturing_searches(runtime, monkeypatch):
    class Client:
        def chat(self, messages, temperature=0):
            p = json.loads(messages[-1]['content'])
            assert p['stage'] == 'GAP_DISCOVERY'
            return ChatResult(json.dumps({'thought': 'No anchored factual uncertainty is present.',
                'action': 'GAP_MEMORY', 'gaps': []}), 'fixture', {}, None)
    s = state()
    s['timeline_events'] = [event('e1')]
    memory.sync_events(s)
    memory.commit_summary(s, [], 1)
    monkeypatch.setattr(runtime.coverage_pipeline, 'checkpoint', lambda *a: None)
    config = {'phase2_max_gap_cycles': 2, 'batch_controller': {}, 'label_repair_attempts': 0, 'temperature': 0}
    trace = {'steps': [], 'audits': []}
    usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'logical_calls': 0, 'http_attempts': 0}
    result = runtime.run_phase2(Client(), s, config, None, trace, usage)
    assert not result['memory']['gaps']
    assert s['phase2_termination'] == 'no_actionable_gaps'
    assert all(step['action'] != 'SEARCH' for step in trace['steps'])


def test_changed_support_reopens_resolved_gap():
    s = state()
    s['timeline_events'] = [event('e1')]
    memory.sync_events(s)
    gap = {'gap_id': 'g1', 'status': 'RESOLVED', 'left_event_id': 'e1',
           'resolution_evidence': [{'event_id': 'e1'}]}
    s['gap_memory'] = {'gaps': [gap]}
    s['timeline_events'][0]['summary'] = 'The approval was revoked.'
    memory.sync_events(s)
    assert gap['status'] == 'OPEN' and gap['resolution_evidence'] == []


def test_local_stop_does_not_cancel_other_gap(runtime, monkeypatch):
    s = state()
    memory.initialize(s)
    gaps = {'gaps': [{'gap_id': name, 'status': 'OPEN', 'last_review_revision': 0}
                     for name in ('g1', 'g2')], 'discovery_revision': 0}
    s['gap_memory'] = gaps
    monkeypatch.setattr(runtime, 'discover_gaps', lambda *a: gaps)
    seen = []
    trace = {'steps': [], 'audits': []}
    def decide(*args):
        seen.append(args[-1]['gap_id'])
        trace['steps'].append({'observation': {}})
        return stop(), {}
    monkeypatch.setattr(runtime, 'decide', decide)
    monkeypatch.setattr(runtime, 'refresh_summary', lambda *a, **k: None)
    monkeypatch.setattr(runtime.coverage_pipeline, 'checkpoint', lambda *a: None)
    runtime.run_phase2(None, s, {'phase2_max_gap_cycles': 2}, None, trace, {})
    assert sorted(seen) == ['g1', 'g2']
    assert all(g['status'] == 'DEFERRED' for g in gaps['gaps'])
    assert s['phase2_termination'] == 'no_actionable_gaps'
