import csv
import io
import boto3
import logging

logger = logging.getLogger()
s3_client = boto3.client("s3")


def extract_data(source_bucket, source_key, step_context=None):
    logger.info(f"Extracting from s3://{source_bucket}/{source_key}")
    response = s3_client.get_object(Bucket=source_bucket, Key=source_key)
    content = response["Body"].read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    records = list(reader)
    schema = {
        "columns": reader.fieldnames,
        "source_file": source_key,
        "file_size_bytes": response["ContentLength"]
    }
    logger.info(f"Extracted {len(records)} records with {len(schema['columns'])} columns")
    return {"data": records, "record_count": len(records), "schema": schema}
