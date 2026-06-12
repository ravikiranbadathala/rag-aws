import json
import boto3
import os
import tempfile
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_community.vectorstores import FAISS
from langchain.chains import RetrievalQA

def handler(event, context):
    body = json.loads(event["body"])
    query = body["query"]

    embeddings = OpenAIEmbeddings(
        api_key=os.environ["OPENAI_API_KEY"]
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

    llm = ChatOpenAI(
        model="gpt-3.5-turbo",
        api_key=os.environ["OPENAI_API_KEY"]
    )

    chain = RetrievalQA.from_chain_type(
        llm=llm,
        retriever=vectorstore.as_retriever(search_kwargs={"k": 3})
    )

    result = chain.invoke(query)

    return {
        "statusCode": 200,
        "body": json.dumps({"answer": result["result"]})
    }