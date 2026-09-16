import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import batched_search_phase as runtime
from run_full_timeline_api_agent import decide_merge
from chronos_repro import batch_memory as memory
from chronos_repro.batch_policy import recover_duplicate_queries, validate_action
from chronos_repro.fact_memory import FactLengthError, validate_facts, recover_fact_lengths, fact_text_limits
from chronos_repro.full_timeline import recover_exact_duplicate_appends, apply_merge_operations
from chronos_repro.llm import ChatResult
from chronos_repro.tisa_rollout import SKELETON


def event(i='e1', summary='The committee approved the agreement.'):
    return {'event_id': i, 'time': '2020-01-02', 'summary': summary, 'actors': [],
            'location': None, 'evidence_ids': ['d1'], 'confidence': .9, 'conflict': False}


def state():
    return {'dataset': 'fixture', 'topic': 'Aurora', 'timeline_events': [],
            'search_history': [], 'phase': SKELETON}


def query(text='Aurora regulatory approval decision'):
    return {'query': text, 'time_filter': {'mode': 'none', 'start': None, 'end': None}}


def action(rows):
    return {'action': 'SEARCH', 'reason': 'Check the agreement approval.', 'queries': rows, 'stop_reason': None}


def trace_usage():
    return {'steps': [], 'audits': []}, {k: 0 for k in
        ('prompt_tokens', 'completion_tokens', 'total_tokens', 'logical_calls', 'http_attempts')}


class Repeater:
    model = 'offline'
    def __init__(self, payload):
        self.payload, self.messages, self.accepted = payload, [], []
    def chat(self, messages, temperature=0):
        self.messages.append(json.loads(messages[-1]['content']))
        return ChatResult(json.dumps(self.payload), self.model,
                          {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}, None)
    def accept_last_response(self, value):
        self.accepted.append(value)


CONFIG = {'batch_controller': {'max_queries': 3, 'summary_interval_batches': 3},
          'label_repair_attempts': 1, 'temperature': 0, 'phase1_max_rounds': 3,
          'coverage_pipeline': {'enabled': True}}


def test_failed_length_repair_selects_intact_rows_and_keeps_archive():
    s, (trace, usage) = state(), trace_usage()
    s['timeline_events'] = [event(), event('e2')]
    original = copy.deepcopy(s['timeline_events'])
    payload = {'action': 'MEMORY_UPDATE', 'facts': [
        {'text': 'X' * 230, 'event_ids': ['e1']},
        {'text': 'The committee approved the agreement.', 'event_ids': ['e2']}]}
    client = Repeater(payload)
    assert runtime.refresh_summary(client, s, CONFIG, trace, usage, force=True)
    assert client.messages[1]['repair']['constraints']['invalid_fields'][0]['actual_chars'] == 230
    assert s['timeline_events'] == original
    assert s['controller_memory']['facts'] == payload['facts'][1:]
    assert trace['steps'][-1]['observation']['executor_recovery']['omitted_rows'] == payload['facts'][:1]
    assert usage['logical_calls'] == 2 and usage['total_tokens'] == 240
    assert not client.accepted


def test_all_overlong_without_intact_source_preserves_previous_memory_and_delta():
    s, (trace, usage) = state(), trace_usage()
    s['timeline_events'] = [event(summary='X' * 250)]
    client = Repeater({'action': 'MEMORY_UPDATE', 'facts': [{'text': 'X' * 250, 'event_ids': ['e1']}]})
    assert not runtime.refresh_summary(client, s, CONFIG, trace, usage, force=True)
    assert s['controller_memory']['version'] == 0
    assert s['controller_memory']['pending_changes']
    assert not runtime.refresh_summary(client, s, CONFIG, trace, usage, force=True)
    assert len(client.messages) == 2
    assert memory.policy_view(s, SKELETON)['memory']['summary_update_deferred']


def test_length_recovery_never_hides_invalid_provenance():
    payload = {'action': 'MEMORY_UPDATE', 'facts': [{'text': 'X' * 240, 'event_ids': ['unknown']}]}
    with pytest.raises(ValueError, match='unseen'):
        recover_fact_lengths(payload, {'e1'}, {'e1'}, [event()])


def test_extended_summary_limit_applies_to_prompt_validator_and_repair():
    s, (trace, usage) = state(), trace_usage()
    s['timeline_events'] = [event()]
    payload = {'action': 'MEMORY_UPDATE', 'facts': [{'text': 'X' * 300, 'event_ids': ['e1']}]}
    cfg = copy.deepcopy(CONFIG)
    cfg['batch_controller'].update(summary_fact_max_chars=320, summary_fact_target_chars=240)
    client = Repeater(payload)
    assert runtime.refresh_summary(client, s, cfg, trace, usage, force=True)
    assert len(client.messages) == 1
    assert client.messages[0]['text_limits']['max'] == 320
    assert s['controller_memory']['facts'] == payload['facts']
    payload['facts'][0]['text'] = 'X' * 321
    with pytest.raises(FactLengthError) as captured:
        validate_facts(payload, {'e1'}, {'e1'}, max_chars=320, target_chars=240)
    assert captured.value.repair_context['hard_max_chars'] == 320
    assert captured.value.repair_context['target_max_chars'] == 240


