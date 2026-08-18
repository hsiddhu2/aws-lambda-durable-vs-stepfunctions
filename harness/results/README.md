# results/ — measured evidence trail

Every file here is a **real measurement** from a real AWS run. These files feed the
UCC 2026 paper and are committed as the evidence trail. Do **not** hand-edit them and
never write synthetic/estimated numbers here (see the INTEGRITY RULES in
`../REPORT.md` and the task brief).

## Record schema (`<arm>-<volume>-r<rep>.json`)

| field | meaning |
|-------|---------|
| `arm` | `durable` or `sfn_standard` |
| `volume` | workflows requested this rep (1 CSV = 1 workflow) |
| `rep` | repetition index (1..R) |
| `run_id`, `region`, `account` | identity of the run |
| `window.start` / `window.end` | UTC ISO8601 metric window (trigger → after settle) |
| `workflows_requested` | = volume |
| `workflows_completed` | **measured** terminal completions in-window (durable: `etl-job-metadata` status=COMPLETED; sfn: SUCCEEDED executions) |
| `functions[]` | per-function `memory_mb` (live from Lambda API), `invocations`, `duration_ms_sum`, `gb_seconds` |
| `invocations` | total Lambda invocations across arm functions (CloudWatch `Invocations` Sum) |
| `duration_ms_sum` | total billed duration ms (CloudWatch `Duration` Sum) |
| `gb_seconds` | Σ (duration_ms/1000 × memory_mb/1024) — from real duration + real memory |
| `state_transitions` | **sfn only**; `gross`, `matched_executions`, `sampled_executions`, `sample_mean_per_exec`, `sample_stdev`, `method`. `null` for durable by design |
| `dynamodb.writes` / `.reads` | CloudWatch `ConsumedWrite/ReadCapacityUnits` Sum over the arm's tables + approvals table |
| `s3.puts` / `.gets` | CloudWatch S3 request metrics — **null unless per-bucket request metrics are enabled** |
| `sns.publishes` | CloudWatch `NumberOfMessagesPublished` Sum for the approval topic |
| `null_fields[]` | every counter that could not be read; left `null`, never back-filled |
| `notes[]` | provenance notes (e.g. why a field is null) |

## Derived tables

`summary.csv` (written by `analysis.py`) aggregates reps → per-workflow mean ± 95% CI
(Student's t), plus gross-vs-net-of-free-tier Step Functions transition cost.
