"""Small live check on already-authorized historical snippets, not a full rollout."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from chronos_repro.envfile import load_env_file
from chronos_repro.exploration_memory import (
    initial_memory, load_keywords, validate_memory_output, apply_memory_output,
    validate_exploration_policy,
)
from chronos_repro.llm import DeepSeekClient, InsufficientBalanceError, LLMError
from chronos_repro.llm_cache import CachedLLMClient
from chronos_repro.retrieval import search
from chronos_repro.tisa_data import parse_json_object
from exploration_phase import memory_instruction, policy_instruction
from run_tisa_two_phase_annotation import POLICY_SYSTEM, phase1_visible, repaired_call, save_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--source-trajectory", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--cached-memory-response", help="Revalidate an existing response instead of calling MEMORY_UPDATE")
    parser.add_argument("--execute", action="store_true", help="Explicitly enable the two logical API calls")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    source = json.loads(Path(args.source_trajectory).read_text(encoding="utf-8"))
    if source["topic"] != config["topic"] or source["dataset"] != config["dataset"]:
        raise ValueError("source trajectory does not match configured topic/dataset")
    step = next(s for s in source["steps"] if s["action"] == "SEARCH" and s["observation"].get("documents"))
    documents = step["observation"]["documents"]
    query = step["model_output"]["query"]
    keywords = load_keywords(Path(config["data"]) / config["topic"])
    state = {"topic": config["topic"], "dataset": config["dataset"], "keywords": keywords,
             "timeline_events": [], "exploration_memory": initial_memory(keywords)}
    visible = phase1_visible(state, ["MEMORY_UPDATE"])
    visible["tool_observation"] = {"query": query, "retrieved_documents": documents}
    folder = Path(args.output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / "report.json").exists() and not (folder / "pre_repair_report.json").exists():
        save_json(folder / "pre_repair_report.json", json.loads((folder / "report.json").read_text(encoding="utf-8")))
    save_json(folder / "memory_request.json", memory_instruction(visible))
    report = {"status": "prepared", "source_trajectory": args.source_trajectory,
              "source_step": step["step_id"], "keywords": keywords,
              "document_count": len(documents), "uses_gold": False,
              "limitations": ["Historical retrieval seeds this smoke test; it is not a new full trajectory.",
                              "Quote membership checks do not establish semantic correctness of dates or summaries."]}
    if not args.execute:
        save_json(folder / "report.json", report)
        return
    load_env_file(args.env_file)
    client = CachedLLMClient(DeepSeekClient(model=config["model"], max_retries=4, retry_backoff_seconds=5.0), folder / "api_cache")
    try:
        if args.cached_memory_response:
            cached_path = Path(args.cached_memory_response).resolve()
            if cached_path.parent != (folder / "api_cache").resolve():
                raise ValueError("cached memory response must be inside this run's api_cache")
            cached = json.loads(cached_path.read_text(encoding="utf-8"))
            output = validate_memory_output(parse_json_object(cached["response"]["text"]), state["exploration_memory"], documents)
            audits = [{"revalidated_cached_response": cached_path.name, "new_api_call": False}]
            report["manually_reused_memory_response"] = cached_path.name
        else:
            output, audits = repaired_call(client, POLICY_SYSTEM, memory_instruction(visible),
                                          lambda p: validate_memory_output(p, state["exploration_memory"], documents), config)
        report["memory_output"] = output
        report["memory_audits"] = audits
        state["exploration_memory"] = apply_memory_output(state["exploration_memory"], output, query, "DISCOVER", documents)
        report["memory_after"] = state["exploration_memory"]
        save_json(folder / "report.json", report)
        policy_visible = phase1_visible(state, ["SEARCH"])
        request = policy_instruction(policy_visible)
        save_json(folder / "policy_request.json", request)
        policy, audits = repaired_call(client, POLICY_SYSTEM, request,
                                      lambda p: validate_exploration_policy(p, policy_visible), config)
        report["next_policy"] = policy
        report["policy_audits"] = audits
        # Local retrieval only; these newly returned snippets are NOT sent to the API.
        results = search(config["index"], [policy["query"]], config["top_k"], f"{config['dataset']} {config['topic']}")
        report["next_retrieval"] = [{"id": r["id"], "publication_date": str(r.get("timestamp", ""))[:10]} for r in results]
        previous_ids = {d["id"] for d in documents}
        report["new_document_count"] = len({r["id"] for r in results} - previous_ids)
        report["status"] = "ok"
    except InsufficientBalanceError as error:
        report.update(status="stopped_insufficient_balance", error=str(error))
    except (LLMError, ValueError, KeyError, TypeError) as error:
        report.update(status="stopped_error", error=str(error))
    finally:
        report["api_cache"] = client.statistics()
        save_json(folder / "report.json", report)
    print(json.dumps({"status": report["status"], "next_policy": report.get("next_policy"),
                      "api_cache": report["api_cache"]}, ensure_ascii=False), flush=True)
    if report["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
