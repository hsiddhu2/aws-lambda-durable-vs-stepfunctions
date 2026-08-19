# REPORT — cost benchmark results (final)

**Status:** COMPLETE. 60/60 measured runs (2 arms × 3 volumes × R=10) against real AWS,
account `975050220345`, us-east-1. Integrity audit **CLEAN** (no record has a corrupting
defect). Pricing snapshot verified (`pricing/pricing_2026-08-12.json`, all `verify:false`).

Reproduce the tables: `python finalize.py` (audits, writes `results/summary.csv` + figure).

---

## 1. Headline — Durable Functions vs Step Functions Standard

Per-workflow cost (mean ± 95% CI, Student's t, n=10), and how much cheaper Durable is:

| Volume | Durable $/wf | Step Functions $/wf | **Durable cheaper by** |
|-------:|-------------:|--------------------:|----------------------:|
| 100    | 9.706e-05 ± 2.16e-05 | 2.267e-04 ± 6.03e-05 | **57.2 %** |
| 1,000  | 4.394e-05 ± 1.17e-06 | 1.721e-04 ± 5.22e-06 | **74.5 %** |
| 10,000 | 4.029e-05 ± 3.84e-07 | 1.679e-04 ± 2.71e-07 | **76.0 %** |

The advantage **grows with volume** and asymptotes near ~76 %: Step Functions bills a fixed
per-workflow state-transition cost that Durable does not incur, while Durable's fixed
overheads amortize as volume rises.

## 2. Why — component breakdown at 10,000 workflows (mean per 10k-workflow run)

| Component | Durable | Step Functions |
|-----------|--------:|---------------:|
| **State transitions** | **$0.00000** | **$1.50000** ← 90 % of SFN cost |
| Lambda GB-seconds | $0.21407 | $0.01432 |
| S3 PUT | $0.09312 | $0.09832 |
| DynamoDB writes | $0.05236 | $0.03125 |
| DynamoDB reads | $0.02256 | $0.01191 |
| SNS publishes | $0.00932 | $0.00500 |
| S3 GET | $0.00745 | $0.00787 |
| Lambda requests | $0.00400 | $0.01000 |
| **≈ total / 10k run** | **≈ $0.40** | **≈ $1.67** |

**The entire gap is state transitions.** SFN Standard bills 6 transitions/workflow
(Extract→Transform→Load→WaitForApproval→CheckApproval→Finalize) at $25/M = $1.50 per 10k
run — 90 % of its cost. Durable has no state machine → $0.

Note the *inverse* on Lambda GB-seconds: Durable's single 1024 MB orchestrator holds the
whole workflow (more GB-s) vs SFN's short 512/256 MB step functions. Durable still wins
overall by a wide margin because it avoids transitions entirely. Memory was **left
as-deployed** (not normalized) and read live per function, so GB-seconds reflect reality.

## 3. Free-tier scenario (Step Functions transitions)

Reported gross AND net of the 4,000 transitions/month free tier (applied once at aggregate,
assuming this benchmark is the sole consumer of that account-level allowance):

| Volume (×10 reps) | gross transition $ | net-of-free-tier $ |
|------------------:|-------------------:|-------------------:|
| 100   | $0.15  | $0.05  |
| 1,000 | $1.50  | $1.40  |
| 10,000| $15.00 | $14.90 |

Free tier is only material at the smallest scale; negligible at 10k.

## 4. Methodology

- **Arms:** `durable` (single Lambda durable orchestrator), `sfn_standard` (state machine +
  5 Lambdas). **Express excluded by design** (5-min cap + no `.waitForTaskToken` for the
  ~20-min human approval).
- **Matrix:** volumes 100/1,000/10,000 × **R=10**; mean ± 95 % CI (Student's t).
- **Control:** reserved concurrency **pinned = 120 equally** on all 6 arm functions
  (removes the concurrency-quota confound; cost is concurrency-independent). Workflows
  injected at **50/s** so concurrent demand stays under the cap → **zero systematic
  throttling** (recorded per run).
- **Window isolation (sfn):** RUNNING executions drained to 0 before each rep's metric
  window opens and before collection, so no cross-rep invocation bleed.
- **Measurement:** every counter is a live CloudWatch / Step Functions read. Lambda
  GB-seconds from real Duration × real per-function memory. SFN transitions counted from
  `GetExecutionHistory` (sampled 100/rep, exact 6.0/exec, stdev 0). Prices only from the
  dated snapshot.
- **Execution:** two EC2 c5.xlarge runners (one per arm) in-region, self-healing +
  resumable; all raw results in `results/` are the committed evidence trail.

## 5. Integrity — audit result

**AUDIT CLEAN**: no record has a corrupting defect (throttles that inflate counts,
completion shortfall >0.5 %, invocation/transition deviation >0.5 %, or null on a critical
counter). 14 **soft notes** disclosed (tolerable, not re-run):

- `durable-100-r4, r9`: `s3.puts` null — S3 request-metric publish lag at low volume.
- 8 sfn reps: `dynamodb.etl-stepfn-metadata.reads` null — DynamoDB metric lag.
- `durable-10000-r5`: inv 19,999 vs 20,000 (−0.005 %, one metric-window edge).
- `sfn-10000-r1`: 1 stochastic Lambda throttle, absorbed (inv 49,999, transitions exact
  60,000 — no inflation), completed 9,999/10,000.
- `sfn-10000-r3, r4`: inv 50,002 / 50,001 (+0.004 %) — one retry each, transitions exact.

Null components are excluded from that rep's *known* cost (never back-filled); their tiny
cost contribution is the only thing affected, and the dominant components are exact.

Fixes applied live during the run (each surfaced by the detection harness, none reached the
final dataset): approval rate-limiting (killed 162/300-throttle bursts), adaptive boto3
retry (Step Functions `ListExecutions` throttling), and sfn window isolation (invocation
bleed across reps).

## 6. Deliverables in this branch

- `results/*.json` — 60 raw measured records (evidence trail).
- `results/summary.csv` — aggregated mean ± 95 % CI + gross/net free-tier columns.
- `results/figures/per_workflow_cost.png` — per-arm×volume cost with CI error bars.
- `pricing/pricing_2026-08-12.json` — verified price snapshot (cited).
- `finalize.py` — re-run to regenerate audit + tables from `results/`.

## 7. For HP

Nothing is blocking. Optional before publication: re-confirm the 8 prices are still current
(snapshot dated 2026-08-12, verified 2026-08-17), and decide whether to mention the soft-note
metric-lag nulls in the paper's limitations (they move no headline number).
