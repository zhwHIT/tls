import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chronos_repro.full_timeline import validate_verified_candidates
from chronos_repro.llm import ChatResult, DeepSeekClient, RetryableLLMError, LLMError
from chronos_repro.verification_efficiency import extraction_key, partition_exact_duplicates


DOCUMENT = {'id': 'p', 'text': 'Aurora launched on 2020-01-01. Aurora landed on 2020-01-02.'}


def candidate(key='c1', landed=False):
    day = '2020-01-02' if landed else '2020-01-01'
    verb = 'landed' if landed else 'launched'
    return {'candidate_id': key, 'status': 'SUPPORTED',
            'event': {'time': day, 'summary': f'Aurora {verb} its first mission.', 'actors': ['Aurora'], 'location': None},
            'evidence_ids': ['p'], 'confidence': .9, 'relevance_pass': True, 'contribution_pass': True,
            'date_evidence': {'document_id': 'p', 'quote': f'Aurora {verb} on {day}.', 'time_expression': day}}


def payload(rows, complete=True):
    return {'thought': 'Inspect the explicitly dated mission evidence.', 'candidates': rows, 'extraction_complete': complete}


class Responses:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.requests = []

    def chat(self, messages, temperature=0):
        self.requests.append(json.loads(messages[1]['content']))
        row = next(self.rows)
        if isinstance(row, Exception):
            raise row
        return ChatResult(row if isinstance(row, str) else json.dumps(row), 'fake', {'total_tokens': 10}, 'offline')


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    import run_full_timeline_api_agent
    return run_full_timeline_api_agent


def run_verify(runner, responses):
    client = Responses(responses)
    out, audits = runner.verify_batch(client, {'model_visible_state': {'events': [], 'memory': {}}},
        [DOCUMENT], {'temperature': 0, 'label_repair_attempts': 1, 'max_candidates_per_round': 6,
                     'coverage_extraction': True, 'strict_date_evidence': True})
    return out, audits, client.requests


def test_repair_only_bad_slot_preserves_good_candidate_and_accounting(runner):
    good, bad = candidate(), candidate('c2', True)
    bad['date_evidence']['quote'] = 'Invented landing quote on 2020-01-02.'
    fixed = {**candidate('c2', True), 'repair_index': 1}
    out, audits, requests = run_verify(runner, [payload([good, bad]), payload([fixed])])
    expected_good = validate_verified_candidates([good], [DOCUMENT], 6)[0]
    assert out['candidates'][0] == expected_good
    assert len(out['candidates']) == 2 and out['extraction_complete']
    assert out['partial_repair_applied'] and not out['training_target']
    repair = requests[1]['repair']
    assert repair['mode'] == 'invalid_candidates_only'
    assert repair['retained_candidate_ids'] == ['c1']
    assert [r['repair_index'] for r in repair['invalid_candidates']] == [1]
    assert requests[1]['limits']['maximum_candidates'] == 1
    assert 'previous_response' not in repair
    assert sum(a.get('usage', {}).get('total_tokens', 0) for a in audits) == 20


def test_failed_repair_cannot_mutate_good_candidate_or_claim_completion(runner):
    good, bad = candidate(), candidate('c2', True)
    bad['date_evidence']['quote'] = 'Invented quote.'
    attempted_change = {**candidate(), 'repair_index': 0}
    attempted_change['event']['summary'] = 'Aurora cancelled its first mission.'
    out, _, _ = run_verify(runner, [payload([good, bad]), payload([attempted_change])])
    assert out['repair_exhausted'] and not out['extraction_complete']
    assert out['candidates'] == validate_verified_candidates([good], [DOCUMENT], 6)
    assert len(out['quarantined_candidates']) == 1


def test_metadata_repair_does_not_request_valid_candidates_again(runner):
    first = payload([candidate()])
    first['thought'] = 'x'
    out, _, requests = run_verify(runner, [first, payload([])])
    assert len(out['candidates']) == 1
    assert requests[1]['required_json']['candidates'] == []
    assert requests[1]['repair']['invalid_candidates'] == []


