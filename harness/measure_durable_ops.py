"""Clean, controlled measurement of durable operations + bytes per workflow.

The retroactive backfill was contaminated: DurableExecutionOperations is emitted
per FunctionName and aggregates every execution active in the (padded) window, so
overlapping benchmark runs inflated it 20x.

This drives ONE workflow at a time with nothing else touching the function, over a
tight window. With a single execution in-window, the metric Sum IS that one
workflow's operations / checkpointed bytes -- a real measurement. Repeat N times
for mean +/- 95% CI.

    python measure_durable_ops.py --trials 5

Requires the etl-durable stack deployed (it still is).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config

CFG = Config(retries={"max_attempts": 10, "mode": "adaptive"})
FUNCTION = "ETLDurableOrchestrator"
RAW_BUCKET = "etl-raw-data-bucket-975050220345"
UPLOADS = "uploads/"
APPROVALS_TABLE = "etl-pending-approvals"
METADATA_TABLE = "etl-job-metadata"
WF_TYPE = "durable-functions"

_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
        8: 2.306, 9: 2.262}


def now():
    return datetime.now(timezone.utc)


def log(m):
    print(f"[{now().strftime('%H:%M:%S')}] {m}", flush=True)


def api_url(cfn):
    for e in cfn.list_exports()["Exports"]:
        if e["Name"] == "ETL-ApprovalApiUrl":
            return e["Value"]
    raise RuntimeError("ETL-ApprovalApiUrl export not found")


def clean_uploads(s3):
    p = s3.get_paginator("list_objects_v2")
    dels = [{"Key": o["Key"]} for pg in p.paginate(Bucket=RAW_BUCKET, Prefix=UPLOADS)
            for o in pg.get("Contents", [])]
    if dels:
        s3.delete_objects(Bucket=RAW_BUCKET, Delete={"Objects": dels})


def running_durable(cw):
    """Recent value of the account-level running-durable-executions gauge."""
    e = now(); s = e - timedelta(minutes=5)
    r = cw.get_metric_statistics(Namespace="AWS/Lambda",
        MetricName="ApproximateRunningDurableExecutions", Dimensions=[],
        StartTime=s, EndTime=e, Period=60, Statistics=["Maximum"])
    return max((p["Maximum"] for p in r["Datapoints"]), default=0)


def wait_idle(cw, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if running_durable(cw) == 0:
            return True
        log("  waiting for durable idle...")
        time.sleep(15)
    return False


def pending_jobs(ddb):
    ids = []
    for pg in ddb.get_paginator("scan").paginate(
        TableName=APPROVALS_TABLE, FilterExpression="#s=:p AND #w=:t",
        ExpressionAttributeNames={"#s": "status", "#w": "workflowType"},
        ExpressionAttributeValues={":p": {"S": "pending"}, ":t": {"S": WF_TYPE}},
        ProjectionExpression="jobId"):
        ids += [i["jobId"]["S"] for i in pg.get("Items", [])]
    return ids


def approve(url, jid):
    body = json.dumps({"reviewer": "ops-measure", "reason": "measure"}).encode()
    req = urllib.request.Request(f"{url}/approve/{jid}", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=30).read()


def completed_since(ddb, since_iso):
    n = 0
    for pg in ddb.get_paginator("scan").paginate(
        TableName=METADATA_TABLE, FilterExpression="#s=:c AND #t>=:since",
        ExpressionAttributeNames={"#s": "status", "#t": "timestamp"},
        ExpressionAttributeValues={":c": {"S": "COMPLETED"}, ":since": {"S": since_iso}},
        ProjectionExpression="jobId"):
        n += len(pg.get("Items", []))
    return n


def metric_sum(cw, metric, start, end):
    r = cw.get_metric_statistics(Namespace="AWS/Lambda", MetricName=metric,
        Dimensions=[{"Name": "FunctionName", "Value": FUNCTION}],
        StartTime=start, EndTime=end, Period=60, Statistics=["Sum"])
    pts = r["Datapoints"]
    return sum(p["Sum"] for p in pts) if pts else None


def mean_ci(xs):
    n = len(xs)
    if n == 0:
        return None, None, None
    m = sum(xs) / n
    if n == 1:
        return m, 0.0, None
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    t = _T95.get(n - 1, 1.96)
    return m, sd, t * sd / math.sqrt(n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=5)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--settle", type=int, default=240, help="metric publication settle (s)")
    ap.add_argument("--out", default="durable_ops_measured.json")
    args = ap.parse_args()

    s3 = boto3.client("s3", region_name=args.region, config=CFG)
    ddb = boto3.client("dynamodb", region_name=args.region, config=CFG)
    cw = boto3.client("cloudwatch", region_name=args.region, config=CFG)
    cfn = boto3.client("cloudformation", region_name=args.region, config=CFG)
    url = api_url(cfn)
    log(f"approval api: {url}")

    trials = []
    for i in range(1, args.trials + 1):
        log(f"=== trial {i}/{args.trials} ===")
        clean_uploads(s3)
        wait_idle(cw)                       # ensure nothing else durable is running
        time.sleep(10)

        t_start = now()
        since_iso = t_start.replace(tzinfo=None).isoformat()
        key = f"{UPLOADS}measure_{t_start.strftime('%Y%m%d%H%M%S')}.csv"
        csv = "id,name,date,amount,quantity,region\n" + \
              "\n".join(f"{n},Widget,2025-01-01,10.0,1,US-East" for n in range(1, 101))
        s3.put_object(Bucket=RAW_BUCKET, Key=key, Body=csv.encode())
        log(f"  uploaded 1 workflow ({key})")

        # wait for the single approval to appear, approve it
        jid = None
        for _ in range(60):
            ids = pending_jobs(ddb)
            if ids:
                jid = ids[0]; break
            time.sleep(5)
        if not jid:
            log("  WARN no pending approval appeared; skipping trial")
            continue
        approve(url, jid)
        log(f"  approved {jid}")

        # wait for completion
        for _ in range(60):
            if completed_since(ddb, since_iso) >= 1:
                break
            time.sleep(5)
        wait_idle(cw)
        t_end = now()

        log(f"  settling {args.settle}s for durable metric publication")
        time.sleep(args.settle)

        ops = metric_sum(cw, "DurableExecutionOperations", t_start - timedelta(seconds=30), t_end + timedelta(seconds=args.settle))
        byt = metric_sum(cw, "DurableExecutionStorageWrittenBytes", t_start - timedelta(seconds=30), t_end + timedelta(seconds=args.settle))
        started = metric_sum(cw, "DurableExecutionStarted", t_start - timedelta(seconds=30), t_end + timedelta(seconds=args.settle))
        log(f"  trial {i}: started={started} ops={ops} bytes={byt}")
        trials.append({"trial": i, "jobId": jid,
                       "window": {"start": t_start.isoformat(), "end": t_end.isoformat()},
                       "durable_executions_started": started,
                       "operations": ops, "bytes_written": byt})

    # Keep only clean trials: exactly one execution started in-window.
    clean = [t for t in trials if t["durable_executions_started"] == 1
             and t["operations"] is not None and t["bytes_written"] is not None]
    ops_list = [t["operations"] for t in clean]
    byt_list = [t["bytes_written"] for t in clean]

    with open(args.out, "w") as f:
        json.dump({"trials": trials, "clean_n": len(clean)}, f, indent=2, default=str)
    log(f"wrote {args.out}")

    print("\n=== MEASURED (isolated, 1 workflow/window) ===")
    print(f"  clean trials (exactly 1 execution in-window): {len(clean)}/{len(trials)}")
    mo, sdo, cio = mean_ci(ops_list)
    mb, sdb, cib = mean_ci(byt_list)
    if mo is not None:
        ci = f"+/- {cio:.2f}" if cio is not None else "(n<2)"
        print(f"  operations per workflow:   {mo:.2f} {ci}  (sd {sdo:.2f}, n={len(ops_list)})")
        print(f"     paper derived 9 -> {'CONFIRMED' if abs(mo-9) < 1 else 'MEASURED value differs; use it'}")
    if mb is not None:
        ci = f"+/- {cib:.0f}" if cib is not None else "(n<2)"
        print(f"  bytes written per workflow: {mb:,.0f} ({mb/1024:.1f} KB) {ci}  (n={len(byt_list)})")
        print(f"     paper derived ~21 KB -> {'CONFIRMED' if abs(mb/1024-21) < 5 else 'MEASURED value differs; use it'}")


if __name__ == "__main__":
    main()
