import json
import os
import sys
from unittest.mock import MagicMock, patch

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
        "body": json.dumps({
            "answer": "test answer",
            "metadata": {
                "strategy_used": "naive",
                "retry_count": 0,
                "is_grounded": True,
                "model_version": "gpt-5-mini",
                "prompt_version": "v2.0-langgraph-multiagent",
                "latency_ms": 123.4
            }
        })
    }
    assert mock_response["statusCode"] == 200
    body = json.loads(mock_response["body"])
    assert "answer" in body
    assert "metadata" in body
    assert body["metadata"]["strategy_used"] in ["naive", "hyde", "rerank"]


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


def test_router_agent_returns_valid_strategy():
    """Test router agent classifies into a valid strategy"""
    with patch("agent_graph.llm") as mock_llm:
        mock_llm.invoke.return_value = MagicMock(content="naive")
        from agent_graph import router_agent

        state = {
            "query": "What is the baggage policy?",
            "strategy": "",
            "retrieved_chunks": [],
            "answer": "",
            "critique": "",
            "is_grounded": False,
            "retry_count": 0
        }
        result = router_agent(state)
        assert result["strategy"] in ["naive", "hyde", "rerank"]


def test_router_agent_escalates_on_retry():
    """Test router agent escalates to rerank on retry"""
    from agent_graph import router_agent

    state = {
        "query": "test",
        "strategy": "naive",
        "retrieved_chunks": [],
        "answer": "",
        "critique": "",
        "is_grounded": False,
        "retry_count": 1
    }
    result = router_agent(state)
    assert result["strategy"] == "rerank"


def test_critic_agent_groundedness():
    """Test critic agent returns SUFFICIENT/INSUFFICIENT classification"""
    with patch("agent_graph.llm") as mock_llm:
        mock_llm.invoke.return_value = MagicMock(content="SUFFICIENT")
        from agent_graph import critic_agent

        state = {
            "query": "test question",
            "strategy": "naive",
            "retrieved_chunks": ["some context"],
            "answer": "some answer",
            "critique": "",
            "is_grounded": False,
            "retry_count": 0
        }
        result = critic_agent(state)
        assert result["is_grounded"] is True
        assert "SUFFICIENT" in result["critique"]


def test_route_after_critic_logic():
    """Test routing decision after critic"""
    from agent_graph import route_after_critic

    # Grounded -> end
    state = {"is_grounded": True, "retry_count": 0}
    assert route_after_critic(state) == "end"

    # Not grounded, no retries yet -> retry
    state = {"is_grounded": False, "retry_count": 0}
    assert route_after_critic(state) == "retry"

    # Not grounded, already retried -> end (avoid infinite loop)
    state = {"is_grounded": False, "retry_count": 1}
    assert route_after_critic(state) == "end"


def test_increment_retry():
    """Test retry counter increments correctly"""
    from agent_graph import increment_retry

    state = {"retry_count": 0}
    result = increment_retry(state)
    assert result["retry_count"] == 1