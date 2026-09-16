import gzip
import json

from chronos_repro.retrieval import build_bm25_index
from chronos_repro.trajectory import replay_trajectory, run_trajectory


def test_trajectory_roundtrip(tmp_path):
    root = tmp_path / "data"
    topic = root / "egypt"
    topic.mkdir(parents=True)
    with gzip.open(topic / "articles.preprocessed.jsonl.gz", "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"id": "a", "title": "Mubarak resigns", "text": "Egypt protests", "time": "2011-02-11"}) + "\n")
    index = tmp_path / "index.sqlite3"
    build_bm25_index(root, index)
    trace = run_trajectory(index, "crisis egypt", ["Mubarak", "Mubarak"], top_k=5)
    assert trace["stop_reason"] == "queries_exhausted"
    assert trace["rounds"][0]["updated_state"]["new_document_count"] == 1
    assert trace["rounds"][1]["updated_state"]["new_document_count"] == 0
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(trace), encoding="utf-8")
    assert replay_trajectory(path)["valid"] is True


def test_trajectory_records_and_replays_date_filter(tmp_path):
    root = tmp_path / "data"
    topic = root / "egypt"
    topic.mkdir(parents=True)
    with gzip.open(topic / "articles.preprocessed.jsonl.gz", "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"id": "a", "title": "Mubarak", "text": "resigns", "time": "2011-02-11"}) + "\n")
        handle.write(json.dumps({"id": "b", "title": "Mubarak", "text": "resigns", "time": "2012-02-11"}) + "\n")
    index = tmp_path / "index.sqlite3"
    build_bm25_index(root, index)
    trace = run_trajectory(
        index,
        "crisis egypt",
        ["Mubarak"],
        date_from="2011-01-01",
        date_to="2011-12-31",
        date_filter_mode="hard",
    )
    assert trace["rounds"][0]["observation"]["results"][0]["id"] == "a"
    assert trace["rounds"][0]["action"]["date_filter"]["mode"] == "hard"
    path = tmp_path / "dated-trace.json"
    path.write_text(json.dumps(trace), encoding="utf-8")
    assert replay_trajectory(path)["valid"] is True
