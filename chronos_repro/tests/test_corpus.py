import gzip
import json
from pathlib import Path

from chronos_repro.corpus import validate_corpus


def _topic(root: Path, name: str) -> None:
    topic = root / name
    topic.mkdir()
    article = {"id": "1", "time": "2020-01-01", "title": "t", "text": "x", "sentences": []}
    with gzip.open(topic / "articles.preprocessed.jsonl.gz", "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(article) + "\n")
    (topic / "keywords.json").write_text('["x"]', encoding="utf-8")
    (topic / "timelines.jsonl").write_text(
        '[["2020-01-01", ["event"]]]\n', encoding="utf-8"
    )


def test_validate_complete_crisis(tmp_path):
    for name in ("egypt", "libya", "syria", "yemen"):
        _topic(tmp_path, name)
    result = validate_corpus(tmp_path, "crisis")
    assert result["valid"] is True
    assert result["found_topics"] == 4


def test_validate_incomplete_crisis(tmp_path):
    _topic(tmp_path, "egypt")
    result = validate_corpus(tmp_path, "crisis", full=False)
    assert result["valid"] is False
    assert "topic count mismatch" in result["errors"][0]
