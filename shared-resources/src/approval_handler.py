import json
import boto3
import logging
import os
from datetime import datetime
import urllib3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
stepfunctions = boto3.client('stepfunctions')
lambda_client = boto3.client('lambda')
http = urllib3.PoolManager()

APPROVALS_TABLE = os.environ.get('APPROVALS_TABLE', 'etl-pending-approvals')


def handler(event, context):
    """
    API Gateway handler for approval/rejection of ETL jobs.
    
    Endpoints:
    - POST /approve/{jobId} - Approve a job
    - POST /reject/{jobId} - Reject a job
    - GET /status/{jobId} - Get job approval status
    """
    logger.info(f"Received event: {json.dumps(event)}")
    
    http_method = event.get('httpMethod')
    path = event.get('path', '')
    path_params = event.get('pathParameters', {})
    job_id = path_params.get('jobId')
    
    if not job_id:
        return {
            'statusCode': 400,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': 'jobId is required'})
        }
    
    try:
        table = dynamodb.Table(APPROVALS_TABLE)
        
        # Get the pending approval record
        response = table.get_item(Key={'jobId': job_id})
        
        if 'Item' not in response:
            return {
                'statusCode': 404,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'error': f'Job {job_id} not found or already processed'})
            }
        
        approval_record = response['Item']
        
        # Handle GET status request
        if http_method == 'GET':
            return {
                'statusCode': 200,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'jobId': job_id,
                    'status': approval_record.get('status', 'pending'),
                    'summary': approval_record.get('summary', {}),
                    'requestedAt': approval_record.get('requestedAt'),
                    'workflowType': approval_record.get('workflowType')
                })
            }
        
        # Parse request body for approval/rejection
        body = {}
        if event.get('body'):
            body = json.loads(event.get('body', '{}'))
        
        reviewer = body.get('reviewer', 'api-user')
        reason = body.get('reason', '')
        
        # Determine approval decision
        approved = 'approve' in path.lower()
        
        approval_response = {
            'approved': approved,
            'reviewer': reviewer,
            'reason': reason,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        workflow_type = approval_record.get('workflowType')
        
        # Send response based on workflow type
        if workflow_type == 'step-functions':
            # Step Functions: send task success/failure
            task_token = approval_record.get('taskToken')
            if not task_token:
                raise ValueError('Task token not found in approval record')
            
            if approved:
                stepfunctions.send_task_success(
                    taskToken=task_token,
                    output=json.dumps(approval_response)
                )
            else:
                stepfunctions.send_task_failure(
                    taskToken=task_token,
                    error='JobRejected',
                    cause=reason or 'Job rejected by reviewer'
                )
            
        elif workflow_type == 'durable-functions':
            # Durable Functions: use Lambda Durable Execution callback API
            callback_id = approval_record.get('callbackId')
            
            if not callback_id:
                raise ValueError('Callback ID not found in approval record')
            
            # Use Lambda SDK to send durable execution callback
            try:
                if approved:
                    lambda_client.send_durable_execution_callback_success(
                        CallbackId=callback_id,
                        Result=json.dumps(approval_response)
                    )
                else:
                    lambda_client.send_durable_execution_callback_failure(
                        CallbackId=callback_id,
                        Error='JobRejected',
                        Cause=reason or 'Job rejected by reviewer'
                    )
            except Exception as callback_error:
                logger.error(f"Failed to send callback: {str(callback_error)}")
                raise ValueError(f"Failed to send callback: {str(callback_error)}")
        
        else:
            raise ValueError(f'Unknown workflow type: {workflow_type}')
        
        # Update the approval record
        table.update_item(
            Key={'jobId': job_id},
            UpdateExpression='SET #status = :status, processedAt = :processedAt, approvalResponse = :response',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'approved' if approved else 'rejected',
                ':processedAt': datetime.utcnow().isoformat(),
                ':response': approval_response
            }
        )
        
        logger.info(f"Job {job_id} {'approved' if approved else 'rejected'} by {reviewer}")
        
        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'message': f"Job {job_id} {'approved' if approved else 'rejected'} successfully",
                'jobId': job_id,
                'approved': approved,
                'reviewer': reviewer
            })
        }
        
    except Exception as e:
        logger.error(f"Error processing approval: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': str(e)})
        }
