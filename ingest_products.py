import json
import os
from dotenv import load_dotenv
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma # লেটেস্ট ইম্পোর্ট
from langchain_core.documents import Document

load_dotenv()

def ingest():
    # 1. Load Multilingual Embeddings
    model_name = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embeddings = HuggingFaceEmbeddings(model_name=model_name)

    # 2. Load products.json
    file_path = "data/products.json"
    if not os.path.exists(file_path):
        print(f"Error: {file_path} not found.")
        return

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 3. Create Documents
    documents = []
    for item in data:
        # কালার এবং সাইজ লিস্ট আকারে আছে, তাই সেগুলোকে কমা দিয়ে টেক্সট বানানো হচ্ছে
        colors_text = ", ".join(item.get('colors', []))
        sizes_text = ", ".join(item.get('sizes', []))
        
        # ভেক্টর সার্চের সুবিধার জন্য সব তথ্য একসাথে
        searchable_text = f"Product Name: {item.get('name', '')}. Category: {item.get('category', '')}. Colors available: {colors_text}. Sizes available: {sizes_text}. Description: {item.get('description', '')}."
        
        # মেটাডেটায় product_id সেভ রাখা
        doc = Document(
            page_content=searchable_text,
            metadata={"product_id": item.get("product_id", "")}
        )
        documents.append(doc)
    
    # 4. Create and persist Vector DB
    vector_db = Chroma.from_documents(
        documents=documents,
        embedding=embeddings,
        persist_directory="./chroma_db",
        collection_name="products_collection" # এখানে main.py এর সাথে মিল রেখে products_collection দেওয়া হয়েছে
    )
    
    # প্রিন্ট মেসেজ ফিক্স করা হয়েছে
    print(f"✅ Indexed {len(documents)} products successfully in ChromaDB!")

if __name__ == "__main__":
    ingest()