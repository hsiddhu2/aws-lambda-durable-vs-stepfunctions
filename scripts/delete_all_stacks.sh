#!/bin/bash

echo "=========================================="
echo "DELETE ALL AWS RESOURCES"
echo "=========================================="
echo ""
echo "WARNING: This will delete ALL resources including:"
echo "  - Lambda functions"
echo "  - Step Functions state machines"
echo "  - DynamoDB tables (and all data)"
echo "  - S3 buckets (and all files)"
echo "  - API Gateway"
echo "  - CloudFormation stacks"
echo ""
echo "Are you sure you want to continue? (yes/NO)"
read -r CONFIRM

if [ "$CONFIRM" != "yes" ]; then
  echo "Aborted. No resources deleted."
  exit 0
fi

echo ""
echo "Step 1: Empty S3 buckets (required before deletion)"
echo "==================================================="

# Get bucket names from CloudFormation stacks
echo "Getting bucket names from CloudFormation..."
RAW_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name etl-durable \
  --query 'Stacks[0].Outputs[?OutputKey==`RawDataBucket`].OutputValue' \
  --output text 2>/dev/null)

PROCESSED_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name etl-durable \
  --query 'Stacks[0].Outputs[?OutputKey==`ProcessedDataBucket`].OutputValue' \
  --output text 2>/dev/null)

if [ ! -z "$RAW_BUCKET" ]; then
  echo "Emptying raw bucket: $RAW_BUCKET..."
  aws s3 rm "s3://$RAW_BUCKET" --recursive 2>/dev/null || echo "Bucket already empty or doesn't exist"
else
  echo "Raw bucket not found or already deleted"
fi

if [ ! -z "$PROCESSED_BUCKET" ]; then
  echo "Emptying processed bucket: $PROCESSED_BUCKET..."
  aws s3 rm "s3://$PROCESSED_BUCKET" --recursive 2>/dev/null || echo "Bucket already empty or doesn't exist"
else
  echo "Processed bucket not found or already deleted"
fi

echo ""
echo "Step 2: Stop all running Step Functions executions"
echo "==================================================="

# Get state machine ARN from CloudFormation
STEP_FUNCTION_ARN=$(aws cloudformation describe-stacks \
  --stack-name etl-stepfn \
  --query 'Stacks[0].Outputs[?OutputKey==`StateMachineArn`].OutputValue' \
  --output text 2>/dev/null)

if [ -z "$STEP_FUNCTION_ARN" ]; then
  echo "State machine not found or already deleted"
else
  echo "State machine: $STEP_FUNCTION_ARN"
  
  RUNNING_EXECUTIONS=$(aws stepfunctions list-executions \
    --state-machine-arn "$STEP_FUNCTION_ARN" \
    --status-filter RUNNING \
    --max-results 1000 \
    --output json 2>/dev/null | jq -r '.executions[].executionArn')

  if [ -z "$RUNNING_EXECUTIONS" ]; then
    echo "No running executions found."
  else
    echo "Stopping running executions..."
    echo "$RUNNING_EXECUTIONS" | while read -r execution_arn; do
      if [ ! -z "$execution_arn" ]; then
        aws stepfunctions stop-execution --execution-arn "$execution_arn" 2>/dev/null || true
      fi
    done
    echo "All executions stopped."
  fi
fi

echo ""
echo "Step 3: Delete CloudFormation stacks"
echo "====================================="

# Delete in reverse order (dependencies)
echo ""
echo "Deleting etl-durable stack..."
aws cloudformation delete-stack --stack-name etl-durable
echo "Waiting for etl-durable stack deletion..."
aws cloudformation wait stack-delete-complete --stack-name etl-durable 2>/dev/null || echo "Stack deleted or doesn't exist"

echo ""
echo "Deleting etl-stepfn stack..."
aws cloudformation delete-stack --stack-name etl-stepfn
echo "Waiting for etl-stepfn stack deletion..."
aws cloudformation wait stack-delete-complete --stack-name etl-stepfn 2>/dev/null || echo "Stack deleted or doesn't exist"

echo ""
echo "Deleting etl-shared-resources stack..."
aws cloudformation delete-stack --stack-name etl-shared-resources
echo "Waiting for etl-shared-resources stack deletion..."
aws cloudformation wait stack-delete-complete --stack-name etl-shared-resources 2>/dev/null || echo "Stack deleted or doesn't exist"

echo ""
echo "Step 4: Verify deletion"
echo "======================="

echo ""
echo "Remaining ETL stacks:"
aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE DELETE_IN_PROGRESS --output json | jq -r '.StackSummaries[] | select(.StackName | contains("etl")) | "\(.StackName) - \(.StackStatus)"'

echo ""
echo "Remaining Lambda functions:"
aws lambda list-functions --output json | jq -r '.Functions[] | select(.FunctionName | contains("ETL")) | .FunctionName'

echo ""
echo "Remaining Step Functions:"
aws stepfunctions list-state-machines --output json | jq -r '.stateMachines[] | select(.name | contains("ETL")) | .name'

echo ""
echo "=========================================="
echo "DELETION COMPLETE"
echo "=========================================="
echo ""
echo "All ETL resources have been deleted."
echo "Note: It may take a few minutes for all resources to be fully removed."
echo ""
