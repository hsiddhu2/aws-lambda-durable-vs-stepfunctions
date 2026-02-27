import json
import boto3
import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
sns_client = boto3.client('sns')

APPROVALS_TABLE = os.environ.get('APPROVALS_TABLE', 'etl-pending-approvals')
APPROVAL_TOPIC_ARN = os.environ.get('APPROVAL_TOPIC_ARN')
APPROVAL_API_URL = os.environ.get('APPROVAL_API_URL')


def handler(event, context):
    """
    Approval Lambda for Step Functions ETL pipeline.
    This function stores the task token and sends a notification for human approval.
    """
    logger.info(f"Received approval request: {json.dumps(event)}")
    
    task_token = event.get("taskToken")
    job_id = event.get("jobId")
    summary = event.get("summary", {})
    
    if not task_token:
        raise ValueError("taskToken is required")
    
    if not job_id:
        raise ValueError("jobId is required")
    
    logger.info(f"Storing approval request for job {job_id}")
    
    try:
        # Store the task token in DynamoDB
        table = dynamodb.Table(APPROVALS_TABLE)
        
        # TTL: 24 hours from now
        ttl = int((datetime.utcnow() + timedelta(hours=24)).timestamp())
        
        table.put_item(Item={
            'jobId': job_id,
            'taskToken': task_token,
            'workflowType': 'step-functions',
            'summary': json.dumps(summary),  # Convert to JSON string to avoid DynamoDB type issues
            'status': 'pending',
            'requestedAt': datetime.utcnow().isoformat(),
            'ttl': ttl
        })
        
        # Send SNS notification
        if APPROVAL_TOPIC_ARN:
            message = f"""
ETL Job Approval Required

Job ID: {job_id}
Records Processed: {summary.get('record_count', 'N/A')}
Columns: {', '.join(summary.get('columns', []))}

To approve this job:
POST {APPROVAL_API_URL}/approve/{job_id}

To reject this job:
POST {APPROVAL_API_URL}/reject/{job_id}

To check status:
GET {APPROVAL_API_URL}/status/{job_id}

Sample curl commands:
# Approve
curl -X POST {APPROVAL_API_URL}/approve/{job_id} \\
  -H "Content-Type: application/json" \\
  -d '{{"reviewer": "your-name", "reason": "Data looks good"}}'

# Reject
curl -X POST {APPROVAL_API_URL}/reject/{job_id} \\
  -H "Content-Type: application/json" \\
  -d '{{"reviewer": "your-name", "reason": "Data quality issues"}}'
"""
            
            sns_client.publish(
                TopicArn=APPROVAL_TOPIC_ARN,
                Subject=f'ETL Job Approval Required: {job_id}',
                Message=message
            )
            
            logger.info(f"Approval notification sent for job {job_id}")
        
        # Return success - the workflow will wait for callback
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Approval request stored successfully",
                "job_id": job_id,
                "status": "pending"
            })
        }
        
    except Exception as e:
        logger.error(f"Failed to store approval request: {str(e)}")
        raise
