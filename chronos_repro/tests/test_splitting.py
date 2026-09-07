import pytest

from chronos_repro.splitting import row_topic, split_rows, validate_topic_splits


def test_topic_splits_are_disjoint_and_rows_are_grouped():
    assignment = validate_topic_splits({
        "train": ["egypt", "libya"],
        "dev": ["syria"],
        "test": ["yemen"],
    })
    rows = [
        {"topic": "egypt"},
        {"metadata": {"topic": "syria"}},
        {"metadata": {"topic": "yemen"}},
    ]
    grouped = split_rows(rows, assignment)
    assert [row_topic(row) for row in grouped["train"]] == ["egypt"]
    assert [row_topic(row) for row in grouped["dev"]] == ["syria"]
    assert [row_topic(row) for row in grouped["test"]] == ["yemen"]


def test_topic_overlap_is_rejected():
    with pytest.raises(ValueError, match="appears in both"):
        validate_topic_splits({
            "train": ["egypt"],
            "dev": ["egypt"],
            "test": ["yemen"],
        })


def test_unassigned_topic_is_rejected():
    assignment = validate_topic_splits({
        "train": ["egypt"],
        "dev": ["syria"],
        "test": ["yemen"],
    })
    with pytest.raises(ValueError, match="no split assignment"):
        split_rows([{"topic": "libya"}], assignment)
