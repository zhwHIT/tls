"""Offline regression coverage for the failed v7 rollout and audit findings."""
import copy
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from chronos_repro.compact_context import CompactContextClient, ContextLimitError, compact_instruction
from chronos_repro.date_evidence import validate_date_evidence
from chronos_repro.exploration_memory import initial_memory, validate_memory_output
from chronos_repro.label_repair import recover_boundary_summary, repair_details
from chronos_repro.llm import ChatResult
from chronos_repro.tisa_rollout import validate_thought, validate_gap_search_action, validate_gap_memory_action


@pytest.fixture(autouse=True)
def scripts(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))


def test_length_repair_has_measured_constraint():
    raw = {'boundary_assessment': 'x' * 725}
    repaired = repair_details(raw, 'boundary_assessment must be nonempty text <= 600 characters', 1)
    assert repaired['field_constraint'] == {'field': 'boundary_assessment', 'required_type': 'nonempty string',
        'hard_max_chars': 600, 'target_max_chars': 300, 'previous_chars': 725}
    assert repaired['previous_response'] == raw
    assert repaired['previous_response'] is not raw


def memory_payload():
    return {'action': 'MEMORY_UPDATE', 'thought': 'Keep the existing evidence.', 'observations': [],
        'discovered_keywords': [], 'stage_outline': [], 'next_search_directions': [],
        'boundary_assessment': 'x' * 725, 'skeleton_ready': True}


def test_boundary_recovery_is_conservative_and_auditable():
    raw = memory_payload()
    checked, audit = recover_boundary_summary(raw, 'boundary_assessment must be nonempty text <= 600 characters',
        lambda p: validate_memory_output(p, initial_memory([]), []))
    assert checked['skeleton_ready'] is False
    assert len(checked['boundary_assessment']) <= 600
    assert 'unconfirmed' in checked['boundary_assessment']
    assert audit['raw_value'] == raw['boundary_assessment']
    assert audit['training_target'] is False
    assert raw['skeleton_ready'] is True


def test_boundary_recovery_cannot_accept_other_bad_fields():
    raw = memory_payload()
    raw['stage_outline'] = [{'period': '2020', 'description': 'Invented stage', 'evidence_ids': ['unknown']}]
    with pytest.raises(ValueError):
        recover_boundary_summary(raw, 'boundary_assessment must be nonempty text <= 600 characters',
            lambda p: validate_memory_output(p, initial_memory([]), []))


def test_invalid_json_response_is_accounted_and_supplied_to_repair():
    import run_tisa_two_phase_annotation as runner
    class Client:
        def __init__(self):
            self.requests = []
        def chat(self, messages, temperature=0):
            self.requests.append(copy.deepcopy(messages))
            return ChatResult('not json' if len(self.requests) == 1 else '{"ok": true}',
                              'fixture', {'total_tokens': 7}, None)
    client = Client()
    checked, audits = runner.repaired_call(client, 'system', {'stage': 'fixture'}, lambda p: p,
        {'label_repair_attempts': 1, 'temperature': 0})
    assert checked['ok']
    assert sum(a.get('usage', {}).get('total_tokens', 0) for a in audits) == 14
    assert json.loads(client.requests[1][1]['content'])['repair']['previous_response'] == 'not json'


def test_terminal_bad_response_remains_in_exception_audits():
    import run_tisa_two_phase_annotation as runner
    client = SimpleNamespace(chat=lambda *a, **kw: ChatResult('not json', 'fixture', {'total_tokens': 9}, None))
    with pytest.raises(runner.LabelValidationError) as error:
        runner.repaired_call(client, 'system', {}, lambda p: p, {'label_repair_attempts': 0, 'temperature': 0})
    assert sum(a.get('usage', {}).get('total_tokens', 0) for a in error.value.audits) == 9


def test_context_overflow_is_not_retried_and_clears_stale_exchange():
    import run_tisa_two_phase_annotation as runner
    calls = []
    client = CompactContextClient(SimpleNamespace(model='fixture', chat=lambda *a, **k: calls.append(1)),
                                  {'max_request_chars': 1})
    client.last_exchange = {'response': 'old'}
    with pytest.raises(ContextLimitError):
        runner.repaired_call(client, 'system', {'stage': 'SEARCH'}, lambda p: p,
                             {'label_repair_attempts': 2, 'temperature': 0})
    assert not calls
    assert client.last_exchange is None


@pytest.mark.parametrize('key', ['teacher_only_dates', 'private_reference_events', 'gold_events', 'teacher_alignment'])
def test_nested_teacher_fields_cannot_reach_compact_controller(key):
    with pytest.raises(ValueError, match='private'):
        compact_instruction({'stage': 'SEARCH', 'repair': {'nested': [{key: ['private']} ]}})


def test_date_incompatible_lead_is_not_closed():
    import coverage_pipeline as pipeline
    state = {'timeline_events': [{'event_id': 'e1', 'time': '2021-01-01', 'summary': 'Aurora landed.'}],
        'candidate_pool': [{'candidate_id': 'c1', 'status': 'INSUFFICIENT',
            'event': {'time': '2020-01-01', 'summary': 'Aurora landed.', 'actors': ['Aurora']},
            'evidence_ids': ['p'], 'relevance_pass': True, 'contribution_pass': True}]}
    pipeline.refresh_evidence_memory(state)
    assert state['exploration_memory']['lead_queue'][0]['status'] != 'RESOLVED'
    state['timeline_events'][0]['time'] = '2020-01-01'
    pipeline.refresh_evidence_memory(state)
    assert state['exploration_memory']['lead_queue'][0]['status'] == 'RESOLVED'


