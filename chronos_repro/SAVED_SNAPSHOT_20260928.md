# Saved workspace snapshot (2026-09-28)

This save keeps the current implementation, required data, and latest valid
first-phase results. It intentionally does not add the full local experiment
history or per-request response caches. Local excluded files are not deleted.

Included:

- Current source, scripts, tests, configurations, and research notes.
- Existing frozen T17, Entities, and Crisis corpus snapshots and BM25 indexes.
- All 60 topic event exports in `artifacts/llm_tls_extraction_v1/events/`, with
  extraction identity, preparation, export, and transport reports.
- The three `*_dense_collection_v3` indexes and their active hybrid manifests.
- The DeepSeek tokenizer and active MiniLM ONNX runtime/configuration/tokenizer.
- T17 and Entities results in `artifacts/preextracted_all_phase1_20260920/`.
  Root suite reports describe that historical all-dataset run; its Crisis
  topic directories are excluded because the newer Crisis run supersedes them.
- Crisis results in
  `artifacts/preextracted_crisis_developments_topic_only_20260922/`, including
  saved Gold-gap and retrieval diagnoses.
- Crisis time-distribution statistics and the 2026-09-28 implementation audit.
- Pinned LLM-TLS adapter sources and existing upstream submodule references.

Not newly versioned:

- Superseded experiments, interrupted attempts, nested historical backups,
  and per-request API caches.
- The preprocessing working SQLite database and its individual response files;
  the completed event exports are the online pipeline's required input.
- Duplicate source downloads, the duplicate review checkout, full-precision
  embedding weights, redundant ONNX copies, and download/runtime caches.
- Credentials and local environment files.

Existing files already present in earlier commits remain tracked. This save
does not rewrite published Git history. Git LFS stores large binary data;
after cloning, run `git lfs pull` and initialize the upstream submodules with
`git submodule update --init --recursive`. Recorded Windows absolute paths
inside manifests may need relocation on another machine.

The current Crisis evaluation of interest restricts reference dates to dates
present in all topic pre-extractions. Its report is
`artifacts/preextracted_crisis_developments_topic_only_20260922/gold_gap_analysis/extractable_gold_coverage.json`.
The runner's default `gold_date_coverage.json` still uses all reference dates;
these denominators must not be mixed.
