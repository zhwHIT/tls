# CHRONOS / Open-TLS reproducibility layer

Full-run preparation (2026-09-14): 164 offline tests pass; Egypt snapshot/index
preflight and local hybrid retrieval pass. Coverage runs now require `--allow-api`
after approval; `--dry-run` never loads the API key. Full-run request ceilings
persist across restarts. No new API call was made in this preparation step.
See [完整两阶段预检与调用范围](../进度/36_v6完整两阶段预检与调用范围确认.md).

Latest smoke (2026-09-14): 8 approved passages processed, 25 events on 25 dates,
17 cumulative HTTP attempts including retries; no insufficient-balance error.
Gold date recall 22/122 (18.03%); provenance/date-expression audit passes.
152 offline tests pass. Full SEARCH/memory/gap/STOP coverage is not established.
See [授权实测、修复与验收](../进度/35_v6_Egypt授权小规模实测与验收.md).

Coverage-v6 (2026-09-11): query-aware continuous passage access, unread queues,
paginated extraction, post-merge memory, final selection, provenance auditing and
HTTP request ceilings. Config: `configs/tisa_coverage_egypt_v6.json`.
146 offline tests pass; no v6 DeepSeek coverage result is claimed.
See [v6 中文框架与运行说明](../进度/34_v6覆盖导向框架与论文检索配置落地.md).

Previous repair (2026-09-11): literal date evidence for daily events, post-MERGE
exploration gains, guarded unconfirmed deferrals, attempt-balanced gap scheduling,
and exclusion of runner-forced STOP from SFT. The new configuration is
`configs/tisa_two_phase_temporal_v5_exploration_v2.json`. Offline checks: 137 tests;
no new API rollout or coverage claim. See the Chinese repair record:
[日期证据与检索停止及Gap调度修复](../进度/33_日期证据与检索停止及Gap调度修复.md).

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

BM25 remains the reproducible lexical baseline. The production v3 index adds
the frozen multilingual MiniLM encoder, ONNX dynamic INT8 CPU inference, one
stratified beginning/middle/end vector per document, topic-sharded resumable
storage, and weighted reciprocal-rank fusion (RRF). BM25 still indexes the full
text. Raw modality ranks remain in each result for ablation and diagnosis.

```powershell
chronos-repro build-index --data snapshots\crisis\ece08f344cc94933 --dataset crisis `
  --output artifacts\crisis_ece08f344cc94933.sqlite3
chronos-repro search --index artifacts\crisis_ece08f344cc94933.sqlite3 `
  --search-engine "crisis egypt" --query "Mubarak resignation" --top-k 20
```

Build the dense side once, then create a small manifest that binds both indexes.
The collection command atomically publishes one shard per topic and skips valid
completed shards when resumed:

~~~powershell
conda activate tls
chronos-repro build-dense-collection --bm25-index artifacts\crisis_ece08f344cc94933.sqlite3 --model models\paraphrase-multilingual-MiniLM-L12-v2 --output artifacts\crisis_dense_collection_v3 --backend onnx --onnx-file onnx/model_avx2.onnx --storage-dtype float16 --batch-size 256 --max-chunks-per-document 1
chronos-repro create-hybrid-index --bm25-index artifacts\crisis_ece08f344cc94933.sqlite3 --dense-index artifacts\crisis_dense_collection_v3 --output artifacts\crisis_collection_v3_hybrid_index.json
chronos-repro search --index artifacts\crisis_collection_v3_hybrid_index.json --search-engine "crisis egypt" --query "Mubarak resignation" --query "埃及总统辞职" --top-k 20
~~~

Publication-date constraints are available on BM25, dense, hybrid, and cached
trajectory searches:

~~~powershell
chronos-repro search --index artifacts\crisis_collection_v3_hybrid_index.json --search-engine "crisis egypt" --query "Mubarak resigns Egypt" --top-k 20 --date-from 2011-02-01 --date-to 2011-02-28 --date-filter-mode hard
chronos-repro trace-search --index artifacts\crisis_collection_v3_hybrid_index.json --search-engine "crisis egypt" --query "Mubarak resigns Egypt" --date-from 2011-02-01 --date-to 2011-02-28 --date-filter-mode soft --output artifacts\egypt_dated_trace.json
~~~

`none` preserves the historical ranking, `hard` excludes documents before
top-k selection, and `soft` retains outside-range retrospective evidence but
demotes it with a rank penalty. Bounds are inclusive ISO dates. These are
publication-date constraints, not claims about the actual event date.

The compact vector contains bounded samples from the beginning, middle, and end
of a document because this encoder accepts at most 128 tokens. Gold timelines
never enter the runtime retriever; they are used only to score BM25, dense, and
hybrid ablations. Full sliding-passage indexing remains available by setting
`--max-chunks-per-document 0`, but it is an expensive ablation rather than the
default production index.

Validate retrieval quality on the topic-disjoint development split:

