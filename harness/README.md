# harness/ — cost + performance benchmark

Measures the real per-workflow cost of the two ETL implementations (Lambda Durable
Functions vs Step Functions Standard) against live AWS and produces publication-ready
tables (mean ± 95% CI). This directory is the reproducible experiment behind the numbers
in the top-level [README](../README.md).

## Results

See **[REPORT.md](REPORT.md)** for the full write-up. Headline (60 runs, mean ± 95% CI):
Durable Functions is **57.2% / 74.5% / 76.0%** cheaper than Step Functions Standard at
100 / 1,000 / 10,000 workflows — the entire gap is Step Functions' state-transition cost.

Raw evidence: [`results/`](results/) (60 measured records), [`results/summary.csv`](results/summary.csv),
[`results/figures/`](results/figures/).

## Files

| File | Purpose |
|------|---------|
| `run_experiment.py` | Orchestrator: per (arm × volume × rep) clean → pin concurrency → generate + inject workflows → approve → wait for completion → collect metrics. Flags: `--dry-run`, `--only-arm`, `--only-volume`, `--fresh`. |
| `collect_metrics.py` | Live CloudWatch / Step Functions reads → results record. Unreadable counters left `null`. |
| `cost_model.py` | Results record + pricing snapshot → decomposed cost (no hardcoded prices). |
| `analysis.py` | Aggregate reps → mean ± 95% CI (Student's t), `summary.csv`, figures, free-tier column. |
| `finalize.py` | Integrity audit (hard/soft) + aggregate + printed tables. Run after a full matrix. |
| `config.yaml` | Region, arms, volumes, R, reserved concurrency, inject rate, resource resolution, timeouts. |
| `pricing/` | Dated, verified price snapshot (`pricing_YYYY-MM-DD.json`) + template. |
| `tests/test_offline.py` | Offline unit tests for the cost/CI math (no AWS). |
| `RUNBOOK.md` | Step-by-step run instructions. |
| `claims_ledger.md` | Paper claims ↔ evidence ↔ status. |

## Quick start

```bash
cd harness
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python tests/test_offline.py                                  # offline math tests
python run_experiment.py --config config.yaml --dry-run       # 5 real workflows/arm
# full matrix (real spend + hours): see RUNBOOK.md
python finalize.py                                            # audit + tables from results/
```

See **[RUNBOOK.md](RUNBOOK.md)** for prerequisites, the full-run procedure, and the integrity rules.

## Integrity (why the numbers are trustworthy)

- Every value in `results/` is a live AWS/CloudWatch read of a real run — nothing estimated.
- A counter that can't be read is written `null` and left `null` (never back-filled).
- Prices live only in the dated snapshot (`pricing/`), each value flagged until verified.
- Reserved concurrency pinned equally across arms; workflows injected at a paced rate so
  no throttling skews counts (recorded per run); Step Functions rep windows isolated so
  Lambda counts don't bleed across reps.
