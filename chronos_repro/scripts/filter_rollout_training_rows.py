"""Conservative structural curation of a rollout; not semantic label approval."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def curate(rows, trace):
    steps = {s["step_id"]: s for s in trace["steps"]}
    kept, excluded = [], []
    for row in rows:
        step = steps[row["step_id"]]
        observation = step.get("observation", {})
        if step["action"] == "STOP" and (
            observation.get("forced") or observation.get("open_gap_count", 0) > 0
        ):
            excluded.append({"step_id": step["step_id"], "reason": "forced_stop_or_open_gaps"})
            continue
        # Copy the metadata; preserve all original prompts and labels.
        item = {**row, "metadata": {**row.get("metadata", {}),
                                  "semantic_review_required": True,
                                  "validation_filtered": bool(observation.get("validation_rejections"))}}
        kept.append(item)
    return kept, excluded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    folder = Path(args.run_dir)
    trace = json.loads((folder / "trajectory.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (folder / "sft_v5.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    kept, excluded = curate(rows, trace)
    (folder / "sft_v5_structurally_filtered.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept), encoding="utf-8")
    report = {"raw_rows": len(rows), "retained_rows": len(kept), "excluded": excluded,
              "validation_filtered_rows": sum(r["metadata"]["validation_filtered"] for r in kept),
              "training_ready": False,
              "limitation": "Structural filtering only. No semantic faithfulness or final training-quality approval."}
    (folder / "training_filter_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