~~~powershell
$env:PYTHONPATH = "src"
python scripts\evaluate_retriever_date_proxy.py --config configs\retrieval_date_proxy_dev_v1.json --output artifacts\retrieval_date_proxy_dev_v2.json
python scripts\analyze_retrieval_quota_fusion.py --evaluation artifacts\retrieval_date_proxy_dev_v2.json --config configs\retrieval_date_proxy_dev_v1.json --output artifacts\retrieval_quota_fusion_ablation_v2.json
~~~

The date proxy uses evaluator-only Gold event summaries and publication dates;
it does not measure exhaustive evidence relevance. The independent qrel pipeline
is available in `scripts/evaluate_retriever_qrels.py`; it pools all three runs,
validates every LLM judgment, caches each batch, and stops on insufficient
balance. External corpus snippets must be explicitly authorized before use.

Compare the three publication-date modes against already cached qrels without
an LLM call:

~~~powershell
$env:PYTHONPATH = "src"
python scripts\evaluate_temporal_filter_qrels.py --config configs\retrieval_qrel_eval_expanded_v2.json --candidates artifacts\retrieval_qrel_eval_expanded_v2\candidates.json --qrels artifacts\agent_query_qrel_expansion_v1\qrels.merged.jsonl --window-days 14 --soft-penalty 0.5 --output artifacts\temporal_filter_qrel_ablation_v1.json
~~~

The report exposes unjudged counts because the original pool was not built from
all date modes. New soft/hard candidates must be judged before treating the
comparison as a final effectiveness result.

Evaluate queries already emitted by Search Agent trajectories against the frozen
qrels without making any LLM call:

~~~powershell
$env:PYTHONPATH = "src"
python scripts\evaluate_agent_queries_qrels.py --config configs\agent_query_qrel_eval_v1.json --output artifacts\agent_query_qrel_eval_v1.json
python scripts\prepare_agent_query_qrel_expansion.py --config configs\agent_query_qrel_expansion_v1.json --output artifacts\agent_query_qrel_expansion_v1\candidates.json
python scripts\annotate_agent_query_multievent_qrels.py --config configs\agent_query_multievent_annotation_v1.json --output-dir artifacts\agent_query_qrel_expansion_v1
~~~

The final command is a safe dry run unless `--execute` is explicitly supplied.
It groups each document with all still-unjudged sampled events, reducing the
current expansion from an estimated 55 single-event calls to 11 multi-event
matrix calls. Use `--execute` only after explicit authorization for the exact
document count, text limit, destination, model, and batch count.

## P2 search-agent seed data

Build deterministic transition, SFT, and DPO seed files by replaying candidate
queries against the frozen Crisis index. This step does not call an LLM:

```powershell
$env:PYTHONPATH = "src"
python scripts\build_search_agent_training_data.py `
  --data snapshots\crisis\ece08f344cc94933 `
  --index artifacts\crisis_ece08f344cc94933.sqlite3 `
  --artifacts artifacts `
  --output-dir artifacts\training_data `
  --top-k 20
```

Gold dates label offline preferences but are not included in inference prompts.
The current four-topic files are seed data for format and reward validation, not
a sufficient final training corpus.

Create topic-disjoint pipeline-validation splits:

```powershell
python scripts\split_search_agent_training_data.py `
  --input-dir artifacts\training_data `
  --output-dir artifacts\training_splits `
  --config configs\search_agent_topic_split_v1.json
```

The fixed seed split uses Egypt/Libya for train, Syria for dev, and Yemen for
test. Test labels must not be used for optimization or checkpoint selection.

## P3 Gold-supervised LLM annotation

Prepare multi-reference Gold tasks for Crisis, T17, and Entities without an LLM:

```powershell
$env:PYTHONPATH = "src"
python scripts\prepare_tisa_gold_tasks.py `
  --project-root . `
  --config configs\tisa_gold_supervision_v2.json `
  --output-dir artifacts\tisa_gold_tasks_v2
```

Policy-visible tasks and evaluator-private targets are stored separately. The
teacher entry point accepts only paired files under `train`; dev/test private
targets are rejected. After explicit authorization of the outbound training
fields, run a pilot with `scripts\annotate_tisa_train.py --limit N`, then compile
accepted labels using `scripts\compile_tisa_training_data.py`.

Compiled trajectories use `SEARCH -> VERIFY -> MERGE -> STOP`. Local DPO pairs
compare actions at the same state. Curriculum weights are 40% temporal/date/order,
40% gap-to-query/tool use, and 20% merge/evidence/stop.

## P4 two-phase Memory Controller data

Generate phase-one `empty/head/sparse` skeleton states and phase-two controlled
Gold gaps from the same frozen snapshots:

```powershell
$env:PYTHONPATH = "src"
python scripts\prepare_tisa_memory_tasks.py `
  --project-root . `
  --config configs\tisa_memory_supervision_v3.json `
  --output-dir artifacts\tisa_memory_tasks_v3
```

