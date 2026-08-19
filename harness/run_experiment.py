"""run_experiment.py — drive real ETL workflows on AWS and collect measured counters.

Per (arm x volume x rep):
  1. clean the arm's S3 uploads/ prefix (no cross-rep bleed);
  2. pin reserved concurrency = 50 on every arm function;
  3. generate `volume` CSVs (throwaway dir) and upload to uploads/;
  4. trigger  (durable: S3 event auto-invokes; sfn: start one execution per file);
  5. wait for `volume` pending approvals to appear, then auto-approve via the API;
  6. wait (bounded, with settle fallback) for terminal completion;
  7. settle, then collect CloudWatch/SFN counters -> results/<arm>-<volume>-r<rep>.json.

Flags: --config, --only-arm, --only-volume, --dry-run (volume=5, 1 rep, both arms).

INTEGRITY: this orchestrator only creates uploads, pins reserved concurrency, triggers,
and approves. It never edits templates, business logic, memory, or stacks, and it never
writes a synthetic number into results/. Counters come from collect_metrics.py.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

import boto3
import yaml
from botocore.config import Config

from collect_metrics import MetricCollector, build_record

HERE = os.path.dirname(os.path.abspath(__file__))

# Adaptive retry so Step Functions ListExecutions and DynamoDB scans survive API-level
# ThrottlingException at high volume (thousands of executions / large tables) instead
# of raising and failing the rep.
_RETRY = Config(retries={"max_attempts": 12, "mode": "adaptive"})


def now() -> datetime:
    return datetime.now(timezone.utc)


def log(msg: str) -> None:
    print(f"[{now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ----------------------------- resolution -----------------------------

def stack_outputs(cfn, stack: str) -> dict:
    try:
        outs = cfn.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", [])
        return {o["OutputKey"]: o["OutputValue"] for o in outs}
    except Exception as e:
        log(f"WARN could not read outputs for {stack}: {e}")
        return {}


def resolve_bucket(cfn, arm_cfg: dict) -> str:
    outs = stack_outputs(cfn, arm_cfg["stack"])
    val = outs.get(arm_cfg["raw_bucket_output"])
    if val:
        return val.split(":::")[-1] if val.startswith("arn:") else val
    return arm_cfg["raw_bucket_fallback"]


def resolve_sfn_arn(cfn, arm_cfg: dict) -> str:
    outs = stack_outputs(cfn, arm_cfg["stack"])
    return outs.get(arm_cfg.get("state_machine_output", ""), arm_cfg.get("state_machine_arn_fallback"))


def resolve_sfn_functions(lam, prefixes: list[str]) -> list[str]:
    """Map each configured prefix to its live full function name (random suffix)."""
    names = []
    paginator = lam.get_paginator("list_functions")
    all_fns = []
    for page in paginator.paginate():
        all_fns += [f["FunctionName"] for f in page["Functions"]]
    for pfx in prefixes:
        match = [n for n in all_fns if n.startswith(pfx)]
        if not match:
            raise RuntimeError(f"No live Lambda found for prefix {pfx}")
        names.append(sorted(match)[0])
    return names


def resolve_api_url(cfn, appr_cfg: dict) -> str:
    try:
        exps = cfn.list_exports()["Exports"]
        for e in exps:
            if e["Name"] == appr_cfg["api_url_export"]:
                return e["Value"]
    except Exception:
        pass
    return appr_cfg["api_url_fallback"]


# ----------------------------- infra actions -----------------------------

def clean_uploads(s3, bucket: str, prefix: str) -> None:
    try:
        paginator = s3.get_paginator("list_objects_v2")
        to_delete = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                to_delete.append({"Key": obj["Key"]})
        for i in range(0, len(to_delete), 1000):
            s3.delete_objects(Bucket=bucket, Delete={"Objects": to_delete[i:i + 1000]})
        log(f"cleaned {len(to_delete)} objects under s3://{bucket}/{prefix}")
    except Exception as e:
        log(f"WARN clean_uploads failed on {bucket}/{prefix}: {e}")


def pin_concurrency(lam, function_names: list[str], reserved: int) -> None:
    for name in function_names:
        try:
            lam.put_function_concurrency(FunctionName=name, ReservedConcurrentExecutions=reserved)
            log(f"pinned reserved concurrency={reserved} on {name}")
        except Exception as e:
            log(f"WARN could not pin concurrency on {name}: {e}")


def generate_csvs(count: int, out_dir: str, records: int = 100) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    script = os.path.join(HERE, "..", "scripts", "generate_test_data.py")
    subprocess.run(
        [sys.executable, script, "--count", str(count), "--records", str(records),
         "--output-dir", out_dir],
        check=True, capture_output=True,
    )
    return sorted(
        os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".csv")
    )


def upload_csvs(s3, bucket: str, prefix: str, files: list[str],
                rate_per_sec: float = 0) -> list[str]:
    """Upload CSVs. When rate_per_sec > 0, pace the puts (used for the durable arm where
    the S3 upload IS the trigger, so pacing uploads paces the workflow injection)."""
    keys = []
    delay = (1.0 / rate_per_sec) if rate_per_sec and rate_per_sec > 0 else 0
    for path in files:
        key = prefix + os.path.basename(path)
        s3.upload_file(path, bucket, key)
        keys.append(key)
        if delay:
            time.sleep(delay)
    log(f"uploaded {len(keys)} CSVs to s3://{bucket}/{prefix}"
        + (f" at ~{rate_per_sec}/s" if delay else ""))
    return keys


def start_sfn_executions(sfn, arn: str, bucket: str, keys: list[str], run_tag: str,
                         rate_per_sec: float = 0) -> int:
    """Start one execution per key. When rate_per_sec > 0, pace the starts so concurrent
    Lambda demand stays under reserved concurrency (avoids throttle -> retry inflation)."""
    started = 0
    delay = (1.0 / rate_per_sec) if rate_per_sec and rate_per_sec > 0 else 0
    for i, key in enumerate(keys):
        name = f"bench-{run_tag}-{i:05d}"
        try:
            sfn.start_execution(
                stateMachineArn=arn, name=name,
                input=json.dumps({"bucket": bucket, "key": key}),
            )
            started += 1
        except Exception as e:
            log(f"WARN start_execution failed for {key}: {e}")
        if delay:
            time.sleep(delay)
    log(f"started {started}/{len(keys)} Step Functions executions"
        + (f" at ~{rate_per_sec}/s" if delay else ""))
    return started


# ----------------------------- approval + completion -----------------------------

def scan_pending(ddb, table: str, status_attr: str, pending: str,
                 wf_attr: str, wf_type: str) -> list[str]:
    job_ids = []
    paginator = ddb.get_paginator("scan")
    for page in paginator.paginate(
        TableName=table,
        FilterExpression="#s = :p AND #w = :t",
        ExpressionAttributeNames={"#s": status_attr, "#w": wf_attr},
        ExpressionAttributeValues={":p": {"S": pending}, ":t": {"S": wf_type}},
        ProjectionExpression="jobId",
    ):
        for item in page.get("Items", []):
            job_ids.append(item["jobId"]["S"])
    return job_ids


def wait_for_pending(ddb, cfg, arm_cfg, target: int, timeout: int, interval: int) -> list[str]:
    deadline = time.time() + timeout
    appr = cfg["approval"]
    seen: set[str] = set()
    while time.time() < deadline:
        ids = scan_pending(ddb, appr["table"], appr["status_attr"], appr["pending_value"],
                           appr["workflow_type_attr"], arm_cfg["workflow_type"])
        seen.update(ids)
        log(f"pending approvals for {arm_cfg['workflow_type']}: {len(seen)}/{target}")
        if len(seen) >= target:
            return list(seen)
        time.sleep(interval)
    log(f"WARN approval timeout: {len(seen)}/{target} appeared")
    return list(seen)


def approve(api_url: str, job_id: str) -> bool:
    body = json.dumps({"reviewer": "benchmark-harness", "reason": "automated benchmark approval"}).encode()
    req = urllib.request.Request(
        f"{api_url}/approve/{job_id}", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except Exception as e:
        log(f"WARN approve failed for {job_id}: {e}")
        return False


def approve_all(api_url: str, job_ids: list[str], rate_per_sec: float = 50,
                workers: int = 20) -> int:
    """Approve jobs with a RATE-LIMITED thread pool.

    Two failure modes to avoid: the old serial 0.2s loop cost ~33 min at 10k jobs
    (too slow); an unbounded pool approves all 10k at once, so all workflows resume
    simultaneously and blow past reserved concurrency -> Lambda throttling (durable
    arm saw 162). Pacing SUBMISSIONS at rate_per_sec (matched to the injection rate)
    keeps resume concurrency ~= rate*compute < reserved, while the pool absorbs
    per-request latency. ~10k jobs approve in ~10000/rate seconds with zero throttles.
    Approval speed does not affect any per-workflow cost counter."""
    if not job_ids:
        log("approved 0/0 jobs")
        return 0
    from concurrent.futures import ThreadPoolExecutor
    delay = (1.0 / rate_per_sec) if rate_per_sec and rate_per_sec > 0 else 0
    futures = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for jid in job_ids:
            futures.append(ex.submit(approve, api_url, jid))
            if delay:
                time.sleep(delay)
        ok = sum(1 for f in futures if f.result())
    log(f"approved {ok}/{len(job_ids)} jobs (rate~{rate_per_sec}/s, {workers}-way)")
    return ok


def count_durable_completed(ddb, table: str, status_attr: str, terminal: str,
                            ts_attr: str, since_iso: str) -> int:
    n = 0
    paginator = ddb.get_paginator("scan")
    for page in paginator.paginate(
        TableName=table,
        FilterExpression="#s = :c AND #t >= :since",
        ExpressionAttributeNames={"#s": status_attr, "#t": ts_attr},
        ExpressionAttributeValues={":c": {"S": terminal}, ":since": {"S": since_iso}},
        ProjectionExpression="jobId",
    ):
        n += len(page.get("Items", []))
    return n


def count_sfn_running(sfn, arn: str) -> int:
    n = 0
    for page in sfn.get_paginator("list_executions").paginate(
            stateMachineArn=arn, statusFilter="RUNNING"):
        n += len(page["executions"])
    return n


def wait_sfn_idle(sfn, arn: str, timeout: int, interval: int, label: str) -> bool:
    """Wait until the state machine has 0 RUNNING executions. Used to ISOLATE each sfn
    rep's metric window: draining prior/orphaned executions before a window opens (and
    before metric collection) stops their Lambda invocations from bleeding into this
    rep's counts. Returns True if idle reached."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = count_sfn_running(sfn, arn)
        if r == 0:
            return True
        log(f"{label}: waiting sfn idle ({r} running)")
        time.sleep(interval)
    log(f"{label}: WARN sfn not idle within {timeout}s")
    return False


