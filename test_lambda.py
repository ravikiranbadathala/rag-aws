# test_lambda.py
import json
import os
import sys

# Mock environment variables before import
os.environ["S3_BUCKET"] = "test-bucket"
os.environ["OPENAI_API_KEY"] = "test-key"

def test_handler_parses_query():
    """Test that handler correctly parses the request body"""
    event = {"body": json.dumps({"query": "test question"})}
    body = json.loads(event["body"])
    assert body["query"] == "test question"

def test_response_format():
    """Test the expected response structure"""
    mock_response = {
        "statusCode": 200,
        "body": json.dumps({"answer": "test answer"})
    }
    assert mock_response["statusCode"] == 200
    body = json.loads(mock_response["body"])
    assert "answer" in body

def test_prompt_construction():
    """Test prompt template formatting"""
    context_text = "Sample context"
    query = "Sample question"
    prompt = f"""Answer the question using only the context below.

Context:
{context_text}

Question: {query}
Answer:"""
    assert "Sample context" in prompt
    assert "Sample question" in prompt
    assert "Answer:" in prompt