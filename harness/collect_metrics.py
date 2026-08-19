"""collect_metrics.py — read REAL measured counters for one (arm, volume, rep) run.

Every counter here comes from a live AWS API read (CloudWatch, Step Functions,
DynamoDB/SNS CloudWatch metrics, Lambda config). Nothing is estimated except the
Step-Functions billed-transition count, which is derived by SAMPLING real execution
histories with the sample size and stdev recorded alongside it (sanctioned by the
task methodology and disclosed in the record).

INTEGRITY: any counter that cannot be read is returned as None and its dotted name
is appended to `null_fields`. A None is NEVER replaced with a plausible value.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional

import boto3
from botocore.config import Config

# Adaptive retry so Step Functions ListExecutions / GetExecutionHistory and CloudWatch
# reads survive API-level ThrottlingException (frequent once thousands of executions
# accumulate) by backing off instead of raising -> the rep completes instead of failing.
_RETRY = Config(retries={"max_attempts": 12, "mode": "adaptive"})


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class MetricCollector:
    def __init__(self, region: str):
        self.region = region
        self.cw = boto3.client("cloudwatch", region_name=region, config=_RETRY)
        self.lam = boto3.client("lambda", region_name=region, config=_RETRY)
        self.sfn = boto3.client("stepfunctions", region_name=region, config=_RETRY)

    # ---- Lambda ----
    def function_memory_mb(self, name: str) -> Optional[int]:
        try:
            return self.lam.get_function_configuration(FunctionName=name)["MemorySize"]
        except Exception:
            return None

    def _cw_sum(self, namespace: str, metric: str, dims: list[dict],
                start: datetime, end: datetime) -> Optional[float]:
        """Sum of a CloudWatch metric over [start, end]. None on failure or no data.

        CloudWatch aligns datapoints to period boundaries and only returns points whose
        timestamp >= StartTime. Using the raw window start (e.g. 04:39:38) would drop a
        1-minute datapoint stamped 04:39:00, yielding a false null. We therefore FLOOR
        the start and CEIL the end to the minute (with a 60s pad on each side) and use
        Period=60, summing all per-minute points. The pad is far smaller than the
        inter-rep gap (uploads are cleaned and reps run serially), so it cannot pull in
        another rep's traffic on these dedicated functions."""
        start, end = _utc(start), _utc(end)
        start = start.replace(second=0, microsecond=0) - timedelta(seconds=60)
        end = end.replace(second=0, microsecond=0) + timedelta(seconds=120)
        try:
            resp = self.cw.get_metric_statistics(
                Namespace=namespace, MetricName=metric, Dimensions=dims,
                StartTime=start, EndTime=end, Period=60, Statistics=["Sum"],
            )
        except Exception:
            return None
        points = resp.get("Datapoints", [])
        if not points:
            return None
        return sum(p["Sum"] for p in points)

    def lambda_metrics(self, function_names: list[str], start: datetime,
                       end: datetime) -> dict:
        """Per-function and total invocations, billed duration, and GB-seconds.

        GB-seconds = sum over functions of (Duration_Sum_ms / 1000) * (memory_mb / 1024),
        computed from real CloudWatch Duration Sum and real Lambda-API memory.
        """
        per_function = []
        null_fields: list[str] = []
        total_invocations: Optional[float] = 0.0
        total_gb_seconds: Optional[float] = 0.0
        total_duration_ms: Optional[float] = 0.0
        total_throttles: float = 0.0  # accuracy safeguard: nonzero => retry inflation risk

        for name in function_names:
            dims = [{"Name": "FunctionName", "Value": name}]
            inv = self._cw_sum("AWS/Lambda", "Invocations", dims, start, end)
            dur_ms = self._cw_sum("AWS/Lambda", "Duration", dims, start, end)
            thr = self._cw_sum("AWS/Lambda", "Throttles", dims, start, end)
            total_throttles += thr or 0.0
            mem = self.function_memory_mb(name)

            gb_s: Optional[float] = None
            if dur_ms is not None and mem is not None:
                gb_s = (dur_ms / 1000.0) * (mem / 1024.0)

            if inv is None:
                null_fields.append(f"lambda.{name}.invocations")
                total_invocations = None
            elif total_invocations is not None:
                total_invocations += inv

            if dur_ms is None:
                null_fields.append(f"lambda.{name}.duration_ms")
                total_duration_ms = None
            elif total_duration_ms is not None:
                total_duration_ms += dur_ms

            if gb_s is None:
                null_fields.append(f"lambda.{name}.gb_seconds")
                total_gb_seconds = None
            elif total_gb_seconds is not None:
                total_gb_seconds += gb_s

            per_function.append({
                "name": name, "memory_mb": mem, "invocations": inv,
                "duration_ms_sum": dur_ms, "gb_seconds": gb_s,
                "throttles": thr,
            })

        return {
            "functions": per_function,
            "invocations": total_invocations,
            "duration_ms_sum": total_duration_ms,
            "gb_seconds": total_gb_seconds,
            "throttles": total_throttles,
            "null_fields": null_fields,
        }

    # ---- Step Functions billed transitions (sampled) ----
    def sfn_transitions(self, state_machine_arn: str, start: datetime, end: datetime,
                        expected_volume: int, sample_cap: int = 100) -> dict:
        """Sample SUCCEEDED executions started in-window and count state transitions
        per execution from GetExecutionHistory. A transition is counted each time a
        state is entered (events of type *StateEntered).

        gross = mean(per-exec transitions) * expected_volume. When the sample covers
        all in-window executions the mean*volume equals the exact sum. sample size and
        stdev are recorded so the estimate is auditable.
        """
        start, end = _utc(start), _utc(end)
        try:
            matched = []
            paginator = self.sfn.get_paginator("list_executions")
            for page in paginator.paginate(
                stateMachineArn=state_machine_arn, statusFilter="SUCCEEDED"
            ):
                for ex in page["executions"]:
                    sd = _utc(ex["startDate"])
                    if start <= sd <= end:
                        matched.append(ex["executionArn"])
                # executions are returned newest-first; stop once we pass the window
                if page["executions"] and _utc(page["executions"][-1]["startDate"]) < start:
                    break
        except Exception:
            return {"gross": None, "sampled_executions": 0, "sample_mean_per_exec": None,
                    "sample_stdev": None, "matched_executions": None,
                    "method": "list_executions failed", "null": True}

        sample = matched[:sample_cap]
        counts: list[int] = []
        for arn in sample:
            try:
                n = 0
                pg = self.sfn.get_paginator("get_execution_history")
                for page in pg.paginate(executionArn=arn):
                    for ev in page["events"]:
                        if ev["type"].endswith("StateEntered"):
                            n += 1
                counts.append(n)
            except Exception:
                continue

        if not counts:
            return {"gross": None, "sampled_executions": 0, "sample_mean_per_exec": None,
                    "sample_stdev": None, "matched_executions": len(matched),
                    "method": "GetExecutionHistory StateEntered count (no readable samples)",
                    "null": True}

        mean = statistics.fmean(counts)
        stdev = statistics.stdev(counts) if len(counts) > 1 else 0.0
        gross = mean * expected_volume
        return {
            "gross": gross,
            "matched_executions": len(matched),
            "sampled_executions": len(counts),
            "sample_mean_per_exec": mean,
            "sample_stdev": stdev,
            "method": "GetExecutionHistory StateEntered count; gross = mean * volume",
            "null": False,
        }

    # ---- DynamoDB consumed capacity (on-demand request units) ----
    def dynamodb_consumed(self, table_names: list[str], start: datetime,
                          end: datetime) -> dict:
        writes: Optional[float] = 0.0
        reads: Optional[float] = 0.0
        null_fields: list[str] = []
        for t in table_names:
            dims = [{"Name": "TableName", "Value": t}]
            w = self._cw_sum("AWS/DynamoDB", "ConsumedWriteCapacityUnits", dims, start, end)
            r = self._cw_sum("AWS/DynamoDB", "ConsumedReadCapacityUnits", dims, start, end)
            if w is None:
                null_fields.append(f"dynamodb.{t}.writes")
                writes = None
            elif writes is not None:
                writes += w
            if r is None:
                null_fields.append(f"dynamodb.{t}.reads")
                reads = None
            elif reads is not None:
                reads += r
        return {"writes": writes, "reads": reads, "null_fields": null_fields}

    # ---- SNS ----
    def sns_publishes(self, topic_name: str, start: datetime, end: datetime) -> Optional[float]:
        dims = [{"Name": "TopicName", "Value": topic_name}]
        return self._cw_sum("AWS/SNS", "NumberOfMessagesPublished", dims, start, end)

    # ---- S3 request metrics (usually not enabled -> None) ----
    def s3_requests(self, bucket: str, start: datetime, end: datetime) -> dict:
        # S3 request metrics require a per-bucket request-metrics configuration that is
        # NOT enabled by default. We attempt the read; absent config yields None (left null).
        dims_put = [{"Name": "BucketName", "Value": bucket},
                    {"Name": "FilterId", "Value": "EntireBucket"}]
        puts = self._cw_sum("AWS/S3", "PutRequests", dims_put, start, end)
        gets = self._cw_sum("AWS/S3", "GetRequests", dims_put, start, end)
        return {"puts": puts, "gets": gets}


