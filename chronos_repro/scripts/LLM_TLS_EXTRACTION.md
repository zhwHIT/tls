# LLM-TLS event extraction adapter

Upstream: https://github.com/nusnlp/LLM-TLS, commit
`13951f669587662434b5f8e8e9c369103ba55a69`. Original source files, their
SHA-256 values, and GPL-3.0 license are preserved in `../../llm_tls_upstream`.
The adapter reuses upstream prompt literals and the original pure time-selector
functions. It adapts preprocessing without importing upstream's full dependency
stack. This is an adapted extraction run, not a strict Llama2 reproduction.

## Scope

All frozen T17 (9 topics), Entities (47), and Crisis (4) articles are processed.
No reference timeline is read or included in API requests. Existing snapshots,
search indexes, historical runs, and VERIFY remain unchanged.

The original default article prefix is 1500 tokens, using the existing DeepSeek
tokenizer. The upstream one-shot example is additional to that prefix. The API
model is the already-configured `deepseek-v4-flash`, temperature 0, thinking
disabled, maximum output 512 tokens. The main event is requested per article;
this does not exhaustively extract all events from full article text.

Adaptations: preserve partial/unknown event dates and modality; do not force an
event date to equal publication time; use exact request deduplication rather
than dropping all title/date duplicates; persist every article's source mapping.
There is no per-event fact verification or additional fact-check API call.
Calendar/format validation is not factual validation. All events carry
`verification_status: not_fact_checked`.

`sentence_with_time` reproduces the upstream FIRST annotated time per sentence
behavior. It is raw sentence text, not a generated summary. The separate
`time_mentions` list preserves ALL annotated tokens, including later dates.
Neither annotation representation establishes event/date entailment.

## Execution (from D:/paper/chronos_repro)

```powershell
D:/miniforge/envs/tls/python.exe -u scripts/extract_llm_tls_events.py prepare
D:/miniforge/envs/tls/python.exe -u scripts/extract_llm_tls_events.py run --sample-per-dataset 3 --workers 3
D:/miniforge/envs/tls/python.exe -u scripts/llm_tls_network_pause.py
D:/miniforge/envs/tls/python.exe scripts/extract_llm_tls_events.py status
D:/miniforge/envs/tls/python.exe scripts/extract_llm_tls_events.py export
```

Output: `artifacts/llm_tls_extraction_v1/`.

- `extraction.sqlite3`: preprocessed articles, source mappings, API jobs, results.
- `responses/<prefix>/<job_id>.json`: durable paid responses, allowing recovery
  after a crash between response receipt and the database result commit.
- `events/<dataset>/<topic>_events.jsonl`: article-mapped generated events;
  regenerated at the end of each run or explicitly with `export`.
- `preparation_report.json`, `progress.json`, `export_report.json`: preparation,
  live progress, and export completeness, respectively.
- `request_ledger.jsonl`: submissions, results and usage; no API keys.
- `run_identity.json`: source/model/tokenizer/prompt/adapter identity.

The outer `date` remains PUBLICATION DATE for upstream compatibility. Event
dates are in `events[].event_date`; their precision is in `date_precision`.
`llm` preserves the raw generated output. Source file/line, original article ID,
article index, source-record hash and text hash allow joining to the existing
article retrieval indexes. Failed/unfinished rows remain explicit; they are
not silently represented as successful empty event lists.

Only identical requests share a paid response; every source article retains a
row and may reference the same generated event IDs. `unique_generated_events`
counts unique request outputs, not semantic event deduplication.

## Stop and recovery

The production supervisor uses `llm_tls_network_pause.py` for inference.
At worker startup, `configure_direct_transport()` installs an explicit
`urllib.request.ProxyHandler({})`. API calls and HEAD probes connect directly,
ignoring environment and Windows system proxy settings in this process; TLS
certificate verification remains enabled. This does not change OS settings.
The policy is recorded in `transport_policy.json`. Restart existing workers
gracefully to apply it; the lower-level raw runner does not install this policy.
A shared gate pauses all workers when a connection failure, transport timeout,
or interrupted response is detected. Already in-flight requests can finish and
be saved. One unauthenticated HEAD probe checks provider reachability every 30
seconds while paused; no generation request is used as a connectivity probe.
Workers automatically continue after connectivity returns. The live network
state is in `network_status.json`, separate from the extraction counters.
HTTP errors such as rate limiting retain the existing bounded retry policy;
balance/authentication errors remain terminal. An interrupted response may have
been processed remotely; a network pause cannot establish its billing outcome.
The raw `extract_llm_tls_events.py run` entry point is a lower-level runner and
does not install the network gate; use the guarded command above for production.

An explicit provider HTTP 400 `Content Exists Risk` rejection is isolated to its
document: the job remains `error: ContentRejectedError`, its request is not
rewritten or retried, and unrelated pending documents continue. Other terminal
errors still stop the batch. `api_errors.jsonl` records the provider status/code,
message, request hash and action; cancellation of other workers no longer
replaces the original root cause. Rejected records never count as successful
extractions. Resume an already-prepared suite with:

```powershell
D:/miniforge/envs/tls/python.exe -u scripts/run_llm_tls_extraction_suite.py --skip-prepare
```

Create the file `artifacts/llm_tls_extraction_v1/STOP` to stop scheduling new
requests. In-flight calls are allowed to finish and be saved. Remove this file
only when continuing is desired. A `runner.lock` prevents duplicate runners;
after an ungraceful termination verify its recorded PID has exited before
removing only that stale lock. Re-run the same command to resume pending jobs.
Requests waiting on network recovery return to pending on a controlled STOP.
For an explicitly authorized re-fetch of content rejections and parse errors,
use `retry_llm_tls_problem_events.py --batch artifacts/llm_tls_extraction_v1/<unique-batch>`
with the suite stopped. It preserves original rows/caches, sends unchanged
requests over the direct network gate, and replaces only parse-clean results.
The batch report records both unsuccessful attempts and incremental returned
token usage; current-result usage is not cumulative historical billing. Reuse
the same batch path after interruption to recover already saved attempts.

The user-authorized `assistant_corrections_20260920` batch replaces all 24 known
problem requests using assistant-authored responses from their stored target
articles. Its manifest is `scripts/llm_tls_assistant_corrections_20260920.json`;
apply/recover with `scripts/apply_llm_tls_assistant_corrections.py`. The batch
archives previous DB rows, caches and reports, and marks new results with
`model`/event `origin` = `conversation_assistant`, zero provider attempts, and
unavailable assistant usage (`usage: {}`). These are not DeepSeek responses.
Current provider-result usage excludes replaced outputs; historical returned
usage remains in the previous reports and suite accounting. All 25 associated
article rows across nine topic files retain mappings and correction reasons.
Known year intervals are kept in event `time_range` and the summary, with null
single `event_date`; insufficient article bodies use `NONE` with a reason.
No external factual verification is claimed. The legacy full exporter omits
top-level correction metadata; reapply this batch after using that exporter to
restore the metadata. Event-level provenance remains part of exported events.
Do not change prompts/model/source/tokenizer or adapter code in place during a
run: identity checks deliberately reject mixed configurations.

Insufficient balance/authentication errors stop scheduling. Transient API
errors have two retries through the existing client; exhausted failures remain
explicit `error` jobs and need diagnosis before a further paid retry. Successful
paid responses are never re-requested just to fix formatting.

The unavoidable crash window after provider completion but before local cache
write cannot guarantee exactly-once billing. Once a response is cached, resume
does not issue another API call for it.
