import json
import boto3
import os
import tempfile
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_community.vectorstores import FAISS

def handler(event, context):
    body = json.loads(event["body"])
    query = body["query"]

    embeddings = OpenAIEmbeddings(
        api_key=os.environ["OPENAI_API_KEY"],
        model="text-embedding-3-small"
    )

    s3 = boto3.client("s3")
    bucket = os.environ["S3_BUCKET"]

    with tempfile.TemporaryDirectory() as tmpdir:
        s3.download_file(bucket, "faiss_index/index.faiss", f"{tmpdir}/index.faiss")
        s3.download_file(bucket, "faiss_index/index.pkl", f"{tmpdir}/index.pkl")

        vectorstore = FAISS.load_local(
            tmpdir,
            embeddings,
            allow_dangerous_deserialization=True
        )

    # Retrieve top 3 relevant chunks
    docs = vectorstore.similarity_search(query, k=3)
    context_text = "\n\n".join(d.page_content for d in docs)

    # Call LLM directly
    llm = ChatOpenAI(
        model="gpt-3.5-turbo",
        api_key=os.environ["OPENAI_API_KEY"]
    )

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