# Article retrieval with pre-extracted event results

The existing `run_tisa_two_phase_annotation.py` entry point dispatches to the
new mode when `preextracted_events.enabled` is true. This mode currently supports
fresh first-phase-only runs. It does not execute the supplemental phase, phase
two, final selection, raw-passage reading, date-evidence validation, or another
event summarization pass.

Article ranking remains the existing BM25+dense weighted RRF implementation,
with the original top-k and publication-date filtering semantics. Returned
article IDs map to the completed extraction JSONL for the same dataset, topic,
and snapshot. All indexed article IDs must match the extraction inventory.
Missing extraction records fail explicitly; there is no fallback to raw text.
The returned allowlist includes document ID, retrieval rank, publication date,
extraction status, and extracted event ID/date/precision/summary/range/origin.
It excludes article text, titles, snippets, original evidence quotes and time
annotations. Articles with `NONE` remain visible as zero-event results.

VERIFY receives only these immutable extracted candidates plus nearby existing
timeline summaries. Its sole outputs are relevance, APPEND/MERGE/IGNORE,
duplicate target ID, and reason. It cannot rewrite a summary or date. MERGE
requires identical date and time-range fields, preserving different dated
occurrences. Exact same-date/summary duplicates are also eliminated in code.
The executor can recover same-batch extracted-ID merge aliases (including
forward references). Strict validation rejects cycles, ignored targets and
differing dates. After model repair is exhausted, an otherwise valid relevant
MERGE with an invalid link becomes APPEND, with an explicit recovery audit.
Both source dates and summaries remain unchanged; no date is promoted and no
target is inferred from free-text reasons. This can retain semantic duplicates.
Rejected and irrelevant decisions remain in `candidate_pool.json`, with their
reasons. Accepted events preserve year/month/unknown precision and time ranges.
Accepted partial/unknown dates do not generate date-completion tasks. The
extraction error rate is explicitly accepted in this mode.

Unusable classifications are isolated in checkpoint `deferred_verification` and
exported as `deferred_verification.json`. They are not marked reviewed or admitted,
and can be retried when retrieved again. SEARCH feedback and evaluation expose
their count separately from unread candidates. Valid decisions in the same page
still commit; a local validation failure does not discard the page.

The SEARCH controller, query history, factual-summary compression, cache, and
request/token limits are reused. Its first-phase prompt now searches for further
developments, responses, implementation, consequences and outcomes of accepted
events. It includes topic-appropriate directions for people, countries/regions,
organizations/companies and event/disaster/outbreak/crisis topics.
First-phase model input and output schemas omit pending leads, lead reasons,
lead counts and target lead IDs. Historical queues are not selected, presented
or updated by this mode. Batch yield feedback is also excluded from model input:
no batch_feedback, per-query event-change counts or gain attribution, or
retrieval_feedback.event_changes. The prompt has no batch-yield or consecutive
zero-change guidance. These statistics remain internal audit data only.
Accepted event summaries and supplied date precision
remain available through factual memory and event deltas. Dedicated earlier-career prompting and chronology_frontier were
removed. SEARCH budgeting includes the final batch-v9 protocol envelope before
deciding whether to compress or page the state.
Runtime network transport explicitly ignores proxies and pauses
on transport failures, with 30-second unauthenticated reachability checks.
Checkpoint recovery retains the current query batch and reviewed candidate IDs.
History, extracted source outputs and previous experiments are not rewritten.

## Diagnostic execution

`configs/preextracted_phase1_diagnostic_suite.json` selects the three lowest
historical first-phase Gold date recalls from the 2026-09-17 audit:
David Beckham (0/16), Bill Clinton (4/47), Bashar al-Assad (3/24).
These are Gold-selected diagnostic cases, not an untouched test sample.

Each starts with an empty timeline, at most 24 search batches, at most three
queries per batch and top-12 articles per query. VERIFY is paged into at most
eight events per request, with token-aware reduction. Non-VERIFY calls retain
the existing 500-request limit; VERIFY is independently accounted. Three topic
processes run concurrently with separate caches, ledgers and checkpoints.

```powershell
D:/miniforge/envs/tls/python.exe -u scripts/run_preextracted_diagnostic_suite.py --allow-api
```

Results are in `artifacts/preextracted_phase1_20260920/`. The suite records
`suite_status.json` and `comparison.json`; each topic writes its own trajectory,
candidate pool, event pool, prediction, request ledger and Gold coverage report.

Gold timelines are read only after each rollout. The primary score is exact
day-level Gold date recall over the union of that topic's reference dates.
Month/year/unknown events remain in the event pool, but do not become January 1
or the first of a month in the prediction. Reports separately identify Gold
dates absent from all topic extractions, present but not retrieved, and retrieved
but not selected. Endpoint exact hits and span containment are separate fields.
Historical budgets and framework versions differ; the comparison is descriptive,
not an equal-cost causal ablation. Factual/semantic consistency is not inferred
from date overlap and is not rechecked in this mode.

Validation after the same-batch alias repair: 52 relevant tests passed; all three article-ID
inventories matched (2,945 / 2,041 / 1,206), and real top-12 hybrid retrieval
returned only the event projection for all three topics.

After the 2026-09-20 failure fixes: 60 relevant tests passed. Offline replay of
the failed Beckham and Clinton responses completed with one audited MERGE-to-
APPEND recovery each. The Assad checkpoint now pages its final SEARCH request
to 3516 estimated tokens (previously 3802). Replay results are recorded in
`artifacts/preextracted_error_fix_20260920/offline_replay.json`; no API calls or
historical experiment changes were made during this regression.

SEARCH metadata recovery after the all-dataset run is also local and audited:
overlong reasons are retained in full in the recovery audit, with a short executor
note in the decision; an explicit `none` publication filter with only null-valued
fields can be canonicalized to mode/start/end; stale lead associations are removed
without rewriting the query or resolving the underlying lead. Invalid individual
queries are isolated, and exact historical duplicates remain filtered. Missing
hard/soft bounds are never guessed or relaxed to unrestricted retrieval. If no
valid fresh query remains, the stop is marked executor-forced, not autonomous.
These recoveries are excluded from training targets. Existing non-pre-extracted
policy behavior is unchanged.

82 regression tests passed after this change. The four all-dataset SEARCH failures
were replayed offline from their saved responses: Crisis/syria, John_Boehner and
Silvio_Berlusconi retained all three queries; Osama_bin_Laden retained two queries
after one existing duplicate was removed. See
`artifacts/search_schema_fix_20260920/offline_replay.json`. No API continuation or
historical result rewrite was performed as part of this fix.