def count_sfn_succeeded(sfn, arn: str, since: datetime) -> int:
    n = 0
    paginator = sfn.get_paginator("list_executions")
    for page in paginator.paginate(stateMachineArn=arn, statusFilter="SUCCEEDED"):
        for ex in page["executions"]:
            sd = ex["startDate"]
            sd = sd if sd.tzinfo else sd.replace(tzinfo=timezone.utc)
            if sd >= since:
                n += 1
        if page["executions"]:
            last = page["executions"][-1]["startDate"]
            last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
            if last < since:
                break
    return n


def wait_for_completion(arm: str, arm_cfg, clients, target: int, since: datetime,
                        since_iso: str, timeout: int, interval: int) -> int:
    deadline = time.time() + timeout
    last = 0
    stable_since = None
    while time.time() < deadline:
        if arm == "durable":
            c = arm_cfg["completion"]
            done = count_durable_completed(
                clients["ddb"], c["table"], c["status_attr"], c["terminal_value"],
                c["timestamp_attr"], since_iso)
        else:
            done = count_sfn_succeeded(clients["sfn"], clients["sfn_arn"], since)
        log(f"completed {arm}: {done}/{target}")
        if done >= target:
            return done
        # settle fallback: if progress has stalled for a while, accept what we have
        if done == last:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since > max(120, interval * 6):
                log(f"WARN completion settled at {done}/{target} (no progress); accepting")
                return done
        else:
            stable_since = None
            last = done
        time.sleep(interval)
    log(f"WARN completion timeout: {last}/{target}")
    return last


