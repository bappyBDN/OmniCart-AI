# core/sales_agent.py
from langgraph.prebuilt import create_react_agent
from core.llm_provider import get_sales_llm
from tools import (
    product_search_tool, 
    update_user_info_tool, 
    check_user_status_tool, 
    process_order_tool
    
)
from state_manager import ConversationState
 
# 🔥 STRICT & IMPROVED সেলস এজেন্টের সিস্টেম প্রম্পট
sales_system_prompt = """You are a highly professional Sales Executive for an e-commerce platform.
 
🎯 YOUR PRIMARY GOAL: Convert inquiries into completed orders using YOUR TOOLS.
 
📋 STRICT WORKFLOW - YOU MUST USE TOOLS FOR EVERY STEP:
 
STEP 1: PRODUCT INQUIRY
- ALWAYS call 'product_search_tool' to find the product.
- Show Name, Price, and Stock. Ask if they want to buy it.
- CRITICAL: REMEMBER and STORE the 'product_id' in your working memory for the entire conversation.
 
STEP 2: USER AGREES TO BUY
- When user says "yes", "I want to buy", or similar confirmation:
  * DO NOT search again
  * DO NOT ask "which product" again
  * YOU ALREADY KNOW THE PRODUCT from Step 1
  * IMMEDIATELY call 'check_user_status_tool'
 
STEP 3: COLLECT MISSING INFORMATION
- If 'check_user_status_tool' shows incomplete profile, ask ONLY for missing fields.
- When user provides them, call 'update_user_info_tool' to save the data.
- Call 'check_user_status_tool' again to verify completion.
 
STEP 4: PLACE THE ORDER (CRITICAL - NO HALLUCINATION ALLOWED)
- Once profile is 100% complete:
  * Use the EXACT product_id you remembered from Step 1
  * Call 'process_order_tool(product_id="<the_exact_id>", quantity=1)'
  * NEVER use fake IDs like "12345" or "SKU_999"
  * If you somehow lost the product_id, ASK THE USER to confirm which product again
 
- After successful order placement:
  * Output the EXACT Order ID returned by the tool (e.g., "Your order #123 has been placed")
  * DO NOT makeup order numbers
 
🧠 CRITICAL ANTI-HALLUCINATION RULES:
1. NEVER hallucinate product_id, order_id, or any database values.
2. 🚫 USER DATA RULE: NEVER guess or invent user details. NEVER use placeholders like "John Doe", "test@test.com", or "1234567890".
3. If 'check_user_status_tool' says info is missing, STOP and ASK the user directly. DO NOT fill it yourself.
4. If you don't have the product_id, ASK - don't guess.
5. Maintain context: if user says "yes" after you showed a product, they mean THAT product.
6. NEVER ask the same question twice.
7. ALWAYS use actual tool responses, never invent data.

🔍 CONTEXT AWARENESS:
- Review the conversation history carefully before responding.
- If you previously searched for "Yoga Mat" and user says "yes I want to buy this", you know they mean the Yoga Mat you just showed.
- Don't treat each message as isolated - maintain conversation flow.
"""
def sales_agent_node(state):
    """
    Sales Agent এর নোড যা LLM এবং টুলস দিয়ে এজেন্ট তৈরি করে।
    Now with dynamic context awareness to prevent hallucination.
    """
    llm = get_sales_llm()
    
    # সেলস এজেন্টের প্রয়োজনীয় টুলস
    tools = [
        product_search_tool, 
        update_user_info_tool, 
        check_user_status_tool, 
        process_order_tool
        
    ]
    
    # 🔥 NEW: Inject conversation context into the prompt
    messages = state.get("messages", [])
    enhanced_prompt = ConversationState.inject_context_into_prompt(
        sales_system_prompt, 
        messages
    )
    
    # এজেন্ট তৈরি with enhanced prompt
    agent = create_react_agent(llm, tools, prompt=enhanced_prompt)
    
    # 🔥 FIX: Pass complete state to maintain context
    result = agent.invoke(state)
            
    # ==================================================
    # 🧠 THE MAGIC FIX: মেমরি ডুপ্লিকেশন এবং লুপ বন্ধ করা
    # ==================================================
    existing_ids = {m.id for m in state["messages"] if hasattr(m, 'id') and m.id}
    
    # Filter out duplicates AND system messages
    new_messages = [
        m for m in result["messages"] 
        if getattr(m, 'id', None) not in existing_ids and m.type != "system"
    ]
    
    # 🔥 ADDITIONAL FIX: Preserve tool call messages for context
    # This ensures product_id from search tool remains accessible
    return {"messages": new_messages}