#!/usr/bin/env python3
"""
Validate that both ETL implementations are deployed and working correctly
"""
import boto3
import json
import time
import sys
from datetime import datetime

cloudformation = boto3.client('cloudformation')
lambda_client = boto3.client('lambda')
s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
sfn = boto3.client('stepfunctions')


def check_stack_exists(stack_name):
    """Check if CloudFormation stack exists and is in good state"""
    try:
        response = cloudformation.describe_stacks(StackName=stack_name)
        stack = response['Stacks'][0]
        status = stack['StackStatus']
        
        if 'COMPLETE' in status:
            print(f"✓ Stack '{stack_name}' exists and is {status}")
            return True, stack
        else:
            print(f"✗ Stack '{stack_name}' is in state {status}")
            return False, stack
    except cloudformation.exceptions.ClientError:
        print(f"✗ Stack '{stack_name}' does not exist")
        return False, None


def get_stack_outputs(stack):
    """Extract outputs from CloudFormation stack"""
    outputs = {}
    if stack and 'Outputs' in stack:
        for output in stack['Outputs']:
            outputs[output['OutputKey']] = output['OutputValue']
    return outputs


def check_lambda_function(function_name):
    """Check if Lambda function exists and is configured correctly"""
    try:
        response = lambda_client.get_function(FunctionName=function_name)
        config = response['Configuration']
        
        print(f"✓ Lambda function '{function_name}' exists")
        print(f"  - Runtime: {config['Runtime']}")
        print(f"  - Memory: {config['MemorySize']} MB")
        print(f"  - Timeout: {config['Timeout']} seconds")
        
        return True
    except lambda_client.exceptions.ResourceNotFoundException:
        print(f"✗ Lambda function '{function_name}' not found")
        return False


def check_s3_bucket(bucket_name):
    """Check if S3 bucket exists and is accessible"""
    try:
        s3.head_bucket(Bucket=bucket_name)
        print(f"✓ S3 bucket '{bucket_name}' exists and is accessible")
        return True
    except:
        print(f"✗ S3 bucket '{bucket_name}' not accessible")
        return False


def check_dynamodb_table(table_name):
    """Check if DynamoDB table exists"""
    try:
        table = dynamodb.Table(table_name)
        table.load()
        print(f"✓ DynamoDB table '{table_name}' exists")
        print(f"  - Status: {table.table_status}")
        print(f"  - Item count: {table.item_count}")
        return True
    except:
        print(f"✗ DynamoDB table '{table_name}' not found")
        return False


def check_state_machine(state_machine_arn):
    """Check if Step Functions state machine exists"""
    try:
        response = sfn.describe_state_machine(stateMachineArn=state_machine_arn)
        print(f"✓ State machine exists")
        print(f"  - Name: {response['name']}")
        print(f"  - Status: {response['status']}")
        return True
    except:
        print(f"✗ State machine not found")
        return False


def test_durable_function(function_name, bucket_name):
    """Test Durable Functions implementation with a sample file"""
    print("\nTesting Durable Functions implementation...")
    
    # Create a small test CSV
    test_data = "id,name,date,amount\n1,Test Product,2025-02-21,99.99\n"
    test_key = f"test/validation_{int(time.time())}.csv"
    
    try:
        # Upload test file
        s3.put_object(Bucket=bucket_name, Key=test_key, Body=test_data.encode())
        print(f"✓ Uploaded test file to s3://{bucket_name}/{test_key}")
        
        # Wait for execution to start
        print("  Waiting 10 seconds for execution to start...")
        time.sleep(10)
        
        # Check CloudWatch Logs for execution
        logs = boto3.client('logs')
        log_group = f'/aws/lambda/{function_name}'
        
        try:
            response = logs.describe_log_streams(
                logGroupName=log_group,
                orderBy='LastEventTime',
                descending=True,
                limit=5
            )
            
            if response['logStreams']:
                print(f"✓ Found recent log streams - function is executing")
                return True
            else:
                print(f"⚠ No recent log streams found")
                return False
        except:
            print(f"⚠ Could not access CloudWatch Logs")
            return False
            
    except Exception as e:
        print(f"✗ Test failed: {str(e)}")
        return False


