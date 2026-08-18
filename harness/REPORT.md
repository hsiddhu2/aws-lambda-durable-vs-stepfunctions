# REPORT — harness integration + dry-run results

**Status:** harness built, offline unit tests pass (38 assertions), dry run drove **5 real
workflows per arm** and wrote real measured counters. **STOP point reached — awaiting HP
review before any full-matrix run.**

Account `975050220345`, region `us-east-1`. Nothing in the deployed stacks was modified
except: reserved concurrency, S3 uploads, triggering, approvals, plus two HP-requested
usability changes to the approval API/emails (below).

---

## 1. What was wired to the real code

| Concern | Real value found in repo | Where |
|---|---|---|
| Durable pending-approval | table `etl-pending-approvals`, key `jobId`, `status="pending"`, `workflowType="durable-functions"`, `callbackId` | `durable-functions/src/handlers/etl_handler.py` `notify_reviewer()` |
| Durable terminal completion | table `etl-job-metadata` (key `jobId` HASH + `timestamp` RANGE), `status="COMPLETED"` | `durable-functions/src/steps/finalize.py` |
| Durable trigger | S3 `ObjectCreated` on `uploads/*.csv` auto-invokes the orchestrator | `durable-functions/template.yaml` S3 event |
| SFN approval | `.waitForTaskToken`; `taskToken` stored in `etl-pending-approvals`, `workflowType="step-functions"` | `step-functions/statemachine/etl-workflow.asl.json`, `src/steps/approval_lambda.py` |
| SFN terminal completion | execution status `SUCCEEDED` (also writes `etl-stepfn-metadata` `status="COMPLETED"`) | `src/steps/finalize_lambda.py` |
| SFN billed transitions | 6 states entered per execution (Extract→Transform→Load→WaitForApproval→CheckApproval→Finalize) | measured via `GetExecutionHistory` |
| Approval action | `POST /approve/{jobId}` (harness auto-approves all pending in code) | `scripts/approve_all_jobs.sh` flow, reused |

The harness resolves buckets / state-machine ARN / function names **live from
CloudFormation + Lambda** (function names carry random suffixes), falling back to the
snapshot values in `config.yaml`.

---

## 2. Repo facts that DIFFERED from the task spec — read these

1. **`scripts/trigger_stepfunctions.sh` has a latent bug.** It reads CFN output key
   `RawDataBucket`, but the `etl-stepfn` stack actually exports `RawBucketName`. The script
   would get an empty bucket. The harness does **not** use the script; it resolves
   `RawBucketName` correctly and starts executions itself.
2. **The Step Functions raw bucket has NO S3-event trigger** (unlike durable). Executions
   must be started explicitly with `start-execution` (bucket + key input). The harness does
   this; uploading CSVs alone would not start any SFN execution.
3. **SFN `ApprovalFunction` memory = 256 MB, not 512 MB** as the spec stated. The other 4
   SFN functions are 512 MB; durable is 1024 MB. The harness reads **live** `MemorySize`
   per function, so GB-seconds use the real values (see the dry-run JSON `functions[]`).
4. **Both metadata tables use `status="COMPLETED"`** as the terminal marker (durable
   `etl-job-metadata`, sfn `etl-stepfn-metadata`).

---

## 3. HP-requested changes applied to deployed infra (not measurement-affecting)

- **Clickable approval links.** The emailed links opened as browser **GET**, but the API
  only had **POST** → API Gateway returned `{"message":"Missing Authentication Token"}`.
  Added **GET** `/approve/{jobId}` and `/reject/{jobId}` (POST unchanged). `approval_handler.py`
  now treats only `/status` as a status read; `/approve` and `/reject` act on either method.
  Redeployed `etl-shared-resources`, `etl-durable`, `etl-stepfn` (email wording).
  The benchmark still auto-approves via `POST` in code — it never clicks links.
- **SNS email flood avoided (Option A).** For the full load (≈100k emails at 10000×10), the
  email subscription (`hpsiddhu@gmail.com`) was **unsubscribed** from
  `etl-approval-notifications`. The topic still publishes (so SNS cost is still incurred and
  measured); there is just no subscriber. Re-subscribe if manual approval is wanted later.

---

## 4. Operational fixes found during the dry run

