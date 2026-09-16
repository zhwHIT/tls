from __future__ import annotations

import argparse
import json
from pathlib import Path

from chronos_repro.data import iter_topics
from chronos_repro.gold_supervision import (
    GAP_TYPES,
    align_reference_events,
    build_masked_state,
    select_targets,
    validate_cross_dataset_topic_splits,
)
from chronos_repro.retrieval import read_index_metadata
from chronos_repro.snapshot import sha256
from chronos_repro.splitting import REQUIRED_SPLITS, validate_topic_splits


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    config_path = Path(args.config).resolve()
    output_dir = Path(args.output_dir).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    policy_rows = {name: [] for name in REQUIRED_SPLITS}
    private_rows = {name: [] for name in REQUIRED_SPLITS}
    dataset_manifest = {}
    gap_counter = 0
    all_assignments = {
        dataset: validate_topic_splits(dataset_config["splits"])
        for dataset, dataset_config in config["datasets"].items()
    }
    checked_groups = validate_cross_dataset_topic_splits(
        all_assignments, config["split_policy"]["cross_dataset_groups"]
    )

    for dataset, dataset_config in config["datasets"].items():
        data_root = project_root / dataset_config["data"]
        index = project_root / dataset_config["index"]
        assignment = all_assignments[dataset]
        topics = list(iter_topics(data_root))
        actual_topics = {topic.topic_id for topic in topics}
        configured_topics = set(assignment)
        if actual_topics != configured_topics:
            raise ValueError(
                f"{dataset} split mismatch: missing={sorted(actual_topics - configured_topics)}, "
                f"unknown={sorted(configured_topics - actual_topics)}"
            )
        index_metadata = read_index_metadata(index)
        dataset_counts = {name: 0 for name in REQUIRED_SPLITS}
        aligned_count = 0
        for topic in topics:
            events = align_reference_events(
                topic.topic_id,
                topic.timelines,
                config["date_alignment_window_days"],
                config["summary_similarity_threshold"],
            )
            aligned_count += len(events)
            targets = select_targets(events, config["max_targets_per_topic"])
            split_name = assignment[topic.topic_id]
            for target in targets:
                gap_type = GAP_TYPES[gap_counter % len(GAP_TYPES)]
                gap_counter += 1
                state = build_masked_state(
                    topic.topic_id, events, target, gap_type, config["queries_left"]
                )
                task_id = f"{dataset}:{topic.topic_id}:{target['event_id']}:{gap_type.lower()}"
                policy_rows[split_name].append({
                    "schema_version": 2,
                    "task_id": task_id,
                    "dataset": dataset,
                    "topic": topic.topic_id,
                    "split": split_name,
                    "policy_state": state,
                    "max_steps": 6,
                    "allowed_actions": ["SEARCH", "VERIFY", "MERGE", "STOP"],
                })
                private_rows[split_name].append({
                    "schema_version": 2,
                    "task_id": task_id,
                    "dataset": dataset,
                    "topic": topic.topic_id,
                    "split": split_name,
                    "private_target": target,
                })
                dataset_counts[split_name] += 1
        dataset_manifest[dataset] = {
            "snapshot": index_metadata["snapshot"],
            "index_sha256": sha256(index),
            "topic_count": len(topics),
            "aligned_event_count": aligned_count,
            "task_counts": dataset_counts,
        }

    outputs = {}
    for split_name in REQUIRED_SPLITS:
        policy_path = output_dir / split_name / "policy_tasks.jsonl"
        private_path = output_dir / split_name / "private_targets.jsonl"
        write_jsonl(policy_path, policy_rows[split_name])
        write_jsonl(private_path, private_rows[split_name])
        outputs[split_name] = {
            "policy": {"file": policy_path.relative_to(output_dir).as_posix(),
                       "rows": len(policy_rows[split_name]), "sha256": sha256(policy_path)},
            "private": {"file": private_path.relative_to(output_dir).as_posix(),
                        "rows": len(private_rows[split_name]), "sha256": sha256(private_path)},
            "topics": sorted({row["topic"] for row in policy_rows[split_name]}),
        }
    train_ids = {row["task_id"] for row in policy_rows["train"]}
    dev_ids = {row["task_id"] for row in policy_rows["dev"]}
    test_ids = {row["task_id"] for row in policy_rows["test"]}
    if train_ids & dev_ids or train_ids & test_ids or dev_ids & test_ids:
        raise AssertionError("Task leakage detected between splits")
    manifest = {
        "schema_version": 2,
        "config": config_path.name,
        "config_sha256": sha256(config_path),
        "datasets": dataset_manifest,
        "outputs": outputs,
        "checks": {
            "topic_config_complete": True,
            "task_overlap": False,
            "normalized_topic_overlap": False,
            "cross_dataset_groups_checked": len(checked_groups),
            "policy_private_row_counts_match": all(
                len(policy_rows[name]) == len(private_rows[name]) for name in REQUIRED_SPLITS
            ),
            "test_private_targets_separate": True,
        },
        "teacher_boundary": {
            "allowed": "train/policy_tasks.jsonl + train/private_targets.jsonl",
            "forbidden": ["dev/private_targets.jsonl", "test/private_targets.jsonl"],
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "tisa_gold_tasks_v2_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
