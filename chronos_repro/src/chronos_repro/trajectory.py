from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .retrieval import read_index_metadata, search
from .snapshot import sha256


def run_trajectory(
    index: str | Path,
    search_engine: str,
    queries: list[str],
    top_k: int = 20,
    max_rounds: int = 3,
    stop_no_new_rounds: int = 2,
) -> dict:
    """Run deterministic retrieval rounds and retain a replayable state transition log."""
    if max_rounds < 1 or stop_no_new_rounds < 1:
        raise ValueError("round limits must be positive")
    index = Path(index).resolve()
    seen: list[str] = []
    seen_set: set[str] = set()
    rounds = []
    stagnant = 0
    stop_reason = "queries_exhausted"
    for round_index, query in enumerate(queries[:max_rounds], 1):
        state = {"round": round_index, "seen_doc_ids": list(seen), "document_count": len(seen)}
        results = search(index, [query], top_k, search_engine)
        new_ids = [item["id"] for item in results if item["id"] not in seen_set]
        for doc_id in new_ids:
            seen_set.add(doc_id)
            seen.append(doc_id)
        stagnant = stagnant + 1 if not new_ids else 0
        rounds.append(
            {
                "state": state,
                "action": {"type": "SEARCH", "query": query, "top_k": top_k},
                "observation": {"results": results, "new_doc_ids": new_ids},
                "updated_state": {
                    "round": round_index,
                    "seen_doc_ids": list(seen),
                    "document_count": len(seen),
                    "new_document_count": len(new_ids),
                },
            }
        )
        if stagnant >= stop_no_new_rounds:
            stop_reason = "no_new_documents"
            break
    else:
        if len(queries) >= max_rounds:
            stop_reason = "max_rounds"
    return {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "search_engine": search_engine,
        "config": {
            "top_k": top_k,
            "max_rounds": max_rounds,
            "stop_no_new_rounds": stop_no_new_rounds,
        },
        "provenance": {
            "index": str(index),
            "index_sha256": sha256(index),
            "index_metadata": read_index_metadata(index),
        },
        "rounds": rounds,
        "final_state": {"seen_doc_ids": seen, "document_count": len(seen)},
        "stop_reason": stop_reason,
    }


def replay_trajectory(path: str | Path, index: str | Path | None = None) -> dict:
    """Repeat every recorded action and fail on retrieval drift."""
    path = Path(path)
    trace = json.loads(path.read_text(encoding="utf-8"))
    index_path = Path(index or trace["provenance"]["index"]).resolve()
    if sha256(index_path) != trace["provenance"]["index_sha256"]:
        raise RuntimeError("Index hash differs from the trajectory provenance")
    mismatches = []
    for number, record in enumerate(trace["rounds"], 1):
        action = record["action"]
        actual = search(index_path, [action["query"]], action["top_k"], trace["search_engine"])
        expected = record["observation"]["results"]
        if actual != expected:
            mismatches.append(number)
    if mismatches:
        raise RuntimeError(f"Retrieval replay mismatch in rounds: {mismatches}")
    return {"valid": True, "rounds_replayed": len(trace["rounds"]), "index_sha256": sha256(index_path)}
