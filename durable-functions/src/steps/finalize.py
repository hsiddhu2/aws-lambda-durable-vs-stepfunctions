import boto3
import logging
from datetime import datetime

logger = logging.getLogger()
dynamodb = boto3.resource("dynamodb")
s3_client = boto3.client("s3")


def finalize_job(job_id, source_bucket, source_key, load_result, approval, metadata_table, step_context=None):
    logger.info(f"Finalizing job {job_id}")
    completed_at = datetime.utcnow().isoformat()
    table = dynamodb.Table(metadata_table)
    table.put_item(Item={
        "jobId": job_id, "timestamp": completed_at,
        "sourceFile": f"s3://{source_bucket}/{source_key}",
        "outputPath": load_result["target_path"],
        "recordCount": load_result["record_count"],
        "approvedBy": approval.get("reviewer", "auto"),
        "status": "COMPLETED"
    })
    # Extract just the filename from source_key to avoid nested archive paths
    filename = source_key.split('/')[-1]
    archive_key = f"archive/{filename}"
    s3_client.copy_object(Bucket=source_bucket, CopySource={"Bucket": source_bucket, "Key": source_key}, Key=archive_key)
    s3_client.delete_object(Bucket=source_bucket, Key=source_key)
    return {"completed_at": completed_at, "archived_to": archive_key}
