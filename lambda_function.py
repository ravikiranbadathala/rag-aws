import json
import boto3
import os
import tempfile
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_community.vectorstores import FAISS

S3_BUCKET = os.environ["S3_BUCKET"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

s3 = boto3.client("s3")

embeddings = OpenAIEmbeddings(
    api_key=OPENAI_API_KEY,
    model="text-embedding-3-small"
)

llm = ChatOpenAI(
    model="gpt-5-mini",
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
    """Generate a hypothetical answer to embed for retrieval (HyDE)"""
    prompt = f"""Write a short hypothetical passage (2-3 sentences) that would answer this question,
as if it came from a company policy document. Be specific and use plausible details.

Question: {query}

Hypothetical passage:"""
    response = llm.invoke(prompt)
    return response.content


def handler(event, context):
    body = json.loads(event["body"])
    query = body["query"]
    mode = body.get("mode", "naive")  # "naive", "multi_query", "rerank", "hyde"

    vectorstore = get_vectorstore()

    if mode == "multi_query":
        queries = generate_query_variations(query)
        all_docs = []
        seen = set()
        for q in queries:
            docs = vectorstore.similarity_search(q, k=3)
            for d in docs:
                if d.page_content not in seen:
                    seen.add(d.page_content)
                    all_docs.append(d)
        context_text = "\n\n".join(d.page_content for d in all_docs[:5])

    elif mode == "rerank":
        candidates = vectorstore.similarity_search(query, k=8)
        top_docs = rerank_documents(query, candidates, top_k=3)
        context_text = "\n\n".join(d.page_content for d in top_docs)

    elif mode == "hyde":
        # Generate hypothetical answer, embed THAT instead of the query
        hypothetical = generate_hypothetical_answer(query)
        docs = vectorstore.similarity_search(hypothetical, k=3)
        context_text = "\n\n".join(d.page_content for d in docs)

    else:
        docs = vectorstore.similarity_search(query, k=3)
        context_text = "\n\n".join(d.page_content for d in docs)

    prompt = f"""Answer the question using only the context below.

Context:
{context_text}

Question: {query}
Answer:"""

    response = llm.invoke(prompt)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "answer": response.content,
            "mode": mode,
            "chunks_used": len(context_text.split("\n\n"))
        })
    }