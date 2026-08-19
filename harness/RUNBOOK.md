# RUNBOOK — cost benchmark harness

## Prerequisites
- AWS CLI authenticated to account `975050220345`, region `us-east-1` (the three ETL
  stacks are already deployed; the harness does **not** deploy or modify them).
- Python 3.12+ with a venv:
  ```bash
  cd harness
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  ```

## 0. Verify the pricing snapshot (REQUIRED before any cost number is used)
`pricing/pricing_2026-08-12.json` is pre-filled with typical list prices, every value
flagged `"verify": true`. Open each `source_url`, confirm the us-east-1 value, correct
it, and set `"verify": false`. Until then, `cost_model` reports the price under
`prices_needing_verification` and no cost figure is publication-ready.

## 1. Offline unit tests (no AWS, no cost)
```bash
source .venv/bin/activate
python tests/test_offline.py
```
Proves the mean±95%CI math, null propagation, and the free-tier column on synthetic
records written to `/tmp` and deleted afterwards. Synthetic data NEVER enters `results/`.

## 2. Dry run (drives 5 REAL workflows per arm)
```bash
python run_experiment.py --config config.yaml --dry-run
```
Writes `results/durable-5-r1.json` and `results/sfn_standard-5-r1.json`. Confirm
non-null `invocations`, `gb_seconds`, and (sfn) `state_transitions.gross`.

**STOP after the dry run. Do not run the full matrix until HP reviews `REPORT.md`.**

## 3. Full matrix (only after sign-off)
Control knobs (already in `config.yaml`): volumes 100/1000/10000, R=10,
reserved_concurrency=50 pinned per arm function, memory left as-deployed.
```bash
python run_experiment.py --config config.yaml                 # everything
python run_experiment.py --config config.yaml --only-arm durable
python run_experiment.py --config config.yaml --only-volume 100
```
⚠️ The 10000 volume drives 10,000 real workflows per rep × 10 reps per arm — real
spend and hours of wall-clock. Run deliberately.

## 4. Aggregate → tables + figures
```bash
python -c "import glob,json,yaml,analysis; \
cfg=yaml.safe_load(open('config.yaml')); \
recs=[json.load(open(p)) for p in glob.glob('results/*-r*.json')]; \
pricing=json.load(open(cfg['pricing_snapshot'])); \
rows=analysis.aggregate(recs,pricing); \
analysis.write_summary_csv(rows,'results/summary.csv'); \
print(analysis.write_figures(rows,'results/figures'))"
```

## Integrity reminders
- Never fabricate/estimate/hardcode a measured number. Unreadable counter → `null`, left null.
- Prices live only in the dated snapshot. No price hardcoded in code.
- Only reserved concurrency, S3 uploads, triggering, and approvals touch AWS. No template,
  logic, memory, or stack changes. No stack deletion.
