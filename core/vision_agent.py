# core/vision_agent.py
import os
# এখানে আমরা llm_provider থেকে ভিশন এলএলএম ইম্পোর্ট করছি
from core.llm_provider import get_vision_llm 
from langgraph.prebuilt import create_react_agent
from tools import vision_to_product_search_tool

vision_system_prompt = """You are an expert Vision AI Assistant for an online shop.

STRICT RULES:
1. Carefully analyze the uploaded image and identify the product (type, color, design, etc.).
2. IMMEDIATELY call 'vision_to_product_search_tool' with a concise search query based on your analysis.
3. DO NOT invent or guess products, prices, or stock status. ONLY show products explicitly returned by the tool.
4. If similar products are found, present them nicely to the user.
5. If the image is unclear, not a product, or if the tool finds no match, politely inform the user and ask for a better image or text query.
6. Reply in the user's language (Bangla or English)."""

# ❌ এখান থেকে পুরনো get_vision_llm() ফাংশনটি মুছে দেওয়া হয়েছে!

def vision_agent_node(state):
    # ✅ এখন এটি সরাসরি llm_provider এর আপডেটেড মডেলটি ব্যবহার করবে
    llm = get_vision_llm()
    tools = [vision_to_product_search_tool]
    agent = create_react_agent(llm, tools, prompt=vision_system_prompt)
    result = agent.invoke(state)
    return {"messages": result["messages"]}