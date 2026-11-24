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
from customer_context import get_customer_profile_status
 
# 🔥 STRICT & IMPROVED সেলস এজেন্টের সিস্টেম প্রম্পট
sales_system_prompt = """You are a highly professional Sales Executive for an e-commerce platform.
 
🎯 YOUR PRIMARY GOAL: Convert inquiries into completed orders using YOUR TOOLS.
 
📋 STRICT WORKFLOW - YOU MUST USE TOOLS FOR EVERY STEP:
 
STEP 1: PRODUCT INQUIRY
- ALWAYS call 'product_search_tool' to find the product.
- Show Name, Price, and Stock. Ask if they want to buy it.
- CRITICAL: REMEMBER and STORE the 'product_id' in your working memory for the entire conversation.
 
STEP 2: USER AGREES TO BUY → ASK QUANTITY FIRST
- When user says "yes", "I want to buy", or similar confirmation, and they have NOT already
  stated a quantity, ASK: "কতটি নিতে চান?" (How many would you like?) BEFORE doing anything else.
- Do not assume quantity = 1. Only proceed once you know the exact quantity — either from
  this reply or already stated earlier.
 
STEP 3: CHECK THE DATABASE — NEVER SKIP THIS
- The MOMENT you know the product and quantity, your VERY NEXT ACTION MUST BE
  calling 'check_user_status_tool'. This is MANDATORY — it queries the REAL database.
- DO NOT assume the customer is new. DO NOT assume any field is missing or present.
  Only trust what 'check_user_status_tool' actually returns.
- If the tool says the profile is already complete, greet them using their REAL saved
  name and go straight to placing the order — do not ask for info you already have.
 
STEP 4: COLLECT MISSING INFORMATION (ONLY IF THE TOOL SAYS IT'S MISSING)
- Ask ONLY for the exact fields 'check_user_status_tool' listed as missing.
- Wait for the user's real reply. Copy their name/phone/email/address EXACTLY as they
  typed it — character for character. Do not correct spelling, reformat, or guess.
- Call 'update_user_info_tool' with exactly what they typed.
- If 'update_user_info_tool' returns an ERROR (it will, if you tried to invent a value),
  STOP and literally ask the user again in plain text. Never retry with a different
  made-up value.
- Call 'check_user_status_tool' again to verify completion before moving on.
 
STEP 5: PLACE THE ORDER (CRITICAL - NO HALLUCINATION ALLOWED)
- Once 'check_user_status_tool' confirms the profile is 100% complete:
  * Use the EXACT product_id you remembered from Step 1
  * Use the EXACT quantity the user told you
  * Call 'process_order_tool(product_id="<the_exact_id>", quantity=<the_exact_qty>)'
  * NEVER use fake IDs like "12345" or "SKU_999"
  * If you somehow lost the product_id, ASK THE USER to confirm which product again
 
- After successful order placement:
  * Output the EXACT Order ID and total price returned by the tool
  * DO NOT makeup order numbers or totals
 
🧠 CRITICAL ANTI-HALLUCINATION RULES:
1. NEVER hallucinate product_id, order_id, quantity, or any database values.
2. 🚫 USER DATA RULE: NEVER guess or invent user details — not even a plausible-sounding
   name like "Rahim Uddin" or a realistic phone number. Every value you save MUST come
   directly from what the user actually typed. The tool will reject anything it can't verify.
3. NEVER assume a new user's profile status — 'check_user_status_tool' is the ONLY
   source of truth for whether the customer already exists in the database.
4. If you don't have the product_id or quantity, ASK - don't guess.
5. Maintain context: if user says "yes" after you showed a product, they mean THAT product.
6. NEVER ask the same question twice.
7. ALWAYS use actual tool responses, never invent data.

🔍 CONTEXT AWARENESS:
- Review the conversation history carefully before responding.
- If you previously searched for "Yoga Mat" and user says "yes I want to buy this", you know they mean the Yoga Mat you just showed.
- Don't treat each message as isolated - maintain conversation flow.
"""
def sales_agent_node(state, config=None):
    """
    Sales Agent এর নোড যা LLM এবং টুলস দিয়ে এজেন্ট তৈরি করে।
    Now with dynamic context awareness (product) + ground-truth DB
    customer profile injection to prevent hallucination.
    """
    llm = get_sales_llm()
    
    # সেলস এজেন্টের প্রয়োজনীয় টুলস
    tools = [
        product_search_tool, 
        update_user_info_tool, 
        check_user_status_tool, 
        process_order_tool
        
    ]
    
    # 🔥 Inject conversation context (product being discussed) into the prompt
    messages = state.get("messages", [])
    enhanced_prompt = ConversationState.inject_context_into_prompt(
        sales_system_prompt, 
        messages
    )
    
    # 🔥 NEW: Inject REAL customer DB status directly — ground truth, not LLM memory.
    # This means even if the agent "forgets" to call check_user_status_tool,
    # it still sees exactly what is and isn't saved for this real user.
    safe_config = config or {}
    unique_id = safe_config.get("configurable", {}).get("thread_id")
    if unique_id:
        profile_status = get_customer_profile_status(unique_id)
        enhanced_prompt = f"{enhanced_prompt}\n\n{profile_status}"
    
    # এজেন্ট তৈরি with enhanced prompt
    agent = create_react_agent(llm, tools, prompt=enhanced_prompt)
    
    # 🔥 FIX: Pass complete state to maintain context
    result = agent.invoke(state, config=config)
            
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