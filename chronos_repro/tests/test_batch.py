import json

from chronos_repro.batch import evaluate_directory


def test_batch_date_only(tmp_path):
    data = tmp_path / "data"
    predictions = tmp_path / "predictions"
    predictions.mkdir()
    for topic in ("a", "b"):
        directory = data / topic
        directory.mkdir(parents=True)
        (directory / "timelines.jsonl").write_text(
            '[["2020-01-01", ["event"]]]\n', encoding="utf-8"
        )
        (predictions / f"{topic}.json").write_text(
            json.dumps([["2020-01-01", ["event"]]]), encoding="utf-8"
        )
    result = evaluate_directory(data, predictions, date_only=True)
    assert result["evaluated_topics"] == 2
    assert result["macro_topic_mean"]["date_score"]["f_score"] == 1.0
    assert result["topics"][0]["prediction_sha256"]
