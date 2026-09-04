from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from .snapshot import MANIFEST, sha256


CHRONOS_UPSTREAM_COMMIT = "4dadc9707c9a4f55476ac28259510fecc0d5c8a9"


def find_snapshot(root: str | Path) -> dict | None:
    current = Path(root).resolve()
    for candidate in (current, *current.parents):
        path = candidate / MANIFEST
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            return {
                "dataset": payload.get("dataset"),
                "snapshot_id": payload.get("snapshot_id"),
                "manifest": str(path),
                "manifest_sha256": sha256(path),
            }
    return None


def runtime_metadata(data_root: str | Path, backend: str, upstream_commit: str | None) -> dict:
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "rouge_backend": backend,
        "upstream_commit": upstream_commit,
        "data_root": str(Path(data_root).resolve()),
        "snapshot": find_snapshot(data_root),
    }
