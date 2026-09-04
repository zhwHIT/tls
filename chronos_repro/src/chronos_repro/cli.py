from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .data import iter_topics, load_prediction, load_queries
from .batch import evaluate_directory
from .corpus import validate_corpus
from .evaluate import evaluate_dates, evaluate_tilse
from .provenance import CHRONOS_UPSTREAM_COMMIT, runtime_metadata
from .snapshot import sha256
from .snapshot import freeze, verify
from .retrieval import build_bm25_index, read_index_metadata, search
from .trajectory import replay_trajectory, run_trajectory


def _write(payload: object, output: str | None) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


def cmd_audit(args: argparse.Namespace) -> None:
    queries = load_queries(args.queries)
    rows = []
    for topic in iter_topics(args.data, queries):
        all_dates = set().union(*(set(timeline) for timeline in topic.timelines))
        rows.append(
            {
                "topic_id": topic.topic_id,
                "query": topic.query,
                "reference_timelines": len(topic.timelines),
                "unique_dates": len(all_dates),
                "events": sum(len(events) for timeline in topic.timelines for events in timeline.values()),
            }
        )
    _write({"topics": len(rows), "items": rows}, args.output)


def cmd_evaluate(args: argparse.Namespace) -> None:
    topics = {topic.topic_id: topic for topic in iter_topics(args.data)}
    topic = topics.get(args.topic)
    if topic is None:
        raise SystemExit(f"Unknown topic {args.topic!r}; found {len(topics)} topics")
    prediction = load_prediction(args.prediction)
    if args.date_only:
        result = {"date_score": asdict(evaluate_dates(prediction, topic.timelines))}
    else:
        result = evaluate_tilse(prediction, topic.timelines, args.rouge_backend)
    _write(
        {
            "topic_id": topic.topic_id,
            **result,
            "provenance": {
                **runtime_metadata(args.data, "date-only" if args.date_only else args.rouge_backend, args.upstream_commit),
                "prediction": str(Path(args.prediction).resolve()),
                "prediction_sha256": sha256(Path(args.prediction)),
            },
        },
        args.output,
    )


def cmd_evaluate_batch(args: argparse.Namespace) -> None:
    _write(
        evaluate_directory(
            args.data,
            args.predictions,
            args.rouge_backend,
            args.date_only,
            args.allow_missing,
            args.upstream_commit,
        ),
        args.output,
    )


def cmd_validate_corpus(args: argparse.Namespace) -> None:
    result = validate_corpus(args.data, args.dataset, full=not args.quick)
    _write(result, args.output)
    if not result["valid"]:
        raise SystemExit(2)


def cmd_freeze(args: argparse.Namespace) -> None:
    if not args.skip_corpus_validation:
        validation = validate_corpus(args.source, args.dataset, full=True)
        if not validation["valid"]:
            raise SystemExit(
                "Corpus validation failed; refusing to freeze an incomplete/invalid corpus:\n"
                + "\n".join(validation["errors"])
            )
    target = freeze(args.source, args.destination, args.dataset, args.source_note)
    _write({"snapshot": str(target), "manifest": str(target / "MANIFEST.sha256.json")}, None)


def cmd_verify(args: argparse.Namespace) -> None:
    _write(verify(args.snapshot), args.output)


def cmd_build_index(args: argparse.Namespace) -> None:
    validation = validate_corpus(args.data, args.dataset, full=True)
    if not validation["valid"]:
        raise SystemExit("Corpus validation failed; refusing to index:\n" + "\n".join(validation["errors"]))
    _write(build_bm25_index(args.data, args.output), args.report)


def cmd_search(args: argparse.Namespace) -> None:
    result = search(args.index, args.query, args.top_k, args.search_engine)
    _write(
        {"query": args.query, "search_engine": args.search_engine, "top_k": args.top_k,
         "index_metadata": read_index_metadata(args.index), "results": result},
        args.output,
    )


def cmd_trace(args: argparse.Namespace) -> None:
    _write(
        run_trajectory(
            args.index, args.search_engine, args.query, args.top_k,
            args.max_rounds, args.stop_no_new_rounds,
        ),
        args.output,
    )


