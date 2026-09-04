from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


MANIFEST = "MANIFEST.sha256.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inventory(root: Path) -> list[dict[str, int | str]]:
    files = []
    for path in sorted((p for p in root.rglob("*") if p.is_file())):
        if path.name == MANIFEST:
            continue
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    return files


def freeze(source: str | Path, destination: str | Path, dataset: str, source_note: str) -> Path:
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Corpus source is not a directory: {source}")
    source_files = _inventory(source)
    if not source_files:
        raise ValueError(f"Refusing to snapshot an empty corpus: {source}")
    content_id = hashlib.sha256(
        json.dumps(source_files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    target = destination / dataset / content_id
    if target.exists():
        verify(target)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{content_id}.staging")
    if staging.exists():
        raise FileExistsError(f"Stale staging directory exists: {staging}")
    shutil.copytree(source, staging)
    copied_files = _inventory(staging)
    if copied_files != source_files:
        shutil.rmtree(staging)
        raise RuntimeError("Source changed while snapshot was being copied")
    manifest = {
        "schema_version": 1,
        "dataset": dataset,
        "snapshot_id": content_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_note": source_note,
        "file_count": len(copied_files),
        "total_bytes": sum(int(item["bytes"]) for item in copied_files),
        "files": copied_files,
    }
    (staging / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    staging.rename(target)
    return target


def verify(snapshot: str | Path) -> dict:
    snapshot = Path(snapshot).resolve()
    manifest_path = snapshot / MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = _inventory(snapshot)
    if actual != manifest.get("files"):
        raise RuntimeError(f"Snapshot verification failed: {snapshot}")
    return manifest
