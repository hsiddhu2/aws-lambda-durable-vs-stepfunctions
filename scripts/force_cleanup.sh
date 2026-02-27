#!/bin/bash

echo "=========================================="
echo "FORCE CLEANUP - Manual Resource Deletion"
echo "=========================================="
echo ""

echo "Step 1: Check current stack status"
echo "==================================="
aws cloudformation list-stacks --stack-status-filter DELETE_IN_PROGRESS DELETE_FAILED UPDATE_COMPLETE CREATE_COMPLETE --output json | jq -r '.StackSummaries[] | select(.StackName | contains("etl")) | "\(.StackName) - \(.StackStatus)"'

echo ""
echo "Step 2: Delete Lambda functions manually"
echo "========================================"

# Delete Durable Function
echo "Deleting ETLDurableOrchestrator..."
aws lambda delete-function --function-name ETLDurableOrchestrator 2>/dev/null && echo "  ✓ Deleted" || echo "  ✗ Already deleted or error"

# Delete Approval Handler
echo "Deleting ETL-Approval-Handler..."
aws lambda delete-function --function-name ETL-Approval-Handler 2>/dev/null && echo "  ✓ Deleted" || echo "  ✗ Already deleted or error"

echo ""
echo "Step 3: Delete DynamoDB tables manually"
echo "======================================="

echo "Deleting etl-job-metadata..."
aws dynamodb delete-table --table-name etl-job-metadata 2>/dev/null && echo "  ✓ Deleted" || echo "  ✗ Already deleted or error"

echo "Deleting etl-pending-approvals..."
aws dynamodb delete-table --table-name etl-pending-approvals 2>/dev/null && echo "  ✓ Deleted" || echo "  ✗ Already deleted or error"

echo ""
echo "Step 4: Empty and delete S3 buckets"
echo "==================================="

RAW_BUCKET="etl-raw-data-bucket-YOUR_AWS_ACCOUNT_ID"
PROCESSED_BUCKET="etl-processed-data-bucket-YOUR_AWS_ACCOUNT_ID"

echo "Emptying and deleting $RAW_BUCKET..."
aws s3 rm "s3://$RAW_BUCKET" --recursive 2>/dev/null
aws s3 rb "s3://$RAW_BUCKET" 2>/dev/null && echo "  ✓ Deleted" || echo "  ✗ Already deleted or error"

echo "Emptying and deleting $PROCESSED_BUCKET..."
aws s3 rm "s3://$PROCESSED_BUCKET" --recursive 2>/dev/null
aws s3 rb "s3://$PROCESSED_BUCKET" 2>/dev/null && echo "  ✓ Deleted" || echo "  ✗ Already deleted or error"

echo ""
echo "Step 5: Delete Step Functions state machine"
echo "==========================================="

STATE_MACHINE_ARN="arn:aws:states:us-east-1:YOUR_AWS_ACCOUNT_ID:stateMachine:ETL-Pipeline-StateMachine"
echo "Deleting ETL-Pipeline-StateMachine..."
aws stepfunctions delete-state-machine --state-machine-arn "$STATE_MACHINE_ARN" 2>/dev/null && echo "  ✓ Deleted" || echo "  ✗ Already deleted or error"

echo ""
echo "Step 6: Wait a moment for resources to be deleted..."
sleep 10

echo ""
echo "Step 7: Retry CloudFormation stack deletion"
echo "==========================================="

echo "Deleting etl-durable..."
aws cloudformation delete-stack --stack-name etl-durable 2>/dev/null && echo "  ✓ Delete initiated" || echo "  ✗ Already deleted or error"

echo "Deleting etl-stepfn..."
aws cloudformation delete-stack --stack-name etl-stepfn 2>/dev/null && echo "  ✓ Delete initiated" || echo "  ✗ Already deleted or error"

echo "Deleting etl-shared-resources..."
aws cloudformation delete-stack --stack-name etl-shared-resources 2>/dev/null && echo "  ✓ Delete initiated" || echo "  ✗ Already deleted or error"

echo ""
echo "Step 8: Final status check"
echo "=========================="
sleep 5

echo ""
echo "Remaining stacks:"
aws cloudformation list-stacks --stack-status-filter DELETE_IN_PROGRESS DELETE_FAILED UPDATE_COMPLETE CREATE_COMPLETE --output json | jq -r '.StackSummaries[] | select(.StackName | contains("etl")) | "  \(.StackName) - \(.StackStatus)"'

echo ""
echo "Remaining Lambda functions:"
aws lambda list-functions --output json | jq -r '.Functions[] | select(.FunctionName | contains("ETL")) | "  \(.FunctionName)"'

echo ""
echo "Remaining Step Functions:"
aws stepfunctions list-state-machines --output json | jq -r '.stateMachines[] | select(.name | contains("ETL")) | "  \(.name)"'

echo ""
echo "=========================================="
echo "FORCE CLEANUP COMPLETE"
echo "=========================================="
echo ""
echo "If stacks are still showing DELETE_IN_PROGRESS or DELETE_FAILED:"
echo "  1. Wait 5-10 minutes for AWS to process deletions"
echo "  2. Check AWS Console CloudFormation for detailed errors"
echo "  3. You may need to manually delete stuck resources from AWS Console"
echo ""
