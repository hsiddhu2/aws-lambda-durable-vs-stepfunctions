import json
import boto3
import logging
import os
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)
s3_client = boto3.client("s3")


def handler(event, context):
    """Load step for Step Functions ETL pipeline"""
    transform_result = event.get("transformResult", event)
    extract_result = event.get("extractResult", {})
    
    transformed_data = transform_result["data"]
    source_key = transform_result.get("source_key") or extract_result.get("source_key")
    
    # Generate job ID
    job_id = f"etl-stepfn-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{source_key.split('/')[-1]}"
    
    # Get target bucket from environment or use default
    target_bucket = os.environ.get("PROCESSED_BUCKET", "etl-processed-data-bucket")
    target_key = f"processed/{job_id}/output.jsonl"
    
    logger.info(f"Loading {len(transformed_data)} records to s3://{target_bucket}/{target_key}")
    
    try:
        output_lines = "\n".join(json.dumps(r) for r in transformed_data)
        s3_client.put_object(
            Bucket=target_bucket,
            Key=target_key,
            Body=output_lines.encode("utf-8"),
            ContentType="application/jsonl",
            Metadata={"record_count": str(len(transformed_data))}
        )
        
        summary = {
            "record_count": len(transformed_data),
            "columns": list(transformed_data[0].keys()) if transformed_data else [],
            "sample_records": transformed_data[:3]
        }
        
        logger.info(f"Successfully loaded to {target_key}")
        
        return {
            "job_id": job_id,
            "target_path": f"s3://{target_bucket}/{target_key}",
            "record_count": len(transformed_data),
            "summary": summary,
            "source_bucket": transform_result.get("source_bucket"),
            "source_key": source_key
        }
    except Exception as e:
        logger.error(f"Load failed: {str(e)}")
        raise
