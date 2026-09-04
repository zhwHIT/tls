import json
from datetime import date

import pytest

from chronos_repro.data import iter_topics, load_prediction, load_timelines, parse_date
from chronos_repro.evaluate import evaluate_dates
from chronos_repro.evaluate import evaluate_tilse


def test_parse_date_supports_chronos_and_partial_dates():
    assert parse_date("2024-02-03T00:00:00") == date(2024, 2, 3)
    assert parse_date("2024-02T00:00:00") == date(2024, 2, 1)
    assert parse_date("2024T00:00:00") == date(2024, 1, 1)
    assert parse_date("2024-02-03 00:00:00") == date(2024, 2, 3)
    with pytest.raises(ValueError):
        parse_date("not-a-date")


def test_load_and_date_evaluation(tmp_path):
    topic = tmp_path / "Topic_A"
    topic.mkdir()
    (topic / "timelines.jsonl").write_text(
        json.dumps([["2024-01-01T00:00:00", ["A"]], ["2024-01-02T00:00:00", ["B"]]]) + "\n",
        encoding="utf-8",
    )
    loaded = list(iter_topics(tmp_path))
    assert len(loaded) == 1
    assert len(loaded[0].timelines[0]) == 2
    prediction_path = tmp_path / "prediction.json"
    prediction_path.write_text(
        json.dumps([{"start": "2024-01-01", "events": ["A"]}, {"start": "2024-01-03", "summary": "C"}]),
        encoding="utf-8",
    )
    score = evaluate_dates(load_prediction(prediction_path), loaded[0].timelines)
    assert score.precision == score.recall == score.f_score == 0.5


def test_empty_prediction_is_zero(tmp_path):
    path = tmp_path / "prediction.json"
    path.write_text("[]", encoding="utf-8")
    score = evaluate_dates(load_prediction(path), ({date(2024, 1, 1): ("A",)},))
    assert score.f_score == 0.0


def test_tilse_reimplementation_gives_one_for_gold():
    gold = {date(2024, 1, 1): ("A significant event happened.",)}
    result = evaluate_tilse(gold, (gold,), rouge_backend="reimpl")
    assert result["date_score"]["f_score"] == 1.0
    assert result["rouge"]["concat"]["rouge_1"]["f_score"] == 1.0
