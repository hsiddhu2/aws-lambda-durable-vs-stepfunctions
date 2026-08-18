"""analysis.py — aggregate per-rep cost records into mean ± 95% CI tables.

Reads a set of results records (one per arm×volume×rep), prices each with
cost_model.compute_cost, then aggregates across reps to produce:
  * mean, sample stdev, and 95% confidence interval half-width (Student's t)
    for per-workflow cost and per-run cost, per (arm, volume);
  * a free-tier scenario column for Step Functions transitions: gross vs net of
    the 4,000-transitions/month account-level free tier (assumption stated in
    the output and RUNBOOK).
  * summary.csv and, when matplotlib is present, comparison figures.

Integrity: any rep whose total cost is None (because a counter was null) is
EXCLUDED from that metric's aggregate and COUNTED in `n_excluded_null`, never
coerced to a number. If fewer than 2 usable reps exist, the CI is None (a CI is
undefined for n<2) rather than a fabricated 0.
"""

from __future__ import annotations

import csv
import math
import os
from typing import Optional

from cost_model import compute_cost

# Student's t critical values (two-sided, 95%) for small samples, df = n-1.
# df >= 30 falls back to the normal approximation 1.96.
_T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056,
    27: 2.052, 28: 2.048, 29: 2.045,
}


def _t_critical(n: int) -> Optional[float]:
    if n < 2:
        return None
    df = n - 1
    if df in _T95:
        return _T95[df]
    return 1.96  # normal approximation for df >= 30


def mean_ci(values: list[float]) -> dict:
    """Mean, sample stdev, and 95% CI half-width via Student's t.

    n<1 -> all None. n==1 -> mean known, stdev/CI None (undefined)."""
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "stdev": None, "ci95_halfwidth": None,
                "ci95_low": None, "ci95_high": None}
    mean = sum(values) / n
    if n == 1:
        return {"n": 1, "mean": mean, "stdev": None, "ci95_halfwidth": None,
                "ci95_low": None, "ci95_high": None}
    var = sum((x - mean) ** 2 for x in values) / (n - 1)  # sample variance
    stdev = math.sqrt(var)
    t = _t_critical(n)
    half = t * stdev / math.sqrt(n) if t is not None else None
    return {
        "n": n,
        "mean": mean,
        "stdev": stdev,
        "ci95_halfwidth": half,
        "ci95_low": None if half is None else mean - half,
        "ci95_high": None if half is None else mean + half,
    }


def _net_transition_cost(gross_transitions: float, pricing: dict,
                         free_tier: int) -> Optional[float]:
    """Cost of SFN transitions after subtracting the monthly free tier, applied
    ONCE at the aggregate (an account-level monthly allowance, not per-run)."""
    node = pricing.get("step_functions_standard", {})
    price = node.get("state_transition_per_million")
    if isinstance(price, dict):
        price = price.get("value")
    if price is None:
        return None
    billable = max(0.0, gross_transitions - free_tier)
    return billable * price / 1_000_000


def aggregate(records: list[dict], pricing: dict) -> list[dict]:
    """Group records by (arm, volume) and aggregate reps. Returns one row per group."""
    groups: dict[tuple, list[dict]] = {}
    for rec in records:
        costed = compute_cost(rec, pricing)
        key = (rec.get("arm"), rec.get("volume"))
        groups.setdefault(key, []).append({"record": rec, "cost": costed})

    free_tier = (
        pricing.get("step_functions_standard", {})
        .get("free_tier_transitions_per_month", 4000)
    )

    rows = []
    for (arm, volume), items in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), kv[0][1] or 0)):
        # per_workflow_known (sum of READABLE components) is the primary reported metric:
        # some components (notably S3 request cost) are legitimately null when request
        # metrics are not enabled, so an all-or-nothing total would always be null.
        # per_workflow_full is the strict all-components variant, reported alongside.
        per_wf = [it["cost"]["per_workflow_usd"] for it in items
                  if it["cost"]["per_workflow_usd"] is not None]
        per_wf_known = [it["cost"]["per_workflow_known_usd"] for it in items
                        if it["cost"]["per_workflow_known_usd"] is not None]
        n_total = len(items)
        # A rep is excluded from the reported (known) aggregate only when even the known
        # cost is undefined (e.g. zero completions), never merely because one component null.
        n_null = sum(1 for it in items if it["cost"]["per_workflow_known_usd"] is None)
        reps_with_full = sum(1 for it in items if it["cost"]["total_usd"] is not None)
        null_components_union = sorted({c for it in items for c in it["cost"]["null_components"]})

        # Gross transitions summed across reps, then net-of-free-tier once.
        gross_list = []
        for it in items:
            st = it["record"].get("state_transitions") or {}
            g = st.get("gross") if isinstance(st, dict) else None
            if g is not None:
                gross_list.append(g)
        gross_total = sum(gross_list) if gross_list else None
        net_transition_usd = (
            _net_transition_cost(gross_total, pricing, free_tier)
            if gross_total is not None else None
        )
        gross_transition_usd = (
            _net_transition_cost(gross_total, pricing, 0)
            if gross_total is not None else None
        )

        rows.append({
            "arm": arm,
            "volume": volume,
            "reps_total": n_total,
            "reps_excluded_null": n_null,
            "reps_with_full_total": reps_with_full,
            "null_components_union": null_components_union,
            "per_workflow_full": mean_ci(per_wf),
            "per_workflow_known": mean_ci(per_wf_known),
            "sfn_transitions_gross_total": gross_total,
            "sfn_transition_cost_gross_usd": gross_transition_usd,
            "sfn_transition_cost_net_freetier_usd": net_transition_usd,
            "free_tier_transitions_per_month": free_tier,
            "free_tier_assumption": (
                "Net column subtracts the 4,000 transitions/month Step Functions "
                "free tier ONCE at aggregate, assuming this benchmark is the only "
                "consumer of that account-level monthly allowance."
            ),
        })
    return rows


