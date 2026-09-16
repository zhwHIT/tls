from pathlib import Path


def test_excludes_forced_or_unfinished_stops_without_mutating_raw_rows(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from filter_rollout_training_rows import curate
    rows = [{"step_id": str(i), "metadata": {}} for i in range(4)]
    trace = {"steps": [
        {"step_id": "0", "action": "STOP", "observation": {"forced": True}},
        {"step_id": "1", "action": "STOP", "observation": {"open_gap_count": 2}},
        {"step_id": "2", "action": "SEARCH", "observation": {}},
        {"step_id": "3", "action": "MEMORY_UPDATE", "observation": {"validation_rejections": [{"kind": "keyword"}]}},
    ]}
    kept, excluded = curate(rows, trace)
    assert [r["step_id"] for r in kept] == ["2", "3"]
    assert len(excluded) == 2
    assert kept[1]["metadata"]["validation_filtered"]
    assert all(r["metadata"]["semantic_review_required"] for r in kept)
    assert rows[3]["metadata"] == {}
