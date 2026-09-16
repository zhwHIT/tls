import copy

import pytest

from chronos_repro.two_phase_state import build_policy_state
from chronos_repro.multitopic_evaluation import summarize_topics


def fixture_state():
    return {'dataset': 'demo', 'topic': 'a', 'events': [
        {'event_id': f'e{i}', 'time': f'2020-01-{i:02}', 'summary': f'event {i}',
         'evidence_quote': 'long source'} for i in range(1, 31)],
        'memory': {'exploration': {'observed_events': ['do not transmit'] * 1000,
                                  'stage_outline': [{'period': '2020', 'description': 'development'}]},
                   'gaps': [{'gap_id': 'g1', 'status': 'OPEN'}]},
        'active_gap': {'gap_id': 'g1', 'left_event_id': 'e10', 'right_event_id': 'e12',
                       'description': 'missing development'}, 'budget': 999,
        'teacher_only_reference_events': ['private']}


def test_local_projection_and_archive_integrity():
    state = fixture_state()
    original = copy.deepcopy(state)
    result = build_policy_state(state, purpose='repair_gap')
    assert state == original
    assert len(result['events']) == 16
    assert {'e10', 'e12'} <= {e['event_id'] for e in result['events']}
    assert result['events_omitted_count'] == 14
    assert 'budget' not in result and 'teacher_only_reference_events' not in result
    assert 'observed_events' not in result['memory']
    assert all('evidence_quote' not in e for e in result['events'])


@pytest.mark.parametrize('purpose', ['explore', 'discover_gaps', 'repair_gap', 'stop'])
def test_projection_idempotence(purpose):
    result = build_policy_state(fixture_state(), purpose=purpose)
    assert build_policy_state(result, purpose=purpose) == result
    if purpose != 'repair_gap':
        assert len(result['events']) == 30


def test_bad_limits_and_unknown_anchor():
    with pytest.raises(ValueError):
        build_policy_state(fixture_state(), purpose='explore', recent_query_limit=0)
    state = fixture_state()
    state['active_gap']['left_event_id'] = 'absent'
    with pytest.raises(ValueError):
        build_policy_state(state, purpose='repair_gap')


def test_strict_micro_macro_differ():
    rows = [{'dataset': 'd', 'topic': 'a', 'status': 'ok', 'gold_date_hits': 47,
             'gold_date_count': 122, 'predicted_date_count': 86},
            {'dataset': 'd', 'topic': 'b', 'status': 'ok', 'gold_date_hits': 8,
             'gold_date_count': 10, 'predicted_date_count': 10}]
    report = summarize_topics(rows, [('d', 'a'), ('d', 'b')])
    assert report['topics'][0]['gold_date_recall'] == 47 / 122
    assert report['topics'][0]['minimum_date_hits'] == 86
    assert report['aggregate']['micro_gold_date_recall'] == 55 / 132
    assert report['aggregate']['macro_gold_date_recall'] == (47 / 122 + .8) / 2
    assert not report['aggregate']['all_topics_meet_target']


def test_missing_and_interrupted_topics_not_silently_excluded():
    rows = [{'dataset': 'd', 'topic': 'a', 'status': 'stopped_insufficient_balance',
             'gold_date_hits': 1, 'gold_date_count': 2, 'predicted_date_count': 1}]
    result = summarize_topics(rows, [('d', 'a'), ('d', 'b')])
    assert result['aggregate'] is None
    assert result['completed_topic_count'] == 0
    assert result['topics'][1]['status'] == 'missing'


def test_duplicate_topics_rejected():
    with pytest.raises(ValueError):
        summarize_topics([], [('d', 'a'), ('d', 'a')])
