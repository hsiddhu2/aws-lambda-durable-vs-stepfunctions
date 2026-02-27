import json
import logging
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    """Transform step for Step Functions ETL pipeline"""
    extract_result = event.get("extractResult", event)
    raw_data = extract_result["data"]
    schema_config = extract_result.get("schema", {})
    
    logger.info(f"Transforming {len(raw_data)} records")
    
    valid_records, rejected_records = [], []
    
    for i, record in enumerate(raw_data):
        try:
            cleaned = {k: v.strip() if isinstance(v, str) else v for k, v in record.items()}
            
            if not cleaned.get("id") or not cleaned.get("name"):
                rejected_records.append({"index": i, "reason": "Missing required field"})
                continue
            
            if "date" in cleaned:
                cleaned["date"] = normalize_date(cleaned["date"])
            
            cleaned["_processed_at"] = datetime.utcnow().isoformat()
            
            for key in ["amount", "quantity", "price"]:
                if key in cleaned and cleaned[key]:
                    try:
                        cleaned[key] = float(cleaned[key])
                    except ValueError:
                        cleaned[key] = None
            
            valid_records.append(cleaned)
        except Exception as e:
            rejected_records.append({"index": i, "reason": str(e)})
    
    logger.info(f"Transformed: {len(valid_records)} valid, {len(rejected_records)} rejected")
    
    return {
        "data": valid_records,
        "valid_records": len(valid_records),
        "rejected_records": len(rejected_records),
        "rejection_details": rejected_records[:100],
        "source_bucket": extract_result.get("source_bucket"),
        "source_key": extract_result.get("source_key")
    }


def normalize_date(date_str):
    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d"]:
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str
