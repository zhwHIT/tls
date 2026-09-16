"""Audit annotation scope and compare temporal runs on one common qrel pool."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from chronos_repro.retrieval_evaluation import aggregate_scores


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def key(row):
    return row["dataset"], row["topic"], row["event_id"], str(row["document_id"])


def audit(candidates, source, delta, merged, old_report, new_report):
    expected = {
        (case["dataset"], case["topic"], event_id, str(doc["id"]))
        for case in candidates["cases"] for doc in case["documents"]
        for event_id in doc["missing_event_ids"]
    }
    source_map = {key(row): row for row in source}
    delta_map = {key(row): row for row in delta}
    merged_map = {key(row): row for row in merged}
    checks = {
        "source_unique": len(source_map) == len(source),
        "delta_unique": len(delta_map) == len(delta),
        "merged_unique": len(merged_map) == len(merged),
        "delta_exact_authorized_scope": set(delta_map) == expected,
        "delta_disjoint_source": not (set(delta_map) & set(source_map)),
        "merged_exact_union": merged_map == {**source_map, **delta_map},
        "valid_grades": all(type(row["grade"]) is int and row["grade"] in (0, 1, 2) for row in merged),
    }
    old_events = {(c["dataset"], c["topic"], e["event_id"]): e for c in old_report["cases"] for e in c["events"]}
    ranking_checks = []
    cases = []
    for case in new_report["cases"]:
        for event in case["events"]:
            prior = old_events[(case["dataset"], case["topic"], event["event_id"])]
            for mode, run in event["runs"].items():
                ranking_checks.append(run["ranked_document_ids"] == prior["runs"][mode]["ranked_document_ids"])
        cases.append({
            "dataset": case["dataset"], "topic": case["topic"],
            "overall": {
                mode: aggregate_scores([e["runs"][mode]["score"] for e in case["events"]], new_report["settings"]["cutoffs"])
                for mode in new_report["settings"]["modes"]
            },
        })
    checks["all_rankings_unchanged"] = all(ranking_checks) and len(ranking_checks) == 87
    checks["zero_unjudged_all_modes"] = all(row["unjudged"] == 0 for row in new_report["overall"].values())
    result = {
        "schema": "chronos-repro.temporal-qrel-completion-audit.v1",
        "valid": all(checks.values()), "checks": checks,
        "source_count": len(source), "delta_count": len(delta), "merged_count": len(merged),
        "delta_grades": dict(sorted(Counter(row["grade"] for row in delta).items())),
        "rankings_checked": len(ranking_checks),
        "overall": new_report["overall"], "cases": cases,
    }
    return result


def main():
    parser = argparse.ArgumentParser()
    for field in ("candidates", "source", "delta", "merged", "old-report", "new-report", "output"):
        parser.add_argument("--" + field, required=True)
    args = parser.parse_args()
    result = audit(read_json(args.candidates), read_rows(args.source), read_rows(args.delta), read_rows(args.merged), read_json(args.old_report), read_json(args.new_report))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
