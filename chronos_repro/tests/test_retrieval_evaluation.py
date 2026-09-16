import json

import pytest

from scripts.evaluate_retriever_qrels import (
    _call_history,
    _load_cached_judgments,
    _rewrite_judgments,
    _seed_qrels,
)

from chronos_repro.retrieval_evaluation import (
    aggregate_publication_date_scores,
    aggregate_scores,
    parse_json_object,
    quota_fusion,
    score_ranking,
    score_publication_date_proxy,
    stratified_events,
    validate_judgments,
)


def test_stratified_events_cover_start_middle_end():
    events = [
        {"canonical_date": f"2020-01-{index + 1:02d}", "event_id": str(index)}
        for index in range(9)
    ]
    selected = stratified_events(events, 3)
    assert [row["event_id"] for row in selected] == ["0", "4", "8"]


def test_parse_and_validate_judgments():
    payload = parse_json_object(
        '```json\n{"judgments": ['
        '{"id": "a", "grade": 2, "event_date": "2020-01-01", "reason": "direct"},'
        '{"id": "b", "grade": 0, "event_date": null, "reason": "unrelated"}'
        ']}\n```'
    )
    rows = validate_judgments(payload, ["a", "b"])
    assert [row["grade"] for row in rows] == [2, 0]


def test_validate_judgments_rejects_incomplete_response():
    with pytest.raises(ValueError, match="Missing judgments"):
        validate_judgments(
            {"judgments": [{"id": "a", "grade": 2, "reason": "direct"}]},
            ["a", "b"],
        )


def test_score_and_aggregate_rankings():
    grades = {"a": 2, "b": 1, "c": 2, "d": 0}
    first = score_ranking(["d", "a", "b", "c"], grades, [1, 3, 4])
    assert first["mrr"] == 0.5
    assert first["success@1"] is False
    assert first["recall@3"] == 0.5
    assert first["recall@4"] == 1.0
    assert 0 < first["ndcg@4"] <= 1

    unresolved = score_ranking(["d"], {"d": 0}, [1, 3, 4])
    summary = aggregate_scores([first, unresolved], [1, 3, 4])
    assert summary["events"] == 2
    assert summary["resolved_events"] == 1
    assert summary["success@3"] == 0.5
    assert summary["recall@4"] == 1.0


def test_publication_date_proxy_scores_exact_and_window_hits():
    rows = [
        {"id": "a", "timestamp": "2020-01-03"},
        {"id": "b", "timestamp": "2020-01-01"},
        {"id": "c", "timestamp": "2020-02-01"},
    ]
    score = score_publication_date_proxy(rows, ["2020-01-01"], [1, 2, 3], 2)
    assert score["exact_hit@1"] is False
    assert score["exact_hit@2"] is True
    assert score["exact_mrr"] == 0.5
    assert score["window_2d_hit@1"] is True
    assert score["window_2d_mrr"] == 1.0
    summary = aggregate_publication_date_scores([score], [1, 2, 3], 2)
    assert summary["exact_hit@2"] == 1.0
    assert summary["window_2d_hit@1"] == 1.0


def test_qrel_cache_uses_composite_event_key_and_compacts(tmp_path):
    row = {
        "case_event_id": "entities:David_Bowie:gold-1",
        "dataset": "entities",
        "topic": "David_Bowie",
        "event_id": "gold-1",
        "document_id": "doc-1",
        "grade": 2,
    }
    path = tmp_path / "qrels.jsonl"
    path.write_text(
        json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8"
    )
    cached = _load_cached_judgments(path)
    assert list(cached) == [("entities:David_Bowie:gold-1", "doc-1")]
    _rewrite_judgments(path, list(cached.values()))
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_seed_qrels_filters_to_current_candidate_pool(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    rows = [
        {
            "case_event_id": "t17:mj:gold-1",
            "dataset": "t17",
            "topic": "mj",
            "event_id": "gold-1",
            "document_id": document_id,
            "grade": 2,
        }
        for document_id in ("kept", "excluded")
    ]
    (seed / "qrels.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    candidates = {
        "cases": [
            {
                "dataset": "t17",
                "topic": "mj",
                "sampled_events": [
                    {"event_id": "gold-1", "pool": [{"id": "kept"}]}
                ],
            }
        ]
    }
    target = tmp_path / "target" / "qrels.jsonl"
    target.parent.mkdir()
    meta = _seed_qrels(target, candidates, [str(seed)])
    assert meta == {"existing": 0, "imported": 1, "available": 1}
    assert _load_cached_judgments(target)[("t17:mj:gold-1", "kept")]["grade"] == 2


def test_call_history_aggregates_persisted_usage(tmp_path):
    path = tmp_path / "calls.jsonl"
    path.write_text(
        json.dumps({"usage": {"total_tokens": 10, "prompt_tokens": 4}})
        + "\n"
        + json.dumps({"usage": {"total_tokens": 20, "prompt_tokens": 7}})
        + "\n",
        encoding="utf-8",
    )
    assert _call_history(path) == {
        "records": 2,
        "usage": {"total_tokens": 30, "prompt_tokens": 11},
    }


def test_quota_fusion_keeps_depth_and_backfills_unique_documents():
    assert quota_fusion(["a", "b", "c"], ["a", "d", "e"], 3, 0) == [
        "a", "b", "c"
    ]
    assert quota_fusion(["a", "b", "c"], ["a", "d", "e"], 3, 1) == [
        "a", "b", "d"
    ]
