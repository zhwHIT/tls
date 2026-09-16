from pathlib import Path
import pytest
from chronos_repro import atomic_io


def test_retries_sharing_failure_without_rerunning_writer(monkeypatch):
    calls, sleeps = [], []
    def replace(self, target):
        calls.append((self, target))
        if len(calls) < 3:
            raise PermissionError('temporary sharing conflict')
        return Path(target)
    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr(atomic_io.time, 'sleep', sleeps.append)
    assert atomic_io.replace_with_retry('checkpoint.tmp', 'checkpoint.json') == Path('checkpoint.json')
    assert len(calls) == 3
    assert sleeps == [0.05, 0.1]


def test_permanent_failure_is_bounded(monkeypatch):
    calls = []
    def replace(self, target):
        calls.append(1)
        raise PermissionError('permanent')
    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr(atomic_io.time, 'sleep', lambda _: None)
    with pytest.raises(PermissionError):
        atomic_io.replace_with_retry('a', 'b', attempts=3)
    assert len(calls) == 3


def test_nonsharing_error_is_not_retried(monkeypatch):
    def replace(self, target):
        raise FileNotFoundError('missing temporary')
    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr(atomic_io.time, 'sleep', lambda _: pytest.fail('must not retry'))
    with pytest.raises(FileNotFoundError):
        atomic_io.replace_with_retry('a', 'b')
