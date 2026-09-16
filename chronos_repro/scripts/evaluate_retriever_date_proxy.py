from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from chronos_repro.data import iter_topics
from chronos_repro.dense_retrieval import search_dense
from chronos_repro.gold_supervision import align_reference_events
from chronos_repro.retrieval import search, search_single
from chronos_repro.retrieval_evaluation import (
    aggregate_publication_date_scores,
    score_publication_date_proxy,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _validate_dev_topics(config: dict, base: Path) -> None:
    if config.get("split") != "dev":
        raise ValueError("This evaluation is restricted to split=dev")
    splits = _read_json(base / "configs" / "tisa_gold_supervision_v2.json")
    for case in config["cases"]:
        allowed = set(splits["datasets"][case["dataset"]]["splits"]["dev"])
        unknown = sorted(set(case["topics"]) - allowed)
        if unknown:
            raise ValueError(
                f"Non-dev topics requested for {case['dataset']}: {unknown}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    started = time.perf_counter()
    config_path = Path(args.config).resolve()
    base = config_path.parent.parent
    config = _read_json(config_path)
    _validate_dev_topics(config, base)
    depth = int(config["retrieve_depth"])
    cutoffs = [int(value) for value in config["cutoffs"]]
    window_days = int(config["window_days"])
    all_scores = {"bm25": [], "dense": [], "hybrid": []}
    run_seconds = {"bm25": 0.0, "dense": 0.0, "hybrid": 0.0}
    cases = []
    completed = 0

    for case in config["cases"]:
        dataset = case["dataset"]
        data = _resolve(base, case["data"])
        bm25_index = _resolve(base, case["bm25_index"])
        dense_index = _resolve(base, case["dense_index"])
        hybrid_index = _resolve(base, case["hybrid_index"])
        topics = {topic.topic_id: topic for topic in iter_topics(data)}
        for topic_id in case["topics"]:
            topic = topics[topic_id]
            events = align_reference_events(topic_id, topic.timelines)
            topic_scores = {"bm25": [], "dense": [], "hybrid": []}
            event_reports = []
            for event in events:
                query = event["summary"]
                rankings = {}
                before = time.perf_counter()
                rankings["bm25"] = search_single(bm25_index, query, topic_id, depth)
                run_seconds["bm25"] += time.perf_counter() - before
                before = time.perf_counter()
                rankings["dense"] = search_dense(dense_index, query, topic_id, depth)
                run_seconds["dense"] += time.perf_counter() - before
                before = time.perf_counter()
                rankings["hybrid"] = search(
                    hybrid_index, [query], depth, f"{dataset} {topic_id}"
                )
                run_seconds["hybrid"] += time.perf_counter() - before

                scores = {}
                for name, results in rankings.items():
                    score = score_publication_date_proxy(
                        results, event["accepted_dates"], cutoffs, window_days
                    )
                    scores[name] = score
                    topic_scores[name].append(score)
                    all_scores[name].append(score)
                event_reports.append(
                    {
                        "event_id": event["event_id"],
                        "canonical_date": event["canonical_date"],
                        "summary": event["summary"],
                        "scores": scores,
                        "ranked_document_ids": {
                            name: [str(row["id"]) for row in results]
                            for name, results in rankings.items()
                        },
                    }
                )
                completed += 1
                if completed % 10 == 0:
                    print(f"evaluated events={completed}", flush=True)
            cases.append(
                {
                    "dataset": dataset,
                    "topic": topic_id,
                    "events": len(events),
                    "aggregate": {
                        name: aggregate_publication_date_scores(
                            scores, cutoffs, window_days
                        )
                        for name, scores in topic_scores.items()
                    },
                    "event_results": event_reports,
                }
            )
            print(
                f"completed {dataset}:{topic_id} events={len(events)}",
                flush=True,
            )

    report = {
        "schema": "chronos-repro.retrieval-date-proxy-evaluation.v1",
        "status": "complete",
        "split": config["split"],
        "events": completed,
        "method": {
            "query": "Gold event summary used as evaluator-side oracle query",
            "relevance_proxy": (
                f"article publication date exact or within ±{window_days} days "
                "of an accepted Gold event date"
            ),
            "cutoffs": cutoffs,
            "retrieve_depth": depth,
            "warning": config["warning"],
        },
        "overall": {
            name: aggregate_publication_date_scores(scores, cutoffs, window_days)
            for name, scores in all_scores.items()
        },
        "runtime_seconds": run_seconds,
        "elapsed_seconds": time.perf_counter() - started,
        "cases": cases,
    }
    _write_json(Path(args.output).resolve(), report)
    print(json.dumps(report["overall"], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
