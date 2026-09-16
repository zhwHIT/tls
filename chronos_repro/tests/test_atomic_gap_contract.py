import copy
import json
from pathlib import Path

import pytest

from chronos_repro.compact_context import compact_instruction
from chronos_repro.gap_contract import GAP_FEW_SHOTS, validate_atomic_gap, validate_resolution
from chronos_repro.llm import ChatResult
from chronos_repro.tisa_rollout import apply_gap_update, next_open_gap, student_state, validate_gap_memory_action


EVENTS = [
    {'event_id': 'e1', 'time': '2020-01-01', 'summary': GAP_FEW_SHOTS[0]['anchor_summary']},
    {'event_id': 'e2', 'time': '2020-02-01', 'summary': 'The regulator approved the Aurora purchase agreement.'},
    {'event_id': 'e3', 'time': '2020-03-01', 'summary': 'The unrelated satellite launched after a weather delay.'},
]


def gap(gap_id='g1'):
    return {'gap_id': gap_id, 'type': 'MISSING_KEY_EVENT', 'description': 'The agreement requires an approval decision.',
            'status': 'OPEN', 'priority': .8, 'left_event_id': 'e1', 'right_event_id': None,
            'retrieval_target': copy.deepcopy(GAP_FEW_SHOTS[0]['retrieval_target'])}


def initialization(gaps):
    return {'thought': 'Look for the approval required by the agreement.', 'action': 'GAP_MEMORY', 'gaps': gaps}


def resolution():
    return {'resolved_gap_ids': ['g1'], 'resolution_evidence': [
        {'gap_id': 'g1', 'event_id': 'e2', 'quote': 'The regulator approved the Aurora purchase agreement.'}]}


def test_atomic_target_roundtrip_and_no_archive_mutation():
    original = gap()
    result = validate_gap_memory_action(initialization([original]), EVENTS, 4, require_atomic=True)
    assert result['gaps'][0]['retrieval_target'] == original['retrieval_target']
    assert 'attempted_queries' not in original


@pytest.mark.parametrize('field', ['question', 'anchor_quote', 'completion_criterion', 'seed_query'])
def test_missing_target_fields_rejected(field):
    row = gap()
    del row['retrieval_target'][field]
    with pytest.raises(ValueError, match='retrieval_target'):
        validate_atomic_gap(row, EVENTS, required=True)


def test_legacy_archive_compatibility_is_explicit():
    row = gap()
    del row['retrieval_target']
    assert validate_atomic_gap(row, EVENTS) == row
    with pytest.raises(ValueError, match='retrieval_target'):
        validate_atomic_gap(row, EVENTS, required=True)


@pytest.mark.parametrize('question', ['Explain the entire history of Aurora.',
    'Find all events and all causes of this transaction.', 'What was approved? Who objected?',
    'Identify the underlying drivers of the movement.', '补齐完整历史与所有原因。'])
def test_obviously_broad_or_multiple_questions_rejected(question):
    row = gap()
    row['retrieval_target']['question'] = question
    with pytest.raises(ValueError):
        validate_atomic_gap(row, EVENTS, required=True)


def test_completion_condition_cannot_be_a_tautology():
    row = gap()
    row['retrieval_target']['completion_criterion'] = 'The gap is resolved with enough information.'
    with pytest.raises(ValueError, match='observable fact'):
        validate_atomic_gap(row, EVENTS, required=True)


def test_anchorless_and_hallucinated_anchor_quotes_rejected():
    row = gap()
    row['left_event_id'] = None
    with pytest.raises(ValueError, match='anchor'):
        validate_atomic_gap(row, EVENTS, required=True)
    row = gap()
    row['retrieval_target']['anchor_quote'] = 'A fictional investigation was opened.'
    with pytest.raises(ValueError, match='verbatim'):
        validate_atomic_gap(row, EVENTS, required=True)


def test_resolution_needs_a_real_nonconflicted_event_quote():
    assert validate_resolution(resolution(), gap(), EVENTS)
    with pytest.raises(ValueError, match='resolution_evidence'):
        validate_resolution({'resolved_gap_ids': ['g1']}, gap(), EVENTS)
    raw = resolution()
    raw['resolution_evidence'][0]['quote'] = 'The regulator rejected the purchase.'
    with pytest.raises(ValueError, match='verbatim'):
        validate_resolution(raw, gap(), EVENTS)
    events = copy.deepcopy(EVENTS)
    events[1]['conflict'] = True
    with pytest.raises(ValueError, match='conflicted'):
        validate_resolution(resolution(), gap(), events)


