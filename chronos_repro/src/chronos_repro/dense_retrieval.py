from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np

from .temporal_filter import apply_date_filter, in_publication_range, validate_date_filter


DENSE_SCHEMA = "chronos-repro.dense-index.v1"
DENSE_COLLECTION_SCHEMA = "chronos-repro.dense-collection.v1"


class Encoder(Protocol):
    @property
    def dimension(self) -> int: ...

    def encode(self, texts: Sequence[str], batch_size: int) -> np.ndarray: ...


class SentenceTransformerEncoder:
    """Lazy SentenceTransformers adapter used by index and query paths."""

    def __init__(
        self,
        model: str | Path,
        device: str | None = None,
        *,
        backend: str = "torch",
        onnx_file: str | None = None,
    ):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - depends on optional package
            raise RuntimeError(
                "Dense retrieval requires sentence-transformers; install chronos-repro[retrieval]"
            ) from exc
        self.model_path = str(model)
        if backend not in {"torch", "onnx"}:
            raise ValueError("backend must be torch or onnx")
        model_kwargs = {"file_name": onnx_file} if onnx_file else None
        self.model = SentenceTransformer(
            self.model_path,
            device=device if backend == "torch" else None,
            backend=backend,
            model_kwargs=model_kwargs,
        )
        self.backend = backend
        self.onnx_file = onnx_file

    @property
    def dimension(self) -> int:
        value = self.model.get_sentence_embedding_dimension()
        if value is None:
            raise RuntimeError("The embedding model did not report an output dimension")
        return int(value)

    def encode(self, texts: Sequence[str], batch_size: int) -> np.ndarray:
        vectors = self.model.encode(
            list(texts),
            batch_size=batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return np.asarray(vectors, dtype=np.float32)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _content_hash(title: str, body: str) -> str:
    normalized = (_normalize_text(title) + "\n" + _normalize_text(body)).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def chunk_document(
    title: str,
    body: str,
    max_chars: int = 600,
    overlap_chars: int = 100,
) -> list[str]:
    """Create bounded, overlapping semantic passages with the title in every chunk."""
    if max_chars < 200:
        raise ValueError("max_chars must be at least 200")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars must satisfy 0 <= overlap_chars < max_chars")
    title = _normalize_text(title)
    body = _normalize_text(body)
    prefix = f"{title}. " if title else ""
    body_limit = max(80, max_chars - len(prefix))
    if not body:
        return [title] if title else []
    if len(body) <= body_limit:
        return [(prefix + body).strip()]
    step = max(1, body_limit - overlap_chars)
    chunks: list[str] = []
    start = 0
    while start < len(body):
        end = min(len(body), start + body_limit)
        if end < len(body):
            boundary = max(
                body.rfind(". ", start, end),
                body.rfind("。", start, end),
                body.rfind("! ", start, end),
                body.rfind("? ", start, end),
            )
            if boundary > start + body_limit // 2:
                end = boundary + 1
        chunks.append((prefix + body[start:end]).strip())
        if end >= len(body):
            break
        start = max(start + 1, end - overlap_chars)
    return chunks


def stratified_document_chunk(
    title: str,
    body: str,
    max_chars: int = 600,
    segments: int = 3,
) -> list[str]:
    """Represent a full article using bounded passages sampled across its span."""
    title = _normalize_text(title)
    body = _normalize_text(body)
    prefix = f"{title}. " if title else ""
    available = max_chars - len(prefix)
    if available < 80:
        return [title[:max_chars]] if title else []
    if not body:
        return [title] if title else []
    if len(body) <= available:
        return [(prefix + body).strip()]
    separator = " ... "
    count = max(1, min(segments, available // 80))
    width = max(40, (available - len(separator) * (count - 1)) // count)
    last_start = max(0, len(body) - width)
    starts = (
        [round(index * last_start / (count - 1)) for index in range(count)]
        if count > 1
        else [0]
    )
    sampled = separator.join(body[start : start + width].strip() for start in starts)
    return [(prefix + sampled).strip()[:max_chars]]


def select_document_chunks(
    title: str,
    body: str,
    *,
    max_chars: int,
    overlap_chars: int,
    max_chunks_per_document: int | None,
) -> list[str]:
    if max_chunks_per_document is None:
        return chunk_document(title, body, max_chars, overlap_chars)
    if max_chunks_per_document < 1:
        raise ValueError("max_chunks_per_document must be positive")
    if max_chunks_per_document == 1:
        return stratified_document_chunk(title, body, max_chars)
    chunks = chunk_document(title, body, max_chars, overlap_chars)
    if len(chunks) <= max_chunks_per_document:
        return chunks
    indexes = [
        round(index * (len(chunks) - 1) / (max_chunks_per_document - 1))
        for index in range(max_chunks_per_document)
    ]
    return [chunks[index] for index in indexes]


def read_dense_metadata(index_dir: str | Path) -> dict:
    path = Path(index_dir) / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") not in {DENSE_SCHEMA, DENSE_COLLECTION_SCHEMA}:
        raise ValueError(f"Unsupported dense index schema in {path}")
    return payload


def _write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _sqlite_topics(index: Path) -> list[str]:
    with sqlite3.connect(index) as connection:
        rows = connection.execute(
            "SELECT DISTINCT topic FROM documents ORDER BY topic"
        ).fetchall()
    return [str(row[0]) for row in rows]


def _iter_sqlite_documents(index: Path):
    with sqlite3.connect(index) as connection:
        cursor = connection.execute(
            "SELECT topic, doc_id, title, text, timestamp FROM documents "
            "ORDER BY topic, doc_id"
        )
        for topic, doc_id, title, body, timestamp in cursor:
            normalized_title = str(title or "")
            if normalized_title in {"None", "null"}:
                normalized_title = ""
            yield {
                "topic": str(topic),
                "doc_id": str(doc_id),
                "title": normalized_title,
                "body": str(body or ""),
                "timestamp": str(timestamp or "")[:10],
            }


def build_dense_index(
    bm25_index: str | Path,
    output_dir: str | Path,
    model: str | Path,
    *,
    batch_size: int = 32,
    max_chars: int = 600,
    overlap_chars: int = 100,
    storage_dtype: str = "float32",
    device: str | None = None,
    encoder: Encoder | None = None,
    topics: set[str] | None = None,
    max_chunks_per_document: int | None = None,
    backend: str = "torch",
    onnx_file: str | None = None,
) -> dict:
    """Build a chunk-level dense index without loading all vectors into RAM."""
    bm25_index = Path(bm25_index).resolve()
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite dense index: {output_dir}")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if storage_dtype not in {"float16", "float32"}:
        raise ValueError("storage_dtype must be float16 or float32")
    if not bm25_index.is_file():
        raise FileNotFoundError(bm25_index)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    chunks_path = temp_dir / "chunks.jsonl"
    chunk_count = 0
    topic_counts: dict[str, int] = defaultdict(int)
    try:
        with chunks_path.open("w", encoding="utf-8", newline="\n") as handle:
            for document in _iter_sqlite_documents(bm25_index):
                if topics is not None and document["topic"] not in topics:
                    continue
                digest = _content_hash(document["title"], document["body"])
                chunks = select_document_chunks(
                    document["title"],
                    document["body"],
                    max_chars=max_chars,
                    overlap_chars=overlap_chars,
                    max_chunks_per_document=max_chunks_per_document,
                )
                for chunk_number, text in enumerate(chunks):
                    row = {
                        "row": chunk_count,
                        "topic": document["topic"],
                        "doc_id": document["doc_id"],
                        "title": document["title"],
                        "timestamp": document["timestamp"],
                        "chunk": chunk_number,
                        "content_hash": digest,
                        "text": text,
                    }
                    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                    chunk_count += 1
                    topic_counts[document["topic"]] += 1
        if chunk_count == 0:
            raise ValueError("The BM25 index contains no encodable documents")

        active_encoder = encoder or SentenceTransformerEncoder(
            model, device=device, backend=backend, onnx_file=onnx_file
        )
        vectors = np.lib.format.open_memmap(
            temp_dir / "vectors.npy",
            mode="w+",
            dtype=np.dtype(storage_dtype),
            shape=(chunk_count, active_encoder.dimension),
        )
        texts: list[str] = []
        rows: list[int] = []

        def flush() -> None:
            if not texts:
                return
            encoded = active_encoder.encode(texts, batch_size=batch_size)
            if encoded.shape != (len(texts), active_encoder.dimension):
                raise ValueError(f"Unexpected encoder output shape: {encoded.shape}")
            vectors[rows[0] : rows[-1] + 1] = encoded.astype(storage_dtype)
            texts.clear()
            rows.clear()

        with chunks_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                texts.append(row["text"])
                rows.append(int(row["row"]))
                if len(texts) >= batch_size:
                    flush()
        flush()
        vectors.flush()
        # Windows refuses to rename a directory while a memmap owns a file handle.
        memory_map = getattr(vectors, "_mmap", None)
        if memory_map is not None:
            memory_map.close()
        del vectors

        model_manifest_path = Path(model) / "MODEL_MANIFEST.json"
        model_manifest = (
            json.loads(model_manifest_path.read_text(encoding="utf-8"))
            if model_manifest_path.is_file()
            else {"model": str(model)}
        )
        manifest = {
            "schema": DENSE_SCHEMA,
            "bm25_index": str(bm25_index),
            "model": model_manifest,
            "dimension": active_encoder.dimension,
            "dtype": storage_dtype,
            "chunk_count": chunk_count,
            "topic_chunk_counts": dict(sorted(topic_counts.items())),
            "topic_filter": sorted(topics) if topics is not None else None,
            "chunking": {
                "max_chars": max_chars,
                "overlap_chars": overlap_chars,
                "max_chunks_per_document": max_chunks_per_document,
                "selection": (
                    "sliding"
                    if max_chunks_per_document is None
                    else (
                        "stratified"
                        if max_chunks_per_document == 1
                        else "sliding_evenly_capped"
                    )
                ),
                "stratified_segments": 3 if max_chunks_per_document == 1 else None,
            },
            "encoder_backend": backend,
            "onnx_file": onnx_file,
        }
        (temp_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temp_dir.replace(output_dir)
        return {**manifest, "index": str(output_dir)}
    except BaseException:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def build_dense_collection(
    bm25_index: str | Path,
    output_dir: str | Path,
    model: str | Path,
    *,
    batch_size: int = 256,
    max_chars: int = 600,
    overlap_chars: int = 100,
    max_chunks_per_document: int | None = 1,
    storage_dtype: str = "float16",
    device: str | None = None,
    backend: str = "onnx",
    onnx_file: str | None = "onnx/model_avx2.onnx",
    topics: set[str] | None = None,
    encoder: Encoder | None = None,
) -> dict:
    """Build resumable topic shards while loading the encoder only once."""
    bm25_index = Path(bm25_index).resolve()
    output_dir = Path(output_dir).resolve()
    if not bm25_index.is_file():
        raise FileNotFoundError(bm25_index)
    available_topics = _sqlite_topics(bm25_index)
    selected_topics = (
        [topic for topic in available_topics if topic in topics]
        if topics is not None
        else available_topics
    )
    unknown = sorted((topics or set()) - set(available_topics))
    if unknown:
        raise ValueError(f"Unknown topics in BM25 index: {unknown}")
    if not selected_topics:
        raise ValueError("No topics selected for dense collection")

    output_dir.mkdir(parents=True, exist_ok=True)
    shards_dir = output_dir / "shards"
    shards_dir.mkdir(exist_ok=True)
    active_encoder = encoder or SentenceTransformerEncoder(
        model, device=device, backend=backend, onnx_file=onnx_file
    )
    model_manifest_path = Path(model) / "MODEL_MANIFEST.json"
    model_manifest = (
        json.loads(model_manifest_path.read_text(encoding="utf-8"))
        if model_manifest_path.is_file()
        else {"model": str(model)}
    )
    shard_paths: dict[str, str] = {}
    topic_counts: dict[str, int] = {}
    skipped: list[str] = []

    def collection_payload(complete: bool) -> dict:
        return {
            "schema": DENSE_COLLECTION_SCHEMA,
            "complete": complete,
            "bm25_index": str(bm25_index),
            "model": model_manifest,
            "dimension": active_encoder.dimension,
            "dtype": storage_dtype,
            "encoder_backend": backend,
            "onnx_file": onnx_file,
            "chunking": {
                "max_chars": max_chars,
                "overlap_chars": overlap_chars,
                "max_chunks_per_document": max_chunks_per_document,
                "selection": (
                    "sliding"
                    if max_chunks_per_document is None
                    else (
                        "stratified"
                        if max_chunks_per_document == 1
                        else "sliding_evenly_capped"
                    )
                ),
                "stratified_segments": 3 if max_chunks_per_document == 1 else None,
            },
            "requested_topics": selected_topics,
            "shards": dict(sorted(shard_paths.items())),
            "topic_chunk_counts": dict(sorted(topic_counts.items())),
            "chunk_count": sum(topic_counts.values()),
        }

    for number, topic in enumerate(selected_topics, start=1):
        shard = shards_dir / topic
        if (shard / "manifest.json").is_file():
            metadata = read_dense_metadata(shard)
            expected_chunking = collection_payload(False)["chunking"]
            if (
                metadata.get("bm25_index") != str(bm25_index)
                or metadata.get("encoder_backend", "torch") != backend
                or metadata.get("onnx_file") != onnx_file
                or metadata.get("chunking") != expected_chunking
            ):
                raise ValueError(f"Existing shard configuration mismatch: {shard}")
            skipped.append(topic)
        else:
            print(f"[dense {number}/{len(selected_topics)}] building {topic}", flush=True)
            metadata = build_dense_index(
                bm25_index,
                shard,
                model,
                batch_size=batch_size,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
                storage_dtype=storage_dtype,
                device=device,
                encoder=active_encoder,
                topics={topic},
                max_chunks_per_document=max_chunks_per_document,
                backend=backend,
                onnx_file=onnx_file,
            )
        shard_paths[topic] = str(shard.relative_to(output_dir))
        topic_counts[topic] = int(metadata["chunk_count"])
        _write_json_atomic(
            output_dir / "manifest.json",
            collection_payload(number == len(selected_topics)),
        )
        print(
            f"[dense {number}/{len(selected_topics)}] ready {topic}: "
            f"{topic_counts[topic]} vectors",
            flush=True,
        )
    payload = collection_payload(True)
    _write_json_atomic(output_dir / "manifest.json", payload)
    return {**payload, "index": str(output_dir), "skipped_topics": skipped}


_INDEX_CACHE: dict[str, tuple[dict, np.ndarray, list[dict], dict[str, list[int]]]] = {}
_ENCODER_CACHE: dict[str, SentenceTransformerEncoder] = {}


def _load_dense_index(index_dir: Path):
    key = str(index_dir.resolve())
    if key in _INDEX_CACHE:
        return _INDEX_CACHE[key]
    manifest = read_dense_metadata(index_dir)
    vectors = np.load(index_dir / "vectors.npy", mmap_mode="r")
    rows: list[dict] = []
    topic_rows: dict[str, list[int]] = defaultdict(list)
    with (index_dir / "chunks.jsonl").open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            position = len(rows)
            if int(row["row"]) != position:
                raise ValueError("Dense chunk metadata is not contiguous")
            rows.append(row)
            topic_rows[row["topic"]].append(position)
    if vectors.shape != (len(rows), int(manifest["dimension"])):
        raise ValueError("Dense vector and metadata shapes disagree")
    value = (manifest, vectors, rows, dict(topic_rows))
    _INDEX_CACHE[key] = value
    return value


def _model_from_manifest(manifest: dict) -> str:
    model = manifest.get("model", {})
    local_path = model.get("local_path")
    if local_path and Path(local_path).exists():
        return str(local_path)
    return str(model.get("repo_id") or model.get("model") or "")


def search_dense(
    index_dir: str | Path,
    query: str,
    topic: str,
    limit: int = 50,
    *,
    batch_size: int = 32,
    device: str | None = None,
    encoder: Encoder | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    date_filter_mode: str = "none",
    date_soft_penalty: float = 0.5,
) -> list[dict]:
    if limit < 1:
        raise ValueError("limit must be positive")
    validate_date_filter(date_from, date_to, date_filter_mode, date_soft_penalty)
    index_dir = Path(index_dir).resolve()
    root_metadata = read_dense_metadata(index_dir)
    if root_metadata.get("schema") == DENSE_COLLECTION_SCHEMA:
        shard_value = root_metadata.get("shards", {}).get(topic)
        if not shard_value:
            raise ValueError(f"Unknown topic {topic!r} in dense collection")
        return search_dense(
            index_dir / shard_value,
            query,
            topic,
            limit,
            batch_size=batch_size,
            device=device,
            encoder=encoder,
            date_from=date_from,
            date_to=date_to,
            date_filter_mode=date_filter_mode,
            date_soft_penalty=date_soft_penalty,
        )
    manifest, vectors, rows, topic_rows = _load_dense_index(index_dir)
    positions = topic_rows.get(topic)
    if not positions:
        raise ValueError(f"Unknown topic {topic!r} in dense index")
    if date_filter_mode == "hard":
        positions = [
            position
            for position in positions
            if in_publication_range(rows[position].get("timestamp"), date_from, date_to)
        ]
        if not positions:
            return []
    if encoder is None:
        model = _model_from_manifest(manifest)
        if not model:
            raise ValueError("Dense manifest has no usable embedding model")
        backend = str(manifest.get("encoder_backend", "torch"))
        onnx_file = manifest.get("onnx_file")
        cache_key = f"{model}|{device or 'auto'}|{backend}|{onnx_file or ''}"
        if cache_key not in _ENCODER_CACHE:
            _ENCODER_CACHE[cache_key] = SentenceTransformerEncoder(
                model,
                device=device,
                backend=backend,
                onnx_file=onnx_file,
            )
        encoder = _ENCODER_CACHE[cache_key]
    query_vector = encoder.encode([query], batch_size=batch_size)[0].astype(np.float32)
    query_norm = float(np.linalg.norm(query_vector))
    if query_norm == 0:
        raise ValueError("The query encoded to a zero vector")
    query_vector /= query_norm

    position_array = np.asarray(positions, dtype=np.int64)
    topic_vectors = np.asarray(vectors[position_array], dtype=np.float32)
    scores = topic_vectors @ query_vector
    candidate_limit = limit * 4 if date_filter_mode == "soft" else limit
    take = min(len(scores), max(candidate_limit * 8, candidate_limit))
    candidates = np.argpartition(-scores, take - 1)[:take]
    candidates = candidates[np.argsort(-scores[candidates], kind="stable")]

    best_by_doc: dict[str, tuple[float, dict]] = {}
    for local_position in candidates:
        row = rows[int(position_array[int(local_position)])]
        score = float(scores[int(local_position)])
        previous = best_by_doc.get(row["doc_id"])
        if previous is None or score > previous[0]:
            best_by_doc[row["doc_id"]] = (score, row)
    ranked = sorted(
        best_by_doc.values(), key=lambda item: (-item[0], item[1]["doc_id"])
    )[:candidate_limit]
    items = [
        {
            "id": row["doc_id"],
            "title": row["title"],
            "snippet": row["text"],
            "url": "",
            "timestamp": row["timestamp"],
            "score": score,
            "topic": row["topic"],
            "content_hash": row["content_hash"],
            "chunk": row["chunk"],
        }
        for score, row in ranked
    ]
    return apply_date_filter(
        items,
        limit,
        date_from=date_from,
        date_to=date_to,
        mode=date_filter_mode,
        soft_penalty=date_soft_penalty,
    )
