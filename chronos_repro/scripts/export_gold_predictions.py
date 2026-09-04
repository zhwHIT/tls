"""Export reference 0 as predictions for evaluator plumbing tests only.

These files leak gold answers and must never be reported as a model baseline.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from chronos_repro.data import iter_topics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("data")
    parser.add_argument("output")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    for topic in iter_topics(args.data):
        payload = [
            [day.isoformat(), list(events)] for day, events in topic.timelines[0].items()
        ]
        (output / f"{topic.topic_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
