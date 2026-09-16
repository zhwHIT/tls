import json
import subprocess
import sys

import pytest

from chronos_repro.run_guard import exclusive_run, prepare_execution, source_binding


def project(tmp_path):
    source = tmp_path / 'src/chronos_repro'
    source.mkdir(parents=True)
    (source / 'fixture.py').write_text('x = 1\n', encoding='utf-8')
    output = tmp_path / 'output'
    output.mkdir()
    return output


def test_cross_process_lock_and_release(tmp_path):
    script = ('from chronos_repro.run_guard import exclusive_run, RunLockedError; import sys\n'
              'try:\n with exclusive_run(sys.argv[1]): pass\n'
              'except RunLockedError: sys.exit(23)\n')
    with exclusive_run(tmp_path):
        result = subprocess.run([sys.executable, '-B', '-c', script, str(tmp_path)], capture_output=True)
        assert result.returncode == 23, result.stderr
    result = subprocess.run([sys.executable, '-B', '-c', script, str(tmp_path)], capture_output=True)
    assert result.returncode == 0, result.stderr


def test_exception_releases_lock(tmp_path):
    with pytest.raises(ValueError):
        with exclusive_run(tmp_path):
            raise ValueError('fixture')
    with exclusive_run(tmp_path):
        pass


def test_source_fingerprint_changes_with_code(tmp_path):
    project(tmp_path)
    before = source_binding(tmp_path)
    (tmp_path / 'src/chronos_repro/fixture.py').write_text('x = 2\n', encoding='utf-8')
    assert before['source_sha256'] != source_binding(tmp_path)['source_sha256']


def test_replay_archives_old_outputs_without_reset_or_secret_copy(tmp_path):
    output = project(tmp_path)
    ledger = {'requests_started': 77, 'request_limit': 400, 'balance_stop': False}
    (output / 'trajectory.json').write_text('{"status":"stopped_error"}', encoding='utf-8')
    (output / 'request_ledger.json').write_text(json.dumps(ledger), encoding='utf-8')
    (output / '.env').write_text('fixture-only', encoding='utf-8')
    with pytest.raises(ValueError, match='explicit'):
        prepare_execution(tmp_path, output)
    record = prepare_execution(tmp_path, output, replay=True)
    from pathlib import Path
    archive = Path(record['archive'])
    assert not (archive / '.env').exists()
    assert (archive / 'trajectory.json').read_bytes() == (output / 'trajectory.json').read_bytes()
    assert json.loads((output / 'request_ledger.json').read_text()) == ledger
    assert record['mode'] == 'replay_from_start' and not record['checkpoint_resume']


@pytest.mark.parametrize('status,balance,count', [('ok', False, 77), ('stopped_error', True, 77),
                                               ('stopped_error', False, 400)])
def test_replay_never_bypasses_terminal_guards(tmp_path, status, balance, count):
    output = project(tmp_path)
    (output / 'trajectory.json').write_text(json.dumps({'status': status}), encoding='utf-8')
    (output / 'request_ledger.json').write_text(json.dumps(
        {'requests_started': count, 'request_limit': 400, 'balance_stop': balance}), encoding='utf-8')
    with pytest.raises(ValueError):
        prepare_execution(tmp_path, output, replay=True)
    assert not (output / 'history').exists()
