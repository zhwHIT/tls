from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from evaluate_retriever_qrels import (
    _load_cached_judgments,
    _seed_qrels,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--target-qrels", required=True)
    parser.add_argument("--seed-run-dir", action="append", default=[])
    parser.add_argument("--batch-size", type=int, default=30)
    args = parser.parse_args()

    candidates = json.loads(
        Path(args.candidates).read_text(encoding="utf-8")
    )
    target = Path(args.target_qrels)
    seed = _seed_qrels(target, candidates, args.seed_run_dir)
    cached = _load_cached_judgments(target)
    missing_by_event = []
    total = 0
    for case in candidates["cases"]:
        for event in case["sampled_events"]:
            case_event_id = (
                f"{case['dataset']}:{case['topic']}:{event['event_id']}"
            )
            total += len(event["pool"])
            missing_by_event.append(
                {
                    "case_event_id": case_event_id,
                    "pool": len(event["pool"]),
                    "missing": sum(
                        (case_event_id, str(document["id"])) not in cached
                        for document in event["pool"]
                    ),
                }
            )
    payload = {
        "events": len(missing_by_event),
        "pool_judgments": total,
        "seed": seed,
        "new_judgments": sum(row["missing"] for row in missing_by_event),
        "estimated_calls": sum(
            math.ceil(row["missing"] / args.batch_size)
            for row in missing_by_event
            if row["missing"]
        ),
        "missing_by_event": missing_by_event,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