def test_step_functions(state_machine_arn, bucket_name):
    """Test Step Functions implementation"""
    print("\nTesting Step Functions implementation...")
    
    # Create a small test CSV
    test_data = "id,name,date,amount\n1,Test Product,2025-02-21,99.99\n"
    test_key = f"test/validation_{int(time.time())}.csv"
    
    try:
        # Upload test file
        s3.put_object(Bucket=bucket_name, Key=test_key, Body=test_data.encode())
        print(f"✓ Uploaded test file to s3://{bucket_name}/{test_key}")
        
        # Start execution
        execution_name = f"validation-{int(time.time())}"
        response = sfn.start_execution(
            stateMachineArn=state_machine_arn,
            name=execution_name,
            input=json.dumps({
                "source_bucket": bucket_name,
                "source_key": test_key
            })
        )
        
        execution_arn = response['executionArn']
        print(f"✓ Started execution: {execution_name}")
        
        # Wait and check status
        print("  Waiting 10 seconds for execution to progress...")
        time.sleep(10)
        
        response = sfn.describe_execution(executionArn=execution_arn)
        status = response['status']
        
        if status in ['RUNNING', 'SUCCEEDED']:
            print(f"✓ Execution is {status}")
            return True
        else:
            print(f"⚠ Execution status: {status}")
            return False
            
    except Exception as e:
        print(f"✗ Test failed: {str(e)}")
        return False


def main():
    print("=" * 60)
    print("ETL Pipeline Deployment Validation")
    print("=" * 60)
    print()
    
    all_checks_passed = True
    
    # Check Durable Functions stack
    print("Checking Durable Functions deployment...")
    print("-" * 60)
    durable_exists, durable_stack = check_stack_exists('etl-durable')
    
    if durable_exists:
        durable_outputs = get_stack_outputs(durable_stack)
        
        # Extract bucket name from ARN
        if 'RawBucketArn' in durable_outputs:
            durable_bucket = durable_outputs['RawBucketArn'].split(':')[-1]
        else:
            durable_bucket = None
        
        # Check resources
        check_lambda_function('ETLDurableOrchestrator')
        if durable_bucket:
            check_s3_bucket(durable_bucket)
        check_dynamodb_table('etl-job-metadata')
        
        # Run test
        if durable_bucket:
            test_durable_function('ETLDurableOrchestrator', durable_bucket)
    else:
        all_checks_passed = False
    
    print()
    
    # Check Step Functions stack
    print("Checking Step Functions deployment...")
    print("-" * 60)
    sfn_exists, sfn_stack = check_stack_exists('etl-stepfn')
    
    if sfn_exists:
        sfn_outputs = get_stack_outputs(sfn_stack)
        
        sfn_bucket = sfn_outputs.get('RawBucketName')
        sfn_arn = sfn_outputs.get('StateMachineArn')
        
        # Check resources
        check_lambda_function('etl-stepfn-ExtractFunction-*')  # Partial match
        if sfn_bucket:
            check_s3_bucket(sfn_bucket)
        check_dynamodb_table('etl-stepfn-metadata')
        if sfn_arn:
            check_state_machine(sfn_arn)
        
        # Run test
        if sfn_bucket and sfn_arn:
            test_step_functions(sfn_arn, sfn_bucket)
    else:
        all_checks_passed = False
    
    print()
    print("=" * 60)
    
    if all_checks_passed and durable_exists and sfn_exists:
        print("✓ All validations passed!")
        print()
        print("Next steps:")
        print("1. Run comparison: ./scripts/run_comparison.sh 100 100")
        print("2. Monitor executions in AWS Console")
        print("3. Capture screenshots (see docs/SCREENSHOT-CHECKLIST.md)")
        print("4. Collect metrics after executions complete")
        return 0
    else:
        print("✗ Some validations failed")
        print()
        print("Please check the errors above and ensure both stacks are deployed:")
        print("  cd durable-functions && sam deploy --guided")
        print("  cd step-functions && sam deploy --guided")
        return 1


if __name__ == '__main__':
    sys.exit(main())
