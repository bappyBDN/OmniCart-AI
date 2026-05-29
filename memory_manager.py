import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver
from langchain_core.messages import RemoveMessage

def get_memory_saver(db_name="chat_memory.db"):
    """
    এই ফাংশনটি SQLite ডাটাবেস তৈরি করবে এবং LangGraph এর Memory Saver রিটার্ন করবে।
    """
    conn = sqlite3.connect(db_name, check_same_thread=False)
    return SqliteSaver(conn)

# এখানে max_history=10 এর বদলে 50 বা 80 করে দিন
def cleanup_memory(agent_executor, config, max_history=50): 
    """
    এই ফাংশনটি চেক করবে ইউজারের হিস্ট্রি লিমিট পার হয়েছে কি না।
    লিমিট পার হলে পুরনো মেসেজগুলো ডিলিট করে দেবে, কিন্তু System Prompt ঠিক রাখবে।
    """
    current_state = agent_executor.get_state(config)
    all_messages = current_state.values.get("messages", [])
    
    if len(all_messages) > max_history:
        # System Prompt (0) এবং শেষের max_history মেসেজগুলো রেখে বাকিগুলো ডিলিট করা
        old_messages_to_delete = all_messages[1:-max_history]
        
        if old_messages_to_delete:
            agent_executor.update_state(
                config,
                {"messages": [RemoveMessage(id=m.id) for m in old_messages_to_delete]}
            )
            print(f"   [🧹 Memory Cleaned: Deleted {len(old_messages_to_delete)} old messages]")