import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_preextracted_all_suite as suite_runner


@pytest.mark.parametrize('stop_file,exit_code,expected_started,expected_stage', [
    (True, 0, 0, 'stopped'), (False, 3, 1, 'stopped'),
    (False, 2, 2, 'completed_with_errors'), (False, 0, 2, 'completed')])
def test_scheduler_stop_balance_and_isolated_errors(tmp_path, monkeypatch, stop_file,
                                                     exit_code, expected_started, expected_stage):
    root = tmp_path / 'output'
    root.mkdir()
    if stop_file:
        (root / 'STOP').touch()
    items = [{'dataset': 'fixture', 'topic': str(i), 'config': f'c{i}.json',
              'output_dir': f'output/topic{i}'} for i in range(2)]
    (tmp_path / 'suite.json').write_text(json.dumps({'topics': items}))
    monkeypatch.setattr(suite_runner, '__file__', str(tmp_path / 'scripts/runner.py'))
    monkeypatch.setattr(suite_runner, 'preflight', lambda *a: {'topics': items})
    monkeypatch.setattr(suite_runner, 'source_binding', lambda *a: {'source_sha256': 'fixed'})
    monkeypatch.setattr(suite_runner.time, 'sleep', lambda *a: None)
    started = []
    class Process:
        pid = 123
        def __init__(self, command, **kwargs):
            started.append(command)
        def poll(self):
            return exit_code
    monkeypatch.setattr(suite_runner.subprocess, 'Popen', Process)
    monkeypatch.setattr(sys, 'argv', ['runner', '--suite-config', 'suite.json',
        '--output-root', 'output', '--workers', '1', '--allow-api'])
    suite_runner.main()
    state = json.loads((root / 'suite_status.json').read_text())
    assert len(started) == expected_started
    assert state['stage'] == expected_stage
    assert state['pending_topics'] == (2 - expected_started)
    assert all('--allow-api' in cmd for cmd in started)


def test_aggregation_excludes_partial_error_results(tmp_path):
    items = []
    for i, (hits, total, status) in enumerate([(1, 2, 'ok'), (1, 10, 'ok'), (9, 10, 'stopped_error')]):
        path = tmp_path / str(i)
        path.mkdir()
        items.append({'dataset': 'fixture', 'topic': str(i), 'output_dir': str(i)})
        row = {k: 0 for k in ('completed_batches', 'query_count', 'accepted_event_count',
            'retrieved_extracted_date_hits', 'all_topic_extracted_date_hits', 'deferred_verification_count',
            'gold_start_exactly_matched', 'gold_end_exactly_matched')}
        row.update(runtime_status=status, phase1_termination='runner_limit', gold_date_count=total,
                   matched_gold_date_count=hits, gold_date_recall=hits / total)
        (path / 'gold_date_coverage.json').write_text(json.dumps(row))
    report = suite_runner.summarize(tmp_path, {'topics': items})
    result = report['datasets']['fixture']
    assert result['finished_without_runtime_error'] == 2
    assert result['partial_error_results'] == 1
    assert result['macro_gold_date_recall'] == pytest.approx(0.3)
    assert result['micro_gold_date_recall'] == pytest.approx(2 / 12)
    assert len(report['rows']) == 3