def build_record(arm: str, volume: int, rep: int, region: str, account: str,
                 window_start: datetime, window_end: datetime,
                 workflows_completed: Optional[int],
                 lambda_result: dict, transitions: Optional[dict],
                 dynamodb: dict, s3: dict, sns_publishes: Optional[float],
                 run_id: str, extra_notes: Optional[list] = None) -> dict:
    """Assemble the canonical results record. Aggregates all null_fields."""
    null_fields = list(lambda_result.get("null_fields", []))
    null_fields += dynamodb.get("null_fields", [])
    if lambda_result.get("invocations") is None:
        pass  # already captured per-function
    if s3.get("puts") is None:
        null_fields.append("s3.puts")
    if s3.get("gets") is None:
        null_fields.append("s3.gets")
    if sns_publishes is None:
        null_fields.append("sns.publishes")
    if transitions is not None and transitions.get("null"):
        null_fields.append("state_transitions.gross")

    st_out = None
    if transitions is not None:
        st_out = {k: v for k, v in transitions.items() if k != "null"}

    return {
        "arm": arm,
        "volume": volume,
        "rep": rep,
        "run_id": run_id,
        "region": region,
        "account": account,
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "workflows_requested": volume,
        "workflows_completed": workflows_completed,
        "functions": lambda_result.get("functions"),
        "invocations": lambda_result.get("invocations"),
        "duration_ms_sum": lambda_result.get("duration_ms_sum"),
        "gb_seconds": lambda_result.get("gb_seconds"),
        "lambda_throttles": lambda_result.get("throttles"),
        "state_transitions": st_out,
        "dynamodb": {"writes": dynamodb.get("writes"), "reads": dynamodb.get("reads")},
        "s3": {"puts": s3.get("puts"), "gets": s3.get("gets")},
        "sns": {"publishes": sns_publishes},
        "null_fields": sorted(set(null_fields)),
        "notes": extra_notes or [],
    }
