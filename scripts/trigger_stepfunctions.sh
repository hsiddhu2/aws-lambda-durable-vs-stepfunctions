#!/bin/bash
set -e

echo "========================================="
echo "Triggering 1,000 Step Functions Executions"
echo "========================================="
echo ""

STATE_MACHINE_ARN="arn:aws:states:us-east-1:YOUR_AWS_ACCOUNT_ID:stateMachine:ETL-Pipeline-StateMachine"
BUCKET="etl-stepfn-rawdatabucket-vjseqyewtowe"

# Get list of files from S3
echo "Getting list of files from S3..."
FILES=$(aws s3 ls s3://$BUCKET/uploads/ | awk '{print $4}')
FILE_COUNT=$(echo "$FILES" | wc -l | tr -d ' ')

echo "Found $FILE_COUNT files in S3"
echo ""

if [ "$FILE_COUNT" -eq 0 ]; then
    echo "❌ No files found in S3 bucket. Please upload files first."
    exit 1
fi

echo "Starting Step Functions executions..."
echo ""

COUNT=0
FAILED=0

for FILE in $FILES; do
    # Create execution name
    EXECUTION_NAME="etl-stepfn-$(date +%Y%m%d%H%M%S)-${FILE%.csv}"
    
    # Create input JSON
    INPUT=$(cat <<EOF
{
  "bucket": "$BUCKET",
  "key": "uploads/$FILE"
}
EOF
)
    
    # Start execution
    if aws stepfunctions start-execution \
        --state-machine-arn "$STATE_MACHINE_ARN" \
        --name "$EXECUTION_NAME" \
        --input "$INPUT" \
        --output json > /dev/null 2>&1; then
        COUNT=$((COUNT + 1))
        if [ $((COUNT % 100)) -eq 0 ]; then
            echo "  ✅ Started $COUNT executions..."
        fi
    else
        FAILED=$((FAILED + 1))
        if [ $FAILED -le 5 ]; then
            echo "  ⚠️  Failed to start execution for $FILE"
        fi
    fi
    
    # Small delay to avoid throttling
    if [ $((COUNT % 50)) -eq 0 ]; then
        sleep 1
    fi
done

echo ""
echo "========================================="
echo "Execution Summary"
echo "========================================="
echo "  Total files: $FILE_COUNT"
echo "  Started: $COUNT"
echo "  Failed: $FAILED"
echo ""

if [ $COUNT -gt 0 ]; then
    echo "✅ Step Functions executions started successfully!"
    echo ""
    echo "Monitor progress:"
    echo "  aws stepfunctions list-executions \\"
    echo "    --state-machine-arn $STATE_MACHINE_ARN \\"
    echo "    --status-filter RUNNING \\"
    echo "    --max-results 10"
else
    echo "❌ No executions were started. Please check the error messages above."
fi
