#!/bin/bash

echo "=========================================="
echo "CLEANUP ALL RESOURCES"
echo "=========================================="
echo ""

# Configuration
STEP_FUNCTION_ARN="arn:aws:states:us-east-1:YOUR_AWS_ACCOUNT_ID:stateMachine:ETL-Pipeline-StateMachine"
DURABLE_FUNCTION_NAME="ETLDurableOrchestrator"
METADATA_TABLE="etl-job-metadata"
APPROVALS_TABLE="etl-pending-approvals"
RAW_BUCKET="etl-raw-data-bucket-YOUR_AWS_ACCOUNT_ID"
PROCESSED_BUCKET="etl-processed-data-bucket-YOUR_AWS_ACCOUNT_ID"

echo "Step 1: Stop all running Step Functions executions"
echo "=================================================="

# Get all running Step Functions executions
RUNNING_EXECUTIONS=$(aws stepfunctions list-executions \
  --state-machine-arn "$STEP_FUNCTION_ARN" \
  --status-filter RUNNING \
  --max-results 1000 \
  --output json | jq -r '.executions[].executionArn')

if [ -z "$RUNNING_EXECUTIONS" ]; then
  echo "No running Step Functions executions found."
else
  echo "Found running executions. Stopping them..."
  echo "$RUNNING_EXECUTIONS" | while read -r execution_arn; do
    if [ ! -z "$execution_arn" ]; then
      echo "Stopping: $execution_arn"
      aws stepfunctions stop-execution --execution-arn "$execution_arn" 2>/dev/null || true
    fi
  done
  echo "All Step Functions executions stopped."
fi

echo ""
echo "Step 2: Check for pending Durable Functions approvals"
echo "====================================================="

PENDING_COUNT=$(aws dynamodb scan \
  --table-name "$APPROVALS_TABLE" \
  --select COUNT \
  --output json 2>/dev/null | jq -r '.Count // 0')

echo "Pending approvals: $PENDING_COUNT"

if [ "$PENDING_COUNT" -gt 0 ]; then
  echo "Note: Durable Functions with pending approvals will timeout after 24 hours."
  echo "They are in a paused state and not consuming resources."
fi

echo ""
echo "Step 3: Clear S3 buckets (optional - keeps data for blog post)"
echo "=============================================================="
echo "Do you want to delete all files from S3 buckets? (y/N)"
read -r CLEAR_S3

if [ "$CLEAR_S3" = "y" ] || [ "$CLEAR_S3" = "Y" ]; then
  echo "Clearing raw bucket..."
  aws s3 rm "s3://$RAW_BUCKET" --recursive || true
  
  echo "Clearing processed bucket..."
  aws s3 rm "s3://$PROCESSED_BUCKET" --recursive || true
  
  echo "S3 buckets cleared."
else
  echo "Skipping S3 cleanup. Files retained for blog post."
fi

echo ""
echo "Step 4: Summary of resources"
echo "============================"

echo ""
echo "DynamoDB Tables:"
echo "  - $METADATA_TABLE: $(aws dynamodb scan --table-name "$METADATA_TABLE" --select COUNT --output json 2>/dev/null | jq -r '.Count // 0') items"
echo "  - $APPROVALS_TABLE: $(aws dynamodb scan --table-name "$APPROVALS_TABLE" --select COUNT --output json 2>/dev/null | jq -r '.Count // 0') items"

echo ""
echo "S3 Buckets:"
echo "  - $RAW_BUCKET: $(aws s3 ls "s3://$RAW_BUCKET" --recursive 2>/dev/null | wc -l) files"
echo "  - $PROCESSED_BUCKET: $(aws s3 ls "s3://$PROCESSED_BUCKET" --recursive 2>/dev/null | wc -l) files"

echo ""
echo "Step Functions:"
RUNNING_SF=$(aws stepfunctions list-executions --state-machine-arn "$STEP_FUNCTION_ARN" --status-filter RUNNING --max-results 10 --output json 2>/dev/null | jq -r '.executions | length')
echo "  - Running executions: $RUNNING_SF"

echo ""
echo "Lambda Durable Functions:"
echo "  - Function: $DURABLE_FUNCTION_NAME (deployed)"
echo "  - Pending approvals: $PENDING_COUNT (will timeout in 24h)"

echo ""
echo "=========================================="
echo "CLEANUP COMPLETE"
echo "=========================================="
echo ""
echo "Notes:"
echo "  - All running Step Functions executions have been stopped"
echo "  - Durable Functions with pending approvals will timeout automatically"
echo "  - DynamoDB tables and S3 data retained for blog post metrics"
echo "  - Lambda functions remain deployed (not consuming resources when idle)"
echo ""
echo "To completely delete all infrastructure, run:"
echo "  cd durable-functions && sam delete --stack-name etl-durable"
echo "  cd step-functions && sam delete --stack-name etl-stepfn"
echo "  cd shared-resources && sam delete --stack-name etl-shared"
echo ""
