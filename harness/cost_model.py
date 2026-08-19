"""cost_model.py — decompose a single measured results record into a cost breakdown.

Integrity contract (see harness/REPORT.md + the task INTEGRITY RULES):
  * This module NEVER invents a measured quantity. It consumes counters that were
    measured by collect_metrics.py and prices them using a dated pricing snapshot.
  * A counter that is `None` (unreadable at collection time) produces a `None` cost
    component. Nulls are propagated, never back-filled with a plausible value.
  * Prices come ONLY from the pricing snapshot dict passed in. No price is hardcoded
    here. Each snapshot value may carry a "verify": true flag; that flag is surfaced
    in the output so downstream reporting can flag unverified prices.

The record schema is documented in results/README.md. The pricing snapshot schema is
in pricing/pricing.example.json.
"""

from __future__ import annotations

from typing import Any, Optional


def _price(snapshot: dict, *path: str) -> tuple[Optional[float], bool]:
    """Walk `path` into the pricing snapshot and return (value, needs_verify).

    Returns (None, False) if any key is missing so a missing price cannot be
    silently treated as zero.
    """
    node: Any = snapshot
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None, False
        node = node[key]
    if isinstance(node, dict) and "value" in node:
        return node["value"], bool(node.get("verify", False))
    return node, False


def _mul(qty: Optional[float], unit_price: Optional[float], per: float) -> Optional[float]:
    """qty * unit_price / per, propagating None. `per` is the pricing denominator
    (e.g. 1_000_000 for a per-million price)."""
    if qty is None or unit_price is None:
        return None
    return qty * unit_price / per


def compute_cost(record: dict, pricing: dict) -> dict:
    """Return a decomposed cost breakdown for one (arm, volume, rep) results record.

    Output components are absolute USD for the whole run (all `volume` workflows).
    `per_workflow` divides by workflows_completed when that is a positive int.
    Any component whose input counter is None stays None.
    """
    arm = record.get("arm")
    verify_flags: set[str] = set()

    def priced(qty, per, *path):
        val, needs_verify = _price(pricing, *path)
        if needs_verify:
            verify_flags.add(".".join(path))
        return _mul(qty, val, per)

    components: dict[str, Optional[float]] = {}

    # ---- Lambda (compute) ----
    invocations = record.get("invocations")
    gb_seconds = record.get("gb_seconds")
    components["lambda_requests"] = priced(
        invocations, 1_000_000, "lambda", "arm64", "request_per_million"
    )
    components["lambda_gb_seconds"] = priced(
        gb_seconds, 1, "lambda", "arm64", "gb_second"
    )

    # ---- Step Functions state transitions ----
    # Durable arm has NO state machine: its transition cost is a STRUCTURAL zero
    # (documented, not measured — see claims_ledger #5), so it is a known 0.0, not null.
    # The sfn arm's transition count is measured (sampled); a null there stays null.
    if arm == "durable":
        components["sfn_transitions_gross"] = 0.0
    else:
        st = record.get("state_transitions") or {}
        transitions_gross = st.get("gross") if isinstance(st, dict) else None
        components["sfn_transitions_gross"] = priced(
            transitions_gross, 1_000_000, "step_functions_standard", "state_transition_per_million"
        )
    # Net-of-free-tier is applied at the aggregate level in analysis.py, not per-record,
    # because the 4,000/month free tier is a monthly account-level allowance. We still
    # expose the gross here; net is derived downstream with the assumption stated.

    # ---- DynamoDB (on-demand request units) ----
    ddb = record.get("dynamodb") or {}
    components["dynamodb_writes"] = priced(
        ddb.get("writes"), 1_000_000, "dynamodb", "write_request_unit_per_million"
    )
    components["dynamodb_reads"] = priced(
        ddb.get("reads"), 1_000_000, "dynamodb", "read_request_unit_per_million"
    )

    # ---- S3 requests ----
    s3 = record.get("s3") or {}
    components["s3_puts"] = priced(s3.get("puts"), 1000, "s3", "put_per_1000")
    components["s3_gets"] = priced(s3.get("gets"), 1000, "s3", "get_per_1000")

    # ---- SNS ----
    sns = record.get("sns") or {}
    components["sns_publishes"] = priced(
        sns.get("publishes"), 1_000_000, "sns", "publish_per_million"
    )

    # ---- Totals ----
    # total is None if ANY included component is None, to avoid a partial figure
    # masquerading as complete. total_known sums only the readable components.
    null_components = [k for k, v in components.items() if v is None]
    total_known = sum(v for v in components.values() if v is not None)
    total = None if null_components else total_known

    completed = record.get("workflows_completed")
    per_workflow_total = None
    per_workflow_known = None
    if isinstance(completed, int) and completed > 0:
        per_workflow_known = total_known / completed
        per_workflow_total = None if total is None else total / completed

    return {
        "arm": arm,
        "volume": record.get("volume"),
        "rep": record.get("rep"),
        "workflows_completed": completed,
        "components_usd": components,
        "null_components": null_components,
        "total_usd": total,
        "total_known_usd": total_known,
        "per_workflow_usd": per_workflow_total,
        "per_workflow_known_usd": per_workflow_known,
        "prices_needing_verification": sorted(verify_flags),
    }
