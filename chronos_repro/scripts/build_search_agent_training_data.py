from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from chronos_repro.data import iter_topics
from chronos_repro.retrieval import read_index_metadata, search
from chronos_repro.snapshot import sha256
from chronos_repro.training_data import (
    prompt_messages,
    rank_candidates,
    result_dates,
    score_candidate,
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def candidate_pool(base: dict, exploration: dict, round_number: int) -> list[dict]:
    sources: dict[str, set[str]] = {}
    for name, baseline in base["baselines"].items():
        for query in baseline.get("queries", []):
            sources.setdefault(query.strip(), set()).add(name)
    for index, query in enumerate(exploration.get("queries", [])[:round_number], 1):
        sources.setdefault(query.strip(), set()).add(f"exploration_round_{index}")
    return [
        {"query": query, "sources": sorted(names)}
        for query, names in sorted(sources.items(), key=lambda item: item[0].casefold())
        if query
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--artifacts", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--top-k", type=int, default=20)
    args = parser.parse_args()
    if args.top_k < 1:
        raise ValueError("top-k must be positive")

    data_root = Path(args.data)
    index = Path(args.index)
    artifacts = Path(args.artifacts)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    transitions: list[dict] = []
    sft_rows: list[dict] = []
    dpo_rows: list[dict] = []
    source_hashes: dict[str, str] = {}

    for topic in iter_topics(data_root):
        topic_id = topic.topic_id
        gold_dates = set().union(*(set(timeline) for timeline in topic.timelines))
        base_path = artifacts / f"{topic_id}_query_baselines_deepseek_v1.json"
        exploration_path = artifacts / f"{topic_id}_exploration_chronos_v1.json"
        base = read_json(base_path)
        exploration = read_json(exploration_path)
        if exploration.get("status") != "ok":
            raise ValueError(f"Exploration artifact is not successful: {exploration_path}")
        source_hashes[base_path.name] = sha256(base_path)
        source_hashes[exploration_path.name] = sha256(exploration_path)
        seen_ids: set[str] = set()
        prior_evidence: list[dict] = []
        previous_queries: list[str] = []

        for round_item in exploration["rounds"]:
            round_number = int(round_item["round"])
            query = round_item["action"]["query"]
            results = round_item["observation"]["results"]
            covered_before = {
                date.fromisoformat(value)
                for value in round_item["state"]["covered_dates"]
            }
            behavior_metrics = score_candidate(
                query, results, covered_before, seen_ids, gold_dates, previous_queries
            )
            new_results = [item for item in results if str(item["id"]) not in seen_ids]
            new_dates = result_dates(new_results) - covered_before
            prompt_state = {
                "topic": topic_id,
                "round": round_number,
                "corpus_date_bounds": exploration["corpus_date_bounds"],
                "covered_dates": sorted(day.isoformat() for day in covered_before),
                "date_gaps": round_item["state"]["date_gaps"],
                "previous_queries": list(previous_queries),
                "recent_evidence": [
                    {"date": item.get("timestamp"), "title": item.get("title")}
                    for item in prior_evidence[-12:]
                ],
            }
            transition = {
                "schema_version": 1,
                "topic": topic_id,
                "round": round_number,
                "state": prompt_state,
                "action": round_item["action"],
                "observation": {
                    "result_doc_ids": [str(item["id"]) for item in results],
                    "new_doc_ids": [str(item["id"]) for item in new_results],
                    "new_dates": sorted(day.isoformat() for day in new_dates),
                },
                "updated_state": {
                    "covered_dates": sorted(
                        day.isoformat() for day in covered_before | result_dates(new_results)
                    )
                },
                "metrics": behavior_metrics,
                "source_artifact": exploration_path.name,
            }
            transitions.append(transition)

            if round_number >= 2:
                candidates = []
                for candidate in candidate_pool(base, exploration, round_number):
                    replay_results = search(
                        index, [candidate["query"]], args.top_k, exploration["search_engine"]
                    )
                    candidates.append({
                        **candidate,
                        "metrics": score_candidate(
                            candidate["query"], replay_results, covered_before,
                            seen_ids, gold_dates, previous_queries,
                        ),
                    })
                ranked = rank_candidates(candidates)
                chosen, rejected = ranked[0], ranked[-1]
                messages = prompt_messages(prompt_state)
                metadata = {
                    "topic": topic_id,
                    "round": round_number,
                    "chosen_metrics": chosen["metrics"],
                    "rejected_metrics": rejected["metrics"],
                    "candidate_count": len(ranked),
                    "candidate_ranking": ranked,
                    "label_source": "closed_domain_replay_with_gold_date_coverage",
                }
                sft_rows.append({
                    "schema_version": 1,
                    "messages": messages + [
                        {"role": "assistant", "content": chosen["query"]}
                    ],
                    "metadata": metadata,
                })
                if chosen["query"].casefold() != rejected["query"].casefold():
                    dpo_rows.append({
                        "schema_version": 1,
                        "prompt": messages,
                        "chosen": [{"role": "assistant", "content": chosen["query"]}],
                        "rejected": [{"role": "assistant", "content": rejected["query"]}],
                        "metadata": metadata,
                    })

            seen_ids.update(str(item["id"]) for item in new_results)
            prior_evidence.extend(new_results)
            previous_queries.append(query)

    paths = {
        "transitions": output_dir / "crisis_search_agent_transitions_v1.jsonl",
        "sft": output_dir / "crisis_search_agent_sft_seed_v1.jsonl",
        "dpo": output_dir / "crisis_search_agent_dpo_seed_v1.jsonl",
    }
    write_jsonl(paths["transitions"], transitions)
    write_jsonl(paths["sft"], sft_rows)
    write_jsonl(paths["dpo"], dpo_rows)
    metadata = read_index_metadata(index)
    manifest = {
        "schema_version": 1,
        "dataset": "crisis",
        "snapshot": metadata.get("snapshot"),
        "index_sha256": sha256(index),
        "source_artifact_sha256": source_hashes,
        "top_k": args.top_k,
        "reward": "1000*exact_gold_gain + 100*window_2d_gold_gain + 2*new_date_count + query_novelty",
        "counts": {
            "transitions": len(transitions),
            "sft": len(sft_rows),
            "dpo": len(dpo_rows),
        },
        "outputs": {
            name: {"file": path.name, "sha256": sha256(path)}
            for name, path in paths.items()
        },
        "limitations": [
            "Seed data only; four Crisis topics are insufficient for final training.",
            "Labels use article publication-date coverage, not final TLS content quality.",
            "Gold dates are used only for offline preference labeling.",
        ],
    }
    manifest_path = output_dir / "crisis_search_agent_training_manifest_v1.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
