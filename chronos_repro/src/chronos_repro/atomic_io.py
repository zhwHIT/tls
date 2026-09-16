"""Retry transient Windows sharing failures without repeating the operation upstream."""
import time
import json
from pathlib import Path


def atomic_write_json(path, payload):
    """Stream JSON into a temporary file; keep the last checkpoint on failure."""
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        replace_with_retry(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def replace_with_retry(temporary, target, *, attempts=8):
    if type(attempts) is not int or attempts < 1:
        raise ValueError('attempts must be a positive integer')
    for index in range(attempts):
        try:
            return Path(temporary).replace(target)
        except PermissionError:
            if index + 1 == attempts:
                raise
            time.sleep(min(0.05 * 2 ** index, 1.0))
