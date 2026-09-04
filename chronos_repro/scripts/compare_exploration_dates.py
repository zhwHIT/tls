from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from chronos_repro.data import iter_topics


def score(results: list[dict], gold: set[date]) -> dict:
    dates = {date.fromisoformat(item["timestamp"][:10]) for item in results if item.get("timestamp")}
    exact = {day for day in gold if day in dates}
    window = {day for day in gold if any(abs((found - day).days) <= 2 for found in dates)}
    return {
        "documents": len(results), "unique_dates": len(dates),
        "exact_covered": len(exact), "exact_recall": len(exact) / len(gold),
        "window_2d_covered": len(window), "window_2d_recall": len(window) / len(gold),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--previous", required=True)
    parser.add_argument("--exploration", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    topic = next(item for item in iter_topics(args.data) if item.topic_id == args.topic)
    gold = set().union(*(set(timeline) for timeline in topic.timelines))
    previous = json.loads(Path(args.previous).read_text(encoding="utf-8"))
    exploration = json.loads(Path(args.exploration).read_text(encoding="utf-8"))
    result = {
        "topic": args.topic,
        "gold_dates": len(gold),
        "metrics": {
            "direct": score(previous["baselines"]["direct"]["results"], gold),
            "rewrite": score(previous["baselines"]["rewrite"]["results"], gold),
            "free_chronos": score(previous["baselines"]["chronos"]["unique_results"], gold),
            "exploration_chronos": score(exploration["unique_results"], gold),
        },
        "note": "Retrieval publication-date coverage diagnostic; not final TLS Date-F1.",
    }
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")


if __name__ == "__main__":
    main()
