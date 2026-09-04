from __future__ import annotations

import argparse
import json
from pathlib import Path

from chronos_repro.envfile import load_env_file
from chronos_repro.llm import DeepSeekClient, InsufficientBalanceError, LLMError
from chronos_repro.retrieval import read_index_metadata, search
from chronos_repro.snapshot import sha256
from chronos_repro.strategies import chronos_query, direct_query, rewrite_query


def _usage_total(audits: list[dict]) -> dict:
    fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    return {field: sum(int(audit.get("usage", {}).get(field, 0)) for audit in audits)
            for field in fields}


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
    audits: list[dict] = []
    payload = {
        "schema_version": 1,
        "status": "running",
        "config": {"model": client.model, "base_url": client.base_url,
                   "temperature": 0.0, "top_k": args.top_k,
                   "max_rounds": args.max_rounds},
        "provenance": {"index": str(Path(args.index).resolve()),
                       "index_sha256": sha256(Path(args.index)),
                       "index_metadata": read_index_metadata(args.index)},
        "baselines": {},
    }
    exit_code = 0
    try:
        direct = direct_query(args.topic_query)
        payload["baselines"]["direct"] = {
            "queries": [direct],
            "results": search(args.index, [direct], args.top_k, args.search_engine),
            "llm_calls": 0,
        }
        rewritten, audit = rewrite_query(client, args.topic_query)
        audits.append(audit)
        payload["baselines"]["rewrite"] = {
            "queries": [rewritten],
            "results": search(args.index, [rewritten], args.top_k, args.search_engine),
            "llm_calls": 1,
            "audit": audit,
        }
        previous, evidence, seen, rounds = [], [], set(), []
        for number in range(1, args.max_rounds + 1):
            query, audit = chronos_query(client, args.topic_query, number, previous, evidence)
            audits.append(audit)
            results = search(args.index, [query], args.top_k, args.search_engine)
            new_results = [item for item in results if item["id"] not in seen]
            seen.update(item["id"] for item in new_results)
            evidence.extend(new_results)
            previous.append(query)
            rounds.append({"round": number, "query": query, "results": results,
                           "new_doc_ids": [item["id"] for item in new_results],
                           "audit": audit})
        payload["baselines"]["chronos"] = {
            "queries": previous, "rounds": rounds, "unique_results": evidence,
            "llm_calls": len(rounds),
        }
        payload["status"] = "ok"
    except InsufficientBalanceError as error:
        payload.update(status="stopped_insufficient_balance", error=str(error))
        exit_code = 3
    except LLMError as error:
        payload.update(status="stopped_llm_error", error=str(error))
        exit_code = 2
    payload["usage"] = _usage_total(audits)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
