# state_manager.py
"""
Conversation State Manager
Helps track important context like product_id across multiple turns
"""
from typing import Dict, Optional, Any
from langchain_core.messages import BaseMessage, AIMessage, ToolMessage
import json

class ConversationState:
    """
    Tracks important contextual information during a conversation
    to prevent parameter hallucination
    """
    
    @staticmethod
    def extract_product_context(messages: list) -> Optional[Dict[str, Any]]:
        """
        Extracts the most recent product information from conversation history.
        Looks for tool results from product_search_tool.
        
        Returns:
            Dict with product_id, name, price, or None if not found
        """
        # Traverse messages in reverse to find most recent product search
        for message in reversed(messages):
            # Check if this is a tool message (tool result)
            if isinstance(message, ToolMessage):
                try:
                    # Try to parse the tool result
                    content = message.content
                    
                    # Check if it's from product_search_tool
                    if "product_id" in content.lower():
                        # Parse JSON result
                        products = json.loads(content)
                        
                        if isinstance(products, list) and len(products) > 0:
                            # Return the first product (most relevant)
                            return {
                                "product_id": products[0].get("product_id"),
                                "name": products[0].get("name"),
                                "price": products[0].get("price"),
                                "stock": products[0].get("stock_count")
                            }
                except (json.JSONDecodeError, AttributeError, KeyError):
                    continue
            
            # Also check AI messages that might contain product info
            elif isinstance(message, AIMessage) and message.tool_calls:
                for tool_call in message.tool_calls:
                    if tool_call.get("name") == "product_search_tool":
                        # This indicates a product search was initiated
                        # The result would be in the next ToolMessage
                        continue
        
        return None
    
    @staticmethod
    def detect_purchase_intent(messages: list) -> bool:
        """
        Checks if user has expressed purchase intent in recent messages.
        
        Returns:
            True if user said yes/wants to buy, False otherwise
        """
        # Check last 3 user messages
        user_messages = [m for m in messages if m.type == "human"][-3:]
        
        purchase_keywords = [
            "yes", "হ্যাঁ", "buy", "কিনব", "want", "চাই", 
            "order", "অর্ডার", "confirm", "কনফার্ম", "i want to buy"
        ]
        
        for msg in user_messages:
            content = msg.content.lower() if isinstance(msg.content, str) else str(msg.content).lower()
            if any(keyword in content for keyword in purchase_keywords):
                return True
        
        return False
    
    @staticmethod
    def build_context_summary(messages: list) -> str:
        """
        Creates a summary of the conversation state for the agent.
        This helps prevent context loss.
        
        Returns:
            A text summary of current conversation state
        """
        product_context = ConversationState.extract_product_context(messages)
        has_purchase_intent = ConversationState.detect_purchase_intent(messages)
        
        summary_parts = []
        
        if product_context:
            summary_parts.append(
                f"🛍️ ACTIVE PRODUCT CONTEXT: "
                f"ID={product_context['product_id']}, "
                f"Name='{product_context['name']}', "
                f"Price=${product_context['price']}"
            )
        
        if has_purchase_intent:
            summary_parts.append("✅ User has expressed purchase intent")
        
        if summary_parts:
            return "\n".join(summary_parts)
        
        return "No active product context"
    
    @staticmethod
    def inject_context_into_prompt(base_prompt: str, messages: list) -> str:
        """
        Injects conversation context into the system prompt dynamically.
        This helps the LLM maintain awareness of the conversation state.
        
        Args:
            base_prompt: The original system prompt
            messages: Full conversation history
            
        Returns:
            Enhanced prompt with context
        """
        context_summary = ConversationState.build_context_summary(messages)
        
        if "No active product context" not in context_summary:
            enhanced_prompt = f"""{base_prompt}

🔍 CURRENT CONVERSATION STATE:
{context_summary}

IMPORTANT: Use this context to maintain continuity. If the user confirms a purchase, 
use the product_id shown above. DO NOT make up new product IDs."""
            return enhanced_prompt
        
        return base_prompt