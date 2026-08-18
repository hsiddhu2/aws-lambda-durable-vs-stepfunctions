"""Offline unit tests for cost_model.py and analysis.py.

No AWS. Synthetic records are written to a throwaway /tmp dir and DELETED afterwards;
they never touch harness/results/. Run: `python tests/test_offline.py`.
Exit code 0 = all assertions passed.
"""

import math
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cost_model
import analysis


# Deterministic pricing (verify flags on to also exercise the flag surfacing).
PRICING = {
    "snapshot_date": "2026-08-12",
    "lambda": {"arm64": {
        "request_per_million": {"value": 0.20, "verify": True},
        "gb_second": {"value": 0.0000133334, "verify": True},
    }},
    "step_functions_standard": {
        "state_transition_per_million": {"value": 25.00, "verify": True},
        "free_tier_transitions_per_month": 4000,
    },
    "dynamodb": {
        "write_request_unit_per_million": {"value": 1.25, "verify": True},
        "read_request_unit_per_million": {"value": 0.25, "verify": True},
    },
    "s3": {"put_per_1000": {"value": 0.005, "verify": True},
           "get_per_1000": {"value": 0.0004, "verify": True}},
    "sns": {"publish_per_million": {"value": 0.50, "verify": True}},
}

PASSED = 0


def check(name, cond):
    global PASSED
    assert cond, f"FAILED: {name}"
    PASSED += 1
    print(f"  ok: {name}")


def approx(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) <= tol * max(1, abs(b))


def make_record(arm, volume, rep, invocations, gb_seconds, transitions_gross=None,
                completed=None, ddb_writes=0.0, ddb_reads=0.0, s3_puts=None, s3_gets=None,
                sns_pub=0.0):
    st = None
    if transitions_gross is not None:
        st = {"gross": transitions_gross, "sampled_executions": volume,
              "sample_mean_per_exec": transitions_gross / volume if volume else 0,
              "sample_stdev": 0.0}
    return {
        "arm": arm, "volume": volume, "rep": rep,
        "workflows_completed": completed if completed is not None else volume,
        "invocations": invocations, "gb_seconds": gb_seconds,
        "state_transitions": st,
        "dynamodb": {"writes": ddb_writes, "reads": ddb_reads},
        "s3": {"puts": s3_puts, "gets": s3_gets},
        "sns": {"publishes": sns_pub},
    }


def test_cost_model_math():
    print("test_cost_model_math")
    rec = make_record("sfn_standard", 5, 1, invocations=25, gb_seconds=10.0,
                      transitions_gross=35, completed=5, ddb_writes=10, ddb_reads=5,
                      s3_puts=10, s3_gets=5, sns_pub=5)
    out = cost_model.compute_cost(rec, PRICING)
    c = out["components_usd"]
    check("lambda_requests", approx(c["lambda_requests"], 25 * 0.20 / 1_000_000))
    check("lambda_gb_seconds", approx(c["lambda_gb_seconds"], 10.0 * 0.0000133334))
    check("sfn_transitions_gross", approx(c["sfn_transitions_gross"], 35 * 25.0 / 1_000_000))
    check("dynamodb_writes", approx(c["dynamodb_writes"], 10 * 1.25 / 1_000_000))
    check("s3_puts", approx(c["s3_puts"], 10 * 0.005 / 1000))
    check("sns", approx(c["sns_publishes"], 5 * 0.50 / 1_000_000))
    check("no null components", out["null_components"] == [])
    check("total is sum", approx(out["total_usd"], sum(c.values())))
    check("per_workflow", approx(out["per_workflow_usd"], out["total_usd"] / 5))
    check("verify flags surfaced", len(out["prices_needing_verification"]) > 0)


def test_null_propagation():
    print("test_null_propagation")
    # Unreadable gb_seconds and s3 -> those components null, total null, but total_known present.
    rec = make_record("durable", 5, 1, invocations=9, gb_seconds=None,
                      transitions_gross=None, completed=5, s3_puts=None, s3_gets=None)
    out = cost_model.compute_cost(rec, PRICING)
    check("gb_seconds null -> component null", out["components_usd"]["lambda_gb_seconds"] is None)
    check("total null when a component null", out["total_usd"] is None)
    check("total_known still computed", out["total_known_usd"] is not None)
    check("null_components listed", "lambda_gb_seconds" in out["null_components"])
    check("per_workflow_total null", out["per_workflow_usd"] is None)
    check("per_workflow_known present", out["per_workflow_known_usd"] is not None)


