#!/bin/bash

# Script to approve all pending ETL jobs
# Usage: ./approve_all_jobs.sh [reviewer-name] [reason]

set -e

REVIEWER=${1:-"Harry"}
REASON=${2:-"Batch approval for comparison study"}

echo "========================================="
echo "Approve All Pending Jobs"
echo "========================================="
echo "Reviewer: $REVIEWER"
echo "Reason: $REASON"
echo ""

# Get the API URL from CloudFormation exports
API_URL=$(aws cloudformation list-exports \
    --query "Exports[?Name=='ETL-ApprovalApiUrl'].Value" \
    --output text)

if [ -z "$API_URL" ]; then
    echo "Error: Could not find approval API URL. Make sure shared-resources stack is deployed."
    exit 1
fi

echo "API URL: $API_URL"
echo ""

# Get all pending job IDs from DynamoDB
echo "Fetching pending jobs from DynamoDB..."
JOB_IDS=$(aws dynamodb scan \
    --table-name etl-pending-approvals \
    --filter-expression "#status = :pending" \
    --expression-attribute-names '{"#status":"status"}' \
    --expression-attribute-values '{":pending":{"S":"pending"}}' \
    --projection-expression "jobId" \
    --output text | awk '{print $2}')

# Count jobs
TOTAL_JOBS=$(echo "$JOB_IDS" | wc -l | tr -d ' ')

if [ -z "$JOB_IDS" ] || [ "$TOTAL_JOBS" -eq 0 ]; then
    echo "No pending jobs found."
    exit 0
fi

echo "Found $TOTAL_JOBS pending jobs"
echo ""
echo "Starting approval process..."
echo ""

# Build the request body
REQUEST_BODY=$(cat <<EOF
{
  "reviewer": "$REVIEWER",
  "reason": "$REASON"
}
EOF
)

# Approve each job
count=0
success=0
failed=0

for job_id in $JOB_IDS; do
    count=$((count + 1))
    
    # Make the API call
    RESPONSE=$(curl -s -X POST \
        -H "Content-Type: application/json" \
        -d "$REQUEST_BODY" \
        "$API_URL/approve/$job_id")
    
    # Check if successful
    if echo "$RESPONSE" | grep -q "approved successfully"; then
        success=$((success + 1))
        echo "[$count/$TOTAL_JOBS] ✓ Approved: $job_id"
    else
        failed=$((failed + 1))
        echo "[$count/$TOTAL_JOBS] ✗ Failed: $job_id"
        echo "  Error: $RESPONSE"
    fi
    
    # Small delay to avoid throttling
    sleep 0.5
done

echo ""
echo "========================================="
echo "Approval Complete"
echo "========================================="
echo "Total jobs: $TOTAL_JOBS"
echo "Successful: $success"
echo "Failed: $failed"
echo ""

if [ $failed -eq 0 ]; then
    echo "✓ All jobs approved successfully!"
else
    echo "⚠ Some jobs failed to approve. Check the output above."
fi

echo ""
echo "Next steps:"
echo "1. Wait 5-10 minutes for all jobs to finalize"
echo "2. Check DynamoDB for completed jobs"
echo "3. Wait 24-48 hours for Cost Explorer data"
echo "4. Run: python3 scripts/collect_metrics.py"
echo ""