- **CloudWatch datapoint alignment.** Querying `get_metric_statistics` with the raw run
  start (e.g. `04:39:38`) dropped the minute-aligned datapoint stamped `04:39:00` (before
  `StartTime`) → false null for invocations/gb_seconds. `collect_metrics._cw_sum` now floors
  start / ceils end to the minute (+60s pad) with `Period=60`. Verified: it returns the real
  counters.
- **Keep the Mac awake.** An earlier background run hit macOS idle-sleep (wall-clock jumped
  hours, polls timed out). Runs now launch under `caffeinate -i`. The full matrix (hours of
  wall-clock) MUST run on a machine that will not sleep.
- Metric collection = 180 s settle, then **poll up to `metrics_wait` (600 s)** until
  Invocations is readable, instead of a fixed guess.

---

## 5. Dry-run results — 5 real workflows per arm (both completed 5/5)

### `results/durable-5-r1.json`
```
window            2026-08-18T02:59:13Z → 03:02:28Z
workflows_completed  5 / 5
ETLDurableOrchestrator  memory_mb=1024  invocations=10  duration_ms_sum=9533.75  gb_seconds=9.5337
invocations       10          (2 per workflow: initial + post-approval resume)
gb_seconds        9.5337
state_transitions null        (BY DESIGN — durable has no state machine; costed as 0)
dynamodb          writes=25.0  reads=8.5
sns.publishes     5.0
s3                puts=null  gets=null
null_fields       ["s3.gets", "s3.puts"]
```

### `results/sfn_standard-5-r1.json`
```
window            2026-08-18T03:02:29Z → 03:05:52Z
workflows_completed  5 / 5
5 functions       Extract/Transform/Load/Finalize = 512 MB, Approval = 256 MB (all read live)
invocations       25          (5 functions × 5 workflows)
gb_seconds        1.2555
state_transitions gross=30  sampled=5/5  mean_per_exec=6.0  stdev=0.0
                  method: GetExecutionHistory StateEntered count; gross = mean × volume
dynamodb          writes=25.0  reads=6.5
sns.publishes     5.0
s3                puts=null  gets=null
null_fields       ["s3.gets", "s3.puts"]
```

**Acceptance check:** `invocations`, `gb_seconds`, and (sfn) `state_transitions.gross` are
all **non-null**. The only nulls are `s3.puts`/`s3.gets` (see §6).

### Dry-run cost decomposition (NOT publication figures — n=1, prices unverified)
Per-workflow *known* cost (readable components only), from `pricing_2026-08-12.json`:
- durable ≈ **$0.000033** (dominated by Lambda GB-seconds)
- sfn_standard ≈ **$0.000161** (dominated by state transitions: $0.00075 gross / 5)
- Free-tier: 30 transitions < 4,000/month → **net transition cost $0** at this scale.

These match the expected direction but mean each metric only 1 rep, no CI. The full run
(R=10) produces the mean ± 95% CI.

---

## 6. Null fields — reported, not back-filled

Only `s3.puts` / `s3.gets` are null, in every record. **S3 request metrics are not enabled
by default** on the buckets (they require a per-bucket request-metrics configuration, which
itself costs money). Consequently the S3 request-cost component is `null` and excluded from
the *known* cost. **HP decision needed:** either (a) enable S3 request metrics on the four
buckets before the full run (adds a small metrics cost, gives real S3 counts), or (b) accept
S3 request cost as an explicit, stated limitation and leave it null. Nothing is estimated.

---

## 7. What HP must verify / decide BEFORE the full run

1. **Pricing (required).** All 8 values in `pricing/pricing_2026-08-12.json` are pre-filled
   with typical list prices and flagged `"verify": true`. Confirm each against the cited
   `source_url` for us-east-1 and set `"verify": false`. Until then no cost figure is
   publication-ready (`cost_model` reports them under `prices_needing_verification`).
   Checklist in `claims_ledger.md`.
2. **S3 request metrics** — decide (a) enable or (b) accept-as-limitation (see §6).
3. **Full-run logistics** — the 10000 volume drives 10,000 real workflows × 10 reps × 2 arms
   (real spend, many hours). Confirm you want all three volumes, or start with 100 + 1000.
   Machine must stay awake (`caffeinate`).
4. **Reserved concurrency = 50** is pinned per arm function before each rep (durable: 1 fn;
   sfn: 5 fns). Confirm that is the intended control value.

Run the full matrix only after sign-off:
```
caffeinate -i python run_experiment.py --config config.yaml            # all
caffeinate -i python run_experiment.py --config config.yaml --only-volume 100
```
