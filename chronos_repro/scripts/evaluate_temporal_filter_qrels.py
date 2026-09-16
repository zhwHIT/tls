from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import date, timedelta
from pathlib import Path

from chronos_repro.retrieval import search
from chronos_repro.retrieval_evaluation import aggregate_scores, score_ranking


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_qrels(path: Path) -> dict[tuple[str, str, str], dict[str, int]]:
    output: dict[tuple[str, str, str], dict[str, int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = (str(row["dataset"]), str(row["topic"]), str(row["event_id"]))
        output.setdefault(key, {})[str(row["document_id"])] = int(row["grade"])
    return output


def resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bounds(canonical_date: str, window_days: int) -> tuple[str, str]:
    center = date.fromisoformat(canonical_date)
    return (
        (center - timedelta(days=window_days)).isoformat(),
        (center + timedelta(days=window_days)).isoformat(),
    )


def evaluate(args: argparse.Namespace) -> dict:
    started = time.perf_counter()
    project_root = Path(args.project_root).resolve()
    config_path = resolve(project_root, args.config)
    candidates_path = resolve(project_root, args.candidates)
    qrels_path = resolve(project_root, args.qrels)
    config = read_json(config_path)
    candidates = read_json(candidates_path)
    qrels = read_qrels(qrels_path)
    cutoffs = sorted(set(int(value) for value in args.cutoff))
    top_k = max(cutoffs)
    modes = list(dict.fromkeys(args.mode))
    case_config = {
        (str(row["dataset"]), str(row["topic"])): row for row in config["cases"]
    }
    all_scores: dict[str, list[dict]] = {mode: [] for mode in modes}
    totals = {
        mode: {"returned": 0, "judged": 0, "unjudged": 0, "in_range": 0}
        for mode in modes
    }
    case_reports = []

    for case in candidates["cases"]:
        dataset, topic = str(case["dataset"]), str(case["topic"])
        configured = case_config[(dataset, topic)]
        hybrid_index = resolve(project_root, configured["hybrid_index"])
        event_reports = []
        for number, event in enumerate(case["sampled_events"], start=1):
            event_id = str(event["event_id"])
            grades = qrels.get((dataset, topic, event_id), {})
            date_from, date_to = bounds(
                str(event["canonical_date"]), int(args.window_days)
            )
            runs = {}
            for mode in modes:
                date_kwargs = (
                    {}
                    if mode == "none"
                    else {
                        "date_from": date_from,
                        "date_to": date_to,
                        "date_filter_mode": mode,
                        "date_soft_penalty": float(args.soft_penalty),
                    }
                )
                results = search(
                    hybrid_index,
                    [str(event["summary"])],
                    top_k,
                    f"{dataset} {topic}",
                    **date_kwargs,
                )
                ranked_ids = [str(item["id"]) for item in results]
                score = score_ranking(ranked_ids, grades, cutoffs)
                judged = sum(document_id in grades for document_id in ranked_ids)
                in_range = sum(
                    date_from <= str(item.get("timestamp", ""))[:10] <= date_to
                    for item in results
                )
                totals[mode]["returned"] += len(results)
                totals[mode]["judged"] += judged
                totals[mode]["unjudged"] += len(results) - judged
                totals[mode]["in_range"] += in_range
                all_scores[mode].append(score)
                runs[mode] = {
                    "ranked_document_ids": ranked_ids,
                    "score": score,
                    "returned": len(results),
                    "judged": judged,
                    "unjudged": len(results) - judged,
                    "in_range": in_range,
                }
            cached_none = [str(value) for value in event.get("runs", {}).get("hybrid", [])]
            runs["none"]["cached_ranking_exact"] = (
                runs["none"]["ranked_document_ids"] == cached_none[:top_k]
            )
            event_reports.append(
                {
                    "event_number": number,
                    "event_id": event_id,
                    "canonical_date": event["canonical_date"],
                    "date_from": date_from,
                    "date_to": date_to,
                    "summary": event["summary"],
                    "qrels": len(grades),
                    "runs": runs,
                }
            )
            print(
                f"{dataset}:{topic} {number}/{len(case['sampled_events'])}",
                flush=True,
            )
        case_reports.append(
            {"dataset": dataset, "topic": topic, "events": event_reports}
        )

    overall = {}
    for mode in modes:
        diagnostics = totals[mode]
        returned = diagnostics["returned"]
        overall[mode] = {
            **aggregate_scores(all_scores[mode], cutoffs),
            **diagnostics,
            "judged_rate": diagnostics["judged"] / returned if returned else 0.0,
            "in_range_rate": diagnostics["in_range"] / returned if returned else 0.0,
        }
    return {
        "schema": "chronos-repro.temporal-filter-qrel-eval.v1",
        "config": str(config_path),
        "candidates": str(candidates_path),
        "qrels": str(qrels_path),
        "qrels_sha256": sha256(qrels_path),
        "settings": {
            "modes": modes,
            "window_days": int(args.window_days),
            "soft_penalty": float(args.soft_penalty),
            "cutoffs": cutoffs,
        },
        "overall": overall,
        "cases": case_reports,
        "elapsed_seconds": time.perf_counter() - started,
        "limitations": [
            "The range uses document publication dates, not inferred event dates.",
            "Qrels are pooled and incomplete; unjudged returns are reported and scored as non-relevant.",
            "Gold summaries and dates are evaluator-only oracle inputs, never runtime Agent inputs.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare none/soft/hard publication-date retrieval on cached qrels."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--config", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode", action="append", choices=["none", "soft", "hard"])
    parser.add_argument("--window-days", type=int, default=14)
    parser.add_argument("--soft-penalty", type=float, default=0.5)
    parser.add_argument("--cutoff", action="append", type=int)
    args = parser.parse_args()
    args.mode = args.mode or ["none", "soft", "hard"]
    args.cutoff = args.cutoff or [1, 3, 5, 10, 20]
    if args.window_days < 0:
        raise ValueError("window_days must be non-negative")
    report = evaluate(args)
    output = resolve(Path(args.project_root).resolve(), args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["overall"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
