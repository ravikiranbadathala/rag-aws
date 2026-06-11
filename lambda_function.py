import json
import boto3
import os
from langchain_aws import BedrockEmbeddings, ChatBedrock
from langchain_community.vectorstores import OpenSearchVectorSearch
from langchain.chains import RetrievalQA

def handler(event, context):
    body = json.loads(event["body"])
    query = body["query"]

    embeddings = BedrockEmbeddings(
        model_id="amazon.titan-embed-text-v1",
        region_name=os.environ["AWS_REGION"]
    )

    vectorstore = OpenSearchVectorSearch(
        opensearch_url=os.environ["OPENSEARCH_ENDPOINT"],
        index_name="southwest-docs",
        embedding_function=embeddings,
    )

    llm = ChatBedrock(
        model_id="anthropic.claude-3-sonnet-20240229-v1:0"
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