# ----------------------------- per-run orchestration -----------------------------

def run_one(arm: str, volume: int, rep: int, cfg: dict, resolved: dict, clients: dict) -> dict:
    arm_cfg = cfg["arms"][arm]
    to = cfg["timeouts"]
    run_id = f"{arm}-{volume}-r{rep}"
    run_tag = now().strftime("%Y%m%d%H%M%S")
    log(f"===== RUN {run_id} =====")

    bucket = resolved[arm]["bucket"]
    fns = resolved[arm]["functions"]

    # 1. clean uploads
    clean_uploads(clients["s3"], bucket, arm_cfg["uploads_prefix"])
    # 2. pin concurrency
    pin_concurrency(clients["lam"], fns, cfg["reserved_concurrency"])

    # 3. generate + upload
    rep_dir = os.path.join(HERE, cfg["test_data_dir"], run_id)
    files = generate_csvs(volume, rep_dir)

    # Window isolation (sfn): drain any prior/orphaned RUNNING executions to 0 BEFORE the
    # window opens, so their Lambda invocations cannot bleed into this rep's counts.
    if arm == "sfn_standard":
        wait_sfn_idle(clients["sfn"], resolved[arm]["sfn_arn"],
                      to["completion"], to["poll_interval"], f"{run_id} pre-window")

    window_start = now()
    # etl-job-metadata.timestamp is written NAIVE (datetime.utcnow().isoformat()), so the
    # durable completion scan must compare against a naive UTC ISO string. A small backdate
    # guards against sub-second clock skew between this host and the Lambda.
    since_iso = (window_start.replace(tzinfo=None)).isoformat()
    rate = cfg.get("inject_rate_per_sec", 0)

    # 3b + 4. trigger, RATE-LIMITED so concurrent demand stays under reserved concurrency.
    if arm_cfg["trigger"] == "start_execution":
        # sfn: uploading does NOT trigger (no S3 event), so upload fast, then pace the starts.
        keys = upload_csvs(clients["s3"], bucket, arm_cfg["uploads_prefix"], files)
        start_sfn_executions(clients["sfn"], resolved[arm]["sfn_arn"], bucket, keys,
                             run_tag, rate_per_sec=rate)
    else:
        # durable: the S3 upload IS the trigger, so pace the uploads themselves.
        keys = upload_csvs(clients["s3"], bucket, arm_cfg["uploads_prefix"], files,
                           rate_per_sec=rate)

    # 5. wait for approvals + approve
    pending = wait_for_pending(clients["ddb_res"], cfg, arm_cfg, volume,
                               to["approval_appear"], to["poll_interval"])
    approve_all(resolved["api_url"], pending, rate_per_sec=cfg.get("inject_rate_per_sec", 50))

    # 6. wait for completion
    completed = wait_for_completion(
        arm, arm_cfg, {**clients, "sfn_arn": resolved[arm].get("sfn_arn")},
        volume, window_start, since_iso, to["completion"], to["poll_interval"])

    # Window isolation (sfn): ensure THIS rep's executions are all finished (0 RUNNING)
    # before the window closes, so the invocation/duration sums are exactly this rep.
    if arm == "sfn_standard":
        wait_sfn_idle(clients["sfn"], resolved[arm]["sfn_arn"],
                      to["completion"], to["poll_interval"], f"{run_id} post-run")

    # 7. settle + collect. CloudWatch Lambda metrics lag several minutes behind the
    # invocation, so after an initial settle we POLL the fixed window (window_end frozen
    # once) until Invocations is readable, up to metrics_wait. This turns a fixed guess
    # into a real "wait until the counter exists" — nulls only remain if truly unreadable.
    log(f"settling {to['cloudwatch_settle']}s for CloudWatch metrics")
    time.sleep(to["cloudwatch_settle"])
    window_end = now()

    mc: MetricCollector = clients["collector"]
    metrics_wait = to.get("metrics_wait", 600)
    metrics_deadline = time.time() + metrics_wait
    while True:
        lam_result = mc.lambda_metrics(fns, window_start, window_end)
        if lam_result.get("invocations") is not None:
            break
        if time.time() >= metrics_deadline:
            log(f"WARN Lambda metrics still unreadable after {metrics_wait}s; leaving null")
            break
        log("Lambda metrics not yet available; polling...")
        time.sleep(to["poll_interval"])

    transitions = None
    if arm == "sfn_standard":
        transitions = mc.sfn_transitions(
            resolved[arm]["sfn_arn"], window_start, window_end, volume,
            sample_cap=arm_cfg.get("transition_sample_cap", 100))

    ddb_tables = arm_cfg.get("dynamodb_tables", [])
    if arm == "durable":
        ddb_tables = ["etl-job-metadata", cfg["approval"]["table"]]
    else:
        ddb_tables = list(ddb_tables) + [cfg["approval"]["table"]]
    ddb_metrics = mc.dynamodb_consumed(ddb_tables, window_start, window_end)

    s3_metrics = mc.s3_requests(bucket, window_start, window_end)
    sns_pub = mc.sns_publishes(cfg["approval"]["sns_topic_name"], window_start, window_end)

    throttles = lam_result.get("throttles") or 0
    if throttles > 0:
        log(f"WARN {int(throttles)} Lambda throttles in-window — retry inflation risk; "
            f"consider lowering inject_rate_per_sec or raising reserved_concurrency")

    notes = []
    if arm == "durable":
        notes.append("state_transitions is null by design: durable functions have zero SFN transitions.")
    notes.append("s3 request counts are null unless S3 request metrics are enabled on the bucket.")
    notes.append(f"lambda_throttles={int(throttles)} in-window (nonzero indicates possible "
                 f"retry inflation of invocation/transition counts).")

    record = build_record(
        arm=arm, volume=volume, rep=rep, region=cfg["region"], account=str(cfg["account"]),
        window_start=window_start, window_end=window_end, workflows_completed=completed,
        lambda_result=lam_result, transitions=transitions, dynamodb=ddb_metrics,
        s3=s3_metrics, sns_publishes=sns_pub, run_id=run_id, extra_notes=notes)

    out_dir = os.path.join(HERE, cfg["results_dir"])
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{run_id}.json")
    with open(out_path, "w") as f:
        json.dump(record, f, indent=2, default=str)
    log(f"wrote {out_path}  (completed={completed}/{volume}, null_fields={record['null_fields']})")
    return record


