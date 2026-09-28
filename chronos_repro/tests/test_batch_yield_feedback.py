from chronos_repro import batch_memory as memory


def test_joint_query_gain_is_reported_once_and_phase_isolated():
    state = {'query_batches': [
        {'batch_id': 1, 'query_count': 3, 'event_change_count': 0},
        {'batch_id': 2, 'query_count': 3, 'event_change_count': 0},
        {'batch_id': 3, 'query_count': 3, 'event_change_count': 0}]}
    saved = memory.initialize(state)
    saved['query_history'] = [
        {'batch_id': b, 'gap_id': None if b == 1 else 'g1'}
        for b in (1, 2, 3) for _ in range(3)]
    feedback = memory.batch_feedback(state, 'GAP_REFINEMENT')
    assert feedback['consecutive_zero_change_batches'] == 2
    assert [r['batch_id'] for r in feedback['recent_completed_batches']] == [2, 3]


def test_partial_resume_zero_does_not_claim_whole_batch_zero_gain():
    state = {'query_batches': [
        {'batch_id': 1, 'event_change_count': 0},
        {'batch_id': 2, 'event_change_count': 0, 'continued_partial_batch': True,
         'gain_scope': 'continuation_only_prior_partial_gain_in_parent'},
        {'batch_id': 3, 'event_change_count': 0}]}
    feedback = memory.batch_feedback(state, 'GAP_REFINEMENT')
    assert feedback['consecutive_zero_change_batches'] == 1
    assert feedback['recent_completed_batches'][1]['continued_partial_batch']


def test_feedback_is_bounded_and_does_not_change_saved_state():
    state = {'query_batches': [{'batch_id': i, 'event_change_count': 1,
                               'appended_event_count': 0, 'updated_event_count': 1}
                              for i in range(10)]}
    feedback = memory.batch_feedback(state, 'SKELETON_EXPLORATION')
    assert len(feedback['recent_completed_batches']) == 4
    assert feedback['consecutive_zero_change_batches'] == 0
    assert feedback['recent_completed_batches'][-1]['appended_event_count'] == 0
    feedback['recent_completed_batches'][-1]['updated_event_count'] = 100
    assert state['query_batches'][-1]['updated_event_count'] == 1
