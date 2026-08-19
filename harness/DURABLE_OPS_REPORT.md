# Durable-Execution Operations & Checkpoint Size — Measurement Report

**Scope:** convert two *derived* durable-execution claims into *direct measurements*, for
the Lambda Durable Functions ETL orchestrator (`ETLDurableOrchestrator`, us-east-1).
Standalone companion to the main [`REPORT.md`](REPORT.md).

## 1. Result

| Quantity | Derived (assumed) | **Measured** | Verdict |
|----------|------------------:|-------------:|---------|
| Durable operations / workflow | 9 | **9.00 ± 0.00** (sd 0.00) | derived value **confirmed exactly** |
| Checkpointed bytes / workflow | ~21 KB | **49,673 B = 48.5 KB ± 184 B** | derived value **~2.3× too low — use the measured figure** |

n = 5 clean trials (each with exactly one durable execution in its metric window),
mean ± 95 % CI (Student's t). Evidence: [`durable_ops_measured.json`](durable_ops_measured.json).

## 2. Why this measurement was needed

These two numbers were originally **derived** — AWS's documented durable-operation table
applied to the handler's code path — not measured. That is the paper's weakest empirical
claim. Both quantities are, in fact, emitted by AWS as CloudWatch metrics in the
`AWS/Lambda` namespace:

- `DurableExecutionOperations` — durable operations performed
- `DurableExecutionStorageWrittenBytes` — bytes checkpointed to durable storage

So they can be measured rather than assumed.

## 3. Method — isolated single-execution measurement

**The naive approach does not work.** Reading these metrics over the original 60-run
windows is contaminated: `DurableExecutionOperations` aggregates by `FunctionName` across
**every** execution active in the window, and windows were padded for publication lag. With
many benchmark runs overlapping, the metric Sum captured other runs' operations. Symptoms
that prove contamination:

- per-workflow operations *varied with batch size* — ~198 at volume 100, ~30 at 1,000,
  ~10–25 at 10,000 — but a per-workflow constant cannot legitimately swing 20×;
- coefficient of variation ≈ 92 % (mean 49, sd 45);
- `durable-100-r10`: 198 ops/wf × 100 = 19,800 operations for 100 workflows — ~22× the
  expected ~900.

**The valid approach** (`measure_durable_ops.py`): drive **one workflow at a time** with
nothing else touching the function, over a tight window. With a single execution in-window,
the metric Sum *is* that one workflow's operations / bytes. Each trial:

1. clean the `uploads/` prefix; wait until `ApproximateRunningDurableExecutions == 0`;
2. record `t_start`; upload exactly one CSV → triggers one durable workflow;
3. wait for its approval to appear; approve it; wait for `status=COMPLETED`;
4. wait until durable is idle again; record `t_end`;
5. settle 240 s for metric publication;
6. sum `DurableExecutionOperations`, `DurableExecutionStorageWrittenBytes`, and
   `DurableExecutionStarted` over `[t_start, t_end + settle]` (dimension
   `FunctionName=ETLDurableOrchestrator`);
7. keep the trial only if `DurableExecutionStarted == 1` (exactly one execution in-window).

All 5 trials satisfied the `started == 1` cleanliness gate.

## 4. Per-trial data

| Trial | executions in-window | operations | bytes written |
|------:|:--------------------:|-----------:|--------------:|
| 1 | 1 | 9.0 | 49,607 |
| 2 | 1 | 9.0 | 49,607 |
| 3 | 1 | 9.0 | 49,938 |
| 4 | 1 | 9.0 | 49,607 |
| 5 | 1 | 9.0 | 49,607 |
| **mean ± 95 % CI** | | **9.00 ± 0.00** | **49,673 ± 184** |

Operations are identical across trials (zero variance) — the workflow performs exactly 9
durable operations. Bytes are near-constant (the small spread is one trial at 49,938 vs
49,607, ~0.7 %).

## 5. Implication for the paper

- **Operations = 9** is now a **measurement**, not a derivation — the weakest claim is
  removed. Report it as measured (n=5, sd 0).
- **Use 48.5 KB, not ~21 KB** for the checkpointed payload. If durable-storage cost is
  priced from this byte count, that line increases ~2.3×; it remains a negligible fraction
  of total cost, so the headline (Durable 57–76 % cheaper, driven by Step Functions'
  state-transition cost) is **unchanged**.
- Do **not** cite the retroactive backfill numbers (49 ops, 269 KB) — they are window-bleed
  artifacts, documented here so the failure mode is transparent.

## 6. Reproduce

```bash
cd harness && source .venv/bin/activate
python backfill_durable_metrics.py --discover     # confirm the durable metrics exist
python measure_durable_ops.py --trials 5          # isolated measurement (~30 min, ~$0)
```

Requires the `etl-durable` stack deployed. Tools: `measure_durable_ops.py` (valid,
isolated) and `backfill_durable_metrics.py` (`--discover` only; naive backfill is
contaminated — see §3).
