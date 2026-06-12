import os
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

loader = PyPDFLoader("New-Hire-Booklet-Version-3-2022.pdf")
pages = loader.load()

splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
docs = splitter.split_documents(pages)

embeddings = OpenAIEmbeddings(api_key=OPENAI_API_KEY)
vectorstore = FAISS.from_documents(docs, embeddings)

vectorstore.save_local("faiss_index")
print(f"FAISS index built successfully! {len(docs)} chunks indexed.")