from pathlib import Path


def test_tests_or_dates_alone_never_approve_training(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    from finish_coverage_evaluation import assessment
    result = assessment({'status': 'ok', 'steps': []},
        {'coverage': {'covered_gold_dates': 122, 'gold_date_count': 122, 'gold_date_recall': 1.0}},
        {'valid': True}, {'requests_started': 100, 'request_limit': 400})
    assert result['date_target_reached']
    assert not result['training_ready']
    assert not result['semantic_coverage_established']
    assert not result['full_two_phase_executed']
    assert not result['gap_state_available']


def test_forced_stop_is_not_autonomous_completion(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    from finish_coverage_evaluation import assessment
    trace = {'status': 'ok', 'steps': [
        {'phase': 'SKELETON_EXPLORATION', 'action': 'SEARCH'},
        {'phase': 'GAP_REFINEMENT', 'action': 'STOP', 'observation': {'forced': True}}],
        'final_gap_memory': {'gaps': [{'status': 'OPEN'}]}}
    result = assessment(trace, {}, {}, {})
    assert result['full_two_phase_executed']
    assert result['forced_stops'] == result['remaining_gaps'] == 1
    assert not result['phase2_autonomous_stop']
