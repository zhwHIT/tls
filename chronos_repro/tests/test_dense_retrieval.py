import gzip
import json

import numpy as np

from chronos_repro.dense_retrieval import (
    build_dense_collection,
    build_dense_index,
    chunk_document,
    read_dense_metadata,
    search_dense,
    stratified_document_chunk,
)
from chronos_repro.retrieval import (
    bm25_dense_quota_fusion,
    build_bm25_index,
    create_hybrid_manifest,
    read_index_metadata,
    reciprocal_rank_fusion,
    search,
)


class SemanticStubEncoder:
    dimension = 3

    def encode(self, texts, batch_size):
        vectors = []
        for text in texts:
            lowered = text.lower()
            if "停火" in text or "ceasefire" in lowered:
                vector = [1.0, 0.0, 0.0]
            elif "football" in lowered or "足球" in text:
                vector = [0.0, 1.0, 0.0]
            else:
                vector = [0.0, 0.0, 1.0]
            vectors.append(vector)
        return np.asarray(vectors, dtype=np.float32)


def _indexes(tmp_path):
    root = tmp_path / "data"
    topic = root / "egypt"
    topic.mkdir(parents=True)
    records = [
        {
            "id": "ceasefire",
            "title": "Ceasefire agreement",
            "text": "The government and opposition signed a ceasefire agreement.",
            "time": "2012-01-10",
        },
        {
            "id": "football",
            "title": "Football result",
            "text": "A football team won the championship.",
            "time": "2012-01-11",
        },
    ]
    with gzip.open(topic / "articles.preprocessed.jsonl.gz", "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    bm25 = tmp_path / "bm25.sqlite3"
    dense = tmp_path / "dense"
    build_bm25_index(root, bm25)
    build_dense_index(bm25, dense, "stub", encoder=SemanticStubEncoder())
    return bm25, dense


def test_chunk_document_is_bounded_and_overlapping():
    chunks = chunk_document("Title", "A" * 1400, max_chars=600, overlap_chars=100)
    assert len(chunks) >= 3
    assert all(len(chunk) <= 600 for chunk in chunks)
    assert all(chunk.startswith("Title.") for chunk in chunks)


def test_stratified_chunk_samples_beginning_middle_and_end():
    body = "BEGIN" + "a" * 1000 + "MIDDLE" + "b" * 1000 + "ENDING"
    chunk = stratified_document_chunk("Title", body, max_chars=600)[0]
    assert chunk.startswith("Title.")
    assert "BEGIN" in chunk
    assert "MIDDLE" in chunk
    assert chunk.endswith("ENDING")
    assert len(chunk) <= 600


def test_dense_index_supports_cross_language_query(tmp_path):
    _, dense = _indexes(tmp_path)
    metadata = read_dense_metadata(dense)
    assert metadata["dimension"] == 3
    results = search_dense(
        dense, "政府宣布停火", "egypt", 2, encoder=SemanticStubEncoder()
    )
    assert results[0]["id"] == "ceasefire"
    assert results[0]["score"] > results[1]["score"]


def test_weighted_rrf_preserves_modality_evidence():
    lexical = [{"id": "a", "score": 9.0, "timestamp": "2020-01-01"}]
    semantic = [
        {"id": "b", "score": 0.9, "timestamp": "2020-01-02"},
        {"id": "a", "score": 0.8, "timestamp": "2020-01-01"},
    ]
    results = reciprocal_rank_fusion(
        [("bm25:0", 1.0, lexical), ("dense:0", 1.0, semantic)]
    )
    assert results[0]["id"] == "a"
    assert set(results[0]["retrieval"]["signals"]) == {"bm25", "dense"}


def test_quota_fusion_preserves_lexical_prefix_and_adds_dense_tail():
    lexical = [
        {"id": "a", "score": 3.0},
        {"id": "b", "score": 2.0},
        {"id": "c", "score": 1.0},
    ]
    semantic = [
        {"id": "a", "score": 0.9},
        {"id": "d", "score": 0.8},
        {"id": "e", "score": 0.7},
    ]
    results = bm25_dense_quota_fusion(lexical, semantic, 3, 1 / 3)
    assert [row["id"] for row in results] == ["a", "b", "d"]
    assert set(results[0]["retrieval"]["signals"]) == {"bm25", "dense"}
    assert results[-1]["retrieval"]["fusion"] == "bm25_dense_quota"


def test_hybrid_manifest_is_backward_compatible_with_search(tmp_path, monkeypatch):
    bm25, dense = _indexes(tmp_path)
    manifest = tmp_path / "hybrid.json"
    create_hybrid_manifest(bm25, dense, manifest, temporal_diversity=0)

    import chronos_repro.dense_retrieval as dense_module

    original = dense_module.search_dense
    monkeypatch.setattr(
        dense_module,
        "search_dense",
        lambda index, query, topic, limit, **kwargs: original(
            index, query, topic, limit, encoder=SemanticStubEncoder(), **kwargs
        ),
    )
    results = search(manifest, ["政府宣布停火"], 2, "crisis egypt")
    assert results[0]["id"] == "ceasefire"
    assert read_index_metadata(manifest)["schema"] == "chronos-repro.hybrid-index.v1"


def test_quota_hybrid_manifest_uses_dense_when_bm25_is_empty(tmp_path, monkeypatch):
    bm25, dense = _indexes(tmp_path)
    manifest = tmp_path / "quota.json"
    payload = create_hybrid_manifest(
        bm25,
        dense,
        manifest,
        fusion_method="bm25_dense_quota",
        dense_quota=0.3,
        temporal_diversity=0,
    )
    assert payload["fusion"]["method"] == "bm25_dense_quota"

    import chronos_repro.dense_retrieval as dense_module

    original = dense_module.search_dense
    monkeypatch.setattr(
        dense_module,
        "search_dense",
        lambda index, query, topic, limit, **kwargs: original(
            index, query, topic, limit, encoder=SemanticStubEncoder(), **kwargs
        ),
    )
    results = search(manifest, ["政府宣布停火"], 2, "crisis egypt")
    assert results[0]["id"] == "ceasefire"
    assert results[0]["retrieval"]["fusion"] == "bm25_dense_quota"


def test_dense_collection_routes_topics_and_resumes(tmp_path):
    root = tmp_path / "data"
    for topic_name, record in {
        "egypt": {
            "id": "a",
            "title": "Ceasefire",
            "text": "A ceasefire agreement was signed.",
            "time": "2012-01-01",
        },
        "sports": {
            "id": "b",
            "title": "Football",
            "text": "A football team won.",
            "time": "2012-01-02",
        },
    }.items():
        topic = root / topic_name
        topic.mkdir(parents=True)
        with gzip.open(
            topic / "articles.preprocessed.jsonl.gz", "wt", encoding="utf-8"
        ) as handle:
            handle.write(json.dumps(record) + "\n")
    bm25 = tmp_path / "bm25.sqlite3"
    collection = tmp_path / "collection"
    build_bm25_index(root, bm25)
    report = build_dense_collection(
        bm25,
        collection,
        "stub",
        backend="torch",
        onnx_file=None,
        encoder=SemanticStubEncoder(),
    )
    assert report["complete"] is True
    assert set(report["shards"]) == {"egypt", "sports"}
    resumed = build_dense_collection(
        bm25,
        collection,
        "stub",
        backend="torch",
        onnx_file=None,
        encoder=SemanticStubEncoder(),
    )
    assert set(resumed["skipped_topics"]) == {"egypt", "sports"}
    results = search_dense(
        collection, "政府宣布停火", "egypt", 1, encoder=SemanticStubEncoder()
    )
    assert results[0]["id"] == "a"


def test_dense_and_hybrid_hard_date_range(tmp_path, monkeypatch):
    bm25, dense = _indexes(tmp_path)
    dense_results = search_dense(
        dense,
        "政府宣布停火",
        "egypt",
        2,
        encoder=SemanticStubEncoder(),
        date_from="2012-01-11",
        date_to="2012-01-11",
        date_filter_mode="hard",
    )
    assert [item["id"] for item in dense_results] == ["football"]

    manifest = tmp_path / "hybrid.json"
    create_hybrid_manifest(bm25, dense, manifest, temporal_diversity=0)
    import chronos_repro.dense_retrieval as dense_module

    original = dense_module.search_dense
    monkeypatch.setattr(
        dense_module,
        "search_dense",
        lambda index, query, topic, limit, **kwargs: original(
            index, query, topic, limit, encoder=SemanticStubEncoder(), **kwargs
        ),
    )
    hybrid_results = search(
        manifest,
        ["政府宣布停火"],
        2,
        "crisis egypt",
        date_from="2012-01-11",
        date_to="2012-01-11",
        date_filter_mode="hard",
    )
    assert hybrid_results
    assert all(item["timestamp"][:10] == "2012-01-11" for item in hybrid_results)
