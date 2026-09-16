from __future__ import annotations

import argparse
import json
from pathlib import Path

from chronos_repro.retrieval_evaluation import (
    aggregate_scores,
    quota_fusion,
    score_ranking,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_qrels(path: Path) -> dict[tuple[str, str], int]:
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows[(row["case_event_id"], str(row["document_id"]))] = int(row["grade"])
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    run_inputs = []
    for value in args.run_dir:
        run_dir = Path(value).resolve()
        run_inputs.append((run_dir, _read_json(run_dir / "candidates.json")))
    depths = {int(candidates["retrieve_depth"]) for _, candidates in run_inputs}
    if len(depths) != 1:
        raise ValueError(f"All candidate runs must use the same retrieve_depth: {depths}")
    depth = depths.pop()
    cutoffs = sorted({1, 3, 5, 10, depth})
    dense_slot_values = sorted(
        {max(1, min(depth - 1, round(depth * fraction))) for fraction in (0.1, 0.2, 0.3, 0.5)}
    )
    strategies = {
        "bm25": None,
        "dense": None,
        "hybrid_rrf_1_1": None,
    }
    strategies.update(
        {
            f"bm25_{depth - dense_slots}_dense_{dense_slots}": dense_slots
            for dense_slots in dense_slot_values
        }
    )
    scores = {name: [] for name in strategies}
    events = []
    sources = []
    for run_dir, candidates in run_inputs:
        qrels = _read_qrels(run_dir / "qrels.jsonl")
        sources.append(str(run_dir))
        for case in candidates["cases"]:
            for event in case["sampled_events"]:
                case_event_id = (
                    f"{case['dataset']}:{case['topic']}:{event['event_id']}"
                )
                grades = {
                    str(document["id"]): qrels[
                        (case_event_id, str(document["id"]))
                    ]
                    for document in event["pool"]
                }
                rankings = {
                    "bm25": event["runs"]["bm25"],
                    "dense": event["runs"]["dense"],
                    "hybrid_rrf_1_1": event["runs"]["hybrid"],
                }
                for name, dense_slots in strategies.items():
                    if dense_slots is not None:
                        rankings[name] = quota_fusion(
                            event["runs"]["bm25"],
                            event["runs"]["dense"],
                            depth,
                            dense_slots,
                        )
                event_scores = {
                    name: score_ranking(rankings[name], grades, cutoffs)
                    for name in strategies
                }
                for name, row in event_scores.items():
                    scores[name].append(row)
                events.append(
                    {
                        "case_event_id": case_event_id,
                        "dataset": case["dataset"],
                        "topic": case["topic"],
                        "canonical_date": event["canonical_date"],
                        "scores": event_scores,
                    }
                )
    report = {
        "schema": "chronos-repro.qrel-quota-fusion-ablation.v2",
        "source_run_dirs": sources,
        "retrieve_depth": depth,
        "events": len(events),
        "overall": {
            name: aggregate_scores(rows, cutoffs) for name, rows in scores.items()
        },
        "event_results": events,
        "warning": (
            "Small stratified dev pilot with pooled judgments; unresolved events "
            "are excluded from Recall/nDCG."
        ),
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["overall"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