def test_extended_recovery_accepts_intact_longer_event_summary():
    source = event(summary='X' * 300)
    payload = {'action': 'MEMORY_UPDATE', 'facts': [{'text': 'X' * 330, 'event_ids': ['e1']}]}
    out, audit = recover_fact_lengths(payload, {'e1'}, {'e1'}, [source], max_chars=320, target_chars=240)
    assert out['facts'][0]['text'] == source['summary']
    assert audit['mode'] == 'select_intact_event_summaries'


@pytest.mark.parametrize('maximum,target', [(320,321), (True,6), (320,5), (320,240.0)])
def test_invalid_summary_limits_fail_before_api(maximum,target):
    with pytest.raises(ValueError, match='Fact text limits'):
        fact_text_limits({'summary_fact_max_chars': maximum, 'summary_fact_target_chars': target})


def test_all_overlong_can_select_whole_source_without_truncation():
    payload = {'action': 'MEMORY_UPDATE', 'facts': [{'text': 'X' * 240, 'event_ids': ['e1']}]}
    out, audit = recover_fact_lengths(payload, {'e1'}, {'e1'}, [event()])
    assert out['facts'][0]['text'] == event()['summary']
    assert audit['mode'] == 'select_intact_event_summaries'


def test_repeat_only_policy_hands_off_without_claiming_autonomous_stop():
    s, (trace, usage) = state(), trace_usage()
    memory.record_query(s, query())
    client = Repeater(action([query()]))
    runtime.run_phase1(client, s, CONFIG, None, trace, usage)
    assert s['phase1_termination'] == 'invalid_policy_handoff'
    assert trace['steps'][-1]['observation']['forced'] is True
    assert trace['steps'][-1]['observation']['completion_established'] is False
    assert not client.accepted
    assert client.messages[1]['repair']['constraints']['duplicate_queries'] == [query()]


def test_partial_repeat_keeps_only_fresh_queries_after_repair():
    s, (trace, usage) = state(), trace_usage()
    memory.record_query(s, query())
    fresh = query('Aurora agreement review outcome')
    client = Repeater(action([query(), fresh]))
    out, _ = runtime.decide(client, s, CONFIG, trace, usage, SKELETON)
    assert out['queries'] == [fresh]
    assert trace['steps'][-1]['label_source'] == 'executor_recovery_not_training_target'
    assert not client.accepted


def test_duplicate_filter_does_not_hide_invalid_time_filter():
    bad = query('Aurora review publication announcement')
    bad['time_filter'] = {'mode': 'none', 'start': '2020-01-01', 'end': None}
    with pytest.raises(ValueError, match='null bounds'):
        recover_duplicate_queries(action([query(), bad]), [query()])


def test_merge_recovery_keeps_distinct_candidate_and_original_evidence():
    existing = event()
    duplicate = {'candidate_id': 'c1', 'status': 'SUPPORTED', 'event': {
        k: existing[k] for k in ('time', 'summary', 'actors', 'location')},
        'evidence_ids': ['d2'], 'confidence': .9}
    fresh = copy.deepcopy(duplicate)
    fresh.update(candidate_id='c2')
    fresh['event'].update(time='2020-01-03', summary='The committee withdrew the approval.')
    candidates = [duplicate, fresh]
    payload = {'thought': 'Add both important developments.', 'operations': [
        {'candidate_id': i, 'operation': 'APPEND', 'target_event_id': None, 'reason': 'new milestone'}
        for i in ('c1', 'c2')]}
    client = Repeater(payload)
    merged, audits = decide_merge(client, {'timeline_events': [existing], 'topic': 'Aurora',
        'model_visible_state': {'events': []}}, candidates, CONFIG)
    assert client.messages[1]['repair']['constraints']['matching_event_id'] == 'e1'
    assert [r['operation'] for r in merged['operations']] == ['DROP', 'APPEND']
    assert merged['executor_recovery']['changes'][0]['candidate_retained_for_review'] == duplicate
    timeline, _ = apply_merge_operations([existing], candidates, merged['operations'])
    assert len(timeline) == 2 and timeline[0] == existing
    assert sum('usage' in a for a in audits) == 2


def test_merge_recovery_does_not_swallow_other_invalid_operations():
    existing = event()
    candidate = {'candidate_id': 'c1', 'status': 'SUPPORTED', 'event': existing}
    with pytest.raises(ValueError, match='duplicate MERGE candidate'):
        recover_exact_duplicate_appends([{'candidate_id': 'c1', 'operation': 'APPEND'}] * 2,
                                       [candidate], [existing])
