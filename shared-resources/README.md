# ETL Approval System - Shared Resources

This stack provides the shared infrastructure for manual approval workflows in both Step Functions and Durable Functions ETL pipelines.

## Components

1. **DynamoDB Table** (`etl-pending-approvals`): Stores pending approval requests with task tokens/callback IDs
2. **SNS Topic** (`etl-approval-notifications`): Sends email notifications to reviewers
3. **API Gateway** + **Lambda**: REST API for approving/rejecting jobs
4. **Approval Handler Lambda**: Processes approval/rejection requests and resumes workflows

## Deployment

### Prerequisites
- AWS SAM CLI installed
- AWS credentials configured
- Valid email address for notifications

### Deploy Shared Resources

```bash
cd shared-resources
sam build
sam deploy --guided
```

During guided deployment, provide:
- Stack name: `etl-shared-resources`
- AWS Region: `us-east-1` (or your preferred region)
- ApproverEmail: Your email address for notifications
- Confirm changes: Y
- Allow SAM CLI IAM role creation: Y

**Important**: After deployment, check your email and confirm the SNS subscription!

### Deploy ETL Pipelines

After shared resources are deployed, deploy the ETL pipelines:

```bash
# Deploy Step Functions pipeline
cd ../step-functions
sam build
sam deploy

# Deploy Durable Functions pipeline
cd ../durable-functions
sam build
sam deploy
```

## Usage

### 1. Trigger an ETL Job

Upload a CSV file to trigger the pipeline:

```bash
# For Step Functions
aws s3 cp test-data/data_0001.csv s3://etl-stepfn-rawdatabucket-<account-id>/data_0001.csv

# For Durable Functions
aws s3 cp test-data/data_0001.csv s3://etl-raw-data-bucket-<account-id>/data_0001.csv
```

Or start execution manually:

```bash
# Step Functions
aws stepfunctions start-execution \
  --state-machine-arn <state-machine-arn> \
  --name test-execution-001 \
  --input '{"bucket": "etl-stepfn-rawdatabucket-<account-id>", "key": "data_0001.csv"}'

# Durable Functions
aws lambda invoke \
  --function-name ETLDurableOrchestrator:live \
  --invocation-type Event \
  --payload '{"bucket": "etl-raw-data-bucket-<account-id>", "key": "data_0001.csv"}' \
  response.json
```

### 2. Receive Approval Notification

You'll receive an email with:
- Job ID
- Summary of processed data
- API endpoints for approval/rejection
- Sample curl commands

### 3. Approve or Reject the Job

**Using the helper script:**

```bash
# Approve
./scripts/approve_job.sh approve <job-id> "your-name" "Data looks good"

# Reject
./scripts/approve_job.sh reject <job-id> "your-name" "Data quality issues"

# Check status
./scripts/check_approval_status.sh <job-id>
```

**Using curl directly:**

```bash
# Get API URL
API_URL=$(aws cloudformation list-exports \
    --query "Exports[?Name=='ETL-ApprovalApiUrl'].Value" \
    --output text)

# Approve
curl -X POST "$API_URL/approve/<job-id>" \
  -H "Content-Type: application/json" \
  -d '{"reviewer": "your-name", "reason": "Data looks good"}'

# Reject
curl -X POST "$API_URL/reject/<job-id>" \
  -H "Content-Type: application/json" \
  -d '{"reviewer": "your-name", "reason": "Data quality issues"}'

# Check status
curl -X GET "$API_URL/status/<job-id>"
```

### 4. Workflow Resumes

After approval/rejection:
- **Step Functions**: State machine resumes from WaitForApproval state
- **Durable Functions**: Lambda function resumes from wait_for_callback

## API Endpoints

### GET /status/{jobId}
Get the current approval status of a job.

**Response:**
```json
{
  "jobId": "etl-durable-20260222001234-data_0001.csv",
  "status": "pending",
  "summary": {
    "record_count": 50,
    "columns": ["id", "name", "email"]
  },
  "requestedAt": "2026-02-22T05:12:34.567Z",
  "workflowType": "durable-functions"
}
```

### POST /approve/{jobId}
Approve a pending job.

**Request Body:**
```json
{
  "reviewer": "john-doe",
  "reason": "Data quality verified"
}
```

**Response:**
```json
{
  "message": "Job etl-durable-20260222001234-data_0001.csv approved successfully",
  "jobId": "etl-durable-20260222001234-data_0001.csv",
  "approved": true,
  "reviewer": "john-doe"
}
```

### POST /reject/{jobId}
Reject a pending job.

**Request Body:**
```json
{
  "reviewer": "john-doe",
  "reason": "Data quality issues detected"
}
```

**Response:**
```json
{
  "message": "Job etl-durable-20260222001234-data_0001.csv rejected successfully",
  "jobId": "etl-durable-20260222001234-data_0001.csv",
  "approved": false,
  "reviewer": "john-doe"
}
```

## Cost Implications

### During Wait Period (Key Comparison Point!)

**Durable Functions:**
- Lambda execution ENDS after storing approval request
- **Zero compute charges** while waiting (hours/days)
- Only DynamoDB storage costs (~$0.25/GB-month)
- Lambda resumes only when approved/rejected

**Step Functions:**
- State machine pauses at task token state
- **Zero compute charges** while waiting
- State transitions already counted (before and after wait)
- No additional charges during wait period

**Key Insight:** Both services have zero compute charges during wait, but:
- Durable Functions: Pay per durable operation + storage
- Step Functions: Pay per state transition (fixed cost regardless of wait duration)

## Monitoring

### Check Pending Approvals

```bash
aws dynamodb scan \
  --table-name etl-pending-approvals \
  --filter-expression "#status = :pending" \
  --expression-attribute-names '{"#status": "status"}' \
  --expression-attribute-values '{":pending": {"S": "pending"}}'
```

### View CloudWatch Logs

```bash
# Approval Handler logs
aws logs tail /aws/lambda/ETL-Approval-Handler --follow

# Step Functions approval lambda
aws logs tail /aws/lambda/etl-stepfn-ApprovalFunction-<id> --follow

# Durable Functions orchestrator
aws logs tail /aws/lambda/ETLDurableOrchestrator --follow
```

## Cleanup

```bash
# Delete shared resources stack
aws cloudformation delete-stack --stack-name etl-shared-resources

# Delete ETL pipeline stacks
aws cloudformation delete-stack --stack-name etl-stepfn
aws cloudformation delete-stack --stack-name etl-durable
```

## Troubleshooting

### Email not received
- Check SNS subscription confirmation in your email
- Verify email address in stack parameters
- Check SNS topic subscriptions in AWS Console

### Approval not resuming workflow
- Check approval handler CloudWatch logs
- Verify task token/callback ID in DynamoDB
- Ensure IAM permissions for send_task_success/lambda:InvokeFunction

### Job not found error
- Verify job ID is correct (check DynamoDB table)
- Job may have expired (24-hour TTL)
- Job may have already been processed
