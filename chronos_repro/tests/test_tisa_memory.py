import pytest

from chronos_repro.tisa_memory import (
    REFINE,
    SKELETON,
    attach_phase_memory,
    rank_query_candidates,
    validate_controller_review,
    validate_policy_state,
    validate_query_candidates,
)


def _state():
    return {
        'topic': 'example',
        'events': [
            {
                'event_id': 'e1',
                'time': {'value': '2020-01-01'},
                'support': 1,
                'conflict': False,
            }
        ],
        'gaps': [
            {
                'gap_id': 'g1',
                'type': 'CAUSAL_BRIDGE',
                'priority': 1.0,
                'window_start': '2020-01-01',
                'window_end': '2020-02-01',
            }
        ],
    }


def test_attaches_phase_specific_memory_and_actions():
    skeleton = attach_phase_memory(_state(), SKELETON)
    refine = attach_phase_memory(_state(), REFINE)
    validate_policy_state(skeleton)
    validate_policy_state(refine)
    assert skeleton['memory']['covered_dates'] == ['2020-01-01']
    assert skeleton['valid_actions'] == ['SEARCH', 'SWITCH_PHASE']
    assert refine['memory']['open_gaps'][0]['gap_id'] == 'g1'
    assert refine['valid_actions'] == ['SEARCH', 'STOP']


def test_query_ranking_uses_private_gold_gain_and_novelty():
    candidates = ['target event exact date', 'example broad timeline']
    results = {
        candidates[0]: [{'id': 'd1', 'timestamp': '2020-02-01'}],
        candidates[1]: [{'id': 'd2', 'timestamp': '2020-01-01'}],
    }
    private = {
        'accepted_dates': ['2020-02-01'],
        'summary': 'target event happened',
    }
    ranking = rank_query_candidates(
        candidates,
        results,
        private,
        covered_dates=['2020-01-01'],
        previous_queries=['example timeline'],
    )
    assert ranking[0]['query'] == candidates[0]
    assert ranking[0]['metrics']['exact_gold_gain'] == 1


def test_candidate_validation_rejects_duplicates():
    with pytest.raises(ValueError, match='unique'):
        validate_query_candidates(['same query words', 'same query words'])


def test_controller_review_is_phase_constrained_and_score_checked():
    candidates = ['target event exact date', 'example broad timeline']
    ranking = [
        {'query': candidates[0], 'metrics': {'score': 100}},
        {'query': candidates[1], 'metrics': {'score': 1}},
    ]
    review = {
        'chosen_action': 'SEARCH',
        'rejected_action': 'STOP',
        'chosen_query': candidates[0],
        'rejected_query': candidates[1],
        'memory_update_chosen': {'open_gaps': ['g1']},
        'memory_update_rejected': {'open_gaps': []},
        'preference_scores': {
            name: {'chosen': 0.9, 'rejected': 0.5}
            for name in ('query', 'action', 'memory')
        },
    }
    validate_controller_review(review, REFINE, candidates, ranking, 0.2)
    review['chosen_action'] = 'SWITCH_PHASE'
    with pytest.raises(ValueError, match='invalid for phase'):
        validate_controller_review(review, REFINE, candidates, ranking, 0.2)
