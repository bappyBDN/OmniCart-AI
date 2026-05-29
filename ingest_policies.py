import json
import os
from dotenv import load_dotenv
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma # লেটেস্ট ইম্পোর্ট (ওয়ার্নিং ফিক্স করা হয়েছে)
from langchain_core.documents import Document

load_dotenv()

def ingest():
    # 1. Load Multilingual Embeddings
    model_name = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embeddings = HuggingFaceEmbeddings(model_name=model_name)

    # 2. Load policies.json with basic error handling
    file_path = "data/policies.json"
    if not os.path.exists(file_path):
        print(f"Error: {file_path} not found.")
        return

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 3. Create Documents
    documents = [
        Document(page_content=item["content"], metadata={"topic": item.get("topic", "general")}) 
        for item in data
    ]
    
    # 4. Create and persist Vector DB
    vector_db = Chroma.from_documents(
        documents=documents,
        embedding=embeddings,
        persist_directory="./chroma_db",
        collection_name="policy_collection"
    )
    
    print(f"✅ Indexed {len(documents)} policies successfully in ChromaDB!")

if __name__ == "__main__":
    ingest()