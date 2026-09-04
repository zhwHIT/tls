from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from chronos_repro.data import iter_topics


def score(results: list[dict], gold: set[date]) -> dict:
    dates = {date.fromisoformat(x["timestamp"][:10]) for x in results if x.get("timestamp")}
    exact = sum(day in dates for day in gold)
    window = sum(any(abs((found - day).days) <= 2 for found in dates) for day in gold)
    return {"documents": len(results), "unique_dates": len(dates),
            "exact_recall": exact / len(gold), "window_2d_recall": window / len(gold)}


def macro(rows: list[dict]) -> dict:
    keys = ("documents", "unique_dates", "exact_recall", "window_2d_recall")
    return {key: sum(row[key] for row in rows) / len(rows) for key in keys}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--artifacts", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    artifacts = Path(args.artifacts)
    per_topic, failures = {}, {}
    for topic in iter_topics(args.data):
        gold = set().union(*(set(timeline) for timeline in topic.timelines))
        base = json.loads((artifacts / f"{topic.topic_id}_query_baselines_deepseek_v1.json").read_text(encoding="utf-8"))
        row = {
            "direct": score(base["baselines"]["direct"]["results"], gold),
            "rewrite": score(base["baselines"]["rewrite"]["results"], gold),
            "free_chronos": score(base["baselines"]["chronos"]["unique_results"], gold),
        }
        exploration_path = artifacts / f"{topic.topic_id}_exploration_chronos_v1.json"
        exploration = json.loads(exploration_path.read_text(encoding="utf-8"))
        if exploration.get("status") == "ok":
            row["exploration_chronos"] = score(exploration["unique_results"], gold)
        else:
            failures[topic.topic_id] = {"status": exploration.get("status"),
                                       "error": exploration.get("error")}
        per_topic[topic.topic_id] = row
    strategies = ("direct", "rewrite", "free_chronos", "exploration_chronos")
    aggregate = {}
    for strategy in strategies:
        available = [(topic, row[strategy]) for topic, row in per_topic.items() if strategy in row]
        aggregate[strategy] = {"topics": len(available), "topic_ids": [x[0] for x in available],
                               "macro": macro([x[1] for x in available])}
    paired_topics = [topic for topic, row in per_topic.items() if all(s in row for s in strategies)]
    paired_macro = {strategy: macro([per_topic[topic][strategy] for topic in paired_topics])
                    for strategy in strategies}
    output = {"schema_version": 1, "per_topic": per_topic, "aggregate_available": aggregate,
              "paired_topics": paired_topics, "paired_macro": paired_macro,
              "failures": failures,
              "note": "Publication-date retrieval diagnostic, not final TLS Date-F1."}
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
