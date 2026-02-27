import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def s3_event():
    return {"Records": [{"s3": {"bucket": {"name": "test-bucket"}, "object": {"key": "uploads/test.csv"}}}]}


@pytest.fixture
def sample_csv():
    return "id,name,date,amount\n1,Widget A,2025-01-15,99.99\n2,Gadget X,2025-02-20,149.50\n,Invalid,,\n"


class TestETLDurableFunction:
    """
    Tests use LocalDurableTestRunner from the AWS Durable Execution SDK testing package.
    Install: pip install aws-lambda-durable-execution-sdk-testing

    Example usage:
        runner = LocalDurableTestRunner(handler_function=lambda_handler, skip_time=True)
        execution = runner.run(event=s3_event)
        assert execution.get_operation("extract-data").status == "SUCCEEDED"
    """

    def test_extract_parses_csv(self, sample_csv):
        from steps.extract import extract_data
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=sample_csv.encode())),
            "ContentLength": len(sample_csv)
        }
        mock_ctx = MagicMock()
        with patch("steps.extract.s3_client", mock_s3):
            result = extract_data("bucket", "key.csv", mock_ctx)
            assert result["record_count"] == 3

    def test_transform_filters_invalid(self):
        from steps.transform import transform_data
        raw = [
            {"id": "1", "name": "A", "amount": "10"},
            {"id": "", "name": "", "amount": ""},
        ]
        mock_ctx = MagicMock()
        result = transform_data(raw, {}, mock_ctx)
        assert result["valid_records"] == 1
        assert result["rejected_records"] == 1
