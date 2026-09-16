import copy

from chronos_repro.compact_context import deduplicate_gap_observation, compact_instruction, compact_gap_update


def observation():
    row = {'candidate_id': 'c1', 'status': 'SUPPORTED', 'event': {'time': '2020-01-01', 'summary': 'Aurora launched.'}}
    operation = {'candidate_id': 'c1', 'operation': 'APPEND', 'reason': 'Distinct dated event'}
    return {'all_extracted_candidates': [row], 'verified_candidates': [copy.deepcopy(row)],
            'verified_candidates_omitted_count': 3, 'merge_operations': [operation],
            'applied': [{**operation, 'event_id': 'e1'}]}


def test_exact_records_are_replaced_by_references_without_mutation():
    original = observation()
    saved = copy.deepcopy(original)
    result = deduplicate_gap_observation(original)
    assert original == saved
    assert result['verified_candidate_ids'] == ['c1']
    assert 'verified_candidates' not in result
    assert result['all_extracted_candidates'] == original['all_extracted_candidates']
    assert result['verified_candidates_omitted_count'] == 3
    assert result['applied'] == original['applied']
    assert result['merge_operations_recorded_in_applied'] is True


def test_different_candidate_facts_are_not_discarded():
    original = observation()
    original['verified_candidates'][0]['event']['summary'] = 'Aurora was delayed.'
    assert deduplicate_gap_observation(original)['verified_candidates'] == original['verified_candidates']


def test_candidate_outside_extracted_window_is_preserved():
    original = observation()
    original['all_extracted_candidates'] = []
    assert deduplicate_gap_observation(original)['verified_candidates'] == original['verified_candidates']


def test_missing_candidate_arrays_do_not_become_an_empty_verdict():
    assert 'verified_candidate_ids' not in deduplicate_gap_observation({})


def test_unmatched_merge_decision_is_preserved():
    original = observation()
    original['applied'][0]['operation'] = 'DROP'
    assert deduplicate_gap_observation(original)['merge_operations'] == original['merge_operations']


def test_duplicate_candidate_ids_disable_referencing():
    original = observation()
    original['all_extracted_candidates'] *= 2
    assert 'verified_candidate_ids' not in deduplicate_gap_observation(original)


def test_non_gap_tools_keep_the_existing_shape():
    result = compact_instruction({'stage': 'OTHER', 'cycle_observation': observation()})
    assert 'verified_candidates' in result['cycle_observation']
    assert 'merge_operations' in result['cycle_observation']


def test_projected_packet_preserves_state_and_omissions_and_is_idempotent():
    gap = {'gap_id': 'g1', 'description': 'Check the launch outcome.', 'status': 'OPEN',
           'attempted_queries': ['Aurora launch outcome']}
    state = {'active_gap': gap, 'events': [{'event_id': 'e1', 'time': '2020-01-01', 'summary': 'Aurora launched.'}],
             'memory': {'gap_search_history': [{'query': 'Aurora launch outcome'}]}}
    original = {'stage': 'GAP_REFINEMENT: update GAP_MEMORY after MERGE', 'student_visible_state': state,
                'active_gap': gap, 'cycle_observation': observation(), 'context_rule': 'Keep uncertainty.'}
    result = compact_gap_update(original)
    assert result['student_visible_state'] == state
    assert 'attempted_queries' not in result['active_gap']
    assert 'attempted_queries' in original['active_gap']
    assert result['cycle_observation']['verified_candidates_omitted_count'] == 3
    assert compact_gap_update(result) == result


def test_different_root_gap_is_not_silently_replaced():
    original = {'stage': 'GAP_REFINEMENT: update GAP_MEMORY after MERGE',
                'student_visible_state': {'active_gap': {'gap_id': 'g1'}},
                'active_gap': {'gap_id': 'g2', 'attempted_queries': ['Something else']}}
    assert compact_gap_update(original)['active_gap'] == original['active_gap']


def test_missing_field_is_not_an_identical_null_field():
    original = observation()
    original['merge_operations'][0]['target_event_id'] = None
    assert 'merge_operations' in deduplicate_gap_observation(original)


def test_missing_gap_ids_do_not_authorize_dropping_history():
    original = {'stage': 'GAP_REFINEMENT: update GAP_MEMORY after MERGE',
                'student_visible_state': {'active_gap': {}}, 'active_gap': {'attempted_queries': ['q']}}
    assert compact_gap_update(original)['active_gap'] == original['active_gap']
