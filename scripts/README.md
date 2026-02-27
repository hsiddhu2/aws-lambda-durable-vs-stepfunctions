# Scripts

Utility scripts for testing and managing the ETL comparison project.

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

Trigger Step Functions workflows with test data.

```bash
./scripts/trigger_stepfunctions.sh
```

## Approval Scripts

### approve_all_jobs.sh

Approve all pending workflows in both systems.

```bash
./scripts/approve_all_jobs.sh
```

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

## Management Scripts

### validate_deployment.py

Validate that both implementations are deployed correctly.

```bash
python scripts/validate_deployment.py
```

Checks:
- CloudFormation stacks exist
- Lambda functions are deployed
- S3 buckets are accessible
- DynamoDB tables exist
- Step Functions state machine is active

### check_resource_status.sh

Check the status of deployed AWS resources.

```bash
./scripts/check_resource_status.sh
```

## Cleanup Scripts

### cleanup_all_resources.sh

Clean up all AWS resources (recommended).

```bash
./scripts/cleanup_all_resources.sh
```

### delete_all_stacks.sh

Delete all CloudFormation stacks.

```bash
./scripts/delete_all_stacks.sh
```

### force_cleanup.sh

Force cleanup if normal cleanup fails.

```bash
./scripts/force_cleanup.sh
```

## Quick Start Workflow

1. **Deploy the stacks** (see main README)
2. **Generate test data:**
   ```bash
   python scripts/generate_test_data.py --count 100
   ```
3. **Upload to S3 for Durable Functions:**
   ```bash
   aws s3 cp test-data/ s3://etl-raw-data-bucket-YOUR_AWS_ACCOUNT_ID/uploads/ --recursive
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
   ./scripts/cleanup_all_resources.sh
   ```

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
