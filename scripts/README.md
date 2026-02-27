# Scripts

Utility scripts for testing and managing the ETL comparison project.

All scripts are fully automated and require no configuration - they automatically retrieve resource names and ARNs from your deployed CloudFormation stacks.

## Setup

Install Python dependencies:

```bash
pip install -r scripts/requirements.txt
```

## Testing Scripts

### generate_test_data.py

Generate CSV test files for the ETL pipeline.

```bash
python scripts/generate_test_data.py --count 1000 --output test-data/
```

**Options:**
- `--count`: Number of CSV files to generate (default: 10)
- `--records`: Records per file (default: 100)
- `--output`: Output directory (default: test-data/)

### trigger_stepfunctions.sh

Trigger Step Functions workflows with test data. Automatically retrieves the state machine ARN and S3 bucket from CloudFormation.

```bash
./scripts/trigger_stepfunctions.sh
```

This script will:
- Get the state machine ARN from the etl-stepfn stack
- Get the S3 bucket name from the etl-stepfn stack
- List all files in the bucket
- Start a Step Functions execution for each file

## Approval Scripts

### approve_all_jobs.sh

Approve all pending workflows in both systems. Automatically retrieves the approval API URL from CloudFormation.

```bash
./scripts/approve_all_jobs.sh
```

Optional parameters:
```bash
./scripts/approve_all_jobs.sh [reviewer-name] [reason]
```

This script will:
- Get the approval API URL from CloudFormation exports
- Query DynamoDB for all pending approvals
- Approve each workflow via the API

### approve_job.sh

Approve a specific workflow by execution ID.

```bash
./scripts/approve_job.sh <execution_id>
```

### reject_job.sh

Reject a specific workflow by execution ID.

```bash
./scripts/reject_job.sh <execution_id>
```

### check_approval_status.sh

Check the approval status of workflows.

```bash
./scripts/check_approval_status.sh
```

## Cleanup

### delete_all_stacks.sh

Delete all CloudFormation stacks and resources. Automatically retrieves resource names from CloudFormation.

```bash
./scripts/delete_all_stacks.sh
```

This will:
- Get S3 bucket names from CloudFormation
- Empty all S3 buckets
- Get state machine ARN from CloudFormation
- Stop all running Step Functions executions
- Delete all CloudFormation stacks (etl-durable, etl-stepfn, etl-shared-resources)
- Verify deletion

**Note:** You'll be prompted to confirm before deletion.

## Quick Start Workflow

1. **Deploy the stacks** (see main README)

2. **Generate test data:**
   ```bash
   python scripts/generate_test_data.py --count 100
   ```

3. **Upload to S3 for Durable Functions:**
   ```bash
   # Get bucket name from CloudFormation
   BUCKET=$(aws cloudformation describe-stacks \
     --stack-name etl-durable \
     --query 'Stacks[0].Outputs[?OutputKey==`RawDataBucket`].OutputValue' \
     --output text)
   
   # Upload files
   aws s3 cp test-data/ s3://$BUCKET/uploads/ --recursive
   ```

4. **Trigger Step Functions:**
   ```bash
   ./scripts/trigger_stepfunctions.sh
   ```

5. **Approve workflows:**
   ```bash
   ./scripts/approve_all_jobs.sh
   ```

6. **Monitor in AWS Console**

7. **Cleanup when done:**
   ```bash
   ./scripts/delete_all_stacks.sh
   ```

## Key Features

- **Zero Configuration**: All scripts automatically retrieve resource names and ARNs from CloudFormation
- **No Hardcoded Values**: No need to edit scripts or replace placeholders
- **Error Handling**: Scripts check if resources exist before attempting operations
- **User Friendly**: Clear output and progress indicators

## Troubleshooting

### Permission Denied

```bash
chmod +x scripts/*.sh
```

### AWS CLI Not Configured

```bash
aws configure
```

### Script Not Found

Make sure you're running scripts from the repository root:

```bash
./scripts/script_name.sh
```
