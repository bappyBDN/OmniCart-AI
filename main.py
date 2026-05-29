# main.py
import os
from dotenv import load_dotenv

from langchain_groq import ChatGroq
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_core.tools import tool

# আপনার তৈরি করা অন্যান্য মডিউল
  # <--- create_order_tool যোগ করা হলো
from tools import search_product, create_order_tool, send_email_tool
from memory_manager import get_memory_saver, cleanup_memory  # <--- নতুন মডিউল ইমপোর্ট করা হলো

load_dotenv()

# ==========================================
# ১: RAG ডাটাবেস লোড
# ==========================================
embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
policy_db = Chroma(
    persist_directory="./chroma_db",
    embedding_function=embeddings,
    collection_name="policy_collection"
)

# ==========================================
# ২: টুলস সেটআপ
# ==========================================
from tools import search_product, create_order_tool, send_email_tool  # <--- create_order_tool যোগ করা হলো
@tool
def policy_search_tool(query: str) -> str:
    """CRITICAL: You MUST use this tool EVERY TIME the user asks about shop rules, return policy, refund, delivery, shipping, or payment."""
    results = policy_db.similarity_search(query, k=2)
    if not results:
        return "No relevant policy found in database."
    return "\n\n".join([doc.page_content for doc in results])

@tool
def product_search_tool(query: str) -> str:
    """CRITICAL: You MUST use this tool EVERY TIME the user asks about ANY product, price, stock, sizes, or wants to buy something."""
    return search_product(query)


tools = [policy_search_tool, product_search_tool, create_order_tool,send_email_tool]  # <--- create_order_tool টুলস লিস্টে যোগ করা হলো

# ==========================================
# ৩: ব্রেইন এবং লং-টার্ম মেমরি সেটআপ
# ==========================================
# প্রথম LLM (প্রাইমারি কি দিয়ে)
llm_primary = ChatGroq(
    model="llama-3.3-70b-versatile", 
    temperature=0.1, 
    api_key=os.getenv("GROQ_API_KEY")
)

# দ্বিতীয় LLM (ব্যাকআপ কি দিয়ে)
llm_backup = ChatGroq(
    model="llama-3.3-70b-versatile", 
    temperature=0.1, 
    api_key=os.getenv("GROQ_API_KEY1")
)

# Fallback লজিক: প্রাইমারি ফেইল করলে ব্যাকআপ কাজ করবে
llm = llm_primary.with_fallbacks([llm_backup])

system_prompt = """You are a highly professional customer support agent and sales executive for an online shop.
Follow these CRITICAL RULES with 100% strictness:

1. GREETING: If the user just says "Hi" or tells you their name, ONLY greet them back and ask how you can help. DO NOT ask for order details or suggest products.
2. STRICT MATCHING: If the user asks for a 'shirt', ONLY show shirts. DO NOT show pants or shorts.
3. TOOL CALLING: ALWAYS use 'product_search_tool' for products and 'policy_search_tool' for shop rules.
4. ORDER PROCESS (CRITICAL): 
   - If a user says "I want to buy/order X", ask for their: Name, Phone Number, Email, and Delivery Address.
   - Once the user provides all 4 details, call 'create_order_tool' to save the order.
   - IMMEDIATELY after 'create_order_tool' succeeds, you MUST call 'send_email_tool' using the returned Order ID, Delivery Date, and the user's email.
   - Finally, congratulate the user, give them the Order ID, and tell them a confirmation email has been sent.
5. Format the output beautifully in paragraphs or bullet points.
6. Reply in the user's language (Bangla or English)."""

# আমাদের নতুন ফাইল থেকে মেমরি সেভার আনা হচ্ছে
memory = get_memory_saver()
agent_executor = create_react_agent(llm, tools, checkpointer=memory)

# ==========================================
# ৪: চ্যাট লুপ 
# ==========================================
def start_chat():
    print("\n" + "="*50)
    print("🤖 AI Shop Assistant is Ready! (Clean Architecture)")
    print("="*50 + "\n")
    
    config = {"configurable": {"thread_id": "bappy_001"}}
    
    while True:
        try:
            user_input = input("You: ")
            if user_input.lower() in ['exit', 'quit', 'bye']:
                print("Bot: ধন্যবাদ! আবার আসবেন।")
                break
            
            # মেমরি চেক করে মেসেজ পাঠানো
            state = agent_executor.get_state(config)
            if not state.values.get("messages"):
                messages_to_send = [SystemMessage(content=system_prompt), HumanMessage(content=user_input)]
            else:
                messages_to_send = [HumanMessage(content=user_input)]
                
            response = agent_executor.invoke({"messages": messages_to_send}, config=config)
            
            # --- মেমরি ক্লিনআপ ফাংশন কল করা হলো ---
            cleanup_memory(agent_executor, config, max_history=15)
            
            # ট্র্যাকার
            for msg in response["messages"][-3:]:
                if msg.type == "tool":
                    print(f"   [🔍 Tool Used: {msg.name}]")
            
            print(f"\nBot: {response['messages'][-1].content}\n")
            
        except KeyboardInterrupt:
            print("\n\nBot: চ্যাট সেশন বন্ধ করা হয়েছে। ধন্যবাদ!")
            break

if __name__ == "__main__":
    start_chat()