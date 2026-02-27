import json
import boto3
import logging

logger = logging.getLogger()
s3_client = boto3.client("s3")


def load_data(transformed_data, target_bucket, target_key, step_context=None):
    logger.info(f"Loading {len(transformed_data)} records to s3://{target_bucket}/{target_key}")
    output_lines = "\n".join(json.dumps(r) for r in transformed_data)
    s3_client.put_object(
        Bucket=target_bucket, Key=target_key,
        Body=output_lines.encode("utf-8"),
        ContentType="application/jsonl",
        Metadata={"record_count": str(len(transformed_data))}
    )
    summary = {
        "record_count": len(transformed_data),
        "columns": list(transformed_data[0].keys()) if transformed_data else [],
        "sample_records": transformed_data[:3]
    }
    return {"target_path": f"s3://{target_bucket}/{target_key}", "record_count": len(transformed_data), "summary": summary}