def cmd_replay(args: argparse.Namespace) -> None:
    _write(replay_trajectory(args.trajectory, args.index), args.output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chronos-repro")
    sub = parser.add_subparsers(required=True)
    audit = sub.add_parser("audit-open-tls", help="validate and summarize Open-TLS")
    audit.add_argument("--data", required=True)
    audit.add_argument("--queries", help="optional JSON mapping topic IDs to queries")
    audit.add_argument("--output")
    audit.set_defaults(func=cmd_audit)
    evaluate = sub.add_parser("evaluate", help="evaluate one CHRONOS prediction")
    evaluate.add_argument("--data", required=True)
    evaluate.add_argument("--topic", required=True)
    evaluate.add_argument("--prediction", required=True)
    evaluate.add_argument("--date-only", action="store_true")
    evaluate.add_argument(
        "--rouge-backend",
        choices=["original", "reimpl"],
        default="original",
        help="original requires Perl ROUGE; reimpl is portable but approximate",
    )
    evaluate.add_argument("--output")
    evaluate.add_argument("--upstream-commit", default=CHRONOS_UPSTREAM_COMMIT)
    evaluate.set_defaults(func=cmd_evaluate)
    batch = sub.add_parser("evaluate-batch", help="evaluate predictions named <topic>.json")
    batch.add_argument("--data", required=True)
    batch.add_argument("--predictions", required=True)
    batch.add_argument("--date-only", action="store_true")
    batch.add_argument("--allow-missing", action="store_true")
    batch.add_argument("--rouge-backend", choices=["original", "reimpl"], default="original")
    batch.add_argument("--upstream-commit", default=CHRONOS_UPSTREAM_COMMIT)
    batch.add_argument("--output")
    batch.set_defaults(func=cmd_evaluate_batch)
    validate_parser = sub.add_parser("validate-corpus", help="validate closed-corpus completeness and records")
    validate_parser.add_argument("--data", required=True)
    validate_parser.add_argument("--dataset", choices=["t17", "crisis", "entities"], required=True)
    validate_parser.add_argument("--quick", action="store_true", help="parse only the first article per topic")
    validate_parser.add_argument("--output")
    validate_parser.set_defaults(func=cmd_validate_corpus)
    freeze_parser = sub.add_parser("freeze", help="create a content-addressed corpus snapshot")
    freeze_parser.add_argument("--source", required=True)
    freeze_parser.add_argument("--destination", required=True)
    freeze_parser.add_argument("--dataset", choices=["t17", "crisis", "entities"], required=True)
    freeze_parser.add_argument("--source-note", required=True)
    freeze_parser.add_argument(
        "--skip-corpus-validation",
        action="store_true",
        help="unsafe escape hatch for non-standard corpora",
    )
    freeze_parser.set_defaults(func=cmd_freeze)
    verify_parser = sub.add_parser("verify", help="verify every file in a snapshot")
    verify_parser.add_argument("--snapshot", required=True)
    verify_parser.add_argument("--output")
    verify_parser.set_defaults(func=cmd_verify)
    index_parser = sub.add_parser("build-index", help="build a closed-domain SQLite FTS5/BM25 index")
    index_parser.add_argument("--data", required=True)
    index_parser.add_argument("--dataset", choices=["t17", "crisis", "entities"], required=True)
    index_parser.add_argument("--output", required=True)
    index_parser.add_argument("--report")
    index_parser.set_defaults(func=cmd_build_index)
    search_parser = sub.add_parser("search", help="query a closed-domain index")
    search_parser.add_argument("--index", required=True)
    search_parser.add_argument("--search-engine", required=True, help="for example: crisis egypt")
    search_parser.add_argument("--query", action="append", required=True)
    search_parser.add_argument("--top-k", type=int, default=20)
    search_parser.add_argument("--output")
    search_parser.set_defaults(func=cmd_search)
    trace_parser = sub.add_parser("trace-search", help="run and cache deterministic search rounds")
    trace_parser.add_argument("--index", required=True)
    trace_parser.add_argument("--search-engine", required=True)
    trace_parser.add_argument("--query", action="append", required=True)
    trace_parser.add_argument("--top-k", type=int, default=20)
    trace_parser.add_argument("--max-rounds", type=int, default=3)
    trace_parser.add_argument("--stop-no-new-rounds", type=int, default=2)
    trace_parser.add_argument("--output", required=True)
    trace_parser.set_defaults(func=cmd_trace)
    replay_parser = sub.add_parser("replay-search", help="verify a cached search trajectory")
    replay_parser.add_argument("--trajectory", required=True)
    replay_parser.add_argument("--index")
    replay_parser.add_argument("--output")
    replay_parser.set_defaults(func=cmd_replay)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
