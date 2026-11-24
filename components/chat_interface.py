# components/chat_interface.py
import streamlit as st
import base64
from streamlit_autorefresh import st_autorefresh
from models import Customer, ChatHistory
from core.db_handler import get_db 
from core.ai_agent import load_agent
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from memory_manager import cleanup_memory
from security import security_firewall

def encode_image(image_file):
    return base64.b64encode(image_file.getvalue()).decode('utf-8')

def show_chat():
    agent_executor = load_agent()
    
    st.title("🛍️ AI Shop Assistant")
    st.caption(f"Session ID: {st.session_state.thread_id[:8]}...")
    
    db = get_db()
    
    try:
        current_customer = db.query(Customer).filter_by(unique_id=st.session_state.thread_id).first()
        chat_history_db = db.query(ChatHistory).filter_by(customer_id=current_customer.customer_id).order_by(ChatHistory.timestamp.asc()).all()

        # ==========================================
        # 🔥 THE FIX: Smart Auto-Refresh Logic (Dynamic Key)
        # ==========================================
        if not chat_history_db or chat_history_db[-1].sender != "user":
            refresh_key = f"chat_updater_{len(chat_history_db)}"
            st_autorefresh(interval=3000, limit=None, key=refresh_key)
        # ==========================================

        with st.sidebar:
            st.header("⚙️ সেটিংস")
            if st.button("🔄 চ্যাট রিফ্রেশ করুন", use_container_width=True):
                st.rerun()
            st.write("বর্তমান চ্যাট থেকে বের হতে লগআউট করুন:")
            if st.button("🚪 Logout / Switch User", use_container_width=True):
                st.session_state.user_logged_in = False
                st.session_state.thread_id = None
                st.rerun()
            st.markdown("---")

        if not chat_history_db:
            st.info("👋 হ্যালো! আমাদের স্টোরে আপনাকে স্বাগতম। কীভাবে সাহায্য করতে পারি?")
        else:
            for msg in chat_history_db:
                if msg.sender == "user":
                    col1, col2 = st.columns([1, 4]) 
                    with col2:
                        with st.chat_message("user", avatar="🧑‍💻"):
                            st.markdown(msg.message_text)
                elif msg.sender == "ai":
                    col1, col2 = st.columns([4, 1])
                    with col1:
                        with st.chat_message("assistant", avatar="🛍️"):
                            st.markdown(msg.message_text)
                elif msg.sender == "admin":
                    col1, col2 = st.columns([4, 1])
                    with col1:
                        with st.chat_message("assistant", avatar="👨‍💻"):
                            st.markdown(f"**Admin:** {msg.message_text}")

        uploaded_image = st.file_uploader("📷 একটি ছবি আপলোড করুন", type=["jpg", "jpeg", "png"])

        if user_input := st.chat_input("এখানে লিখুন..."):
            user_msg_db = ChatHistory(sender="user", message_text=user_input, customer_id=current_customer.customer_id, shop_id="shop_01")
            db.add(user_msg_db)
            db.commit()
            st.rerun()

        # এআই লজিক এক্সিকিউশন
        if chat_history_db and chat_history_db[-1].sender == "user":
            latest_user_msg = chat_history_db[-1].message_text
            is_safe, security_msg = security_firewall(latest_user_msg)

            if not is_safe:
                ai_msg_db = ChatHistory(sender="ai", message_text=f"🚨 Security Alert: {security_msg}", customer_id=current_customer.customer_id, shop_id="shop_01")
                db.add(ai_msg_db)
                db.commit()
                st.rerun()
            else: 
                if current_customer.human_intervention:
                    st.markdown("⏳ *অ্যাডমিন আপনার মেসেজটি দেখেছেন। অনুগ্রহ করে একটু অপেক্ষা করুন, তিনি এখনই রিপ্লাই দিচ্ছেন...*")
                else:
                    with st.spinner("এজেন্ট কাজ করছে..."):
                        try:
                            config = {"configurable": {"thread_id": st.session_state.thread_id}}
                            messages_to_send = []
                            recent_history = chat_history_db[-10:] if len(chat_history_db) > 10 else chat_history_db
                            
                            for msg in recent_history:
                                if msg.sender == "user":
                                    if msg.message_id == chat_history_db[-1].message_id and uploaded_image:
                                        base64_image = encode_image(uploaded_image)
                                        message_content = [
                                            {"type": "text", "text": msg.message_text},
                                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                                        ]
                                        messages_to_send.append(HumanMessage(content=message_content))
                                    else:
                                        messages_to_send.append(HumanMessage(content=msg.message_text))
                                elif msg.sender == "ai":
                                    messages_to_send.append(AIMessage(content=msg.message_text))
                            
                            response = agent_executor.invoke({"messages": messages_to_send}, config=config)
                            cleanup_memory(agent_executor, config, max_history=20)
                            
                            bot_reply = response["messages"][-1].content
                            ai_msg_db = ChatHistory(sender="ai", message_text=bot_reply, customer_id=current_customer.customer_id, shop_id="shop_01")
                            db.add(ai_msg_db)
                            db.commit()
                        except Exception as e:
                            error_msg = "⚠️ দুঃখিত, সার্ভারে অতিরিক্ত লোড পড়েছে। অনুগ্রহ করে কিছুক্ষণ পর আবার চেষ্টা করুন।"
                            ai_msg_db = ChatHistory(sender="ai", message_text=error_msg, customer_id=current_customer.customer_id, shop_id="shop_01")
                            db.add(ai_msg_db)
                            db.commit()
                            
                    st.rerun()
    finally:
        db.close()