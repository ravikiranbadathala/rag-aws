import json
import boto3
import os
import tempfile
import time
import logging
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from agent_graph import build_graph

logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3_BUCKET = os.environ["S3_BUCKET"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
MODEL_VERSION = "gpt-5-mini"
PROMPT_VERSION = "v2.0-langgraph-multiagent"

s3 = boto3.client("s3")
cloudwatch = boto3.client("cloudwatch")

embeddings = OpenAIEmbeddings(api_key=OPENAI_API_KEY, model="text-embedding-3-small")

_vectorstore = None
_graph = None

def get_resources():
    global _vectorstore, _graph
    if _vectorstore is None:
        with tempfile.TemporaryDirectory() as tmpdir:
            s3.download_file(S3_BUCKET, "faiss_index/index.faiss", f"{tmpdir}/index.faiss")
            s3.download_file(S3_BUCKET, "faiss_index/index.pkl", f"{tmpdir}/index.pkl")
            _vectorstore = FAISS.load_local(
                tmpdir, embeddings, allow_dangerous_deserialization=True
            )
        _graph = build_graph(_vectorstore)
    return _vectorstore, _graph


def put_metric(name, value, unit="Count", dimensions=None):
    try:
        metric_data = {"MetricName": name, "Value": value, "Unit": unit}
        if dimensions:
            metric_data["Dimensions"] = dimensions
        cloudwatch.put_metric_data(Namespace="SouthwestRAG", MetricData=[metric_data])
    except Exception as e:
        logger.error(f"Failed to emit metric {name}: {e}")


def handler(event, context):
    start_time = time.time()
    body = json.loads(event["body"])
    query = body["query"]
    request_id = context.aws_request_id if context else "local"

    logger.info(json.dumps({
        "event": "request_received",
        "request_id": request_id,
        "model_version": MODEL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "query": query
    }))

    vectorstore, graph = get_resources()

    try:
        result = graph.invoke({
            "query": query,
            "strategy": "",
            "retrieved_chunks": [],
            "answer": "",
            "critique": "",
            "is_grounded": False,
            "retry_count": 0
        })

        latency_ms = (time.time() - start_time) * 1000

        logger.info(json.dumps({
            "event": "request_completed",
            "request_id": request_id,
            "strategy_used": result["strategy"],
            "retry_count": result["retry_count"],
            "is_grounded": result["is_grounded"],
            "latency_ms": round(latency_ms, 2),
            "status": "success"
        }))

        put_metric("RequestLatency", latency_ms, "Milliseconds", [{"Name": "Strategy", "Value": result["strategy"]}])
        put_metric("RequestSuccess", 1, "Count")
        put_metric("RetryCount", result["retry_count"], "Count")
        put_metric("GroundedResponse", 1 if result["is_grounded"] else 0, "Count")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "answer": result["answer"],
                "metadata": {
                    "strategy_used": result["strategy"],
                    "retry_count": result["retry_count"],
                    "is_grounded": result["is_grounded"],
                    "critique": result["critique"],
                    "model_version": MODEL_VERSION,
                    "prompt_version": PROMPT_VERSION,
                    "latency_ms": round(latency_ms, 2)
                }
            })
        }

    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        logger.error(json.dumps({
            "event": "request_failed",
            "request_id": request_id,
            "error": str(e),
            "latency_ms": round(latency_ms, 2)
        }))
        put_metric("RequestError", 1, "Count")
        raise