def test_local_update_cannot_resolve_other_gaps():
    with pytest.raises(ValueError, match='only the active'):
        validate_resolution({'resolved_gap_ids': ['g2']}, gap(), EVENTS)


def test_duplicate_reproposal_is_archived_not_rescheduled():
    original = {'gaps': [gap()], 'history': []}
    updated, audit = apply_gap_update(original, 'g1', 'Aurora regulatory approval', [], [gap('g2')], EVENTS, 4)
    assert audit['deduplicated_gap_ids'] == ['g2']
    assert len(updated['gaps']) == 1
    assert updated['duplicate_gap_proposals'][0]['proposal']['gap_id'] == 'g2'
    assert 'duplicate_gap_proposals' not in original


def test_attempt_limit_defers_without_claiming_resolution():
    memory = {'gaps': [gap()], 'history': []}
    for _ in range(2):
        memory, audit = apply_gap_update(memory, 'g1', 'Aurora approval decision', [], [], EVENTS, 4, maximum_attempts=2)
    assert memory['gaps'][0]['status'] == 'DEFERRED'
    assert audit['completion_established'] is False
    assert next_open_gap(memory) is None
    assert len(memory['gaps'][0]['attempted_queries']) == 2


def test_resolution_on_last_attempt_is_not_deferred():
    memory = {'gaps': [{**gap(), 'attempted_queries': ['earlier query']}], 'history': []}
    updated, _ = apply_gap_update(memory, 'g1', 'Aurora approval decision', ['g1'], [], EVENTS, 4, maximum_attempts=2)
    assert updated['gaps'][0]['status'] == 'RESOLVED'


def test_compact_search_preserves_target_but_omits_global_exploration():
    visible = student_state('fixture', 'Aurora', 'GAP_REFINEMENT', EVENTS,
        {'gaps': [gap()], 'exploration': {'stage_outline': [{'period': '2020', 'description': 'global'}]}}, ['SEARCH'], gap())
    result = compact_instruction({'stage': 'GAP_REFINEMENT: analyze one gap then SEARCH',
                                  'student_visible_state': visible})['student_visible_state']
    assert result['active_gap']['retrieval_target'] == gap()['retrieval_target']
    assert 'stage_outline' not in result['memory']
    assert visible['memory']['exploration']['stage_outline']


class FixtureClient:
    def __init__(self, rows):
        self.rows = list(rows)
        self.requests = []

    def chat(self, messages, temperature=0):
        self.requests.append(copy.deepcopy(messages))
        return ChatResult(json.dumps(self.rows.pop(0)), 'offline-fixture', {'total_tokens': 1}, None)


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    import run_tisa_two_phase_annotation
    return run_tisa_two_phase_annotation


def test_real_initialization_retries_broad_gap_and_keeps_contract(runner):
    broad = gap()
    broad['retrieval_target']['question'] = 'Describe the full history of the acquisition.'
    client = FixtureClient([{'student_output': initialization([broad])}, {'student_output': initialization([gap()])}])
    _, output, _ = runner.initialize_gap_memory(client, {'dataset': 'fixture', 'topic': 'Aurora', 'timeline_events': EVENTS}, [],
        {'phase2_max_gaps': 4, 'phase2_teacher_guidance': False, 'label_repair_attempts': 1, 'temperature': 0})
    assert output['student_output']['gaps'][0]['retrieval_target'] == gap()['retrieval_target']
    assert len(client.requests) == 2
    first = json.loads(client.requests[0][1]['content'])
    assert first['few_shots']
    assert 'retrieval_target' in first['required_json']['student_output']['gaps'][0]
    assert 'teacher_only_reference_events' not in first


def test_real_update_retries_unproved_resolution(runner):
    visible = student_state('fixture', 'Aurora', 'GAP_REFINEMENT', EVENTS, {'gaps': [gap()]}, ['GAP_MEMORY'], gap())
    base = {'thought': 'The approval decision now answers the question.', 'action': 'GAP_MEMORY', 'gaps': []}
    client = FixtureClient([{'student_output': {**base, 'resolved_gap_ids': ['g1']}},
                            {'student_output': {**base, **resolution()}}])
    result, _ = runner.refresh_gap_memory(client, visible, gap(), {'applied': []}, [],
        {'phase2_teacher_guidance': False, 'label_repair_attempts': 1, 'temperature': 0})
    assert result['resolution_evidence'] == resolution()['resolution_evidence']
    assert len(client.requests) == 2


