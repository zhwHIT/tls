from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from chronos_repro.envfile import load_env_file
from chronos_repro.exploration import date_gaps, exploration_query, topic_date_bounds
from chronos_repro.llm import DeepSeekClient, InsufficientBalanceError, LLMError
from chronos_repro.retrieval import read_index_metadata, search
from chronos_repro.snapshot import sha256
from chronos_repro.strategies import direct_query


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--index", required=True)
    parser.add_argument("--search-engine", required=True)
    parser.add_argument("--topic-query", required=True)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    load_env_file(args.env_file)
    client = DeepSeekClient()
    dataset, topic = args.search_engine.split(maxsplit=1)
    start, end = topic_date_bounds(args.index, topic)
    payload = {
        "schema_version": 1,
        "status": "running",
        "strategy": "exploration_chronos_v1",
        "search_engine": args.search_engine,
        "config": {"model": client.model, "temperature": 0.0, "top_k": args.top_k,
                   "max_rounds": args.max_rounds, "stop_no_new_date_rounds": 2,
                   "query_similarity_limit": 0.75},
        "provenance": {"index_sha256": sha256(Path(args.index)),
                       "index_metadata": read_index_metadata(args.index)},
        "corpus_date_bounds": [start.isoformat(), end.isoformat()],
        "rounds": [],
    }
    evidence, seen_ids, covered, queries, stagnant, audits = [], set(), set(), [], 0, []
    exit_code = 0
    try:
        for number in range(1, args.max_rounds + 1):
            gaps_before = date_gaps(start, end, covered)
            if number == 1:
                query, audit = direct_query(args.topic_query), None
            else:
                query, audit = exploration_query(
                    client, args.topic_query, gaps_before, queries, evidence
                )
                audits.append(audit)
            results = search(args.index, [query], args.top_k, args.search_engine)
            new_results = [item for item in results if item["id"] not in seen_ids]
            new_dates = {
                date.fromisoformat(item["timestamp"][:10])
                for item in new_results
                if item.get("timestamp") and len(item["timestamp"]) >= 10
            } - covered
            seen_ids.update(item["id"] for item in new_results)
            evidence.extend(new_results)
            covered.update(new_dates)
            queries.append(query)
            stagnant = stagnant + 1 if not new_dates else 0
            payload["rounds"].append(
                {"round": number, "state": {"covered_dates": sorted(d.isoformat() for d in covered - new_dates),
                                              "date_gaps": gaps_before},
                 "action": {"type": "SEARCH", "query": query},
                 "observation": {"results": results,
                                 "new_doc_ids": [item["id"] for item in new_results],
                                 "new_dates": sorted(day.isoformat() for day in new_dates)},
                 "audit": audit}
            )
            if stagnant >= 2:
                payload["stop_reason"] = "no_new_dates"
                break
        else:
            payload["stop_reason"] = "max_rounds"
        payload["status"] = "ok"
    except InsufficientBalanceError as error:
        payload.update(status="stopped_insufficient_balance", stop_reason="insufficient_balance",
                       error=str(error))
        exit_code = 3
    except LLMError as error:
        payload.update(status="stopped_llm_error", stop_reason="llm_error", error=str(error))
        exit_code = 2
    payload["queries"] = queries
    payload["unique_results"] = evidence
    payload["final_covered_dates"] = sorted(day.isoformat() for day in covered)
    payload["usage"] = {
        key: sum(int(audit.get("usage", {}).get(key, 0)) for audit in audits)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
