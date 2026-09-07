from __future__ import annotations

import argparse
import json
from pathlib import Path

from chronos_repro.snapshot import sha256
from chronos_repro.splitting import REQUIRED_SPLITS, row_topic, split_rows, validate_topic_splits


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def validate_dpo(rows: list[dict]) -> None:
    for row in rows:
        chosen = row["chosen"][0]["content"].strip().casefold()
        rejected = row["rejected"][0]["content"].strip().casefold()
        metadata = row["metadata"]
        if chosen == rejected:
            raise ValueError("DPO chosen and rejected must differ")
        if metadata["chosen_metrics"]["reward"] <= metadata["rejected_metrics"]["reward"]:
            raise ValueError("DPO chosen reward must be greater than rejected reward")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    config_path = Path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assignment = validate_topic_splits(config["splits"])
    source_hashes: dict[str, str] = {}
    output_hashes: dict[str, dict] = {}
    counts = {split_name: {} for split_name in REQUIRED_SPLITS}

    for kind, filename in config["input_files"].items():
        source = input_dir / filename
        rows = read_jsonl(source)
        if kind == "dpo":
            validate_dpo(rows)
        partitions = split_rows(rows, assignment)
        source_hashes[filename] = sha256(source)
        for split_name in REQUIRED_SPLITS:
            split_rows_for_file = partitions[split_name]
            expected_topics = set(config["splits"][split_name])
            actual_topics = {row_topic(row) for row in split_rows_for_file}
            if not actual_topics <= expected_topics:
                raise AssertionError("A topic crossed split boundaries")
            target = output_dir / split_name / filename
            write_jsonl(target, split_rows_for_file)
            relative = target.relative_to(output_dir).as_posix()
            output_hashes[relative] = {
                "sha256": sha256(target),
                "rows": len(split_rows_for_file),
                "topics": sorted(actual_topics),
            }
            counts[split_name][kind] = len(split_rows_for_file)

    split_sets = {
        name: set(config["splits"][name])
        for name in REQUIRED_SPLITS
    }
    if any(
        split_sets[left] & split_sets[right]
        for index, left in enumerate(REQUIRED_SPLITS)
        for right in REQUIRED_SPLITS[index + 1:]
    ):
        raise AssertionError("Topic leakage detected between splits")

    manifest = {
        "schema_version": 1,
        "dataset": "crisis",
        "split_unit": "topic",
        "splits": config["splits"],
        "counts": counts,
        "source_manifest": config.get("source_manifest"),
        "source_manifest_sha256": sha256(input_dir / config["source_manifest"]),
        "source_files_sha256": source_hashes,
        "outputs": output_hashes,
        "checks": {
            "topic_overlap": False,
            "all_rows_assigned": True,
            "dpo_reward_order_valid": True,
        },
        "limitations": [
            "This is a pipeline-validation split, not a statistically sufficient benchmark.",
            "Only two train topics and one topic each for dev and test are available.",
            "Test labels must remain excluded from optimization and checkpoint selection.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "crisis_search_agent_split_manifest_v1.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
