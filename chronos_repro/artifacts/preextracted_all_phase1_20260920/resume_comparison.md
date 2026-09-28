# Failed-topic checkpoint continuation

Checkpoint continuation of four failed topics only; first phase only.

| Topic | Previous Gold hits | After continuation | Batches before/after | Termination |
|---|---:|---:|---:|---|
| crisis/syria | 25/106 | 27/106 | 14/20 | invalid_policy_handoff |
| entities/John_Boehner | 4/13 | 4/13 | 15/24 | runner_limit |
| entities/Osama_bin_Laden | 4/16 | 4/16 | 20/24 | runner_limit |
| entities/Silvio_Berlusconi | 14/34 | 16/34 | 10/24 | runner_limit |

Added HTTP requests: 96; added returned tokens: 242704.

All 56 previously successful topic outputs are byte-for-byte unchanged. Previous steps, cached responses, extracted summaries and dates are preserved in the resumed topics. All 60 topics now have runtime_status=ok; stop causes still distinguish batch limits, duplicate handoffs and autonomous stops.

Current aggregates now include all 60 topics. Earlier aggregates excluded the four error cases; changing this denominator is not a decline in already completed results.