def test_mean_ci():
    print("test_mean_ci")
    r = analysis.mean_ci([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    check("mean", approx(r["mean"], 5.0))
    check("stdev sample", approx(r["stdev"], 2.13808993, tol=1e-6))
    # n=8 -> t=2.365; half = t*sd/sqrt(n)
    expected_half = 2.365 * r["stdev"] / math.sqrt(8)
    check("ci95 halfwidth", approx(r["ci95_halfwidth"], expected_half, tol=1e-6))
    check("ci bounds", approx(r["ci95_low"], 5.0 - expected_half) and
          approx(r["ci95_high"], 5.0 + expected_half))
    check("n=1 -> ci None", analysis.mean_ci([3.0])["ci95_halfwidth"] is None)
    check("n=0 -> mean None", analysis.mean_ci([])["mean"] is None)


def test_aggregate_and_freetier():
    print("test_aggregate_and_freetier")
    # 10 sfn reps at volume 5, 7 transitions each => gross_total = 350 < 4000 free tier.
    recs = [make_record("sfn_standard", 5, i + 1, invocations=25, gb_seconds=10.0,
                        transitions_gross=35, completed=5) for i in range(10)]
    rows = analysis.aggregate(recs, PRICING)
    row = rows[0]
    check("reps counted", row["reps_total"] == 10 and row["reps_excluded_null"] == 0)
    check("per_workflow known CI present", row["per_workflow_known"]["ci95_halfwidth"] is not None)
    check("gross transitions summed", approx(row["sfn_transitions_gross_total"], 350))
    check("gross cost > 0", row["sfn_transition_cost_gross_usd"] > 0)
    check("net cost 0 under free tier", approx(row["sfn_transition_cost_net_freetier_usd"], 0.0))

    # Now exceed free tier: 900 per rep * 10 = 9000 > 4000 -> net billable 5000.
    recs2 = [make_record("sfn_standard", 100, i + 1, invocations=500, gb_seconds=200.0,
                         transitions_gross=900, completed=100) for i in range(10)]
    row2 = analysis.aggregate(recs2, PRICING)[0]
    check("net > 0 above free tier",
          approx(row2["sfn_transition_cost_net_freetier_usd"], (9000 - 4000) * 25.0 / 1_000_000))
    check("gross > net", row2["sfn_transition_cost_gross_usd"] > row2["sfn_transition_cost_net_freetier_usd"])


def test_null_rep_excluded():
    print("test_null_rep_excluded")
    # A rep with a null component (gb_seconds) still yields a KNOWN cost -> NOT excluded,
    # but it is not counted among reps_with_full_total.
    good = make_record("durable", 5, 1, invocations=9, gb_seconds=10.0, completed=5,
                       s3_puts=10, s3_gets=5)
    partial = make_record("durable", 5, 2, invocations=9, gb_seconds=None, completed=5,
                          s3_puts=10, s3_gets=5)
    rows = analysis.aggregate([good, partial], PRICING)
    row = rows[0]
    check("partial rep still in known CI", row["per_workflow_known"]["n"] == 2)
    check("partial rep not in full total", row["reps_with_full_total"] == 1)
    check("no rep excluded from known", row["reps_excluded_null"] == 0)
    check("null component surfaced", "lambda_gb_seconds" in row["null_components_union"])

    # A rep with ZERO completions has undefined known per-workflow cost -> excluded.
    zero = make_record("durable", 5, 3, invocations=0, gb_seconds=0.0, completed=0,
                       s3_puts=0, s3_gets=0)
    rows2 = analysis.aggregate([good, zero], PRICING)
    check("zero-completion rep excluded", rows2[0]["reps_excluded_null"] == 1)


def test_csv_written_to_tmp_then_deleted():
    print("test_csv_written_to_tmp_then_deleted")
    tmp = tempfile.mkdtemp(prefix="harness_offline_")
    try:
        recs = [make_record("durable", 5, i + 1, invocations=9, gb_seconds=10.0 + i * 0.1,
                            completed=5) for i in range(10)]
        rows = analysis.aggregate(recs, PRICING)
        path = os.path.join(tmp, "summary.csv")
        analysis.write_summary_csv(rows, path)
        check("summary.csv exists in tmp", os.path.exists(path))
        with open(path) as f:
            content = f.read()
        check("csv has header", "per_workflow_known_mean_usd" in content)
        # Ensure nothing was written under the real results dir by this test.
        real_results = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "results")
        check("no synthetic file leaked to results/",
              not os.path.exists(os.path.join(real_results, "summary.csv")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        check("tmp cleaned up", not os.path.exists(tmp))


if __name__ == "__main__":
    test_cost_model_math()
    test_null_propagation()
    test_mean_ci()
    test_aggregate_and_freetier()
    test_null_rep_excluded()
    test_csv_written_to_tmp_then_deleted()
    print(f"\nALL OFFLINE TESTS PASSED ({PASSED} assertions)")
