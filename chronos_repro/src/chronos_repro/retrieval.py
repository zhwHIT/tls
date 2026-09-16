from __future__ import annotations

import gzip
import json
import math
import os
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from .provenance import find_snapshot
from .temporal_filter import (
    apply_date_filter,
    attach_date_filter_metadata,
    validate_date_filter,
)


TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
HYBRID_SCHEMA = "chronos-repro.hybrid-index.v1"
FTS_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by",
    "for", "from", "had", "has", "have", "he", "her", "his", "in",
    "into", "is", "it", "its", "of", "on", "or", "she", "that", "the",
    "their", "they", "this", "to", "was", "were", "will", "with", "who",
}
MAX_FTS_QUERY_TERMS = 24


def _load_hybrid_manifest(index: str | Path) -> tuple[Path, dict] | None:
    path = Path(index).resolve()
    if path.suffix.lower() != ".json":
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != HYBRID_SCHEMA:
        raise ValueError(f"Unsupported hybrid index schema in {path}")
    return path, payload


def _resolve_manifest_path(manifest_path: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (manifest_path.parent / path).resolve()


def _bm25_path(index: str | Path) -> Path:
    hybrid = _load_hybrid_manifest(index)
    if hybrid is None:
        return Path(index).resolve()
    manifest_path, payload = hybrid
    return _resolve_manifest_path(manifest_path, payload["bm25_index"])


def _sample_document_text(text: str, max_chars: int, segments: int = 4) -> str:
    """Sample bounded passages across a long document instead of keeping only its prefix."""
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(normalized) <= max_chars:
        return normalized
    segment_count = max(1, min(segments, max_chars // 200))
    separator = " ... [passage boundary] ... "
    width = (max_chars - len(separator) * (segment_count - 1)) // segment_count
    last_start = max(0, len(normalized) - width)
    starts = [round(index * last_start / (segment_count - 1)) for index in range(segment_count)] if segment_count > 1 else [0]
    passages = [normalized[start:start + width].strip() for start in starts]
    return separator.join(passages)


def _fts_query(query: str) -> str:
    raw_tokens = TOKEN.findall(query.casefold())
    if not raw_tokens:
        raise ValueError("Query contains no searchable alphanumeric tokens")
    tokens = []
    for token in raw_tokens:
        if token in FTS_STOPWORDS or (len(token) == 1 and not token.isdigit()):
            continue
        if token not in tokens:
            tokens.append(token)
        if len(tokens) == MAX_FTS_QUERY_TERMS:
            break
    if not tokens:
        tokens = list(dict.fromkeys(raw_tokens))[:MAX_FTS_QUERY_TERMS]
    return " OR ".join(f'"{token}"' for token in tokens)


def build_bm25_index(data_root: str | Path, output: str | Path) -> dict:
    """Build a deterministic SQLite FTS5 index from a validated closed corpus."""
    data_root = Path(data_root).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing index: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(output)
    counts: dict[str, int] = {}
    try:
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute(
            "CREATE VIRTUAL TABLE documents USING fts5("
            "topic UNINDEXED, doc_id UNINDEXED, title, text, timestamp UNINDEXED, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        for article_path in sorted(data_root.glob("*/articles.preprocessed.jsonl.gz")):
            topic = article_path.parent.name
            count = 0
            with gzip.open(article_path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    article = json.loads(line)
                    connection.execute(
                        "INSERT INTO documents(topic, doc_id, title, text, timestamp) VALUES (?, ?, ?, ?, ?)",
                        (
                            topic,
                            str(article["id"]),
                            str(article.get("title") or ""),
                            str(article.get("text") or ""),
                            str(article.get("time", ""))[:10],
                        ),
                    )
                    count += 1
            counts[topic] = count
        snapshot = find_snapshot(data_root)
        metadata = {
            "schema_version": 1,
            "engine": "sqlite-fts5-bm25",
            "data_root": str(data_root),
            "snapshot": snapshot,
            "topic_counts": counts,
            "document_count": sum(counts.values()),
        }
        for key, value in metadata.items():
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False, sort_keys=True)),
            )
        connection.commit()
        connection.execute("INSERT INTO documents(documents) VALUES('optimize')")
        connection.commit()
        return {**metadata, "index": str(output), "bytes": output.stat().st_size}
    except Exception:
        connection.close()
        output.unlink(missing_ok=True)
        raise
    finally:
        if connection:
            connection.close()


def read_index_metadata(index: str | Path) -> dict:
    hybrid = _load_hybrid_manifest(index)
    if hybrid is not None:
        manifest_path, payload = hybrid
        bm25_index = _resolve_manifest_path(manifest_path, payload["bm25_index"])
        dense_index = _resolve_manifest_path(manifest_path, payload["dense_index"])
        from .dense_retrieval import read_dense_metadata

        bm25_metadata = read_index_metadata(bm25_index)
        dense_metadata = read_dense_metadata(dense_index)
        supported_topics = set(dense_metadata.get("topic_chunk_counts", {}))
        return {
            **payload,
            "index": str(manifest_path),
            "bm25": bm25_metadata,
            "dense": dense_metadata,
            "snapshot": bm25_metadata.get("snapshot"),
            "topic_counts": {
                topic: count
                for topic, count in bm25_metadata.get("topic_counts", {}).items()
                if topic in supported_topics
            },
        }
    with sqlite3.connect(Path(index)) as connection:
        rows = connection.execute("SELECT key, value FROM metadata ORDER BY key").fetchall()
    return {key: json.loads(value) for key, value in rows}


def create_hybrid_manifest(
    bm25_index: str | Path,
    dense_index: str | Path,
    output: str | Path,
    *,
    bm25_weight: float = 1.0,
    dense_weight: float = 1.0,
    rrf_k: int = 60,
    candidate_multiplier: int = 5,
    temporal_diversity: float = 0.05,
    fusion_method: str = "weighted_rrf",
    dense_quota: float = 0.3,
) -> dict:
    """Bind lexical and dense indexes without copying either artifact."""
    bm25_index = Path(bm25_index).resolve()
    dense_index = Path(dense_index).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite hybrid manifest: {output}")
    if not bm25_index.is_file() or not (dense_index / "manifest.json").is_file():
        raise FileNotFoundError("Both BM25 and dense indexes must already exist")
    if min(bm25_weight, dense_weight) < 0 or bm25_weight + dense_weight <= 0:
        raise ValueError("Fusion weights must be non-negative and not both zero")
    if rrf_k < 1 or candidate_multiplier < 1:
        raise ValueError("rrf_k and candidate_multiplier must be positive")
    if not 0 <= temporal_diversity <= 1:
        raise ValueError("temporal_diversity must be between 0 and 1")
    if fusion_method not in {"weighted_rrf", "bm25_dense_quota"}:
        raise ValueError("Unsupported fusion_method")
    if not 0 <= dense_quota <= 1:
        raise ValueError("dense_quota must be between 0 and 1")

    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": HYBRID_SCHEMA,
        "bm25_index": os.path.relpath(bm25_index, output.parent),
        "dense_index": os.path.relpath(dense_index, output.parent),
        "fusion": {
            "method": fusion_method,
            "bm25_weight": bm25_weight,
            "dense_weight": dense_weight,
            "rrf_k": rrf_k,
            "candidate_multiplier": candidate_multiplier,
            "temporal_diversity": temporal_diversity,
            "dense_quota": dense_quota,
        },
    }
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {**payload, "index": str(output)}


def search_single(
    index: str | Path,
    query: str,
    topic: str,
    limit: int = 20,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    date_filter_mode: str = "none",
    date_soft_penalty: float = 0.5,
) -> list[dict]:
    if limit < 1:
        raise ValueError("limit must be positive")
    validate_date_filter(date_from, date_to, date_filter_mode, date_soft_penalty)
    conditions = ["documents MATCH ?", "topic = ?"]
    parameters: list[object] = [_fts_query(query), topic]
    if date_filter_mode == "hard":
        if date_from:
            conditions.append("substr(timestamp, 1, 10) >= ?")
            parameters.append(date_from)
        if date_to:
            conditions.append("substr(timestamp, 1, 10) <= ?")
            parameters.append(date_to)
    candidate_limit = limit * 4 if date_filter_mode == "soft" else limit
    sql = f"""
        SELECT doc_id, title, snippet(documents, 3, '', '', ' … ', 32), timestamp,
               bm25(documents, 0.0, 0.0, 2.0, 1.0, 0.0) AS rank
        FROM documents
        WHERE {" AND ".join(conditions)}
        ORDER BY rank, timestamp, doc_id
        LIMIT ?
    """
    parameters.append(candidate_limit)
    with sqlite3.connect(Path(index)) as connection:
        rows = connection.execute(sql, parameters).fetchall()
    items = [
        {
            "id": str(doc_id),
            "title": title,
            "snippet": snippet,
            "url": "",
            "timestamp": timestamp,
            "score": -float(rank),
            "topic": topic,
        }
        for doc_id, title, snippet, timestamp, rank in rows
    ]
    return apply_date_filter(
        items,
        limit,
        date_from=date_from,
        date_to=date_to,
        mode=date_filter_mode,
        soft_penalty=date_soft_penalty,
    )


def reciprocal_rank_fusion(
    ranked_lists: list[tuple[str, float, list[dict]]],
    *,
    rrf_k: int = 60,
) -> list[dict]:
    """Fuse incomparable lexical and cosine scores through weighted ranks."""
    fused: dict[str, dict] = {}
    for ranking_name, weight, items in ranked_lists:
        modality = ranking_name.split(":", 1)[0]
        for rank, item in enumerate(items, start=1):
            doc_id = str(item["id"])
            if doc_id not in fused:
                fused[doc_id] = {
                    "item": dict(item),
                    "score": 0.0,
                    "signals": defaultdict(list),
                }
            record = fused[doc_id]
            record["score"] += weight / (rrf_k + rank)
            record["signals"][modality].append(
                {
                    "ranking": ranking_name,
                    "rank": rank,
                    "raw_score": float(item.get("score", 0.0)),
                }
            )
            for key in ("content_hash", "chunk"):
                if key in item:
                    record["item"][key] = item[key]

    output = []
    for doc_id, record in fused.items():
        item = record["item"]
        item["score"] = record["score"]
        item["retrieval"] = {
            "fusion": "weighted_rrf",
            "rrf_score": record["score"],
            "signals": {
                name: values for name, values in sorted(record["signals"].items())
            },
        }
        output.append(item)
    return sorted(
        output,
        key=lambda item: (-item["score"], item.get("timestamp", ""), str(item["id"])),
    )


def bm25_dense_quota_fusion(
    lexical: list[dict],
    semantic: list[dict],
    limit: int,
    dense_quota: float = 0.3,
) -> list[dict]:
    """Keep a BM25 prefix and fill a bounded tail with unique dense results."""
    if limit < 1:
        raise ValueError("limit must be positive")
    if not 0 <= dense_quota <= 1:
        raise ValueError("dense_quota must be between 0 and 1")
    dense_slots = min(limit, max(0, round(limit * dense_quota)))
    lexical_slots = limit - dense_slots
    selected: list[dict] = []
    seen: set[str] = set()

    def add(items: list[dict]) -> None:
        for item in items:
            if len(selected) >= limit:
                break
            document_id = str(item["id"])
            if document_id not in seen:
                seen.add(document_id)
                selected.append(item)

    add(lexical[:lexical_slots])
    add(semantic)
    add(lexical[lexical_slots:])
    add(semantic)

    lexical_ranks = {
        str(item["id"]): (rank, float(item["score"]))
        for rank, item in enumerate(lexical, start=1)
    }
    semantic_ranks = {
        str(item["id"]): (rank, float(item["score"]))
        for rank, item in enumerate(semantic, start=1)
    }
    output = []
    for position, source in enumerate(selected, start=1):
        item = dict(source)
        document_id = str(item["id"])
        signals = {}
        if document_id in lexical_ranks:
            rank, score = lexical_ranks[document_id]
            signals["bm25"] = {"rank": rank, "score": score}
        if document_id in semantic_ranks:
            rank, score = semantic_ranks[document_id]
            signals["dense"] = {"rank": rank, "score": score}
        item["score"] = 1.0 / position
        item["retrieval"] = {
            "fusion": "bm25_dense_quota",
            "dense_quota": dense_quota,
            "dense_slots": dense_slots,
            "signals": signals,
        }
        output.append(item)
    return output


def _deduplicate(items: list[dict]) -> list[dict]:
    output, seen = [], set()
    for item in items:
        signature = item.get("content_hash") or f"id:{item['id']}"
        if signature in seen:
            continue
        seen.add(signature)
        output.append(item)
    return output


def temporal_diversify(
    items: list[dict], limit: int, strength: float = 0.05
) -> list[dict]:
    """Apply a mild same-day penalty after retrieval, preserving relevance dominance."""
    if strength <= 0 or len(items) <= 1:
        return items[:limit]
    remaining = list(items)
    selected: list[dict] = []
    date_counts: dict[str, int] = defaultdict(int)
    high = max(float(item["score"]) for item in items)
    low = min(float(item["score"]) for item in items)
    scale = high - low or 1.0
    while remaining and len(selected) < limit:
        def adjusted(item: dict) -> tuple[float, float, str]:
            relevance = (float(item["score"]) - low) / scale
            date = str(item.get("timestamp", ""))[:10]
            penalty = strength * math.log1p(date_counts[date]) if date else 0.0
            return relevance - penalty, float(item["score"]), str(item["id"])

        best = max(remaining, key=adjusted)
        selected_score = adjusted(best)[0]
        remaining.remove(best)
        date = str(best.get("timestamp", ""))[:10]
        if date:
            date_counts[date] += 1
        best["retrieval"]["diversified_score"] = selected_score
        selected.append(best)
    return selected


def _validate_search_target(
    index: str | Path, search_engine: str
) -> tuple[str, str, dict]:
    parts = search_engine.split(maxsplit=1)
    if len(parts) != 2 or parts[0] not in {"crisis", "t17", "entities"}:
        raise ValueError("search_engine must be '<crisis|t17|entities> <topic>'")
    dataset, topic = parts
    metadata = read_index_metadata(index)
    snapshot = metadata.get("snapshot") or {}
    if snapshot.get("dataset") and snapshot["dataset"] != dataset:
        raise ValueError(
            f"Index dataset is {snapshot['dataset']!r}, not requested {dataset!r}"
        )
    if topic not in metadata.get("topic_counts", {}):
        raise ValueError(f"Unknown topic {topic!r} in index")
    return dataset, topic, metadata


def _search_bm25(
    index: str | Path,
    query_list: list[str],
    n_max_doc: int,
    topic: str,
    *,
    date_from: str | None,
    date_to: str | None,
    date_filter_mode: str,
    date_soft_penalty: float,
) -> list[dict]:
    lists = [
        search_single(
            index,
            query,
            topic,
            max(n_max_doc, 50),
            date_from=date_from,
            date_to=date_to,
            date_filter_mode=date_filter_mode,
            date_soft_penalty=date_soft_penalty,
        )
        for query in query_list
    ]
    output, seen = [], set()
    for rank in range(max((len(items) for items in lists), default=0)):
        for items in lists:
            if rank >= len(items):
                continue
            item = items[rank]
            signature = item["id"]
            if signature not in seen:
                seen.add(signature)
                output.append(item)
                if len(output) == n_max_doc:
                    return output
    return output


def _search_hybrid(
    manifest_path: Path,
    payload: dict,
    query_list: list[str],
    n_max_doc: int,
    topic: str,
    *,
    date_from: str | None,
    date_to: str | None,
    date_filter_mode: str,
    date_soft_penalty: float,
) -> list[dict]:
    from .dense_retrieval import search_dense

    fusion = payload["fusion"]
    bm25_index = _resolve_manifest_path(manifest_path, payload["bm25_index"])
    dense_index = _resolve_manifest_path(manifest_path, payload["dense_index"])
    pool = max(50, n_max_doc * int(fusion["candidate_multiplier"]))
    query_count = max(1, len(query_list))
    rankings: list[tuple[str, float, list[dict]]] = []
    lexical_rankings: list[tuple[str, float, list[dict]]] = []
    semantic_rankings: list[tuple[str, float, list[dict]]] = []
    for query_number, query in enumerate(query_list):
        if not query.strip():
            continue
        lexical = search_single(
            bm25_index,
            query,
            topic,
            pool,
            date_from=date_from,
            date_to=date_to,
            date_filter_mode=date_filter_mode,
            date_soft_penalty=date_soft_penalty,
        )
        semantic = search_dense(
            dense_index,
            query,
            topic,
            pool,
            date_from=date_from,
            date_to=date_to,
            date_filter_mode=date_filter_mode,
            date_soft_penalty=date_soft_penalty,
        )
        lexical_rankings.append((f"bm25:{query_number}", 1.0 / query_count, lexical))
        semantic_rankings.append((f"dense:{query_number}", 1.0 / query_count, semantic))
        rankings.append(
            (f"bm25:{query_number}", float(fusion["bm25_weight"]) / query_count, lexical)
        )
        rankings.append(
            (f"dense:{query_number}", float(fusion["dense_weight"]) / query_count, semantic)
        )
    method = fusion.get("method", "weighted_rrf")
    if method == "bm25_dense_quota":
        lexical = reciprocal_rank_fusion(
            lexical_rankings, rrf_k=int(fusion["rrf_k"])
        )
        semantic = reciprocal_rank_fusion(
            semantic_rankings, rrf_k=int(fusion["rrf_k"])
        )
        selected = bm25_dense_quota_fusion(
            lexical,
            semantic,
            n_max_doc,
            float(fusion.get("dense_quota", 0.3)),
        )
        return attach_date_filter_metadata(
            selected,
            date_from=date_from,
            date_to=date_to,
            mode=date_filter_mode,
            soft_penalty=date_soft_penalty,
        )
    if method != "weighted_rrf":
        raise ValueError(f"Unsupported hybrid fusion method: {method}")
    fused = reciprocal_rank_fusion(rankings, rrf_k=int(fusion["rrf_k"]))
    deduplicated = _deduplicate(fused)
    selected = temporal_diversify(
        deduplicated, n_max_doc, float(fusion.get("temporal_diversity", 0.0))
    )
    return attach_date_filter_metadata(
        selected,
        date_from=date_from,
        date_to=date_to,
        mode=date_filter_mode,
        soft_penalty=date_soft_penalty,
    )


def search(
    index: str | Path,
    query_list: list[str],
    n_max_doc: int,
    search_engine: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    date_filter_mode: str = "none",
    date_soft_penalty: float = 0.5,
) -> list[dict]:
    """CHRONOS adapter supporting BM25-only and BM25+dense manifests."""
    if n_max_doc < 1:
        raise ValueError("n_max_doc must be positive")
    validate_date_filter(date_from, date_to, date_filter_mode, date_soft_penalty)
    if not query_list or not any(query.strip() for query in query_list):
        return []
    _, topic, _ = _validate_search_target(index, search_engine)
    hybrid = _load_hybrid_manifest(index)
    if hybrid is None:
        return _search_bm25(
            index,
            query_list,
            n_max_doc,
            topic,
            date_from=date_from,
            date_to=date_to,
            date_filter_mode=date_filter_mode,
            date_soft_penalty=date_soft_penalty,
        )
    return _search_hybrid(
        hybrid[0],
        hybrid[1],
        query_list,
        n_max_doc,
        topic,
        date_from=date_from,
        date_to=date_to,
        date_filter_mode=date_filter_mode,
        date_soft_penalty=date_soft_penalty,
    )


def fetch_documents(
    index: str | Path, topic: str, document_ids: list[str], max_chars: int = 2000
) -> list[dict]:
    """Fetch bounded document text for already retrieved IDs, preserving input order."""
    if max_chars < 100:
        raise ValueError("max_chars must be at least 100")
    ordered_ids = list(dict.fromkeys(map(str, document_ids)))
    if not ordered_ids:
        return []
    placeholders = ",".join("?" for _ in ordered_ids)
    sql = (
        "SELECT doc_id, title, text, timestamp FROM documents "
        f"WHERE topic = ? AND doc_id IN ({placeholders})"
    )
    with sqlite3.connect(_bm25_path(index)) as connection:
        rows = connection.execute(sql, [topic, *ordered_ids]).fetchall()
    by_id = {
        str(doc_id): {
            "id": str(doc_id),
            "title": str(title or ""),
            "text": _sample_document_text(str(body or ""), max_chars),
            "publication_date": str(timestamp or "")[:10],
        }
        for doc_id, title, body, timestamp in rows
    }
    return [by_id[doc_id] for doc_id in ordered_ids if doc_id in by_id]
