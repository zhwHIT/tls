from __future__ import annotations

import gzip
import json
import re
import sqlite3
from pathlib import Path

from .provenance import find_snapshot


TOKEN = re.compile(r"[A-Za-z0-9]+")


def _fts_query(query: str) -> str:
    tokens = TOKEN.findall(query.lower())
    if not tokens:
        raise ValueError("Query contains no searchable alphanumeric tokens")
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
                            str(article.get("title", "")),
                            str(article.get("text", "")),
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
    with sqlite3.connect(Path(index)) as connection:
        rows = connection.execute("SELECT key, value FROM metadata ORDER BY key").fetchall()
    return {key: json.loads(value) for key, value in rows}


def search_single(index: str | Path, query: str, topic: str, limit: int = 20) -> list[dict]:
    if limit < 1:
        raise ValueError("limit must be positive")
    sql = """
        SELECT doc_id, title, snippet(documents, 3, '', '', ' … ', 32), timestamp,
               bm25(documents, 0.0, 0.0, 2.0, 1.0, 0.0) AS rank
        FROM documents
        WHERE documents MATCH ? AND topic = ?
        ORDER BY rank, timestamp, doc_id
        LIMIT ?
    """
    with sqlite3.connect(Path(index)) as connection:
        rows = connection.execute(sql, (_fts_query(query), topic, limit)).fetchall()
    return [
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


def search(
    index: str | Path, query_list: list[str], n_max_doc: int, search_engine: str
) -> list[dict]:
    """CHRONOS-style adapter for search_engine='<dataset> <topic>'."""
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
    lists = [search_single(index, query, topic, max(n_max_doc, 50)) for query in query_list]
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
