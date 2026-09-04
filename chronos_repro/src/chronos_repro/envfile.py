from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | Path, override: bool = False) -> list[str]:
    """Load simple KEY=VALUE entries without logging or returning secret values."""
    loaded = []
    for line_number, raw in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid .env entry at line {line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "A").isalnum():
            raise ValueError(f"Invalid environment key at line {line_number}")
        if override or key not in os.environ:
            os.environ[key] = value.strip().strip("\"'")
        loaded.append(key)
    return loaded
