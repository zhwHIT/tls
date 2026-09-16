import copy
import json
from pathlib import Path

import pytest

from chronos_repro.date_evidence import explicit_dates
from chronos_repro.full_timeline import validate_verified_candidates, apply_merge_operations, validate_resolved_update
from chronos_repro.exploration_control import similar_intent, check_search_progress, record_cycle_outcome, validate_deferred_directions
from chronos_repro.exploration_memory import initial_memory, validate_memory_output, apply_memory_output, skeleton_stop_allowed
from chronos_repro.tisa_rollout import next_open_gap


def candidate(time, expression, quote, status='SUPPORTED'):
    return {'candidate_id': 'c1', 'status': status,
            'event': {'time': time, 'summary': 'Activists call for an uprising', 'actors': [], 'location': None},
            'evidence_ids': ['d1'], 'confidence': 0.8, 'relevance_pass': True, 'contribution_pass': True,
            'date_evidence': {'document_id': 'd1', 'quote': quote, 'time_expression': expression}}


@pytest.mark.parametrize('expression,expected', [
    ('January 2011', '2011-01'), ('27 Jul 2013', '2013-07-27'),
    ('July 27, 2013', '2013-07-27'), ('2013年7月27日', '2013-07-27'),
    ('2013-07-27', '2013-07-27')])
def test_literal_precision(expression, expected):
    assert expected in explicit_dates(expression)
    if expected == '2011-01':
        assert '2011-01-01' not in explicit_dates(expression)


def test_month_cannot_be_repaired_to_first_day_but_remains_provisional():
    quote = 'January 2011: Activists call for an uprising.'
    docs = [{'id': 'd1', 'text': quote, 'publication_date': '2011-02-14'}]
    row = candidate('2011-01-01', 'January 2011', quote)
    with pytest.raises(ValueError, match='precision'):
        validate_verified_candidates([row], docs, 2, strict_dates=True)
    row['status'] = 'INSUFFICIENT'
    row['event']['time'] = '2011-01'
    checked = validate_verified_candidates([row], docs, 2, strict_dates=True)
    timeline, _ = apply_merge_operations([], checked, [{'candidate_id': 'c1', 'operation': 'APPEND'}])
    assert timeline == []
    row['event']['time'] = None
    assert validate_verified_candidates([row], docs, 2, strict_dates=True)[0]['event']['time'] is None


def test_event_after_publication_is_allowed_with_literal_evidence_and_preserved_by_merge():
    quote = '27 Jul 2013: More than 100 killed in protests.'
    docs = [{'id': 'd1', 'text': quote, 'publication_date': '2011-02-03'}]
    checked = validate_verified_candidates([candidate('2013-07-27', '27 Jul 2013', quote)], docs, 2, strict_dates=True)
    timeline, _ = apply_merge_operations([], checked, [{'candidate_id': 'c1', 'operation': 'APPEND'}])
    assert timeline[0]['date_evidence']['quote'] == quote
    assert validate_resolved_update(timeline[0], checked[0], timeline[0])['date_evidence']['quote'] == quote


def test_date_quote_must_be_in_event_evidence_not_metadata_or_invented_text():
    quote = 'January 2011: Activists call for an uprising.'
    row = candidate('2011-02-14', '2011-02-14', 'On 2011-02-14 activists call for uprising.')
    with pytest.raises(ValueError, match='verbatim'):
        validate_verified_candidates([row], [{'id': 'd1', 'text': quote, 'publication_date': '2011-02-14'}], 2, strict_dates=True)


