from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from chronos_repro.data import iter_topics
from chronos_repro.retrieval import read_index_metadata, search


def score_publication_dates(results: list[dict], gold_dates: set[date]) -> dict:
    """Diagnostic proxy only: publication date is not necessarily the event date."""
    ranked_dates = [
        date.fromisoformat(item["timestamp"][:10])
        for item in results
        if item.get("timestamp") and len(item["timestamp"]) >= 10
    ]
    retrieved_dates = set(ranked_dates)
    exact = gold_dates & retrieved_dates
    window = {
        gold
        for gold in gold_dates
        if any(abs((found - gold).days) <= 2 for found in retrieved_dates)
    }
    reciprocal_rank = 0.0
    for rank, found in enumerate(ranked_dates, start=1):
        if any(abs((found - gold).days) <= 2 for gold in gold_dates):
            reciprocal_rank = 1.0 / rank
            break
    return {
        "documents": len(results),
        "unique_documents": len({str(item["id"]) for item in results}),
        "unique_publication_dates": len(retrieved_dates),
        "gold_dates": len(gold_dates),
        "exact_gold_dates_covered": len(exact),
        "exact_gold_date_recall": len(exact) / len(gold_dates) if gold_dates else 0.0,
        "window_2d_gold_dates_covered": len(window),
        "window_2d_gold_date_recall": len(window) / len(gold_dates) if gold_dates else 0.0,
        "window_2d_mrr": reciprocal_rank,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--search-engine", required=True)
    parser.add_argument("--query", action="append", required=True)
    parser.add_argument(
        "--run",
        action="append",
        nargs=2,
        metavar=("NAME", "INDEX"),
        required=True,
        help="repeat for BM25 and hybrid indexes",
    )
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    topic = next((item for item in iter_topics(args.data) if item.topic_id == args.topic), None)
    if topic is None:
        raise SystemExit(f"Unknown topic: {args.topic}")
    gold_dates = set().union(*(set(timeline) for timeline in topic.timelines))
    runs = {}
    for name, index in args.run:
        results = search(index, args.query, args.top_k, args.search_engine)
        runs[name] = {
            "index": str(Path(index).resolve()),
            "index_metadata": read_index_metadata(index),
            "metrics": score_publication_dates(results, gold_dates),
            "results": results,
        }
    payload = {
        "schema": "chronos-repro.retriever-comparison.v1",
        "topic": args.topic,
        "search_engine": args.search_engine,
        "queries": args.query,
        "top_k": args.top_k,
        "runs": runs,
        "warning": (
            "Gold matching uses article publication dates as a diagnostic proxy; "
            "final evidence recall requires event-to-passage relevance labels."
        ),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
