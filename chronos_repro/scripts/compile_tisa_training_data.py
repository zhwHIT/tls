from __future__ import annotations

import argparse
import json
from pathlib import Path

from chronos_repro.annotation_boundary import load_train_annotation_pairs, read_jsonl
from chronos_repro.snapshot import sha256
from chronos_repro.tisa_data import (
    ACTIONS,
    TOOLS,
    tool_call,
    tool_observation,
    validate_evidence_ids,
    validate_preference_scores,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def preference(
    trajectory_id: str,
    decision: str,
    prompt: list[dict],
    chosen_name: str,
    chosen_args: dict,
    rejected_name: str,
    rejected_args: dict,
    score: dict,
) -> dict:
    return {
        "schema_version": 2,
        "trajectory_id": trajectory_id,
        "decision": decision,
        "prompt": prompt,
        "chosen": [tool_call(f"pref-{decision.lower()}-chosen", chosen_name, chosen_args)],
        "rejected": [tool_call(f"pref-{decision.lower()}-rejected", rejected_name, rejected_args)],
        "metadata": {
            "label_source": "llm_teacher_program_validated",
            "chosen_score": float(score["chosen"]),
            "rejected_score": float(score["rejected"]),
            "margin": float(score["chosen"]) - float(score["rejected"]),
        },
    }


def compile_one(policy: dict, private: dict, label: dict, margin: float) -> tuple[dict, list[dict], dict, list[dict]]:
    task_id = policy["task_id"]
    if label.get("task_id") != task_id or label.get("split") != "train":
        raise ValueError(f"Teacher label identity/split mismatch: {task_id}")
    query = label["query_label"]
    decision = label["decision_label"]
    chosen_docs = label["retrieval"]["chosen"]
    rejected_docs = label["retrieval"]["rejected"]
    verify = decision["verify_chosen"]
    evidence_ids = [str(item) for item in verify["evidence_ids"]]
    validate_evidence_ids(evidence_ids, chosen_docs)
    if str(verify["event"]["time"]) not in set(private["private_target"]["accepted_dates"]):
        raise ValueError(f"Teacher date outside accepted Gold dates: {task_id}")
    validate_preference_scores(decision["preference_scores"], margin)

    search_args = {
        "query": query["chosen_query"],
        "dataset": policy["dataset"],
        "topic": policy["topic"],
        "top_k": len(chosen_docs),
    }
    verify_args = {"event": verify["event"], "evidence_ids": evidence_ids}
    merge_args = {
        "event": {**verify["event"], "evidence_ids": evidence_ids},
        "operation": decision["merge_chosen"]["operation"],
    }
    stop_args = {
        "reason": decision["stop_chosen"]["reason"],
        "confidence": decision["stop_chosen"]["confidence"],
    }
    rejected_search_args = {**search_args, "query": query["rejected_query"], "top_k": len(rejected_docs)}
    rejected_verify_args = {
        "event": decision["verify_rejected"].get("event", {}),
        "evidence_ids": decision["verify_rejected"].get("evidence_ids", []),
    }
    rejected_merge_args = {
        "event": verify["event"],
        "operation": decision["merge_rejected"]["operation"],
    }
    rejected_stop_args = {
        "query": decision["stop_rejected"]["query"],
        "dataset": policy["dataset"],
        "topic": policy["topic"],
        "top_k": len(chosen_docs),
    }

    system = {
        "role": "system",
        "content": "Use SEARCH, VERIFY, MERGE, and STOP to close timeline gaps with cited evidence.",
    }
    user = {"role": "user", "content": json.dumps(policy["policy_state"], ensure_ascii=False, sort_keys=True)}
    messages = [system, user]
    search_prompt = list(messages)
    search_message = tool_call("call-search", "SEARCH", search_args)
    search_message["content"] = f"<reflection>{query['reflection']}</reflection>"
    messages.append(search_message)
    search_observation = {"documents": chosen_docs, "queries_left": max(0, policy["policy_state"]["budget"]["queries_left"] - 1)}
    messages.append(tool_observation("call-search", search_observation))
    verify_prompt = list(messages)
    verify_message = tool_call("call-verify", "VERIFY", verify_args)
    verify_message["content"] = f"<reflection>{decision['reflection']}</reflection>"
    messages.append(verify_message)
    verify_observation = {"supported": True, "valid_evidence_ids": evidence_ids, "confidence": verify["confidence"]}
    messages.append(tool_observation("call-verify", verify_observation))
    merge_prompt = list(messages)
    messages.append(tool_call("call-merge", "MERGE", merge_args))
    merge_observation = {"merged_event_id": private["private_target"]["event_id"], "closed_gap_ids": [policy["policy_state"]["gaps"][0]["gap_id"]], "remaining_high_priority_gaps": 0}
    messages.append(tool_observation("call-merge", merge_observation))
    stop_prompt = list(messages)
    messages.append(tool_call("call-stop", "STOP", stop_args))
    messages.append(tool_observation("call-stop", {"terminal": True}))

    trajectory_id = f"{task_id}:teacher-v2"
    sft = {
        "schema_version": 2,
        "trajectory_id": trajectory_id,
        "tools": TOOLS,
        "messages": messages,
        "metadata": {
            "dataset": policy["dataset"],
            "topic": policy["topic"],
            "split": "train",
            "gap_type": policy["policy_state"]["gaps"][0]["type"],
            "label_source": "llm_teacher_program_validated",
            "required_actions": list(ACTIONS),
        },
    }
    scores = decision["preference_scores"]
    dpo = [
        preference(trajectory_id, "SEARCH", search_prompt, "SEARCH", search_args, "SEARCH", rejected_search_args, scores["search"]),
        preference(trajectory_id, "VERIFY", verify_prompt, "VERIFY", verify_args, "VERIFY", rejected_verify_args, scores["verify"]),
        preference(trajectory_id, "MERGE", merge_prompt, "MERGE", merge_args, "MERGE", rejected_merge_args, scores["merge"]),
        preference(trajectory_id, "STOP", stop_prompt, "STOP", stop_args, "SEARCH", rejected_stop_args, scores["stop"]),
    ]
    steps = [
        {"valid_actions": ["SEARCH"], "action": {"type": "SEARCH", "arguments": search_args}, "observation": search_observation, "reward": float(scores["search"]["chosen"])},
        {"valid_actions": ["SEARCH", "VERIFY", "STOP"], "action": {"type": "VERIFY", "arguments": verify_args}, "observation": verify_observation, "reward": float(scores["verify"]["chosen"])},
        {"valid_actions": ["SEARCH", "VERIFY", "MERGE"], "action": {"type": "MERGE", "arguments": merge_args}, "observation": merge_observation, "reward": float(scores["merge"]["chosen"])},
        {"valid_actions": ["SEARCH", "VERIFY", "STOP"], "action": {"type": "STOP", "arguments": stop_args}, "observation": {"terminal": True}, "reward": float(scores["stop"]["chosen"])},
    ]
    episode = {
        "schema_version": 2,
        "trajectory_id": trajectory_id,
        "initial_state": policy["policy_state"],
        "steps": steps,
        "episode_reward": sum(step["reward"] for step in steps),
        "terminal": True,
    }
    curriculum = [
        {"trajectory_id": trajectory_id, "decision": "VERIFY", "capability": "temporal_date_order", "sampling_weight": 0.4},
        {"trajectory_id": trajectory_id, "decision": "SEARCH", "capability": "gap_query_tool", "sampling_weight": 0.4},
        {"trajectory_id": trajectory_id, "decision": "MERGE", "capability": "merge_evidence_stop", "sampling_weight": 0.1},
        {"trajectory_id": trajectory_id, "decision": "STOP", "capability": "merge_evidence_stop", "sampling_weight": 0.1},
    ]
    return sft, dpo, episode, curriculum


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks-root", required=True)
    parser.add_argument("--teacher-labels", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--preference-margin", type=float, default=0.2)
    args = parser.parse_args()
    root = Path(args.tasks_root).resolve()
    pairs = load_train_annotation_pairs(
        root / "train" / "policy_tasks.jsonl", root / "train" / "private_targets.jsonl"
    )
    pair_by_id = {policy["task_id"]: (policy, private) for policy, private in pairs}
    labels = read_jsonl(Path(args.teacher_labels).resolve())
    label_by_id = {row["task_id"]: row for row in labels}
    if len(label_by_id) != len(labels):
        raise ValueError("Duplicate teacher task_id")
    unknown = sorted(set(label_by_id) - set(pair_by_id))
    if unknown:
        raise ValueError(f"Teacher labels not present in train Gold tasks: {unknown}")

    sft_rows, dpo_rows, episode_rows, curriculum_rows = [], [], [], []
    for task_id in sorted(label_by_id):
        policy, private = pair_by_id[task_id]
        sft, dpo, episode, curriculum = compile_one(
            policy, private, label_by_id[task_id], args.preference_margin
        )
        sft_rows.append(sft)
        dpo_rows.extend(dpo)
        episode_rows.append(episode)
        curriculum_rows.extend(curriculum)
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "tool_sft": output / "tisa_tool_sft_v2.jsonl",
        "local_dpo": output / "tisa_local_dpo_v2.jsonl",
        "rl_episodes": output / "tisa_rl_episodes_v2.jsonl",
        "curriculum": output / "tisa_curriculum_v2.jsonl",
    }
    rows = {"tool_sft": sft_rows, "local_dpo": dpo_rows, "rl_episodes": episode_rows, "curriculum": curriculum_rows}
    for name, path in paths.items():
        write_jsonl(path, rows[name])
    manifest = {
        "schema_version": 2,
        "scope": "train_only",
        "counts": {name: len(value) for name, value in rows.items()},
        "actions": list(ACTIONS),
        "curriculum_mix": {"temporal_date_order": 0.4, "gap_query_tool": 0.4, "merge_evidence_stop": 0.2},
        "outputs": {name: {"file": path.name, "sha256": sha256(path)} for name, path in paths.items()},
    }
    (output / "tisa_training_v2_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
