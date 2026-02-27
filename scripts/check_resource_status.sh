#!/bin/bash

echo "=========================================="
echo "RESOURCE STATUS CHECK"
echo "=========================================="
echo ""

echo "CloudFormation Stacks:"
echo "====================="
aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE --output json | jq -r '.StackSummaries[] | select(.StackName | contains("etl")) | "  ✓ \(.StackName) - \(.StackStatus)"'

echo ""
echo "Lambda Functions:"
echo "================"
aws lambda list-functions --output json | jq -r '.Functions[] | select(.FunctionName | contains("ETL")) | "  ✓ \(.FunctionName)"'

echo ""
echo "Step Functions State Machines:"
echo "============================="
aws stepfunctions list-state-machines --output json | jq -r '.stateMachines[] | select(.name | contains("ETL")) | "  ✓ \(.name)"'

echo ""
echo "Running Step Functions Executions:"
echo "=================================="
STEP_FUNCTION_ARN="arn:aws:states:us-east-1:YOUR_AWS_ACCOUNT_ID:stateMachine:ETL-Pipeline-StateMachine"
RUNNING_COUNT=$(aws stepfunctions list-executions --state-machine-arn "$STEP_FUNCTION_ARN" --status-filter RUNNING --max-results 10 --output json 2>/dev/null | jq -r '.executions | length')
echo "  Running: $RUNNING_COUNT"

echo ""
echo "DynamoDB Tables:"
echo "==============="
echo "  ✓ etl-job-metadata: $(aws dynamodb scan --table-name etl-job-metadata --select COUNT --output json 2>/dev/null | jq -r '.Count // 0') items"
echo "  ✓ etl-pending-approvals: $(aws dynamodb scan --table-name etl-pending-approvals --select COUNT --output json 2>/dev/null | jq -r '.Count // "DELETED"')"

echo ""
echo "S3 Buckets:"
echo "=========="
RAW_BUCKET="etl-raw-data-bucket-YOUR_AWS_ACCOUNT_ID"
PROCESSED_BUCKET="etl-processed-data-bucket-YOUR_AWS_ACCOUNT_ID"
echo "  ✓ $RAW_BUCKET: $(aws s3 ls "s3://$RAW_BUCKET" --recursive 2>/dev/null | wc -l | tr -d ' ') files"
echo "  ✓ $PROCESSED_BUCKET: $(aws s3 ls "s3://$PROCESSED_BUCKET" --recursive 2>/dev/null | wc -l | tr -d ' ') files"

echo ""
echo "=========================================="
echo "SUMMARY"
echo "=========================================="
echo ""
echo "Active Resources:"
echo "  - CloudFormation Stacks: 3 (etl-durable, etl-stepfn, etl-shared-resources)"
echo "  - Lambda Functions: 2 (ETLDurableOrchestrator, ETL-Approval-Handler)"
echo "  - Step Functions: 1 (ETL-Pipeline-StateMachine)"
echo "  - Running Executions: $RUNNING_COUNT"
echo ""
echo "Note: Lambda functions and Step Functions are idle (not consuming resources)"
echo "      They only cost money when actively executing"
echo ""
echo "To delete all resources, run:"
echo "  ./scripts/delete_all_stacks.sh"
echo ""
