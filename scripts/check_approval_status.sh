#!/bin/bash

# Script to check approval status of ETL jobs
# Usage: ./check_approval_status.sh <job-id>

set -e

JOB_ID=$1

if [ -z "$JOB_ID" ]; then
    echo "Usage: $0 <job-id>"
    echo ""
    echo "Example:"
    echo "  $0 etl-durable-20260222001234-data_0001.csv"
    exit 1
fi

# Get the API URL from CloudFormation exports
API_URL=$(aws cloudformation list-exports \
    --query "Exports[?Name=='ETL-ApprovalApiUrl'].Value" \
    --output text)

if [ -z "$API_URL" ]; then
    echo "Error: Could not find approval API URL. Make sure shared-resources stack is deployed."
    exit 1
fi

# Make the API call
echo "Checking status for job: $JOB_ID"
echo "API URL: $API_URL"
echo ""

RESPONSE=$(curl -s -X GET "$API_URL/status/$JOB_ID")

echo "$RESPONSE" | jq '.'

# Extract status
STATUS=$(echo "$RESPONSE" | jq -r '.status // "unknown"')

echo ""
if [ "$STATUS" == "pending" ]; then
    echo "⏳ Job is waiting for approval"
elif [ "$STATUS" == "approved" ]; then
    echo "✓ Job has been approved"
elif [ "$STATUS" == "rejected" ]; then
    echo "✗ Job has been rejected"
else
    echo "? Unknown status or job not found"
fi
