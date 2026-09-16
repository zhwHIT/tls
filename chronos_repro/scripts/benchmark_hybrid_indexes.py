from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from chronos_repro.retrieval import search


def signal_counts(results: list[dict]) -> dict:
    counts = {"bm25": 0, "dense": 0, "both": 0}
    for item in results:
        signals = set(item.get("retrieval", {}).get("signals", {}))
        if "bm25" in signals:
            counts["bm25"] += 1
        if "dense" in signals:
            counts["dense"] += 1
        if {"bm25", "dense"} <= signals:
            counts["both"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    rows = []
    for case in config["cases"]:
        index = Path(case["index"])
        if not index.is_absolute():
            index = (config_path.parent.parent / index).resolve()
        started = time.perf_counter()
        first = search(
            index,
            case["queries"],
            int(config["top_k"]),
            case["search_engine"],
        )
        cold_seconds = time.perf_counter() - started
        started = time.perf_counter()
        second = search(
            index,
            case["queries"],
            int(config["top_k"]),
            case["search_engine"],
        )
        warm_seconds = time.perf_counter() - started
        if [item["id"] for item in first] != [item["id"] for item in second]:
            raise RuntimeError(f"Non-deterministic ranking in {case['name']}")
        rows.append(
            {
                "name": case["name"],
                "index": str(index),
                "search_engine": case["search_engine"],
                "queries": case["queries"],
                "results": len(first),
                "unique_documents": len({item["id"] for item in first}),
                "unique_dates": len(
                    {item.get("timestamp") for item in first if item.get("timestamp")}
                ),
                "signals": signal_counts(first),
                "cold_seconds": cold_seconds,
                "warm_seconds": warm_seconds,
                "top_ids": [item["id"] for item in first[:5]],
            }
        )
    payload = {
        "schema": "chronos-repro.hybrid-runtime-benchmark.v1",
        "top_k": int(config["top_k"]),
        "cases": rows,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            [
                {
                    "name": row["name"],
                    "results": row["results"],
                    "cold_seconds": round(row["cold_seconds"], 4),
                    "warm_seconds": round(row["warm_seconds"], 4),
                }
                for row in rows
            ],
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
