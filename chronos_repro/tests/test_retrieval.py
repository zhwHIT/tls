import gzip
import json

import pytest

from chronos_repro.retrieval import (
    _fts_query,
    _sample_document_text,
    build_bm25_index,
    read_index_metadata,
    search,
)
from chronos_repro.temporal_filter import apply_date_filter


def test_fts_query_filters_stopwords_deduplicates_and_keeps_content():
    assert _fts_query("The president and the cabinet resigns in Yemen Yemen") == (
        '"president" OR "cabinet" OR "resigns" OR "yemen"'
    )


def test_fts_query_caps_pathologically_long_queries():
    query = " ".join(f"term{index}" for index in range(40))
    assert _fts_query(query).count(" OR ") == 23


def test_long_document_sampling_includes_beginning_middle_and_end():
    text = "A" * 1000 + "MIDDLE" + "B" * 1000 + "ENDING"
    sampled = _sample_document_text(text, 600, segments=4)
    assert sampled.startswith("A")
    assert "MIDDLE" in sampled
    assert sampled.endswith("ENDING")
    assert len(sampled) <= 600


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


def test_null_title_is_indexed_as_empty_string(tmp_path):
    root = tmp_path / "data"
    topic = root / "egypt"
    topic.mkdir(parents=True)
    record = {"id": "a", "title": None, "text": "ceasefire", "time": "2012-01-01"}
    with gzip.open(topic / "articles.preprocessed.jsonl.gz", "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    index = tmp_path / "index.sqlite3"
    build_bm25_index(root, index)
    assert search(index, ["ceasefire"], 1, "crisis egypt")[0]["title"] == ""


def test_bm25_hard_date_range_filters_before_top_k(tmp_path):
    root = tmp_path / "data"
    topic = root / "egypt"
    topic.mkdir(parents=True)
    records = [
        {"id": "old", "title": "Ceasefire", "text": "ceasefire signed", "time": "2012-01-01"},
        {"id": "target", "title": "Ceasefire", "text": "ceasefire signed", "time": "2012-02-01"},
        {"id": "new", "title": "Ceasefire", "text": "ceasefire signed", "time": "2012-03-01"},
    ]
    with gzip.open(topic / "articles.preprocessed.jsonl.gz", "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    index = tmp_path / "index.sqlite3"
    build_bm25_index(root, index)
    results = search(
        index,
        ["ceasefire"],
        1,
        "crisis egypt",
        date_from="2012-02-01",
        date_to="2012-02-28",
        date_filter_mode="hard",
    )
    assert [item["id"] for item in results] == ["target"]
    assert results[0]["retrieval"]["date_filter"]["in_range"] is True


def test_date_filter_rejects_ambiguous_or_reversed_bounds(tmp_path):
    with pytest.raises(ValueError, match="require date_filter_mode"):
        search(tmp_path / "missing", ["x"], 1, "crisis x", date_from="2012-01-01")
    with pytest.raises(ValueError, match="must not be later"):
        search(
            tmp_path / "missing",
            ["x"],
            1,
            "crisis x",
            date_from="2012-02-01",
            date_to="2012-01-01",
            date_filter_mode="hard",
        )


def test_soft_date_filter_prefers_range_without_dropping_outside_evidence():
    items = [
        {"id": "outside-first", "timestamp": "2012-01-01"},
        {"id": "inside", "timestamp": "2012-02-01"},
        {"id": "outside-last", "timestamp": "2012-03-01"},
    ]
    results = apply_date_filter(
        items,
        3,
        date_from="2012-02-01",
        date_to="2012-02-28",
        mode="soft",
        soft_penalty=0.5,
    )
    assert results[0]["id"] == "inside"
    assert {item["id"] for item in results} == {item["id"] for item in items}
    assert results[-1]["retrieval"]["date_filter"]["in_range"] is False
