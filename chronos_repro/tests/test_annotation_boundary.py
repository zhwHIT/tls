import json
from pathlib import Path

import pytest

from chronos_repro.annotation_boundary import load_train_annotation_pairs


def _write(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


def _rows(split: str = "train") -> tuple[dict, dict]:
    policy = {
        "task_id": "crisis:egypt:event:missing_date",
        "dataset": "crisis",
        "topic": "egypt",
        "split": split,
        "policy_state": {"topic": "egypt"},
    }
    private = {
        "task_id": policy["task_id"],
        "dataset": "crisis",
        "topic": "egypt",
        "split": split,
        "private_target": {"canonical_date": "2011-01-25"},
    }
    return policy, private


def test_load_train_annotation_pairs_accepts_only_matched_train(tmp_path):
    policy, private = _rows()
    train = tmp_path / "train"
    _write(train / "policy_tasks.jsonl", policy)
    _write(train / "private_targets.jsonl", private)
    pairs = load_train_annotation_pairs(
        train / "policy_tasks.jsonl", train / "private_targets.jsonl"
    )
    assert pairs[0][0]["task_id"] == policy["task_id"]


def test_load_train_annotation_pairs_rejects_dev_path(tmp_path):
    policy, private = _rows("dev")
    dev = tmp_path / "dev"
    _write(dev / "policy_tasks.jsonl", policy)
    _write(dev / "private_targets.jsonl", private)
    with pytest.raises(ValueError, match="only paired files under one train"):
        load_train_annotation_pairs(dev / "policy_tasks.jsonl", dev / "private_targets.jsonl")


def test_load_train_annotation_pairs_rejects_gold_in_policy(tmp_path):
    policy, private = _rows()
    policy["policy_state"]["private_target"] = private["private_target"]
    train = tmp_path / "train"
    _write(train / "policy_tasks.jsonl", policy)
    _write(train / "private_targets.jsonl", private)
    with pytest.raises(ValueError, match="Private Gold leaked"):
        load_train_annotation_pairs(
            train / "policy_tasks.jsonl", train / "private_targets.jsonl"
        )
