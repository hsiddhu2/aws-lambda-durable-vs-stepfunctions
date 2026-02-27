import json
import csv
import io
import boto3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)
s3_client = boto3.client("s3")


def handler(event, context):
    """Extract step for Step Functions ETL pipeline"""
    logger.info(f"Received event: {json.dumps(event)}")
    
    # Handle both S3 event format and direct invocation format
    source_bucket = event.get("source_bucket") or event.get("bucket")
    source_key = event.get("source_key") or event.get("key")
    
    if not source_bucket or not source_key:
        raise ValueError(f"Missing bucket/key in event: {json.dumps(event)}")
    
    logger.info(f"Extracting from s3://{source_bucket}/{source_key}")
    
    try:
        response = s3_client.get_object(Bucket=source_bucket, Key=source_key)
        content = response["Body"].read().decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))
        records = list(reader)
        
        schema = {
            "columns": reader.fieldnames,
            "source_file": source_key,
            "file_size_bytes": response["ContentLength"]
        }
        
        logger.info(f"Extracted {len(records)} records with {len(schema['columns'])} columns")
        
        return {
            "data": records,
            "record_count": len(records),
            "schema": schema,
            "source_bucket": source_bucket,
            "source_key": source_key
        }
    except Exception as e:
        logger.error(f"Extract failed: {str(e)}")
        raise
