# core/support_agent.py
from langgraph.prebuilt import create_react_agent

#from core.ai_agent import get_llm
from core.llm_provider import get_support_llm
from tools import policy_search_tool, send_email_to_admin
from langchain_core.prompts import ChatPromptTemplate

# Support Agent-এর নিজস্ব প্রম্পট
support_system_prompt = """You are a helpful Support Agent. 
Your primary goal is to help customers with shop policies, refunds, returns, and delivery issues.

STRICT RULES:
1. ALWAYS use 'policy_search_tool' FIRST to get accurate information about shop rules.
2. DO NOT hallucinate or invent policies.
3. 🚫 CRITICAL RESTRICTION: YOU CANNOT PROCESS REFUNDS OR CANCELLATIONS YOURSELF. If a user asks to cancel an order or get a refund, YOU MUST IMMEDIATELY call 'send_email_to_admin' to escalate to a human admin. DO NOT say "I have initiated the refund".
4. Keep your tone professional, empathetic, and polite.
5. Reply in the language the user uses (Bangla or English)."""

def support_agent_node(state):
    llm = get_support_llm()
    
    # টুলস লিস্ট
    tools = [policy_search_tool, send_email_to_admin]
    
    # সাপোর্ট এজেন্টের জন্য React Agent তৈরি
    agent = create_react_agent(llm, tools, prompt=support_system_prompt)
    
    # এজেন্টকে রান করা
    result = agent.invoke(state)
    return {"messages": result["messages"]}