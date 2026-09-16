from __future__ import annotations

import argparse
import json
from pathlib import Path

from chronos_repro.retrieval_evaluation import aggregate_scores


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    sources = [
        json.loads(Path(value).read_text(encoding="utf-8")) for value in args.report
    ]
    cutoffs = sources[0]["method"]["cutoffs"]
    if any(source.get("status") != "complete" for source in sources):
        raise ValueError("Every source report must be complete")
    if any(source["method"]["cutoffs"] != cutoffs for source in sources):
        raise ValueError("Source reports use different cutoffs")
    scores = {"bm25": [], "dense": [], "hybrid": []}
    cases = []
    for source in sources:
        for case in source["cases"]:
            cases.append(case)
            for event in case["events"]:
                for name in scores:
                    scores[name].append(event["runs"][name])
    report = {
        "schema": "chronos-repro.retrieval-qrel-merged-evaluation.v1",
        "status": "complete",
        "source_reports": [str(Path(value).resolve()) for value in args.report],
        "events": sum(len(case["events"]) for case in cases),
        "overall": {
            name: aggregate_scores(rows, cutoffs) for name, rows in scores.items()
        },
        "qrels": {
            key: sum(int(source["qrels"][key]) for source in sources)
            for key in ("judgments", "direct_relevant", "contextual", "irrelevant")
        },
        "cases": cases,
        "limitations": [
            "Metrics are macro-averaged over a small stratified dev pilot.",
            "Recall uses pooled judgments, not exhaustive corpus relevance.",
            "Unresolved events remain in Success/MRR but are excluded from Recall/nDCG.",
        ],
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
