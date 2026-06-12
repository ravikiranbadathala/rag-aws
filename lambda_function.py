import json
import boto3
import os
import tempfile
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_community.vectorstores import FAISS

# ---- Global scope: runs ONCE per container (warm start reuse) ----
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

_vectorstore = None  # cached across warm invocations

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

def handler(event, context):
    body = json.loads(event["body"])
    query = body["query"]

    vectorstore = get_vectorstore()

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
        "body": json.dumps({"answer": response.content})
    }