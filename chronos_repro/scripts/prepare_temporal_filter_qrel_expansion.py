from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from chronos_repro.retrieval import fetch_documents


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare unjudged soft/hard results for multi-event qrel annotation."
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    project_root = Path(".").resolve()
    config = read_json(resolve(project_root, args.config))
    report = read_json(resolve(project_root, config["temporal_report"]))
    qrels = read_jsonl(resolve(project_root, config["source_qrels"]))
    configured_cases = {
        (str(row["dataset"]), str(row["topic"])): row
        for row in config["cases"]
    }
    modes = list(config.get("modes", ["soft", "hard"]))
    document_max_chars = int(config["document_max_chars"])
    batch_size = int(config["batch_size"])
    cases = []
    totals = {
        "retrieved_document_union": 0,
        "documents_requiring_judgment": 0,
        "missing_event_document_pairs": 0,
        "estimated_batches": 0,
    }

    for case in report["cases"]:
        dataset, topic = str(case["dataset"]), str(case["topic"])
        case_config = configured_cases[(dataset, topic)]
        document_events: dict[str, list[str]] = {}
        for event in case["events"]:
            event_id = str(event["event_id"])
            for mode in modes:
                for document_id in event["runs"][mode]["ranked_document_ids"]:
                    value = str(document_id)
                    events = document_events.setdefault(value, [])
                    if event_id not in events:
                        events.append(event_id)
        existing = {
            (str(row["event_id"]), str(row["document_id"]))
            for row in qrels
            if str(row["dataset"]) == dataset and str(row["topic"]) == topic
        }
        missing = {
            document_id: [
                event_id
                for event_id in event_ids
                if (event_id, document_id) not in existing
            ]
            for document_id, event_ids in document_events.items()
        }
        missing = {
            document_id: event_ids
            for document_id, event_ids in missing.items()
            if event_ids
        }
        documents = fetch_documents(
            resolve(project_root, case_config["bm25_index"]),
            topic,
            list(missing),
            max_chars=document_max_chars,
        )
        by_id = {str(row["id"]): row for row in documents}
        if set(by_id) != set(missing):
            absent = sorted(set(missing).difference(by_id))
            raise ValueError(f"Could not fetch documents for {dataset}:{topic}: {absent}")
        document_rows = [
            {**by_id[document_id], "missing_event_ids": missing[document_id]}
            for document_id in missing
        ]
        missing_pairs = sum(
            len(row["missing_event_ids"]) for row in document_rows
        )
        batches = math.ceil(len(document_rows) / batch_size)
        totals["retrieved_document_union"] += len(document_events)
        totals["documents_requiring_judgment"] += len(document_rows)
        totals["missing_event_document_pairs"] += missing_pairs
        totals["estimated_batches"] += batches
        cases.append(
            {
                "dataset": dataset,
                "topic": topic,
                "events": [
                    {
                        "event_id": event["event_id"],
                        "canonical_date": event["canonical_date"],
                        "summary": event["summary"],
                    }
                    for event in case["events"]
                ],
                "documents": document_rows,
                "counts": {
                    "retrieved_document_union": len(document_events),
                    "documents_requiring_judgment": len(document_rows),
                    "missing_event_document_pairs": missing_pairs,
                    "estimated_batches": batches,
                },
            }
        )

    payload = {
        "schema": "chronos-repro.temporal-filter-multievent-candidates.v1",
        "split": "dev",
        "source_report": str(resolve(project_root, config["temporal_report"])),
        "source_qrels": str(resolve(project_root, config["source_qrels"])),
        "modes": modes,
        "document_max_chars": document_max_chars,
        "batch_size": batch_size,
        "cases": cases,
        "totals": totals,
        "status": "prepared_not_sent",
        "warning": (
            "Local preparation only. API submission requires explicit authorization "
            "for the exact topics, document count, character cap, and estimated batches."
        ),
    }
    output = resolve(project_root, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"cases": [case["counts"] for case in cases], "totals": totals}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