def test_gap_checkpoint_keeps_latest_gap_memory(tmp_path):
    import coverage_pipeline as pipeline
    gaps = {'gaps': [{'gap_id': 'new', 'status': 'OPEN'}]}
    pipeline.checkpoint({'_output_dir': tmp_path, 'timeline_events': [], 'gap_memory': gaps}, {'steps': []}, {})
    assert json.loads((tmp_path / 'checkpoint.json').read_text(encoding='utf-8'))['gap_memory'] == gaps


def test_gap_description_is_rejected_not_silently_truncated():
    from test_coverage_audit_repairs import gap
    with pytest.raises(ValueError, match='180'):
        validate_gap_memory_action({'thought': 'Inspect the existing sequence.', 'action': 'GAP_MEMORY',
                                   'gaps': [{**gap('g1'), 'description': 'x' * 181}]}, [], 2)


def test_output_types_are_not_coerced_to_fake_text():
    with pytest.raises(ValueError, match='string'):
        validate_thought({'reason': 'This is not a string'})
    action = {'thought': 'Inspect missing details.', 'action': 'SEARCH', 'gap_id': 'g1', 'query': ['one', 'two', 'three']}
    with pytest.raises(ValueError, match='string'):
        validate_gap_search_action(action, {'gap_id': 'g1'})
    action['query'] = ''
    assert validate_gap_search_action(action, {'gap_id': 'g1'})['query'] == ''


def test_failed_run_needs_explicit_replay_without_resetting_ledger(tmp_path, monkeypatch):
    import coverage_preflight as preflight
    monkeypatch.setattr(preflight, 'coverage_envelope', lambda c: {})
    sentinel = object()
    monkeypatch.setattr(preflight, 'LimitedDeepSeekClient', lambda **kwargs: sentinel)
    (tmp_path / 'trajectory.json').write_text('{"status":"stopped_error"}', encoding='utf-8')
    ledger = tmp_path / 'request_ledger.json'
    ledger.write_text('{"http_requests_total":77}', encoding='utf-8')
    config = {'model': 'fixture', 'max_api_requests': 400}
    with pytest.raises(ValueError, match='replay-from-start'):
        preflight.guarded_coverage_client(config, tmp_path)
    assert preflight.guarded_coverage_client(config, tmp_path, allow_replay_from_start=True) is sentinel
    assert json.loads(ledger.read_text())['http_requests_total'] == 77
    (tmp_path / 'trajectory.json').write_text('{"status":"stopped_insufficient_balance"}', encoding='utf-8')
    with pytest.raises(ValueError, match='balance-stopped'):
        preflight.guarded_coverage_client(config, tmp_path, allow_replay_from_start=True)


@pytest.mark.parametrize('quote', ['2012-06-07', 'The assembly formed during 2012-06-07 to 2012-06-12.'])
def test_cropped_iso_interval_endpoint_is_rejected(quote):
    doc = {'id': 'p', 'text': 'The assembly formed during 2012-06-07 to 2012-06-12.'}
    with pytest.raises(ValueError, match='range'):
        validate_date_evidence('2012-06-07', {'document_id': 'p', 'quote': quote,
                              'time_expression': '2012-06-07'}, [doc], ['p'])


def test_independent_point_mention_survives_other_interval():
    doc = {'id': 'p', 'text': 'Work ran 2012-06-07 to 2012-06-12. The launch occurred on 2012-06-07.'}
    checked = validate_date_evidence('2012-06-07', {'document_id': 'p',
        'quote': 'The launch occurred on 2012-06-07.', 'time_expression': '2012-06-07'}, [doc], ['p'])
    assert checked['normalization'] == 'explicit'


def test_suite_consumes_real_strict_coverage_field(tmp_path, monkeypatch):
    import evaluate_multitopic_timeline as evaluator
    output = tmp_path / 'run'
    output.mkdir()
    config = {'dataset': 'fixture', 'topic': 'Aurora', 'phase2_teacher_guidance': False,
              'pipeline_revision': 'test', 'data': 'data'}
    trajectory = {**config, 'status': 'ok', 'final_events': [
        {'event_id': 'e1', 'time': '2020-01-01', 'summary': 'Aurora launched.'}]}
    (output / 'run_config.json').write_text(json.dumps(config), encoding='utf-8')
    (output / 'trajectory.json').write_text(json.dumps(trajectory), encoding='utf-8')
    topic = SimpleNamespace(topic_id='Aurora', timelines=({date(2020, 1, 1): ('Aurora launched.',),
                                                         date(2020, 1, 2): ('Aurora landed.',)},))
    monkeypatch.setattr(evaluator, 'iter_topics', lambda root: iter([topic]))
    result = evaluator.evaluate_suite(tmp_path, {'pipeline_revision': 'test', 'topics': [
        {'dataset': 'fixture', 'topic': 'Aurora', 'output_dir': 'run'},
        {'dataset': 'fixture', 'topic': 'Missing', 'output_dir': 'missing'}]})
    assert not result['all_complete']
    assert result['topics'][0]['gold_date_hits'] == 1
    assert result['topics'][0]['gold_date_count'] == 2
