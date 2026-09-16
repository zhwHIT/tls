from chronos_repro.agent_query_evaluation import (
    evaluate_query_sequence,
    extract_queries,
    missing_event_ids_by_document,
    query_document_ids,
)


def test_extract_queries_from_supported_sources():
    assert extract_queries(
        {"queries": [" first ", "", "second"]},
        {"type": "top_level_queries"},
    ) == ["first", "second"]
    assert extract_queries(
        {"baselines": {"chronos": {"queries": ["one", "two"]}}},
        {"type": "baseline_queries", "name": "chronos"},
    ) == ["one", "two"]
    assert extract_queries(
        {
            "steps": [
                {"action": "VERIFY"},
                {"action": "SEARCH", "arguments": {"query": "alpha beta"}},
                {
                    "action": {"type": "SEARCH", "query": "gamma delta"},
                    "arguments": {},
                },
            ]
        },
        {"type": "trajectory_steps"},
    ) == ["alpha beta", "gamma delta"]


def test_query_sequence_reports_pair_and_event_coverage():
    qrels = [
        {"event_id": "e1", "document_id": "d1", "grade": 2},
        {"event_id": "e1", "document_id": "d2", "grade": 2},
        {"event_id": "e2", "document_id": "d3", "grade": 2},
        {"event_id": "e2", "document_id": "d4", "grade": 1},
        {"event_id": "e2", "document_id": "d5", "grade": 0},
    ]
    result = evaluate_query_sequence(
        ["query one", "query two"],
        [
            [{"id": "unknown"}, {"id": "d1"}],
            [{"id": "d3"}, {"id": "d4"}, {"id": "d1"}],
        ],
        qrels,
    )
    assert result["covered_events"] == 2
    assert result["event_coverage"] == 1.0
    assert result["recovered_direct_pairs"] == 2
    assert result["direct_pair_recall"] == 2 / 3
    assert result["recovered_contextual_or_direct_pairs"] == 3
    assert result["contextual_or_direct_pair_recall"] == 3 / 4
    assert result["unique_retrieved_documents"] == 4
    assert result["unjudged_retrieved_documents"] == 1
    assert result["rounds"][0]["cumulative_event_coverage"] == 0.5
    assert result["rounds"][1]["newly_covered_events"] == ["e2"]


def test_query_document_delta_uses_only_missing_event_pairs():
    case = {
        "runs": [
            {
                "status": "complete",
                "evaluation": {
                    "rounds": [
                        {"ranked_document_ids": ["d1", "d2"]},
                        {"ranked_document_ids": ["d2", "d3"]},
                    ]
                },
            },
            {"status": "missing_query_rollout"},
        ]
    }
    assert query_document_ids(case) == ["d1", "d2", "d3"]
    missing = missing_event_ids_by_document(
        ["e1", "e2"],
        ["d1", "d2", "d3"],
        [
            {"event_id": "e1", "document_id": "d1"},
            {"event_id": "e2", "document_id": "d1"},
            {"event_id": "e1", "document_id": "d2"},
        ],
    )
    assert missing == {"d2": ["e2"], "d3": ["e1", "e2"]}
