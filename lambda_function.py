import json
import boto3
import os
import tempfile
import time
import logging
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_community.vectorstores import FAISS

logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3_BUCKET = os.environ["S3_BUCKET"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
MODEL_VERSION = "gpt-5-mini"
PROMPT_VERSION = "v1.2-context-grounded"

s3 = boto3.client("s3")
cloudwatch = boto3.client("cloudwatch")

embeddings = OpenAIEmbeddings(
    api_key=OPENAI_API_KEY,
    model="text-embedding-3-small"
)

llm = ChatOpenAI(
    model=MODEL_VERSION,
    api_key=OPENAI_API_KEY
)

_vectorstore = None

def get_vectorstore():
    global _vectorstore
    if _vectorstore is None:
        with tempfile.TemporaryDirectory() as tmpdir:
            s3.download_file(S3_BUCKET, "faiss_index/index.faiss", f"{tmpdir}/index.faiss")
            s3.download_file(S3_BUCKET, "faiss_index/index.pkl", f"{tmpdir}/index.pkl")
            _vectorstore = FAISS.load_local(
                tmpdir,
                embeddings,
                allow_dangerous_deserialization=True
            )
    return _vectorstore


def generate_query_variations(query):
    prompt = f"""Generate 2 alternative phrasings of this question, one per line, no numbering:

Question: {query}"""
    response = llm.invoke(prompt)
    variations = [line.strip() for line in response.content.split("\n") if line.strip()]
    return [query] + variations[:2]


def rerank_documents(query, docs, top_k=3):
    scored = []
    for d in docs:
        prompt = f"""Rate how relevant this passage is to the question, on a scale of 0-10.
Respond with ONLY a number.

Question: {query}

Passage: {d.page_content}

Score:"""
        response = llm.invoke(prompt)
        try:
            score = float(response.content.strip())
        except ValueError:
            score = 0
        scored.append((score, d))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in scored[:top_k]]


def generate_hypothetical_answer(query):
    prompt = f"""Write a short hypothetical passage (2-3 sentences) that would answer this question,
as if it came from a company policy document. Be specific and use plausible details.

Question: {query}

Hypothetical passage:"""
    response = llm.invoke(prompt)
    return response.content


def put_metric(name, value, unit="Count", dimensions=None):
    """Emit custom CloudWatch metric"""
    try:
        metric_data = {
            "MetricName": name,
            "Value": value,
            "Unit": unit
        }
        if dimensions:
            metric_data["Dimensions"] = dimensions

        cloudwatch.put_metric_data(
            Namespace="SouthwestRAG",
            MetricData=[metric_data]
        )
    except Exception as e:
        logger.error(f"Failed to emit metric {name}: {e}")


def handler(event, context):
    start_time = time.time()
    body = json.loads(event["body"])
    query = body["query"]
    mode = body.get("mode", "naive")

    request_id = context.aws_request_id if context else "local"

    # Structured request log
    logger.info(json.dumps({
        "event": "request_received",
        "request_id": request_id,
        "mode": mode,
        "model_version": MODEL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "query_length": len(query)
    }))

    vectorstore = get_vectorstore()
    llm_calls = 1  # final answer call always happens

    try:
        if mode == "multi_query":
            queries = generate_query_variations(query)
            llm_calls += 1
            all_docs = []
            seen = set()
            for q in queries:
                docs = vectorstore.similarity_search(q, k=3)
                for d in docs:
                    if d.page_content not in seen:
                        seen.add(d.page_content)
                        all_docs.append(d)
            context_text = "\n\n".join(d.page_content for d in all_docs[:5])
            chunks_retrieved = len(all_docs[:5])

        elif mode == "rerank":
            candidates = vectorstore.similarity_search(query, k=8)
            top_docs = rerank_documents(query, candidates, top_k=3)
            llm_calls += len(candidates)
            context_text = "\n\n".join(d.page_content for d in top_docs)
            chunks_retrieved = len(top_docs)

        elif mode == "hyde":
            hypothetical = generate_hypothetical_answer(query)
            llm_calls += 1
            docs = vectorstore.similarity_search(hypothetical, k=3)
            context_text = "\n\n".join(d.page_content for d in docs)
            chunks_retrieved = len(docs)

        else:
            docs = vectorstore.similarity_search(query, k=3)
            context_text = "\n\n".join(d.page_content for d in docs)
            chunks_retrieved = len(docs)

        prompt = f"""Answer the question using only the context below.

Context:
{context_text}

Question: {query}
Answer:"""

        response = llm.invoke(prompt)
        answer = response.content

        latency_ms = (time.time() - start_time) * 1000

        # Estimate cost (gpt-5-mini approx pricing - adjust as needed)
        estimated_tokens = (len(prompt) + len(answer)) // 4  # rough estimate
        estimated_cost = (estimated_tokens / 1000) * 0.00025 * llm_calls

        # Structured success log
        logger.info(json.dumps({
            "event": "request_completed",
            "request_id": request_id,
            "mode": mode,
            "latency_ms": round(latency_ms, 2),
            "llm_calls": llm_calls,
            "chunks_retrieved": chunks_retrieved,
            "estimated_cost_usd": round(estimated_cost, 6),
            "status": "success"
        }))

        # Emit AIOps metrics
        put_metric("RequestLatency", latency_ms, "Milliseconds", [{"Name": "Mode", "Value": mode}])
        put_metric("LLMCallsPerRequest", llm_calls, "Count", [{"Name": "Mode", "Value": mode}])
        put_metric("EstimatedCostUSD", estimated_cost, "None", [{"Name": "Mode", "Value": mode}])
        put_metric("RequestSuccess", 1, "Count", [{"Name": "Mode", "Value": mode}])

        return {
            "statusCode": 200,
            "body": json.dumps({
                "answer": answer,
                "mode": mode,
                "chunks_used": chunks_retrieved,
                "metadata": {
                    "model_version": MODEL_VERSION,
                    "prompt_version": PROMPT_VERSION,
                    "latency_ms": round(latency_ms, 2),
                    "llm_calls": llm_calls,
                    "estimated_cost_usd": round(estimated_cost, 6)
                }
            })
        }

    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        logger.error(json.dumps({
            "event": "request_failed",
            "request_id": request_id,
            "mode": mode,
            "error": str(e),
            "latency_ms": round(latency_ms, 2),
            "status": "error"
        }))
        put_metric("RequestError", 1, "Count", [{"Name": "Mode", "Value": mode}])
        raise