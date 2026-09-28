import copy
import json
from pathlib import Path
import sys

import pytest

from chronos_repro.batch_policy import recover_policy_action, validate_action


def query(text='Person career award ceremony'):
    return {'query': text, 'target_lead_ids': [],
            'time_filter': {'mode': 'none', 'start': None, 'end': None}}


def action(rows=None):
    return {'action': 'SEARCH', 'reason': 'Check the remaining event dates.',
            'queries': [query()] if rows is None else rows, 'stop_reason': None}


def test_overlong_reason_preserves_full_audit_and_query_intent():
    payload = action()
    payload['reason'] = 'Do not treat this as complete. ' * 9
    original = copy.deepcopy(payload)
    fixed, audit = recover_policy_action(payload, [], visible_lead_ids=set())
    validate_action(fixed, [], visible_lead_ids=set())
    assert fixed['queries'] == original['queries']
    assert fixed['action'] == original['action']
    assert audit['original_payload'] == original
    assert audit['training_target'] is False
    assert not audit['forced'] and not audit['completion_established']
    assert payload == original


def test_explicit_none_typo_is_canonicalized_without_inventing_bounds():
    row = query()
    row['time_filter'] = {'mode': 'none', 'start': None, 'time_filter': None}
    fixed, audit = recover_policy_action(action([row]), [], visible_lead_ids=set())
    assert fixed['queries'][0]['query'] == row['query']
    assert fixed['queries'][0]['time_filter'] == {'mode': 'none', 'start': None, 'end': None}
    assert audit['normalizations'][0]['original_value'] == row['time_filter']


def test_invisible_lead_association_removed_without_rewriting_query():
    row = query()
    row['target_lead_ids'] = ['old', 'visible', 'visible']
    fixed, audit = recover_policy_action(action([row]), [], visible_lead_ids={'visible'})
    assert fixed['queries'][0]['target_lead_ids'] == ['visible']
    assert fixed['queries'][0]['query'] == row['query']
    assert audit['normalizations'][0]['original_value'] == ['old', 'visible', 'visible']


@pytest.mark.parametrize('filt', [
    {'mode': 'hard', 'start': '2020-01-01'},
    {'mode': 'soft', 'start': None, 'end': None},
    {'mode': 'none', 'start': '2020-01-01', 'end': None},
    {'mode': 'none', 'start': None, 'end': None, 'unexpected': 'meaningful'},
])
def test_ambiguous_filters_are_isolated_and_never_relaxed(filt):
    invalid = {**query('Person earlier legal decision'), 'time_filter': filt}
    fresh = query('Person later retirement announcement')
    fixed, audit = recover_policy_action(action([invalid, fresh]), [], visible_lead_ids=set())
    assert fixed['queries'] == [fresh]
    assert audit['invalid_queries'][0]['query'] == invalid
    assert not audit['forced']
    fixed, audit = recover_policy_action(action([invalid]), [], visible_lead_ids=set())
    assert fixed is None and audit['forced'] and not audit['completion_established']


def test_recovery_still_filters_exact_duplicate_queries():
    old, fresh = query(), query('Person new career appointment')
    payload = action([old, fresh])
    payload['reason'] = 'Long explanation. ' * 20
    fixed, audit = recover_policy_action(payload, [old], visible_lead_ids=set())
    assert fixed['queries'] == [fresh]
    assert audit['removed_queries'] == [old]
    fixed, audit = recover_policy_action(action([old]), [old], visible_lead_ids=set())
    assert fixed is None and audit['kind'] == 'duplicate_query_filter' and audit['forced']


@pytest.mark.parametrize('payload', [None, 'not JSON', {}, {**action(), 'stop_reason': 'null'},
                                     {**action(), 'extra': 1}])
def test_bad_envelope_is_audited_without_executing_queries(payload):
    fixed, audit = recover_policy_action(payload, [], visible_lead_ids=set())
    assert fixed is None and audit['forced']
    assert audit['original_payload'] == payload


def test_valid_stop_is_preserved():
    payload = {'action': 'STOP', 'reason': 'No worthwhile question remains.',
               'queries': [], 'stop_reason': 'LOW_EXPECTED_GAIN'}
    fixed, audit = recover_policy_action(payload, [])
    assert fixed == payload and audit == {}


def test_preextracted_controller_wires_recovery_without_second_api_call(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    import batched_search_phase as controller
    from chronos_repro.llm import ChatResult
    calls = []
    payload = action()
    payload['reason'] = 'Long reason. ' * 20
    payload['queries'][0]['target_lead_ids'] = ['stale']
    payload['queries'][0]['time_filter'] = {'mode': 'none', 'start': None, 'time_filter': None}
    class Client:
        def chat(self, messages, temperature=0):
            calls.append(messages)
            return ChatResult(json.dumps(payload), 'fake', {}, None)
    state = {'topic': 'Person', 'timeline_events': [], '_preextracted_events': True}
    trace = {'steps': [], 'audits': []}
    usage = {k: 0 for k in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'logical_calls', 'http_attempts')}
    monkeypatch.setattr(controller.coverage_pipeline, 'checkpoint', lambda *a: None)
    fixed, _ = controller.decide(Client(), state,
        {'batch_controller': {}, 'label_repair_attempts': 1, 'temperature': 0}, trace, usage, 'SKELETON_EXPLORATION')
    assert len(calls) == 1 and fixed['action'] == 'SEARCH'
    validate_action(fixed, [], visible_lead_ids=set())
    expected = query()
    expected.pop('target_lead_ids')
    assert fixed['queries'][0] == expected
    step = trace['steps'][-1]
    assert step['label_source'] == 'executor_recovery_not_training_target'
    assert step['observation']['executor_recovery']['original_payload'] == payload
    assert not step['observation']['forced']
