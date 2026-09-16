from chronos_repro.phase_handoff import can_handoff_repeated_query


def fixtures():
    return ({'stage_outline': [{}, {}, {}], 'search_history': [{'strategy': 'EARLIER'}, {'strategy': 'LATER'}]},
            [{}] * 12, {'phase1_min_search_rounds': 6, 'phase1_min_events': 12})


def test_handoff_only_for_repeated_query_with_established_skeleton():
    memory, events, config = fixtures()
    assert can_handoff_repeated_query(ValueError('query repeats an earlier search'), memory, events, 6, config)
    assert not can_handoff_repeated_query(ValueError('invalid date'), memory, events, 6, config)
    assert not can_handoff_repeated_query(ValueError('query repeats an earlier search'), memory, events, 5, config)


def test_empty_or_unprobed_skeleton_is_not_handed_off():
    memory, events, config = fixtures()
    error = ValueError('query repeats an earlier search')
    assert not can_handoff_repeated_query(error, memory, [], 6, config)
    memory['search_history'] = [{'strategy': 'EARLIER'}]
    assert not can_handoff_repeated_query(error, memory, events, 6, config)


def test_executor_marks_handoff_forced_and_excludes_training(monkeypatch):
    import sys
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    import coverage_pipeline
    import run_tisa_two_phase_annotation as runner
    from chronos_repro.exploration_memory import initial_memory
    memory = initial_memory([])
    memory.update(stage_outline=[{}, {}, {}], search_history=[{'strategy': 'EARLIER'}, {'strategy': 'LATER'}])
    state = {'dataset': 'fixture', 'topic': 'aurora', 'timeline_events': [], 'exploration_memory': memory}
    config = {'phase1_min_search_rounds': 0, 'phase1_min_events': 0, 'phase1_max_rounds': 1}
    trace = {'steps': [], 'audits': []}
    def fail(*args, **kwargs):
        raise runner.LabelValidationError('query repeats an earlier search', {'action': 'SEARCH'}, [])
    monkeypatch.setattr(runner, 'repaired_call', fail)
    coverage_pipeline.run_phase1(None, state, config, None, trace, {})
    assert state['phase1_termination'] == 'policy_stall_handoff'
    assert trace['steps'][-1]['observation']['forced'] is True
    assert trace['steps'][-1]['observation']['skeleton_complete'] is False
    assert runner.compile_sft_rows(trace) == []
