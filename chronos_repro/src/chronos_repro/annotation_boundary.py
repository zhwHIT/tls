from __future__ import annotations

import json
from pathlib import Path


FORBIDDEN_POLICY_KEYS = {"private_target", "evaluator_private_target"}


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            rows.append(row)
    return rows


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, dict):
        if FORBIDDEN_POLICY_KEYS & set(value):
            return True
        return any(_contains_forbidden_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def load_train_annotation_pairs(
    policy_path: Path, private_path: Path
) -> list[tuple[dict, dict]]:
    policy_path = policy_path.resolve()
    private_path = private_path.resolve()
    if policy_path.name != "policy_tasks.jsonl" or private_path.name != "private_targets.jsonl":
        raise ValueError("Annotation inputs must use policy_tasks.jsonl and private_targets.jsonl")
    if policy_path.parent != private_path.parent or policy_path.parent.name != "train":
        raise ValueError("Teacher annotation accepts only paired files under one train directory")

    policy_rows = read_jsonl(policy_path)
    private_rows = read_jsonl(private_path)
    policy_by_id: dict[str, dict] = {}
    private_by_id: dict[str, dict] = {}
    for row in policy_rows:
        if row.get("split") != "train":
            raise ValueError(f"Non-train policy row rejected: {row.get('task_id')}")
        if _contains_forbidden_key(row):
            raise ValueError(f"Private Gold leaked into policy row: {row.get('task_id')}")
        task_id = str(row.get("task_id", ""))
        if not task_id or task_id in policy_by_id:
            raise ValueError(f"Missing or duplicate policy task_id: {task_id}")
        policy_by_id[task_id] = row
    for row in private_rows:
        if row.get("split") != "train":
            raise ValueError(f"Non-train private row rejected: {row.get('task_id')}")
        task_id = str(row.get("task_id", ""))
        if not task_id or task_id in private_by_id:
            raise ValueError(f"Missing or duplicate private task_id: {task_id}")
        if not isinstance(row.get("private_target"), dict):
            raise ValueError(f"Missing private_target: {task_id}")
        private_by_id[task_id] = row
    if set(policy_by_id) != set(private_by_id):
        raise ValueError("Policy/private task IDs do not match")

    pairs = []
    for task_id in sorted(policy_by_id):
        policy, private = policy_by_id[task_id], private_by_id[task_id]
        if (policy.get("dataset"), policy.get("topic")) != (
            private.get("dataset"), private.get("topic")
        ):
            raise ValueError(f"Policy/private metadata mismatch: {task_id}")
        pairs.append((policy, private))
    return pairs
