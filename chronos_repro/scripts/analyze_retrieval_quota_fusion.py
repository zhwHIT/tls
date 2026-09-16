from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from chronos_repro.data import iter_topics
from chronos_repro.gold_supervision import align_reference_events
from chronos_repro.retrieval_evaluation import (
    aggregate_publication_date_scores,
    quota_fusion,
    score_publication_date_proxy,
)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _timestamps(index: Path, topic: str, document_ids: set[str]) -> dict[str, str]:
    if not document_ids:
        return {}
    values = sorted(document_ids)
    output = {}
    with sqlite3.connect(index) as connection:
        for offset in range(0, len(values), 500):
            batch = values[offset : offset + 500]
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                "SELECT doc_id, timestamp FROM documents "
                f"WHERE topic = ? AND doc_id IN ({placeholders})",
                [topic, *batch],
            ).fetchall()
            output.update({str(document_id): str(timestamp) for document_id, timestamp in rows})
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    evaluation = _read(Path(args.evaluation).resolve())
    config_path = Path(args.config).resolve()
    config = _read(config_path)
    base = config_path.parent.parent
    cutoffs = [int(value) for value in config["cutoffs"]]
    depth = int(config["retrieve_depth"])
    window_days = int(config["window_days"])
    case_config = {
        row["dataset"]: row for row in config["cases"]
    }
    strategies = {
        "bm25": 0,
        "bm25_9_dense_1": 1,
        "bm25_8_dense_2": 2,
        "bm25_7_dense_3": 3,
        "bm25_5_dense_5": 5,
    }
    all_scores = {name: [] for name in strategies}
    cases = []

    for case in evaluation["cases"]:
        dataset, topic_id = case["dataset"], case["topic"]
        settings = case_config[dataset]
        data = _resolve(base, settings["data"])
        index = _resolve(base, settings["bm25_index"])
        topic = next(row for row in iter_topics(data) if row.topic_id == topic_id)
        events = {
            row["event_id"]: row for row in align_reference_events(topic_id, topic.timelines)
        }
        ids = {
            document_id
            for event in case["event_results"]
            for run in ("bm25", "dense")
            for document_id in event["ranked_document_ids"][run]
        }
        dates = _timestamps(index, topic_id, ids)
        topic_scores = {name: [] for name in strategies}
        event_rows = []
        for event in case["event_results"]:
            gold = events[event["event_id"]]
            rankings = {}
            scores = {}
            for name, dense_slots in strategies.items():
                ranking = quota_fusion(
                    event["ranked_document_ids"]["bm25"],
                    event["ranked_document_ids"]["dense"],
                    depth,
                    dense_slots,
                )
                rankings[name] = ranking
                results = [
                    {"id": document_id, "timestamp": dates.get(document_id, "")}
                    for document_id in ranking
                ]
                score = score_publication_date_proxy(
                    results, gold["accepted_dates"], cutoffs, window_days
                )
                scores[name] = score
                topic_scores[name].append(score)
                all_scores[name].append(score)
            event_rows.append(
                {
                    "event_id": event["event_id"],
                    "rankings": rankings,
                    "scores": scores,
                }
            )
        cases.append(
            {
                "dataset": dataset,
                "topic": topic_id,
                "events": len(event_rows),
                "aggregate": {
                    name: aggregate_publication_date_scores(rows, cutoffs, window_days)
                    for name, rows in topic_scores.items()
                },
                "event_results": event_rows,
            }
        )
    report = {
        "schema": "chronos-repro.retrieval-quota-fusion-ablation.v1",
        "source_evaluation": str(Path(args.evaluation).resolve()),
        "method": (
            "Keep a BM25 prefix, fill the remaining top-k positions with unique "
            "dense documents, then backfill from BM25. Uses cached rankings only."
        ),
        "overall": {
            name: aggregate_publication_date_scores(rows, cutoffs, window_days)
            for name, rows in all_scores.items()
        },
        "cases": cases,
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
