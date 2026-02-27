import logging
from datetime import datetime

logger = logging.getLogger()


def transform_data(raw_data, schema_config, step_context=None):
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

    return {
        "data": valid_records,
        "valid_records": len(valid_records),
        "rejected_records": len(rejected_records),
        "rejection_details": rejected_records[:100]
    }


def normalize_date(date_str):
    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d"]:
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str
