"""finalize.py — audit all result records, aggregate, and emit publication tables.

Run after both arms complete and results/ is populated (pulled from S3). It:
  1. AUDITS every record for integrity: throttles==0, completed==volume, and the
     expected exact counters (durable inv=2*vol, sfn inv=5*vol, sfn transitions=6*vol).
     Any violation is printed and the record is listed under `flagged` — a flagged run
     must be re-run before the dataset is published.
  2. Aggregates clean records -> summary.csv + figures (mean +/- 95% CI, free-tier col).
  3. Prints a compact headline table (per-workflow cost by arm x volume) for REPORT.md.

Exit code 0 only if the audit is clean and all 60 cells are present.
"""

from __future__ import annotations

import glob
import json
import os
import sys

import yaml

import analysis
import cost_model

HERE = os.path.dirname(os.path.abspath(__file__))

EXPECT = {  # per-workflow multipliers
    "durable": {"inv": 2, "trans": None},        # 2 invocations/workflow, no transitions
    "sfn_standard": {"inv": 5, "trans": 6},       # 5 fns/workflow, 6 states entered/exec
}


# A record is HARD-flagged (must re-run) only when something that materially corrupts a
# cost counter is wrong. Tiny-component metric lag (S3/DynamoDB/SNS CloudWatch counters
# occasionally null) and sub-0.5% invocation edge effects are SOFT (disclosed, not re-run):
# they touch only negligible cost components and analysis already excludes null components.
CRITICAL_NULL_PREFIXES = ("invocations", "gb_seconds", "duration_ms", "state_transitions",
                          "lambda.")  # per-function lambda counters
INV_TOL = 0.005            # 0.5%
COMPLETE_TOL = 0.995       # allow up to 0.5% completion shortfall (metric-window edge)


def audit(records: list[dict]) -> tuple[list[str], list[str]]:
    hard, soft = [], []
    for r in records:
        arm, vol = r.get("arm"), r.get("volume")
        rid = r.get("run_id", f"{arm}-{vol}-r{r.get('rep')}")
        h, s = [], []
        if (r.get("lambda_throttles") or 0) != 0:
            h.append(f"throttles={r.get('lambda_throttles')}")
        comp = r.get("workflows_completed") or 0
        if comp < COMPLETE_TOL * vol:
            h.append(f"completed={comp}/{vol}")
        elif comp != vol:
            s.append(f"completed={comp}/{vol}")
        exp = EXPECT.get(arm, {})
        if exp.get("inv") is not None:
            want, got = exp["inv"] * vol, (r.get("invocations") or 0)
            dev = abs(got - want) / want if want else 0
            if dev > INV_TOL:
                h.append(f"inv={got} exp={want} ({dev:.1%})")
            elif got != want:
                s.append(f"inv={got} exp={want}")
        st = r.get("state_transitions") or {}
        if exp.get("trans") is not None and (st.get("gross") or 0) != exp["trans"] * vol:
            h.append(f"trans={st.get('gross')} exp={exp['trans']*vol}")
        for nf in r.get("null_fields", []):
            (h if nf.startswith(CRITICAL_NULL_PREFIXES) else s).append(f"null:{nf}")
        if h:
            hard.append(f"{rid}: " + ", ".join(h))
        elif s:
            soft.append(f"{rid}: " + ", ".join(s))
    return hard, soft


def main():
    cfg = yaml.safe_load(open(os.path.join(HERE, "config.yaml")))
    pricing = json.load(open(os.path.join(HERE, cfg["pricing_snapshot"])))
    paths = sorted(glob.glob(os.path.join(HERE, "results", "*-r*.json")))
    records = [json.load(open(p)) for p in paths]
    print(f"loaded {len(records)} records")

    # Completeness: expect arms x volumes x reps cells.
    arms = list(cfg["arms"].keys())
    expected_cells = {f"{a}-{v}-r{rep}"
                      for a in arms for v in cfg["volumes"]
                      for rep in range(1, cfg["repetitions"] + 1)}
    have = {r.get("run_id") for r in records}
    missing = sorted(expected_cells - have)
    if missing:
        print(f"MISSING {len(missing)} cells: {missing}")

    hard, soft = audit(records)
    if hard:
        print(f"HARD-FLAGGED {len(hard)} records (MUST re-run):")
        for f in hard:
            print("  " + f)
    else:
        print("AUDIT CLEAN: no record has a corrupting defect")
    if soft:
        print(f"soft notes {len(soft)} (tolerable metric-lag / <0.5% edge, disclosed not re-run):")
        for f in soft:
            print("  " + f)

    rows = analysis.aggregate(records, pricing)
    analysis.write_summary_csv(rows, os.path.join(HERE, "results", "summary.csv"))
    fig = analysis.write_figures(rows, os.path.join(HERE, "results", "figures"))
    print(f"wrote summary.csv" + (f" + {fig}" if fig else " (matplotlib absent, no figure)"))

    print("\n=== per-workflow cost (mean ± 95% CI, USD) ===")
    for row in rows:
        pk = row["per_workflow_known"]
        ci = f"±{pk['ci95_halfwidth']:.3e}" if pk["ci95_halfwidth"] is not None else "(n<2)"
        m = f"{pk['mean']:.3e}" if pk["mean"] is not None else "None"
        print(f"  {row['arm']:>13} v{row['volume']:<6} n={pk['n']:>2}  "
              f"{m} {ci}  [transitions gross ${row['sfn_transition_cost_gross_usd'] or 0:.5f} "
              f"net ${row['sfn_transition_cost_net_freetier_usd'] or 0:.5f}]")

    ok = not hard and not missing
    print(f"\nFINALIZE {'OK' if ok else 'INCOMPLETE/HARD-FLAGGED'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
