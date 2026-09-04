import gzip
import json

from chronos_repro.retrieval import build_bm25_index, read_index_metadata, search


def test_build_and_chronos_style_search(tmp_path):
    root = tmp_path / "data"
    topic = root / "egypt"
    topic.mkdir(parents=True)
    records = [
        {"id": "a", "title": "Egypt election", "text": "voters elected a president", "time": "2012-06-24"},
        {"id": "b", "title": "Football", "text": "a match was played", "time": "2012-06-25"},
    ]
    with gzip.open(topic / "articles.preprocessed.jsonl.gz", "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    index = tmp_path / "index.sqlite3"
    report = build_bm25_index(root, index)
    assert report["document_count"] == 2
    assert read_index_metadata(index)["engine"] == "sqlite-fts5-bm25"
    results = search(index, ["president election"], 5, "crisis egypt")
    assert [result["id"] for result in results] == ["a"]
    assert set(("id", "title", "snippet", "url", "timestamp")) <= results[0].keys()
