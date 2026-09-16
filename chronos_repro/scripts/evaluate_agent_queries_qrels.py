from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from chronos_repro.agent_query_evaluation import (
    evaluate_query_sequence,
    extract_queries,
)
from chronos_repro.retrieval import search


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    started = time.perf_counter()
    config_path = Path(args.config).resolve()
    config = read_json(config_path)
    qrel_path = Path(config["qrels"]).resolve()
    all_qrels = read_jsonl(qrel_path)
    cases = []

    for case in config["cases"]:
        dataset = str(case["dataset"])
        topic = str(case["topic"])
        case_qrels = [
            row
            for row in all_qrels
            if row["dataset"] == dataset and row["topic"] == topic
        ]
        case_result = {
            "dataset": dataset,
            "topic": topic,
            "qrels": len(case_qrels),
            "runs": [],
        }
        for run in case.get("runs", []):
            if run.get("status") == "missing_query_rollout":
                case_result["runs"].append(
                    {
                        "name": run["name"],
                        "status": "missing_query_rollout",
                        "reason": run["reason"],
                    }
                )
                continue
            source_path = Path(run["path"]).resolve()
            queries = extract_queries(read_json(source_path), run["source"])
            retrieved_rounds = [
                search(
                    Path(case["hybrid_index"]).resolve(),
                    [query],
                    int(config["top_k_per_query"]),
                    f"{dataset} {topic}",
                )
                for query in queries
            ]
            evaluation = evaluate_query_sequence(
                queries, retrieved_rounds, case_qrels
            )
            case_result["runs"].append(
                {
                    "name": run["name"],
                    "status": "complete",
                    "source_path": str(source_path),
                    "source_sha256": sha256(source_path),
                    "queries": queries,
                    "evaluation": evaluation,
                }
            )
        cases.append(case_result)

    report = {
        "schema": "chronos-repro.agent-query-qrel-eval.v1",
        "config": str(config_path),
        "config_sha256": sha256(config_path),
        "qrels": str(qrel_path),
        "qrels_sha256": sha256(qrel_path),
        "top_k_per_query": int(config["top_k_per_query"]),
        "cases": cases,
        "elapsed_seconds": time.perf_counter() - started,
        "limitations": [
            "Queries are evaluated against pooled qrels created from oracle-query candidate pools.",
            "Documents outside the qrel pool are reported as unjudged, never treated as irrelevant.",
            "This measures retrieval coverage of generated queries, not VERIFY, MERGE, or final timeline quality.",
        ],
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = {
        f"{case['dataset']}:{case['topic']}": {
            run["name"]: (
                run["evaluation"]["event_coverage"]
                if run["status"] == "complete"
                else run["status"]
            )
            for run in case["runs"]
        }
        for case in cases
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