def write_summary_csv(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # Primary reported metric = per_workflow_known (readable components). The strict
    # all-components figure and the list of null components are reported alongside so a
    # reader sees exactly what the "known" cost omits.
    fields = [
        "arm", "volume", "reps_total", "reps_excluded_null", "reps_with_full_total",
        "per_workflow_known_mean_usd", "per_workflow_known_ci95_halfwidth_usd",
        "per_workflow_known_ci95_low_usd", "per_workflow_known_ci95_high_usd",
        "per_workflow_known_n",
        "per_workflow_full_mean_usd", "per_workflow_full_ci95_halfwidth_usd",
        "sfn_transitions_gross_total",
        "sfn_transition_cost_gross_usd", "sfn_transition_cost_net_freetier_usd",
        "null_components_union",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            pk = r["per_workflow_known"]
            pf = r["per_workflow_full"]
            w.writerow({
                "arm": r["arm"],
                "volume": r["volume"],
                "reps_total": r["reps_total"],
                "reps_excluded_null": r["reps_excluded_null"],
                "reps_with_full_total": r["reps_with_full_total"],
                "per_workflow_known_mean_usd": pk["mean"],
                "per_workflow_known_ci95_halfwidth_usd": pk["ci95_halfwidth"],
                "per_workflow_known_ci95_low_usd": pk["ci95_low"],
                "per_workflow_known_ci95_high_usd": pk["ci95_high"],
                "per_workflow_known_n": pk["n"],
                "per_workflow_full_mean_usd": pf["mean"],
                "per_workflow_full_ci95_halfwidth_usd": pf["ci95_halfwidth"],
                "sfn_transitions_gross_total": r["sfn_transitions_gross_total"],
                "sfn_transition_cost_gross_usd": r["sfn_transition_cost_gross_usd"],
                "sfn_transition_cost_net_freetier_usd": r["sfn_transition_cost_net_freetier_usd"],
                "null_components_union": ";".join(r["null_components_union"]),
            })


def write_figures(rows: list[dict], out_dir: str) -> Optional[str]:
    """Bar chart of per-workflow mean cost with 95% CI error bars, per arm×volume.
    Returns the figure path, or None if matplotlib is unavailable."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    usable = [r for r in rows if r["per_workflow_known"]["mean"] is not None]
    if not usable:
        return None
    os.makedirs(out_dir, exist_ok=True)
    labels = [f"{r['arm']}\n{r['volume']}" for r in usable]
    means = [r["per_workflow_known"]["mean"] for r in usable]
    errs = [r["per_workflow_known"]["ci95_halfwidth"] or 0 for r in usable]
    fig, ax = plt.subplots(figsize=(max(6, len(usable) * 1.2), 4))
    ax.bar(range(len(usable)), means, yerr=errs, capsize=4)
    ax.set_xticks(range(len(usable)))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("Cost per workflow (USD)")
    ax.set_title("Per-workflow cost (mean ± 95% CI)")
    fig.tight_layout()
    path = os.path.join(out_dir, "per_workflow_cost.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path