The trainable Controller chooses `SEARCH(query)`, `SWITCH_PHASE`, or `STOP`
from the current timeline plus phase-specific Memory. Frozen general models handle
`VERIFY` and `MERGE`, with program validation for dates, evidence IDs, and
merge operations. The full runtime trace still contains
`SEARCH -> VERIFY -> MERGE -> ... -> STOP`.

After explicitly authorizing the exact train topic and outbound fields, annotate
a checkpointed pilot:

```powershell
python scripts\annotate_tisa_memory_train.py `
  --env-file .env `
  --tasks-root artifacts\tisa_memory_tasks_v3 `
  --gold-config configs\tisa_memory_supervision_v3.json `
  --annotation-config configs\tisa_memory_annotation_v3.json `
  --output-dir artifacts\tisa_memory_teacher_pilot_v3 `
  --task-id "<train task id>"
```

Compile each accepted controller label into one tool-SFT state, three local DPO
pairs for `QUERY/ACTION/MEMORY`, and one replayable episode:

```powershell
python scripts\compile_tisa_memory_training_data.py `
  --tasks-root artifacts\tisa_memory_tasks_v3 `
  --controller-labels artifacts\tisa_memory_teacher_pilot_v3\controller_labels.accepted.jsonl `
  --output-dir artifacts\tisa_memory_training_pilot_v3
```

## V5 temporal gap Controller

`configs/tisa_two_phase_temporal_v5.json` selects the full hybrid index and
policy-only gap initialization/search/refresh (`phase2_teacher_guidance=false`).
The Controller selects visible event IDs, padding, and none/soft/hard; bounds
must match those visible dates. Invalid outputs enter the repair loop. Search
history retains the window and result IDs; v5 actions are exported to
`sft_v5.jsonl`. Legacy v4 configs remain available. Offline integration tests
use a real BM25 index and model fixtures. The first live Egypt v5 rollout is now
available under artifacts/tisa_two_phase_temporal_egypt_v5: 25 steps, 19 events,
four soft date actions, and six identical local retrieval replays. Two gaps remain
open; the pilot stopped at its cycle limit and does not demonstrate full coverage.
See ../进度/30_Egypt_v5真实轨迹验证与错误修复.md for results and post-run fixes.
See `../进度/29_GAP_REFINEMENT时间窗口接入与v5协议.md` for the contract and command.

## Document-driven skeleton exploration (v5 revision)

The current v5 config removes the Egypt-specific task description. It loads frozen
topic keywords and uses unrelated fictional few-shots for diversified queries.
Each SEARCH is followed by a real MEMORY_UPDATE call extracting provisional events,
event times, literal citations, and a coarse stage outline before VERIFY/MERGE.
The same visible memory is supplied to tool helpers and inherited by phase two.
Exploration permits autonomous STOP after six searches and coarse readiness checks;
the 16-search safety limit is recorded as forced termination, excluded from SFT.
An authorized historical-snippet smoke check produced a LATER query and seven new
documents, including 2013 publications. This is not a new full-rollout coverage result.
See `../进度/31_第一阶段文档驱动Memory与自主发散检索.md` for details and commands.

The full document-memory pilot is now available in
`artifacts/tisa_two_phase_temporal_egypt_v5_exploration_v1`: 83 steps, 20 searches,
81 skeleton events and 94 final events. All retrievals and 16 memory transitions
replay locally. Date F1 increased from 0.1727 to 0.3226, but precision fell and
seven gaps remain open. Exploration hit its runner limit, not autonomous STOP.
After retries, invalid observation rows/ungrounded keywords are rejected with an
audit trail; raw model responses are preserved. Structural SFT filtering retains
81 rows for further semantic review, not immediate training. Targeted checks also
found unsupported day precision and publication metadata inconsistent with an
updated aggregation page. See `../进度/32_Egypt文档Memory完整试跑与诊断.md`.

## Environment

The working environment is the Conda environment `tls`. On this Windows host it
was cloned from the local base environment after the remote Conda metadata request
stalled, then all pip packages were installed explicitly through the Tsinghua mirror:

```powershell
conda activate tls
python -m pip install pytest tilse==0.2.1 gdown `
  -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m pip install sentence-transformers==5.1.2 `
  -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m pip install -e . --no-deps `
  -i https://pypi.tuna.tsinghua.edu.cn/simple
```

Run commands without shell activation with `conda run -n tls <command>`.

### LLM error handling

`DeepSeekClient` makes at most three attempts: one initial request plus two
retries. Recoverable cases include connection failures, timeouts, malformed or
empty responses, HTTP 408/409/425/429, and HTTP 5xx. Retries use exponential
backoff of 1 and 2 seconds. Insufficient balance always stops immediately.
Missing credentials and deterministic HTTP 4xx request or authentication errors
are judged non-recoverable and also stop immediately. Successful audit records
include the actual number of attempts.

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
