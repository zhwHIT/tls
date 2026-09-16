"""Exclusive CLI runs, immutable replay history, and source fingerprints."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from .snapshot import sha256
from .runtime_profile import RUNTIME_REVISION, RUNTIME_OPTIONS


class RunLockedError(RuntimeError):
    pass


@contextmanager
def exclusive_run(output):
    """OS-owned lock survives stale lock files and is released on process death."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    handle = (output / '.run.lock').open('a+b')
    locked = False
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RunLockedError('Output directory already has an active run') from error
        locked = True
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def source_binding(project):
    project = Path(project).resolve()
    files = {}
    for folder in ('src/chronos_repro', 'scripts'):
        for path in sorted((project / folder).rglob('*.py')):
            if not path.resolve().is_relative_to(project):
                raise ValueError('Source file escapes project')
            files[path.relative_to(project).as_posix()] = sha256(path)
    if not files:
        raise ValueError('No runtime source files found')
    fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {'runtime_revision': RUNTIME_REVISION, 'runtime_options': RUNTIME_OPTIONS,
            'source_sha256': fingerprint, 'files': files}


def prepare_execution(project, output, replay=False):
    """Call under exclusive_run, before changing any existing output. Never reset ledger."""
    output = Path(output)
    previous = output / 'trajectory.json'
    has_history = previous.exists() or (output / 'checkpoint.json').exists() or (output / 'request_ledger.json').exists()
    archive = None
    if has_history:
        if not replay:
            raise ValueError('Existing execution requires explicit --replay-from-start; not checkpoint continuation')
        if previous.exists():
            status = json.loads(previous.read_text(encoding='utf-8')).get('status')
            if status in {'ok', 'stopped_insufficient_balance'}:
                raise ValueError('Completed or balance-stopped execution cannot be replayed')
        ledger_path = output / 'request_ledger.json'
        if not ledger_path.exists():
            raise ValueError('Existing execution has no cumulative ledger')
        ledger = json.loads(ledger_path.read_text(encoding='utf-8'))
        if ledger.get('balance_stop'):
            raise ValueError('Balance-stopped ledger cannot be replayed')
        if ledger['requests_started'] >= ledger['request_limit']:
            raise ValueError('Cumulative request ceiling already reached')
        archive = output / 'history' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
        archive.mkdir(parents=True, exist_ok=False)
        # Explicit generated artifacts only: never copy .env or arbitrary user files.
        names = ('trajectory.json', 'checkpoint.json', 'prediction.json', 'evaluation.json', 'manifest.json',
                 'run_config.json', 'run_binding.json', 'code_binding.json', 'execution_record.json',
                 'request_ledger.json', 'candidate_pool.json', 'event_pool.json', 'event_pool_prediction.json',
                 'evidence_reader_manifest.json', 'context_projections.json', 'training_gate.json',
                 'controller_exact.review_required.jsonl', 'controller_candidates.review_required.jsonl',
                 'sft_v4.jsonl', 'sft_v5.jsonl', 'teacher_alignment.private.json', 'preflight.json')
        for name in names:
            path = output / name
            if path.is_file():
                shutil.copy2(path, archive / name)
    binding = source_binding(project)
    record = {'mode': 'replay_from_start' if has_history else 'fresh', 'checkpoint_resume': False,
              'runtime_revision': RUNTIME_REVISION, 'runtime_options': RUNTIME_OPTIONS,
              'archive': str(archive) if archive else None, 'source_sha256': binding['source_sha256'],
              'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'cumulative_ledger_preserved': True}
    for name, value in [('code_binding.json', binding), ('execution_record.json', record)]:
        temporary = output / (name + '.tmp')
        temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
        temporary.replace(output / name)
    return record