def test_partial_repair_cannot_turn_incomplete_extraction_into_complete(runner):
    bad = candidate()
    bad['date_evidence']['quote'] = 'Invented.'
    out, _, _ = run_verify(runner, [payload([bad], False), payload([{**candidate(), 'repair_index': 0}])])
    assert len(out['candidates']) == 1 and not out['extraction_complete']


def test_parse_failure_still_counts_returned_usage(runner):
    out, audits, requests = run_verify(runner, ['not valid JSON', payload([candidate()])])
    assert len(out['candidates']) == 1 and len(requests) == 2
    assert sum(a.get('usage', {}).get('total_tokens', 0) for a in audits) == 20


def test_missing_date_never_falls_back_to_publication_metadata(runner):
    doc = {**DOCUMENT, 'text': 'Aurora launched its first mission.', 'publication_date': '2020-01-01'}
    bad = candidate()
    bad['date_evidence']['quote'] = doc['text']
    client = Responses([payload([bad])])
    out, _ = runner.verify_batch(client, {'model_visible_state': {'events': [], 'memory': {}}}, [doc],
        {'temperature': 0, 'label_repair_attempts': 0, 'max_candidates_per_round': 6, 'coverage_extraction': True})
    assert out['candidates'] == [] and out['repair_exhausted']


def test_transport_four_retries_means_five_attempts(monkeypatch):
    attempts = []
    def fail(*args):
        attempts.append(1)
        raise RetryableLLMError('offline simulated transient error')
    monkeypatch.setattr(DeepSeekClient, '_chat_once', fail)
    with pytest.raises(LLMError, match='5 attempts'):
        DeepSeekClient(max_retries=4, retry_backoff_seconds=0).chat([])
    assert len(attempts) == 5


def test_cache_ignores_reader_ranking_but_not_evidence_phase_or_protocol():
    state = {'phase': 'SKELETON', 'keywords': ['Aurora'], 'memory': {'already_extracted': []}}
    config = {'model': 'fake', 'label_repair_attempts': 1}
    original = extraction_key([DOCUMENT], state, config, 6)
    assert original == extraction_key([{**DOCUMENT, 'reader_score': 42, 'reader_origin': 'historical_reuse'}], state, config, 6)
    assert original != extraction_key([{**DOCUMENT, 'context_before': '2021'}], state, config, 6)
    assert original != extraction_key([{**DOCUMENT, 'temporal_annotations': [{'date': '2021-01-01'}]}], state, config, 6)
    assert original != extraction_key([DOCUMENT], {**state, 'phase': 'GAP_REFINEMENT'}, config, 6)
    assert original != extraction_key([DOCUMENT], state, {**config, 'strict_date_evidence': False}, 6)


def existing(row):
    return {**copy.deepcopy(row['event']), 'event_id': 'event-001', 'evidence_ids': copy.deepcopy(row['evidence_ids']),
            'date_evidence': copy.deepcopy(row['date_evidence']), 'confidence': row['confidence'], 'conflict': False}


@pytest.mark.parametrize('change', ['date', 'summary', 'evidence', 'quote', 'conflict', 'actors', 'confidence'])
def test_local_duplicate_filter_preserves_material_changes(change):
    row = candidate()
    old = existing(row)
    if change == 'date': row['event']['time'] = '2020-01-02'
    elif change == 'summary': row['event']['summary'] = 'Aurora cancelled its first mission.'
    elif change == 'evidence': row['evidence_ids'].append('new-source')
    elif change == 'quote': row['date_evidence']['quote'] += ' Further details.'
    elif change == 'conflict': row['status'] = 'CONFLICTED'
    elif change == 'actors': row['event']['actors'].append('Other actor')
    elif change == 'confidence': row['confidence'] = .95
    remaining, drops, _ = partition_exact_duplicates([row], [old])
    assert remaining == [row] and drops == []


