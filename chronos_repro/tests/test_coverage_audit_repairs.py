import copy
import json
from pathlib import Path

import pytest

from chronos_repro.date_evidence import validate_date_evidence
from chronos_repro.tisa_rollout import apply_gap_update, next_open_gap, validate_gap_memory_action


def gap(key):
    return {'gap_id': key, 'type': 'TEMPORAL_GAP', 'description': 'The mission transition requires additional evidence.',
            'priority': .8, 'status': 'OPEN', 'left_event_id': None, 'right_event_id': None, 'attempted_queries': []}


@pytest.mark.parametrize('expression', ['2012-06-07 to 2012-06-12', 'June 7-12, 2012', 'June 7 through June 12, 2012'])
def test_interval_is_not_a_point(expression):
    doc = {'id': 'p', 'text': 'The assembly formed during ' + expression + '.'}
    with pytest.raises(ValueError, match='range'):
        validate_date_evidence('2012-06-07', {'document_id': 'p', 'quote': doc['text'], 'time_expression': expression}, [doc], ['p'])


def test_gap_history_does_not_consume_new_gap_capacity():
    memory, audit = apply_gap_update({'gaps': [gap('g1')]}, 'g1', 'mission missing stage', ['g1'], [gap('g2')], [], 1)
    assert audit['new_gap_ids'] == ['g2']
    assert next_open_gap(memory)['gap_id'] == 'g2'
    memory, audit = apply_gap_update(memory, 'g2', 'mission later stage', [], [gap('g3')], [], 1)
    assert audit['new_gap_ids'] == ['g3']
    assert memory['overflow_gap_ids'] == ['g3']
    assert len(memory['gaps']) == 3


def test_initial_gap_cannot_claim_to_be_previously_resolved():
    with pytest.raises(ValueError, match='OPEN'):
        validate_gap_memory_action({'action': 'GAP_MEMORY', 'thought': 'Inspect the visible event sequence.',
                                   'gaps': [{**gap('g1'), 'status': 'RESOLVED'}]}, [], 2)


def test_repeated_validation_error_produces_distinct_repair_request(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    import run_tisa_two_phase_annotation as runner
    requests = []
    def call(client, system, instruction, temperature):
        requests.append(copy.deepcopy(instruction))
        return {'invalid': True}, {}
    monkeypatch.setattr(runner, 'call_json', call)
    def reject(payload):
        raise ValueError('same failure')
    with pytest.raises(runner.LabelValidationError):
        runner.repaired_call(None, 'system', {'stage': 'test'}, reject, {'label_repair_attempts': 2, 'temperature': 0})
    assert requests[1] != requests[2]
    assert requests[2]['repair']['previous_response'] == {'invalid': True}


def test_uncertain_memory_is_preserved_and_new_evidence_refreshes_it(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    import coverage_pipeline as pipeline
    state = {'timeline_events': [], 'candidate_pool': [{'candidate_id': 'c1', 'status': 'INSUFFICIENT',
        'event': {'time': None, 'summary': 'Aurora landed on the target.', 'actors': ['Aurora']},
        'evidence_ids': ['p'], 'relevance_pass': True, 'contribution_pass': True}]}
    pipeline.refresh_evidence_memory(state)
    assert state['exploration_memory']['unresolved_lead_count'] == 1
    identity = state['exploration_memory']['lead_queue'][0]['lead_id']
    state['timeline_events'] = [{'event_id': 'e1', 'time': '2020-01-01', 'summary': 'Aurora landed on the target.'}]
    pipeline.refresh_evidence_memory(state)
    assert state['exploration_memory']['lead_queue'][0]['lead_id'] == identity
    assert state['exploration_memory']['lead_queue'][0]['status'] == 'RESOLVED'
    assert state['exploration_memory']['observed_date_range']['from'] == '2020-01-01'


def test_hard_window_applies_to_historical_reader_passages(tmp_path):
    from test_coverage_pipeline import database
    from chronos_repro.evidence_access import EvidenceReader
    path = database(tmp_path, [('old', 'Aurora', 'Aurora mission landing engine.', '2010-01-01', 'aurora'),
                               ('new', 'Aurora', 'Aurora mission exploration.', '2020-01-01', 'aurora')])
    reader = EvidenceReader(path, 'aurora', {})
    reader.add_results([{'id': 'old'}])
    reader.add_results([{'id': 'new'}])
    selected = reader.select('Aurora mission landing engine', 2, temporal={
        'date_filter_mode': 'hard', 'date_from': '2020-01-01', 'date_to': '2020-12-31'})
    assert len(selected) == 1 and selected[0]['document_id'] == 'new'
    assert selected[0]['reader_date_filter']['in_range']
