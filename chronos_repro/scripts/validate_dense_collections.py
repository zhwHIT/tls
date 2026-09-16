from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np

from chronos_repro.dense_retrieval import (
    DENSE_COLLECTION_SCHEMA,
    DENSE_SCHEMA,
    read_dense_metadata,
)


def validate_collection(collection: Path) -> dict:
    manifest = read_dense_metadata(collection)
    errors: list[str] = []
    if manifest.get("schema") != DENSE_COLLECTION_SCHEMA:
        errors.append("root manifest is not a dense collection")
    if not manifest.get("complete"):
        errors.append("collection manifest is not marked complete")
    bm25 = Path(manifest["bm25_index"])
    with sqlite3.connect(bm25) as connection:
        bm25_counts = dict(
            connection.execute(
                "SELECT topic, COUNT(*) FROM documents GROUP BY topic ORDER BY topic"
            ).fetchall()
        )

    shard_rows = {}
    total_vectors = 0
    for topic, relative in sorted(manifest.get("shards", {}).items()):
        shard = collection / relative
        metadata = read_dense_metadata(shard)
        if metadata.get("schema") != DENSE_SCHEMA:
            errors.append(f"{topic}: invalid shard schema")
            continue
        vectors = np.load(shard / "vectors.npy", mmap_mode="r")
        jsonl_rows = sum(
            1
            for line in (shard / "chunks.jsonl").open("r", encoding="utf-8")
            if line.strip()
        )
        expected = int(metadata["chunk_count"])
        finite = bool(np.isfinite(vectors).all())
        nonzero = int(np.count_nonzero(np.any(vectors != 0, axis=1)))
        if vectors.shape != (expected, int(metadata["dimension"])):
            errors.append(f"{topic}: vector shape mismatch")
        if jsonl_rows != expected:
            errors.append(f"{topic}: metadata row mismatch")
        if not finite or nonzero != expected:
            errors.append(f"{topic}: invalid or zero vectors")
        if expected != int(bm25_counts.get(topic, -1)):
            errors.append(f"{topic}: compact vector count differs from BM25 documents")
        shard_rows[topic] = {
            "vectors": expected,
            "shape": list(vectors.shape),
            "dtype": str(vectors.dtype),
            "finite": finite,
            "nonzero_vectors": nonzero,
        }
        total_vectors += expected

    if set(manifest.get("shards", {})) != set(bm25_counts):
        errors.append("collection topics differ from BM25 topics")
    if total_vectors != int(manifest.get("chunk_count", -1)):
        errors.append("collection total differs from shard totals")
    return {
        "collection": str(collection.resolve()),
        "valid": not errors,
        "errors": errors,
        "topics": len(shard_rows),
        "vectors": total_vectors,
        "bm25_documents": sum(int(value) for value in bm25_counts.values()),
        "shards": shard_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    reports = [
        validate_collection(Path(value).resolve()) for value in args.collection
    ]
    payload = {
        "schema": "chronos-repro.dense-collection-validation.v1",
        "valid": all(report["valid"] for report in reports),
        "collections": reports,
        "totals": {
            "collections": len(reports),
            "topics": sum(report["topics"] for report in reports),
            "vectors": sum(report["vectors"] for report in reports),
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload["totals"], ensure_ascii=False))
    if not payload["valid"]:
        for report in reports:
            for error in report["errors"]:
                print(f"{report['collection']}: {error}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