def test_exact_duplicates_skip_merge_model_but_leave_auditable_drop(runner):
    import run_tisa_two_phase_annotation as pipeline
    row = candidate()
    timeline = [existing(row)]
    state = {'timeline_events': copy.deepcopy(timeline)}
    trace = {'steps': [], 'audits': []}
    # No client: any accidental model call fails this offline test.
    merged, applied = pipeline.execute_merge(None, state, [row], {}, trace, {}, 'SKELETON', {'events': timeline})
    assert merged['operations'][0]['operation'] == 'DROP'
    assert state['timeline_events'] == timeline and trace['audits'] == []
    assert trace['steps'][-1]['observation']['local_drop_count'] == 1
    assert not trace['steps'][-1]['label_source'].startswith('deepseek')


def test_mixed_merge_sends_only_nonduplicates_to_model(runner, monkeypatch):
    import run_tisa_two_phase_annotation as pipeline
    first, second = candidate(), candidate('c2', True)
    state = {'timeline_events': [existing(first)]}
    received = []
    def decide(client, state, candidates, config):
        received.extend(candidates)
        return {'thought': 'Append the newly evidenced landing event.',
                'operations': [{'candidate_id': 'c2', 'operation': 'APPEND', 'target_event_id': None, 'reason': 'Distinct event.'}]}, []
    monkeypatch.setattr(pipeline, 'decide_merge', decide)
    merged, _ = pipeline.execute_merge(None, state, [first, second], {}, {'steps': [], 'audits': []}, {}, 'SKELETON', {})
    assert received == [second] and len(state['timeline_events']) == 2
    assert len(merged['operations']) == 2 and not merged['training_target']


def test_no_progress_repeat_reuses_cache_and_remains_incomplete(runner, monkeypatch):
    import coverage_pipeline as pipeline
    import run_tisa_two_phase_annotation as full
    calls = []
    def verify(*args):
        calls.append(1)
        return payload([], False), []
    monkeypatch.setattr(full, 'verify_batch', verify)
    reader = SimpleNamespace(passages={}, summary=lambda: {}, processed=set())
    state = {'dataset': 'fixture', 'topic': 'Aurora', 'keywords': ['Aurora'], 'timeline_events': [], '_evidence_reader': reader}
    config = {'model': 'fake', 'label_repair_attempts': 1, 'coverage_pipeline': {'verification_batch_passages': 1, 'candidates_per_batch': 6}}
    trace = {'steps': [], 'audits': []}
    for score in [1, 2, 3]:
        pipeline.process_passages(None, state, [{**DOCUMENT, 'reader_score': score}], config, trace, {}, 'SKELETON_EXPLORATION', {})
    assert len(calls) == 1
    assert len(state['incomplete_extraction']) == 1 and not reader.processed
    assert [s['observation']['cache_reused'] for s in trace['steps'] if s['action'] == 'VERIFY'] == [False, True, True]


def test_identical_candidates_within_batch_keep_one_representative():
    first = candidate()
    second = {**copy.deepcopy(first), 'candidate_id': 'c2'}
    pending, drops, audit = partition_exact_duplicates([first, second], [])
    assert pending == [first] and [row['candidate_id'] for row in drops] == ['c2']
    assert audit[0]['representative_candidate_id'] == 'c1'


def test_partial_repair_cannot_reuse_a_retained_candidate_id(runner):
    bad = candidate('c2', True)
    bad['date_evidence']['quote'] = 'Invented.'
    repaired = {**candidate('c1', True), 'repair_index': 1}
    out, _, _ = run_verify(runner, [payload([candidate(), bad]), payload([repaired])])
    assert [r['candidate_id'] for r in out['candidates']] == ['c1']
    assert out['candidates'][0]['event']['time'] == '2020-01-01'
    assert out['repair_exhausted'] and not out['extraction_complete']


def test_repair_context_limit_preserves_valid_rows_without_inventing_completion(runner):
    from chronos_repro.compact_context import ContextLimitError
    bad = candidate('c2', True)
    bad['date_evidence']['quote'] = 'Invented.'
    out, audits, _ = run_verify(runner, [payload([candidate(), bad]), ContextLimitError('offline preflight over 3800')])
    assert [r['candidate_id'] for r in out['candidates']] == ['c1']
    assert out['repair_exhausted'] and not out['extraction_complete']
    assert audits[-1]['repair_context_limit']
    assert sum(a.get('usage', {}).get('total_tokens', 0) for a in audits) == 10