def test_retries_request_uncertainty_instead_of_fabricating_day(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    import run_full_timeline_api_agent as runner
    from chronos_repro.llm import ChatResult
    quote = 'January 2011: Activists call for an uprising.'
    class Client:
        calls = 0
        def chat(self, messages, temperature=0):
            self.calls += 1
            request = json.loads(messages[1]['content'])
            row = candidate('2011-01-01', 'January 2011', quote)
            if self.calls == 2:
                assert 'Never invent' in request['repair']['instruction']
                row['event']['time'], row['status'] = None, 'INSUFFICIENT'
            return ChatResult(json.dumps({'thought': 'Only month precision is provided by the source.', 'candidates': [row]}), 'fake', {'total_tokens': 1}, 'offline')
    state = {'model_visible_state': {'events': [], 'memory': {}}}
    client = Client()
    result, audits = runner.verify_batch(client, state, [{'id': 'd1', 'text': quote}],
                                       {'temperature': 0, 'label_repair_attempts': 1, 'max_candidates_per_round': 2})
    assert client.calls == 2 and result['candidates'][0]['status'] == 'INSUFFICIENT'
    assert result['candidates'][0]['date_evidence'] is None
    assert sum(a.get('usage', {}).get('total_tokens', 0) for a in audits) == 2


def no_gain_history():
    return [{'query': query, 'strategy': 'LATER', 'new_verified_event_count': 0, 'evidence_update_count': 0}
            for query in ['Aurora probe arrival results 2022', 'Aurora probe later arrival 2022']]


def test_near_repeat_no_gain_and_strategy_diversification():
    memory = {'progress_control_enabled': True, 'search_history': no_gain_history()}
    with pytest.raises(ValueError, match='Similar intent'):
        check_search_progress(memory, 'Aurora probe arrival 2022', 'LATER')
    check_search_progress(memory, 'Aurora probe arrival 2023', 'LATER')
    assert not similar_intent('Aurora probe arrival 2022', 'Aurora probe arrival 2023')
    memory['search_history'].append({'strategy': 'LATER', 'query': 'other unrelated query'})
    with pytest.raises(ValueError, match='Three consecutive'):
        check_search_progress(memory, 'Aurora mission service outcome', 'LATER')
    check_search_progress(memory, 'Aurora development funding', 'FACET')


def test_gain_uses_merged_events_not_new_docs_or_observations():
    memory = {'search_history': [{'query': 'Aurora mission launch', 'new_document_count': 8, 'new_observation_count': 16}], 'skeleton_ready': True}
    before = [{'event_id': 'e1', 'evidence_ids': ['d1'], 'summary': 'old wording'}]
    reworded = [{**before[0], 'summary': 'new wording'}]
    updated = record_cycle_outcome(memory, before, reworded)
    assert updated['last_cycle_outcome']['new_verified_event_count'] == 0
    assert updated['last_cycle_outcome']['evidence_update_count'] == 0
    enriched = [{**before[0], 'evidence_ids': ['d1', 'd2']}, {'event_id': 'e2', 'evidence_ids': ['d3']}]
    updated = record_cycle_outcome(memory, before, enriched)
    assert updated['last_cycle_outcome']['new_verified_event_count'] == 1
    assert updated['last_cycle_outcome']['evidence_update_count'] == 1
    assert updated['skeleton_ready'] is False
    assert 'last_cycle_outcome' not in memory


def test_deferral_requires_actual_no_gain_and_never_means_resolved():
    memory = {'search_history': no_gain_history()}
    row = {'direction': 'Aurora arrival outcome', 'reason': 'Two probes found no verified outcome.',
           'attempted_queries': [h['query'] for h in memory['search_history']]}
    assert validate_deferred_directions([row], memory)[0]['status'] == 'DEFERRED_UNCONFIRMED'
    memory['search_history'][0]['new_verified_event_count'] = 1
    with pytest.raises(ValueError, match='no-gain'):
        validate_deferred_directions([row], memory)


def test_gap_scheduler_gives_all_open_gaps_a_first_turn():
    memory = {'gaps': [{'gap_id': f'g{i}', 'priority': 1 - i / 10, 'status': 'OPEN', 'attempted_queries': []} for i in range(4)]}
    selected = []
    for _ in range(8):
        gap = next_open_gap(memory)
        selected.append(gap['gap_id'])
        gap['attempted_queries'].append('attempt')
    assert selected == ['g0', 'g1', 'g2', 'g3'] * 2
    assert all(g['status'] == 'OPEN' for g in memory['gaps'])


def test_memory_preserves_partial_dates_and_exposes_supported_deferrals_to_stop():
    memory = initial_memory(['Aurora'])
    memory['progress_control_enabled'] = True
    memory['search_history'] = [{**h, 'result_ids': ['d1']} for h in no_gain_history()]
    memory['search_history'].append({'query': 'Aurora earlier development', 'strategy': 'EARLIER', 'result_ids': ['d1']})
    quote = 'January 2011: Aurora mission planning begins.'
    docs = [{'id': 'd1', 'text': quote}]
    payload = {'action': 'MEMORY_UPDATE', 'thought': 'The source only states the month of planning.',
               'observations': [{'summary': 'Aurora mission planning begins.', 'event_time': '2011-01',
                                 'time_expression': 'January 2011', 'document_id': 'd1', 'evidence_quote': quote}],
               'discovered_keywords': [],
               'stage_outline': [{'period': '2011', 'description': 'A documented mission stage.', 'evidence_ids': ['d1']}] * 3,
               'next_search_directions': [], 'skeleton_ready': True,
               'boundary_assessment': 'Boundary probes found no further verified outcome.',
               'deferred_directions': [{'direction': 'Aurora arrival outcome', 'reason': 'Two searches supplied no additional verified outcome.',
                                        'attempted_queries': [h['query'] for h in no_gain_history()]}]}
    checked = validate_memory_output(payload, memory, docs)
    after = apply_memory_output(memory, checked, 'Aurora mission overview', 'OVERVIEW', docs)
    assert after['observed_events'][0]['event_time'] == '2011-01'
    assert after['deferred_directions'][0]['status'] == 'DEFERRED_UNCONFIRMED'
    assert skeleton_stop_allowed(after, [{}, {}, {}, {}], 6, {'phase1_min_search_rounds': 6, 'phase1_min_events': 4})
    payload['observations'][0]['event_time'] = '2011-01-01'
    with pytest.raises(ValueError, match='precision'):
        validate_memory_output(payload, memory, docs)


def test_supported_candidate_cannot_omit_date_evidence_by_default():
    row = candidate('2011-01-01', '2011-01-01', '2011-01-01: Event occurs.')
    row.pop('date_evidence')
    with pytest.raises(ValueError, match='date_evidence'):
        validate_verified_candidates([row], [{'id': 'd1', 'text': '2011-01-01: Event occurs.'}], 2)
