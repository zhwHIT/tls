from datetime import date

from chronos_repro.training_data import prompt_messages, rank_candidates, score_candidate


def test_candidate_score_rewards_new_gold_and_novel_dates():
    results = [
        {"id": "seen", "timestamp": "2020-01-01"},
        {"id": "new", "timestamp": "2020-01-10"},
        {"id": "near", "timestamp": "2020-01-21"},
    ]
    score = score_candidate(
        "distinct query",
        results,
        {date(2020, 1, 1)},
        {"seen"},
        {date(2020, 1, 10), date(2020, 1, 20)},
        ["old query"],
    )
    assert score["new_document_count"] == 2
    assert score["new_date_count"] == 2
    assert score["exact_gold_gain"] == 1
    assert score["window_2d_gold_gain"] == 2
    assert score["reward"] > 1200


def test_rank_candidates_is_reward_first_and_deterministic():
    rows = [
        {"query": "z", "metrics": {"reward": 2.0}},
        {"query": "b", "metrics": {"reward": 3.0}},
        {"query": "a", "metrics": {"reward": 3.0}},
    ]
    assert [row["query"] for row in rank_candidates(rows)] == ["a", "b", "z"]


def test_prompt_messages_serializes_state_as_conversational_prompt():
    messages = prompt_messages({"topic": "egypt", "round": 2})
    assert [message["role"] for message in messages] == ["system", "user"]
    assert '"topic": "egypt"' in messages[1]["content"]
