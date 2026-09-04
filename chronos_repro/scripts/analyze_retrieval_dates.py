from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from chronos_repro.data import iter_topics


def _score(results: list[dict], gold_dates: set[date]) -> dict:
    retrieved_dates = {
        date.fromisoformat(item["timestamp"][:10])
        for item in results
        if item.get("timestamp") and len(item["timestamp"]) >= 10
    }
    exact = gold_dates & retrieved_dates
    covered = {
        gold
        for gold in gold_dates
        if any(abs((found - gold).days) <= 2 for found in retrieved_dates)
    }
    near_relevant = {
        found
        for found in retrieved_dates
        if any(abs((found - gold).days) <= 2 for gold in gold_dates)
    }
    return {
        "documents": len(results),
        "unique_retrieved_dates": len(retrieved_dates),
        "gold_dates": len(gold_dates),
        "exact_gold_dates_covered": len(exact),
        "exact_date_recall": len(exact) / len(gold_dates) if gold_dates else 0.0,
        "window_2d_gold_dates_covered": len(covered),
        "window_2d_date_recall": len(covered) / len(gold_dates) if gold_dates else 0.0,
        "window_2d_retrieved_date_precision": (
            len(near_relevant) / len(retrieved_dates) if retrieved_dates else 0.0
        ),
        "exact_dates": sorted(day.isoformat() for day in exact),
        "window_2d_gold_dates": sorted(day.isoformat() for day in covered),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--baselines", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    topic = next((item for item in iter_topics(args.data) if item.topic_id == args.topic), None)
    if topic is None:
        raise SystemExit(f"Unknown topic: {args.topic}")
    gold_dates = set().union(*(set(timeline) for timeline in topic.timelines))
    payload = json.loads(Path(args.baselines).read_text(encoding="utf-8"))
    scores = {}
    for name, baseline in payload["baselines"].items():
        results = baseline.get("results", baseline.get("unique_results", []))
        scores[name] = _score(results, gold_dates)
    output = {
        "schema_version": 1,
        "topic": args.topic,
        "reference_timelines": len(topic.timelines),
        "metrics": scores,
        "note": "Retrieval-date diagnostic only; not final TLS Date-F1 or Timeline-ROUGE.",
    }
    Path(args.output).write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
