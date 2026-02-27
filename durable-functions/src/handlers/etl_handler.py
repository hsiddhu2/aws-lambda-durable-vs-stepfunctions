import json
import os
import logging
from datetime import datetime
from aws_durable_execution_sdk_python import durable_execution, DurableContext

from steps.extract import extract_data
from steps.transform import transform_data
from steps.load import load_data
from steps.finalize import finalize_job

logger = logging.getLogger()
logger.setLevel(logging.INFO)

PROCESSED_BUCKET = os.environ.get("PROCESSED_BUCKET")
METADATA_TABLE = os.environ.get("METADATA_TABLE")


@durable_execution
def lambda_handler(event, context: DurableContext):
    logger.info(f"Received event: {json.dumps(event)}")
    
    # Handle both S3 event format and direct invocation format
    if "Records" in event:
        s3_event = event["Records"][0]["s3"]
        source_bucket = s3_event["bucket"]["name"]
        source_key = s3_event["object"]["key"]
    else:
        source_bucket = event.get("bucket")
        source_key = event.get("key")
    
    if not source_bucket or not source_key:
        raise ValueError(f"Missing bucket/key in event: {json.dumps(event)}")
    
    # Generate job_id deterministically using context.step() to cache the result
    job_id = context.step(
        lambda _: f"etl-durable-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{source_key.split('/')[-1]}",
        name="generate-job-id"
    )

    context.logger.info(f"Starting ETL job: {job_id}")

    # Step 1: Extract
    extracted = context.step(
        lambda _: extract_data(source_bucket, source_key, None),
        name="extract-data"
    )
    context.logger.info(f"Extracted {extracted['record_count']} records")

    # Step 2: Transform
    transformed = context.step(
        lambda _: transform_data(extracted["data"], extracted.get("schema", {}), None),
        name="transform-data"
    )
    context.logger.info(f"Transformed: {transformed['valid_records']} valid, {transformed['rejected_records']} rejected")

    # Step 3: Load
    loaded = context.step(
        lambda _: load_data(transformed["data"], PROCESSED_BUCKET, f"processed/{job_id}/output.jsonl", None),
        name="load-data"
    )
    context.logger.info(f"Loaded to {loaded['target_path']}")

    # Wait: Human Quality Check (no compute charges during this wait)
    def submit_for_approval(callback_id: str, ctx):
        return notify_reviewer(job_id, callback_id, loaded["summary"])
    
    approval = context.wait_for_callback(
        submitter=submit_for_approval,
        name="quality-check-approval"
    )
    
    # Parse approval result if it's a string
    if isinstance(approval, str):
        approval = json.loads(approval)

    if not approval or not approval.get("approved"):
        return {"status": "REJECTED", "job_id": job_id, "reason": approval.get("reason", "No reason")}

    # Step 4: Finalize
    final = context.step(
        lambda _: finalize_job(job_id, source_bucket, source_key, loaded, approval, METADATA_TABLE, None),
        name="finalize-job"
    )

    return {
        "status": "COMPLETED",
        "job_id": job_id,
        "records_processed": transformed["valid_records"],
        "output_path": loaded["target_path"],
        "approved_by": approval.get("reviewer"),
        "completed_at": final["completed_at"]
    }


def notify_reviewer(job_id, callback_id, summary):
    """
    Store approval request in DynamoDB and send SNS notification.
    The workflow will pause until approval/rejection via API.
    """
    import boto3
    from datetime import timedelta
    
    dynamodb = boto3.resource('dynamodb')
    sns_client = boto3.client('sns')
    
    approvals_table = os.environ.get('APPROVALS_TABLE', 'etl-pending-approvals')
    approval_topic_arn = os.environ.get('APPROVAL_TOPIC_ARN')
    approval_api_url = os.environ.get('APPROVAL_API_URL')
    function_arn = os.environ.get('AWS_LAMBDA_FUNCTION_NAME')
    
    logger.info(f"Storing approval request for job {job_id}, callback: {callback_id}")
    
    try:
        # Store the callback ID in DynamoDB
        table = dynamodb.Table(approvals_table)
        
        # TTL: 24 hours from now
        ttl = int((datetime.utcnow() + timedelta(hours=24)).timestamp())
        
        table.put_item(Item={
            'jobId': job_id,
            'callbackId': callback_id,
            'functionArn': function_arn,
            'workflowType': 'durable-functions',
            'summary': json.dumps(summary),  # Convert to JSON string to avoid DynamoDB type issues
            'status': 'pending',
            'requestedAt': datetime.utcnow().isoformat(),
            'ttl': ttl
        })
        
        # Send SNS notification
        if approval_topic_arn:
            message = f"""
ETL Job Approval Required (Durable Functions)

Job ID: {job_id}
Records Processed: {summary.get('record_count', 'N/A')}
Columns: {', '.join(summary.get('columns', []))}

To approve this job:
POST {approval_api_url}/approve/{job_id}

To reject this job:
POST {approval_api_url}/reject/{job_id}

To check status:
GET {approval_api_url}/status/{job_id}

Sample curl commands:
# Approve
curl -X POST {approval_api_url}/approve/{job_id} \\
  -H "Content-Type: application/json" \\
  -d '{{"reviewer": "your-name", "reason": "Data looks good"}}'

# Reject
curl -X POST {approval_api_url}/reject/{job_id} \\
  -H "Content-Type: application/json" \\
  -d '{{"reviewer": "your-name", "reason": "Data quality issues"}}'
"""
            
            sns_client.publish(
                TopicArn=approval_topic_arn,
                Subject=f'ETL Job Approval Required: {job_id}',
                Message=message
            )
            
            logger.info(f"Approval notification sent for job {job_id}")
        
        return {"job_id": job_id, "callback_id": callback_id, "summary": summary, "status": "pending"}
        
    except Exception as e:
        logger.error(f"Failed to store approval request: {str(e)}")
        raise

