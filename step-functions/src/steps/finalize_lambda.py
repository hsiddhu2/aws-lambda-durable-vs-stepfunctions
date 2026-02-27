import json
import boto3
import logging
import os
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)
dynamodb = boto3.resource("dynamodb")
s3_client = boto3.client("s3")


def handler(event, context):
    """Finalize step for Step Functions ETL pipeline"""
    logger.info(f"Received event: {json.dumps(event)}")
    
    # Handle both direct call and state machine call
    if "loadResult" in event:
        load_result = event["loadResult"]
        approval = event.get("approvalResult", {"approved": True, "reviewer": "auto"})
    else:
        # Direct call - event is the load result
        load_result = event
        approval = {"approved": True, "reviewer": "auto"}
    
    job_id = load_result.get("job_id")
    source_bucket = load_result.get("source_bucket")
    source_key = load_result.get("source_key")
    
    if not job_id:
        raise ValueError(f"job_id not found in load_result. Event structure: {json.dumps(event)}")
    
    metadata_table = os.environ.get("METADATA_TABLE", "etl-stepfn-metadata")
    
    logger.info(f"Finalizing job {job_id}")
    
    try:
        completed_at = datetime.utcnow().isoformat()
        
        # Store metadata in DynamoDB
        table = dynamodb.Table(metadata_table)
        table.put_item(Item={
            "jobId": job_id,
            "timestamp": completed_at,
            "sourceFile": f"s3://{source_bucket}/{source_key}",
            "outputPath": load_result["target_path"],
            "recordCount": load_result["record_count"],
            "approvedBy": approval.get("reviewer", "auto"),
            "status": "COMPLETED"
        })
        
        # Archive source file
        archive_key = f"archive/{source_key}"
        s3_client.copy_object(
            Bucket=source_bucket,
            CopySource={"Bucket": source_bucket, "Key": source_key},
            Key=archive_key
        )
        s3_client.delete_object(Bucket=source_bucket, Key=source_key)
        
        logger.info(f"Job {job_id} finalized successfully")
        
        return {
            "status": "COMPLETED",
            "job_id": job_id,
            "completed_at": completed_at,
            "archived_to": archive_key,
            "records_processed": load_result["record_count"]
        }
    except Exception as e:
        logger.error(f"Finalize failed: {str(e)}")
        raise
