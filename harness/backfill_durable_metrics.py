"""Retroactively measure durable-execution operations and bytes written.

WHY THIS EXISTS
---------------
The paper's weakest claim is that the durable-operation count (9/workflow) and
checkpointed payload (~21 KB/workflow) are DERIVED from AWS's documented
operation table applied to the handler's code path -- not measured.

But every record in results/ carries the exact CloudWatch window its run
occupied, and CloudWatch retains metrics for 15 months. If AWS emitted
DurableExecutionOperations and DurableExecutionStorageWrittenBytes during those
windows, we can read them now and convert a derived number into a measured one
WITHOUT RE-RUNNING ANYTHING.

    python backfill_durable_metrics.py --discover     # what metrics exist at all
    python backfill_durable_metrics.py                # backfill all durable runs

Run --discover FIRST. The CloudWatch dimensions for these metrics are not
documented by AWS, so the assumed dimension set may be wrong; discovery tells
you the truth in one call.
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

CFG = Config(retries={"max_attempts": 10, "mode": "adaptive"})

METRICS = [
    "DurableExecutionOperations",
    "DurableExecutionStorageWrittenBytes",
]

# The durable orchestrator from the original experiment.
DEFAULT_FUNCTION = "ETLDurableOrchestrator"


def discover(cw, function_name: str) -> None:
    """List every AWS/Lambda metric whose name mentions Durable, with dimensions.

    This is the ground truth for what we can actually query. If nothing comes
    back, the metrics were never emitted for this function and the backfill is
    impossible -- report that plainly rather than guessing.
    """
    print("=== discovering durable metrics in AWS/Lambda ===\n")
    found = {}
    paginator = cw.get_paginator("list_metrics")
    for page in paginator.paginate(Namespace="AWS/Lambda"):
        for m in page["Metrics"]:
            if "Durable" not in m["MetricName"]:
                continue
            dims = tuple(sorted((d["Name"], d["Value"]) for d in m["Dimensions"]))
            found.setdefault(m["MetricName"], set()).add(dims)

    if not found:
        print("  NO durable metrics found in AWS/Lambda.")
        print("  Either they were never emitted, or they live in another namespace.")
        print("  Try:  aws cloudwatch list-metrics --namespace AWS/Lambda "
              "| grep -i durable")
        return

    for name, dimsets in sorted(found.items()):
        print(f"  {name}")
        for dims in sorted(dimsets)[:8]:
            rendered = ", ".join(f"{k}={v}" for k, v in dims) or "(no dimensions)"
            marker = "  <-- our function" if any(
                v == function_name for _, v in dims) else ""
            print(f"      dims: {rendered}{marker}")
        if len(dimsets) > 8:
            print(f"      ... and {len(dimsets) - 8} more dimension sets")
        print()


def candidate_dimension_sets(function_name: str) -> list[list[dict]]:
    """Dimension sets to try, most likely first.

    AWS does not document the dimensions for durable metrics, so we probe.
    """
    return [
        [{"Name": "FunctionName", "Value": function_name}],
        [{"Name": "FunctionName", "Value": function_name},
         {"Name": "Resource", "Value": function_name}],
        [{"Name": "Resource", "Value": function_name}],
        [],  # aggregate across the account
    ]


def query(cw, metric: str, dims: list[dict], start: datetime, end: datetime):
    """Sum a metric over a window. Returns None if there are no datapoints."""
    try:
        resp = cw.get_metric_statistics(
            Namespace="AWS/Lambda",
            MetricName=metric,
            Dimensions=dims,
            StartTime=start,
            EndTime=end,
            Period=300,
            Statistics=["Sum", "SampleCount", "Maximum"],
        )
    except ClientError as exc:
        print(f"    ! {metric}: {exc}", file=sys.stderr)
        return None
    pts = resp.get("Datapoints", [])
    if not pts:
        return None
    return {
        "sum": sum(p["Sum"] for p in pts),
        "samples": sum(p.get("SampleCount", 0) for p in pts),
        "max": max(p["Maximum"] for p in pts),
        "datapoints": len(pts),
    }


def parse_window(rec: dict) -> tuple[datetime, datetime]:
    """Window from the result record, padded for metric publication lag."""
    w = rec["window"]
    s = datetime.fromisoformat(w["start"].replace("Z", "+00:00"))
    e = datetime.fromisoformat(w["end"].replace("Z", "+00:00"))
    return s - timedelta(minutes=5), e + timedelta(minutes=15)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="../repo/harness/results",
                    help="directory of the original 60 result records")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--function", default=DEFAULT_FUNCTION)
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--out", default="durable_metrics_backfill.json")
    args = ap.parse_args()

    cw = boto3.client("cloudwatch", region_name=args.region, config=CFG)

    if args.discover:
        discover(cw, args.function)
        return 0

    files = sorted(glob.glob(str(Path(args.results) / "durable-*.json")))
    if not files:
        print(f"no durable records under {args.results}")
        return 1
    print(f"backfilling {len(files)} durable runs\n")

    # Settle on a dimension set using the first record, then reuse it.
    first = json.loads(Path(files[0]).read_text())
    s, e = parse_window(first)
    chosen = None
    for dims in candidate_dimension_sets(args.function):
        if query(cw, METRICS[0], dims, s, e) is not None:
            chosen = dims
            break
    if chosen is None:
        print("  FAILED: no dimension set returns data for "
              f"{METRICS[0]} in the first run's window.")
        print("  The metrics were likely never emitted. Run --discover.")
        print("  The paper must keep the derived-value framing.")
        return 2
    rendered = ", ".join(f"{d['Name']}={d['Value']}" for d in chosen) or "(aggregate)"
    print(f"  using dimensions: {rendered}\n")

    out = []
    per_wf_ops, per_wf_bytes = [], []
    for f in files:
        rec = json.loads(Path(f).read_text())
        s, e = parse_window(rec)
        completed = rec.get("workflows_completed") or rec.get("volume")
        row = {"run_id": rec["run_id"], "volume": rec["volume"],
               "workflows_completed": completed}
        for m in METRICS:
            r = query(cw, m, chosen, s, e)
            row[m] = r
            if r and completed:
                if m == METRICS[0]:
                    row["ops_per_workflow"] = r["sum"] / completed
                    per_wf_ops.append(row["ops_per_workflow"])
                else:
                    row["bytes_per_workflow"] = r["sum"] / completed
                    per_wf_bytes.append(row["bytes_per_workflow"])
        ops = row.get("ops_per_workflow")
        byt = row.get("bytes_per_workflow")
        print(f"  {rec['run_id']:<22} ops/wf={ops if ops is None else f'{ops:8.2f}'}"
              f"   bytes/wf={byt if byt is None else f'{byt:10.0f}'}")
        out.append(row)

    Path(args.out).write_text(json.dumps(out, indent=2, default=str))
    print(f"\nwrote {args.out}")

    print("\n=== SUMMARY (this is what goes in the paper) ===")
    if per_wf_ops:
        m = statistics.mean(per_wf_ops)
        sd = statistics.stdev(per_wf_ops) if len(per_wf_ops) > 1 else 0.0
        print(f"  MEASURED operations per workflow: {m:.3f} (sd {sd:.3f}, "
              f"n={len(per_wf_ops)} runs)")
        print(f"  paper currently assumes 9 -> {'CONFIRMED' if abs(m-9) < 0.5 else 'DIFFERS, use the measured value'}")
    else:
        print("  operations: NO DATA -- keep the derived framing")
    if per_wf_bytes:
        m = statistics.mean(per_wf_bytes)
        print(f"  MEASURED bytes written per workflow: {m:,.0f} "
              f"({m/1024:.1f} KB), n={len(per_wf_bytes)} runs")
        print(f"  paper currently assumes ~21 KB -> "
              f"{'CONFIRMED' if abs(m/1024 - 21) < 5 else 'DIFFERS, use the measured value'}")
    else:
        print("  bytes written: NO DATA -- keep the derived framing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
