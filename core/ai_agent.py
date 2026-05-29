import os
from typing import Literal
from dotenv import load_dotenv

from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.checkpoint.memory import MemorySaver

# আপনার LLM প্রোভাইডার থেকে রাউটারের জন্য মডেল ইমপোর্ট করুন
from core.llm_provider import get_router_llm
from core.sales_agent import sales_agent_node
from core.support_agent import support_agent_node
from core.vision_agent import vision_agent_node

load_dotenv()

# ==========================================
# ১. হাইব্রিড রাউটার লজিক (Keyword + LLM Text Routing)
# ==========================================
def route_user_message(state: MessagesState) -> Literal["sales_agent", "support_agent", "vision_agent"]:
    """
    প্রথমে কি-ওয়ার্ড চেক করে দ্রুত সিদ্ধান্ত নেয়। 
    কনফিউশন থাকলে বা স্পষ্ট না হলে LLM-এর সাহায্য নেয়।
    """
    messages = state["messages"]
    last_message = messages[-1]

    # ---------------------------------------------------------
    # স্টেপ ১: ভিশন চেক (সবচেয়ে দ্রুত)
    # ---------------------------------------------------------
    if isinstance(last_message.content, list):
        for item in last_message.content:
            if item.get("type") == "image_url":
                print("🚀 [FAST ROUTER] Image detected! Routing to Vision Agent.")
                return "vision_agent"

    # টেক্সট প্রসেসিং
    message_text = last_message.content if isinstance(last_message.content, str) else str(last_message.content)
    message_lower = message_text.lower()

    # ---------------------------------------------------------
    # স্টেপ ২: স্ট্রং কি-ওয়ার্ড ম্যাচিং (Fast Track)
    # ---------------------------------------------------------
    strong_support_keywords = ["refund", "রিফান্ড", "complain", "অভিযোগ", "customer care", "রিটার্ন", "return policy"]
    strong_sales_keywords = ["দাম কত", "price", "buy", "কিনব", "অর্ডার", "order", "discount", "অফার"]

    has_support = any(kw in message_lower for kw in strong_support_keywords)
    has_sales = any(kw in message_lower for kw in strong_sales_keywords)

    if has_support and not has_sales:
        print("⚡ [FAST ROUTER] Clear support keyword found. Routing to Support Agent.")
        return "support_agent"
    
    elif has_sales and not has_support:
        print("⚡ [FAST ROUTER] Clear sales keyword found. Routing to Sales Agent.")
        return "sales_agent"

    # ---------------------------------------------------------
    # স্টেপ ৩: স্মার্ট LLM ফলব্যাক (Pydantic বাদ দিয়ে ডাইরেক্ট প্রম্পট)
    # ---------------------------------------------------------
    print("🧠 [SMART ROUTER] Ambiguous message. Activating AI brain to decide...")
    
    router_prompt = f"""You are a highly intelligent, empathetic, and human-like Dispatch Supervisor for an online e-commerce platform.
    Your job is to read the user's message, understand their true underlying intent, and route them to the absolute best agent.

    AGENT ROLES & RESPONSIBILITIES:

    1. 'sales_agent' (The Friendly Store Clerk):
       - Handles ALL pre-purchase activities (searching products, prices, discounts, availability).
       - Handles the actual order placement and checkout process.
       - CRITICAL RULE: If the user provides personal details like Name, Phone, Email, or Address, ALWAYS route to 'sales_agent' because they are filling out their profile to complete an order.
       - Handles general greetings (e.g., "Hi", "Hello").

    2. 'support_agent' (The Empathetic Care Representative):
       - Handles ALL post-purchase activities, problem-solving, and customer grievances.
       - Route here if the user is complaining, angry, frustrated, or received a damaged item.
       - Handles inquiries about store policies (returns, refunds, shipping rules).

    3. 'vision_agent' (The Visual Inspector):
       - Choose this ONLY IF the user explicitly references an image they uploaded.

    User Message: "{message_text}"

    INSTRUCTION: You MUST reply with ONLY ONE WORD from the following list: sales_agent, support_agent, or vision_agent. Do not write anything else.
    """
    
    try:
        llm = get_router_llm() 
        # 🛡️ THE FIX: Pydantic এবং with_structured_output বাদ দেওয়া হয়েছে
        response = llm.invoke(router_prompt)
        
        # AI এর উত্তরটি ছোট হাতের অক্ষরে কনভার্ট করে চেক করা হচ্ছে
        decision = response.content.strip().lower()
        
        if "support" in decision:
            print("🎯 [SMART ROUTER] AI decided! Routing to support_agent.")
            return "support_agent"
        elif "vision" in decision:
            print("🎯 [SMART ROUTER] AI decided! Routing to vision_agent.")
            return "vision_agent"
        else:
            print("🎯 [SMART ROUTER] AI decided! Routing to sales_agent.")
            return "sales_agent"
            
    except Exception as e:
        print(f"⚠️ [ROUTER ERROR] LLM failed ({e}). Defaulting to Sales Agent.")
        return "sales_agent"

# ==========================================
# ৩. গ্রাফ বা ওয়ার্কফ্লো তৈরি (LangGraph)
# ==========================================
def load_agent():
    workflow = StateGraph(MessagesState)

    workflow.add_node("sales_agent", sales_agent_node)
    workflow.add_node("support_agent", support_agent_node)
    workflow.add_node("vision_agent", vision_agent_node)

    workflow.add_conditional_edges(START, route_user_message)

    workflow.add_edge("sales_agent", END)
    workflow.add_edge("support_agent", END)
    workflow.add_edge("vision_agent", END)

    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)