def resolve_all(cfg: dict, arms: list[str]) -> dict:
    region = cfg["region"]
    cfn = boto3.client("cloudformation", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    lam = boto3.client("lambda", region_name=region)
    resolved = {"api_url": resolve_api_url(cfn, cfg["approval"])}
    for arm in arms:
        arm_cfg = cfg["arms"][arm]
        entry = {"bucket": resolve_bucket(cfn, arm_cfg)}
        if arm == "durable":
            entry["functions"] = list(arm_cfg["functions"])
            entry["sfn_arn"] = None
        else:
            entry["functions"] = resolve_sfn_functions(lam, arm_cfg["function_name_prefixes"])
            entry["sfn_arn"] = resolve_sfn_arn(cfn, arm_cfg)
        resolved[arm] = entry
        log(f"resolved {arm}: bucket={entry['bucket']} functions={entry['functions']}")
    log(f"resolved approval api_url={resolved['api_url']}")
    return resolved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    ap.add_argument("--only-arm", choices=["durable", "sfn_standard"])
    ap.add_argument("--only-volume", type=int)
    ap.add_argument("--dry-run", action="store_true",
                    help="volume=5, 1 rep, both arms")
    ap.add_argument("--fresh", action="store_true",
                    help="force rerun even if a result file already exists (default resumes)")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    if args.dry_run:
        volumes = [5]
        reps = 1
        arms = ["durable", "sfn_standard"]
        log("DRY RUN: volume=5, reps=1, arms=durable+sfn_standard")
    else:
        volumes = [args.only_volume] if args.only_volume else cfg["volumes"]
        reps = cfg["repetitions"]
        arms = [args.only_arm] if args.only_arm else list(cfg["arms"].keys())

    region = cfg["region"]
    resolved = resolve_all(cfg, arms)
    clients = {
        "s3": boto3.client("s3", region_name=region, config=_RETRY),
        "lam": boto3.client("lambda", region_name=region, config=_RETRY),
        "sfn": boto3.client("stepfunctions", region_name=region, config=_RETRY),
        "ddb": boto3.client("dynamodb", region_name=region, config=_RETRY),      # durable completion scan
        "ddb_res": boto3.client("dynamodb", region_name=region, config=_RETRY),  # approvals scan
        "collector": MetricCollector(region),
    }

    results_dir = os.path.join(HERE, cfg["results_dir"])
    all_records = []
    for arm in arms:
        for volume in volumes:
            for rep in range(1, reps + 1):
                run_id = f"{arm}-{volume}-r{rep}"
                out_path = os.path.join(results_dir, f"{run_id}.json")
                # RESUME: skip a run whose result already exists and recorded real
                # completions, so an interrupted matrix continues on relaunch instead
                # of redoing finished reps. Pass --fresh to force a full rerun.
                if not args.fresh and os.path.exists(out_path):
                    try:
                        prev = json.load(open(out_path))
                        if prev.get("workflows_completed"):
                            log(f"skip {run_id}: already complete "
                                f"({prev['workflows_completed']}/{prev.get('volume')})")
                            all_records.append(prev)
                            continue
                    except Exception:
                        pass  # unreadable/partial -> rerun it
                try:
                    rec = run_one(arm, volume, rep, cfg, resolved, clients)
                    all_records.append(rec)
                except Exception as e:
                    # Continue-on-error: log and move on. No result file is written for
                    # this rep, so a later resumable pass (or the runner's retry loop)
                    # re-attempts it. One transient failure never aborts the matrix.
                    log(f"ERROR run {run_id} failed (continuing): {type(e).__name__}: {e}")
                    continue
    log(f"done: {len(all_records)} records total")


if __name__ == "__main__":
    main()
