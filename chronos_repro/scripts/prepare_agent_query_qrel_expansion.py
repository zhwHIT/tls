from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from chronos_repro.agent_query_evaluation import (
    missing_event_ids_by_document,
    query_document_ids,
)
from chronos_repro.retrieval import fetch_documents


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    config = read_json(Path(args.config).resolve())
    source_candidates = read_json(Path(config["source_candidates"]).resolve())
    qrels = read_jsonl(Path(config["source_qrels"]).resolve())
    query_report = read_json(Path(config["agent_query_report"]).resolve())
    source_cases = {
        (case["dataset"], case["topic"]): case
        for case in source_candidates["cases"]
    }
    report_cases = {
        (case["dataset"], case["topic"]): case
        for case in query_report["cases"]
    }
    cases = []
    total_documents = 0
    total_pairs = 0
    total_batches = 0
    batch_size = int(config["batch_size"])

    for case_config in config["cases"]:
        key = (case_config["dataset"], case_config["topic"])
        source_case = source_cases[key]
        report_case = report_cases[key]
        case_qrels = [
            row
            for row in qrels
            if (row["dataset"], row["topic"]) == key
        ]
        event_ids = [
            str(event["event_id"]) for event in source_case["sampled_events"]
        ]
        document_ids = query_document_ids(report_case)
        missing = missing_event_ids_by_document(
            event_ids, document_ids, case_qrels
        )
        documents = fetch_documents(
            Path(case_config["bm25_index"]).resolve(),
            case_config["topic"],
            list(missing),
            max_chars=int(config["document_max_chars"]),
        )
        by_id = {str(row["id"]): row for row in documents}
        if set(by_id) != set(missing):
            raise ValueError(f"Could not fetch all query documents for {key}")
        document_rows = [
            {
                **by_id[document_id],
                "missing_event_ids": missing[document_id],
            }
            for document_id in missing
        ]
        missing_pairs = sum(
            len(document["missing_event_ids"]) for document in document_rows
        )
        estimated_batches = math.ceil(len(document_rows) / batch_size)
        total_documents += len(document_rows)
        total_pairs += missing_pairs
        total_batches += estimated_batches
        cases.append(
            {
                "dataset": key[0],
                "topic": key[1],
                "events": [
                    {
                        "event_id": event["event_id"],
                        "canonical_date": event["canonical_date"],
                        "accepted_dates": event["accepted_dates"],
                        "summary": event["summary"],
                    }
                    for event in source_case["sampled_events"]
                ],
                "documents": document_rows,
                "counts": {
                    "query_union_documents": len(document_ids),
                    "documents_requiring_judgment": len(document_rows),
                    "missing_event_document_pairs": missing_pairs,
                    "estimated_batches": estimated_batches,
                },
            }
        )

    payload = {
        "schema": "chronos-repro.agent-query-multievent-candidates.v1",
        "split": "dev",
        "document_max_chars": int(config["document_max_chars"]),
        "batch_size": batch_size,
        "cases": cases,
        "totals": {
            "documents_requiring_judgment": total_documents,
            "missing_event_document_pairs": total_pairs,
            "estimated_batches": total_batches,
        },
        "status": "prepared_not_sent",
        "warning": (
            "This file is local preparation only. API submission requires explicit "
            "authorization for this exact document and batch scope."
        ),
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["totals"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
