import json
import sys
from pathlib import Path

import pytest

from chronos_repro.llm import ChatResult, DeepSeekClient
from chronos_repro.limited_llm import RequestLimitError


@pytest.fixture
def config(monkeypatch):
    project = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(project / 'scripts'))
    return json.loads((project / 'configs/tisa_coverage_egypt_v6.json').read_text(encoding='utf-8'))


def test_planned_envelope_is_not_a_claim_of_coverage(config):
    from coverage_preflight import coverage_envelope
    scope = coverage_envelope(config)
    assert scope['max_search_rounds'] == 28
    assert scope['max_passage_selection_slots'] == 224
    assert scope['max_http_requests_across_restarts'] == 400
    assert scope['gold_sent_to_model'] is False
    assert 'byte cap' in scope['retransmission_note']


@pytest.mark.parametrize('change', ['gold', 'characters', 'count', 'budget'])
def test_preflight_rejects_incompatible_scope(config, change):
    from coverage_preflight import coverage_envelope
    if change == 'gold':
        config['phase2_teacher_guidance'] = True
    elif change == 'characters':
        config['coverage_pipeline']['passage_chars'] = 3201
    elif change == 'count':
        config['coverage_pipeline']['passages_per_search'] = 9
    else:
        config['max_api_requests'] = 0
    with pytest.raises(ValueError):
        coverage_envelope(config)


def test_full_run_ledger_survives_restart(config, tmp_path, monkeypatch):
    from coverage_preflight import guarded_coverage_client
    calls = []
    def fake_request(*args):
        calls.append(1)
        return ChatResult('{}', 'fixture', {}, None)
    monkeypatch.setattr(DeepSeekClient, '_chat_once', fake_request)
    config['max_api_requests'] = 2
    first = guarded_coverage_client(config, tmp_path)
    first.chat([])
    second = guarded_coverage_client(config, tmp_path)
    second.chat([])
    with pytest.raises(RequestLimitError):
        second.chat([])
    assert len(calls) == 2 and second.http_requests_total == 2


@pytest.mark.parametrize('status', ['ok', 'stopped_insufficient_balance', 'stopped_error'])
def test_full_run_refuses_finished_or_unaccounted_history(config, tmp_path, status):
    from coverage_preflight import guarded_coverage_client
    (tmp_path / 'trajectory.json').write_text(json.dumps({'status': status}), encoding='utf-8')
    with pytest.raises(ValueError):
        guarded_coverage_client(config, tmp_path)


def test_orphaned_cache_cannot_reset_request_count(config, tmp_path):
    from coverage_preflight import guarded_coverage_client
    (tmp_path / 'api_cache').mkdir()
    (tmp_path / 'api_cache/old.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='without a request ledger'):
        guarded_coverage_client(config, tmp_path)


def test_dry_run_never_loads_credentials_or_constructs_client(config, tmp_path, monkeypatch):
    import run_tisa_two_phase_annotation as runner
    config_path = tmp_path / 'config.json'
    config_path.write_text(json.dumps(config), encoding='utf-8')
    output = tmp_path / 'output'
    def prohibited(*args, **kwargs):
        raise AssertionError('dry-run crossed the API/key boundary')
    monkeypatch.setattr(runner, 'load_env_file', prohibited)
    monkeypatch.setattr(runner, 'iter_topics', prohibited)
    monkeypatch.setattr(runner.coverage_preflight, 'guarded_coverage_client', prohibited)
    monkeypatch.setattr(runner.coverage_preflight, 'build_preflight', lambda *args: {'binding': {'id': 'fixture'}, 'api_calls': 0})
    monkeypatch.setattr(sys, 'argv', ['run', '--project-root', str(tmp_path), '--config', str(config_path),
                                   '--env-file', str(tmp_path / 'absent.env'), '--output-dir', str(output), '--dry-run'])
    assert runner.main() == 0
    assert json.loads((output / 'preflight.json').read_text())['api_calls'] == 0
    assert not (output / 'request_ledger.json').exists()


def test_api_is_disabled_without_explicit_flag(config, tmp_path, monkeypatch):
    import run_tisa_two_phase_annotation as runner
    config_path = tmp_path / 'config.json'
    config_path.write_text(json.dumps(config), encoding='utf-8')
    output = tmp_path / 'output'
    monkeypatch.setattr(sys, 'argv', ['run', '--project-root', str(tmp_path), '--config', str(config_path),
                                   '--env-file', str(tmp_path / 'absent.env'), '--output-dir', str(output)])
    with pytest.raises(SystemExit) as caught:
        runner.main()
    assert caught.value.code == 2
    assert not output.exists()
