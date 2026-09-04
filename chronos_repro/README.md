# CHRONOS / Open-TLS reproducibility layer

This directory provides safe dataset loading, CHRONOS-compatible evaluation, and
content-addressed snapshots for closed-domain corpora. The official upstream is
checked out at `../chronos_upstream`, pinned to commit
`4dadc9707c9a4f55476ac28259510fecc0d5c8a9` at the time of this reproduction.

## P0 validation and batch evaluation

```powershell
chronos-repro validate-corpus --data snapshots\crisis\ece08f344cc94933 --dataset crisis
chronos-repro evaluate-batch --data DATA --predictions PREDICTIONS --rouge-backend reimpl
```

Batch files must be named `<topic_id>.json`. Evaluation outputs include the
upstream commit, snapshot identity (when the data path is inside a snapshot),
prediction SHA-256, metric backend, runtime platform, and elapsed time. `freeze`
now performs full corpus validation by default. `reimpl` is for portable
development; paper-comparable scores require `original` Perl ROUGE on Linux/WSL.

## P1 closed-domain retrieval

The Crisis baseline uses dependency-free SQLite FTS5/BM25 and binds index
metadata to the frozen snapshot:

```powershell
chronos-repro build-index --data snapshots\crisis\ece08f344cc94933 --dataset crisis `
  --output artifacts\crisis_ece08f344cc94933.sqlite3
chronos-repro search --index artifacts\crisis_ece08f344cc94933.sqlite3 `
  --search-engine "crisis egypt" --query "Mubarak resignation" --top-k 20
```

## Environment

The working environment is the Conda environment `tls`. On this Windows host it
was cloned from the local base environment after the remote Conda metadata request
stalled, then all pip packages were installed explicitly through the Tsinghua mirror:

```powershell
conda activate tls
python -m pip install pytest tilse==0.2.1 gdown `
  -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m pip install -e . --no-deps `
  -i https://pypi.tuna.tsinghua.edu.cn/simple
```

Run commands without shell activation with `conda run -n tls <command>`.

## 1. Audit Open-TLS

No third-party package is required:

```powershell
$env:PYTHONPATH = "src"
python scripts/export_open_tls_queries.py ..\chronos_upstream\news_keywords.py open_tls_queries.json
python -m chronos_repro.cli audit-open-tls `
  --data ..\chronos_upstream\data\open `
  --queries open_tls_queries.json `
  --output artifacts\open_tls_audit.json
```

## 2. Evaluate predictions

Exact Timeline ROUGE parity uses the same pinned `tilse` release as CHRONOS:

```powershell
python -m pip install -e ".[eval]"
chronos-repro evaluate `
  --data ..\chronos_upstream\data\open `
  --topic Islamic_State_2019.10.27 `
  --prediction path\to\prediction.json `
  --output artifacts\islamic_state_scores.json
```

The default `--rouge-backend original` reproduces the Perl ROUGE backend used
by CHRONOS and requires a working Perl runtime. On native Windows, use
`--rouge-backend reimpl` for Tilse's portable Python approximation; the output
records the selected backend, so approximate scores cannot be mistaken for
paper-comparable scores.

Use `--date-only` when `tilse` is unavailable. Predictions may be CHRONOS saved
objects containing `predict-timeline`, lists of `{start, events}` or
`{start, summary}`, or Open-TLS-style `[timestamp, events]` pairs.

## 3. Freeze a closed-domain corpus

CHRONOS does **not** distribute T17/Crisis article corpora. After obtaining a
licensed/authorized copy, freeze it without modifying the source:

```powershell
chronos-repro freeze `
  --source D:\datasets\crisis `
  --destination .\snapshots `
  --dataset crisis `
  --source-note "Original provider URL, license, download date, preprocessing commit"
```

The snapshot ID is derived from its sorted file inventory. Re-running the command
with identical bytes returns the same directory. Validate before every experiment:

```powershell
chronos-repro verify --snapshot snapshots\crisis\SNAPSHOT_ID
```

The manifest records relative paths, sizes, SHA-256 hashes, source provenance and
UTC creation time. Treat snapshot directories as immutable; changes fail verify.

The validated Crisis snapshot created on 2026-09-04 is:

```text
snapshots/crisis/ece08f344cc94933
```

T17 and Entities downloads may be incomplete when Google Drive throttles public
file access. They must pass topic/file-count and gzip integrity checks before a
snapshot is created; partial directories are deliberately not frozen.

## Reproducibility notes

- Upstream `eval()` loading is replaced by `json.loads()` with safe
  `ast.literal_eval()` fallback for legacy corpora.
- Empty predictions return zero Date-P/R/F instead of raising division by zero.
- Partial year/month dates preserve CHRONOS behavior by mapping to the first day.
- Online search results are not a closed-domain snapshot. Archive retrieved pages
  separately with URL, retrieval time, content hash and query before comparison.