def test_new_gap_must_stay_connected_to_local_repair(runner):
    other = gap('g3')
    other['left_event_id'] = 'e3'
    other['retrieval_target']['anchor_quote'] = 'after a weather delay'
    other['retrieval_target']['question'] = 'When did the weather delay end for the satellite?'
    visible = student_state('fixture', 'Aurora', 'GAP_REFINEMENT', EVENTS, {'gaps': [gap()]}, ['GAP_MEMORY'], gap())
    client = FixtureClient([{'student_output': {'thought': 'Look for the weather decision.', 'action': 'GAP_MEMORY',
                                               'resolved_gap_ids': [], 'gaps': [other]}}])
    with pytest.raises(runner.LabelValidationError, match='linked'):
        runner.refresh_gap_memory(client, visible, gap(), {'applied': []}, [],
            {'phase2_teacher_guidance': False, 'label_repair_attempts': 0, 'temperature': 0})


def test_legacy_gap_cannot_silently_enter_new_search_policy(runner):
    row = gap()
    del row['retrieval_target']
    visible = student_state('fixture', 'Aurora', 'GAP_REFINEMENT', EVENTS, {'gaps': [row]}, ['SEARCH'], row)
    client = FixtureClient([])
    with pytest.raises(ValueError, match='retrieval_target'):
        runner.decide_gap_search(client, visible, [], {})
    assert not client.requests


def test_bad_attempt_limit_fails_before_any_model_request(runner):
    client = FixtureClient([])
    with pytest.raises(ValueError, match='positive integer'):
        runner.run_phase2(client, {}, [], {'phase2_max_attempts_per_gap': 0}, Path('.'), {}, {})
    assert not client.requests


def test_deferred_gap_keeps_runner_terminal_incomplete(runner, monkeypatch):
    row = gap()
    visible = student_state('fixture', 'Aurora', 'GAP_REFINEMENT', EVENTS, {'gaps': [row]}, ['SEARCH'], row)
    monkeypatch.setattr(runner, 'initialize_gap_memory', lambda *a: (visible,
        {'student_output': {'gaps': [row]}, 'teacher_alignment': {}}, []))
    monkeypatch.setattr(runner, 'decide_gap_search', lambda *a: ({'thought': 'Find the required approval decision.',
        'action': 'SEARCH', 'gap_id': 'g1', 'query': 'Aurora approval decision'}, []))
    monkeypatch.setattr(runner, 'search', lambda *a, **kw: [])
    monkeypatch.setattr(runner, 'fetch_documents', lambda *a, **kw: [])
    monkeypatch.setattr(runner, 'verify_batch', lambda *a: ({'candidates': []}, []))
    monkeypatch.setattr(runner, 'execute_merge', lambda *a: ({'operations': []}, []))
    monkeypatch.setattr(runner, 'refresh_gap_memory', lambda *a: (
        {'resolved_gap_ids': [], 'resolution_evidence': [], 'gaps': []}, []))
    client = FixtureClient([])
    state = {'dataset': 'fixture', 'topic': 'Aurora', 'timeline_events': EVENTS, 'search_history': []}
    trace = {'trajectory_id': 'offline', 'dataset': 'fixture', 'topic': 'Aurora', 'steps': [], 'audits': []}
    config = {'phase2_max_gaps': 4, 'phase2_max_gap_cycles': 5, 'phase2_max_attempts_per_gap': 1,
              'phase2_teacher_guidance': False, 'top_k': 4, 'document_char_limit': 100}
    result = runner.run_phase2(client, state, [], config, Path('.'), trace, {})
    assert result['memory']['gaps'][0]['status'] == 'DEFERRED'
    assert trace['steps'][-1]['observation']['forced'] is True
    assert trace['steps'][-1]['observation']['open_gap_count'] == 1
    assert not client.requests
    assert not any(r['metadata']['action'] == 'STOP' for r in runner.compile_sft_rows(trace))
