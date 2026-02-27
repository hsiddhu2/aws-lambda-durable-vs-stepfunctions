#!/bin/bash

# Script to reject ETL jobs
# Usage: ./reject_job.sh <job-id> [reviewer-name] [reason]

set -e

JOB_ID=$1
REVIEWER=${2:-"manual-reviewer"}
REASON=${3:-"Rejected via script"}
ACTION="reject"

if [ -z "$JOB_ID" ]; then
    echo "Usage: $0 <job-id> [reviewer-name] [reason]"
    echo ""
    echo "Examples:"
    echo "  $0 etl-durable-20260222001234-data_0001.csv"
    echo "  $0 etl-stepfn-20260222001234-data_0002.csv 'john-doe' 'Data quality issues'"
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

# Build the request body
REQUEST_BODY=$(cat <<EOF
{
  "reviewer": "$REVIEWER",
  "reason": "$REASON"
}
EOF
)

# Make the API call
echo "Sending $ACTION request for job: $JOB_ID"
echo "API URL: $API_URL"

RESPONSE=$(curl -s -X POST \
    -H "Content-Type: application/json" \
    -d "$REQUEST_BODY" \
    "$API_URL/$ACTION/$JOB_ID")

echo ""
echo "Response:"
echo "$RESPONSE" | jq '.'

# Check if successful
if echo "$RESPONSE" | jq -e '.message' > /dev/null 2>&1; then
    echo ""
    echo "✓ Job $JOB_ID ${ACTION}ed successfully!"
else
    echo ""
    echo "✗ Failed to $ACTION job"
    exit 1
fi
