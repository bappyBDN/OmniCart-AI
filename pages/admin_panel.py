import streamlit as st
import pandas as pd
import bcrypt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Admin, Shop, Product, Customer, Order, Escalation, ChatHistory 

# ==========================================
# ১. ডাটাবেস সেটআপ
# ==========================================
engine = create_engine("sqlite:///ecommerce_saas.db")
SessionLocal = sessionmaker(bind=engine)
session = SessionLocal()

# 🆕 ডাটাবেসের পুরানো ক্যাশে মুছে ফেলা হচ্ছে, যাতে সবসময় নতুন ডেটা আসে!
session.expire_all() 

# ==========================================
# ২. সিকিউরিটি এবং লগইন লজিক
# ==========================================
def verify_password(plain_password, hashed_password):
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

st.set_page_config(page_title="Admin Dashboard", page_icon="🛡️", layout="wide")

# 🚨 Session State Initialization (খুবই গুরুত্বপূর্ণ)
if "admin_logged_in" not in st.session_state:
    st.session_state.admin_logged_in = False
if "admin_shop_id" not in st.session_state:
    st.session_state.admin_shop_id = None
if "admin_name" not in st.session_state:
    st.session_state.admin_name = None

# ==========================================
# ৩. UI ডিজাইন: লগইন স্ক্রিন
# ==========================================
if not st.session_state.admin_logged_in:
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.title("🛡️ Admin Login")
        st.markdown("---")
        
        email = st.text_input("ইমেইল (Email)", value="admin@shop.com")
        password = st.text_input("পাসওয়ার্ড (Password)", type="password", value="123456")
        
        if st.button("লগইন করুন", use_container_width=True):
            admin_user = session.query(Admin).filter_by(email=email).first()
            if admin_user and verify_password(password, admin_user.password_hash):
                st.session_state.admin_logged_in = True
                st.session_state.admin_shop_id = admin_user.shop_id
                st.session_state.admin_name = admin_user.name
                st.rerun()
            else:
                st.error("❌ ভুল ইমেইল বা পাসওয়ার্ড!")

# ==========================================
# ৪. UI ডিজাইন: ড্যাশবোর্ড (লগইন হওয়ার পর)
# ==========================================
else: # 🚨 এই else ব্লকটিই আপনার কোডে মিসিং ছিল
    
    # 🆕 Top Bar (Dashboard Title, Refresh & Logout)
    col_title, col_refresh, col_logout = st.columns([7, 2, 1])
    with col_title:
        st.title(f"📊 Dashboard - Shop ID: {st.session_state.admin_shop_id}")
    with col_refresh:
        if st.button("🔄 Refresh Data", use_container_width=True):
            st.rerun()
    with col_logout:
        if st.button("🚪 Logout"):
            st.session_state.admin_logged_in = False
            st.session_state.admin_shop_id = None
            st.rerun()
    
    st.markdown("---")

    # ==========================================
    # Analytics & Charts 
    # ==========================================
    orders_data = session.query(Order).filter_by(shop_id=st.session_state.admin_shop_id).all()
    customers_data = session.query(Customer).filter_by(shop_id=st.session_state.admin_shop_id).all()
    escalations_data = session.query(Escalation).filter_by(shop_id=st.session_state.admin_shop_id).all()
    
    total_orders = len(orders_data)
    total_customers = len(customers_data)
    pending_issues = len([e for e in escalations_data if e.status in ["Pending", "Open"]])
            
    m1, m2, m3 = st.columns(3) 
    m1.metric("🛒 মোট অর্ডার (Orders)", total_orders)
    m2.metric("👥 কাস্টমার (Customers)", total_customers)
    
    if pending_issues > 0:
        m3.metric("🚨 পেন্ডিং টিকিট", pending_issues, delta="- Action Needed", delta_color="inverse")
    else:
        m3.metric("✅ পেন্ডিং টিকিট", pending_issues, delta="All Clear", delta_color="normal")
            
    st.markdown("---")
    
    # 📑 ড্যাশবোর্ডের ট্যাব তৈরি (শুধুমাত্র একবার)
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📦 Products", "🛒 Orders", "👥 Customers", "🚨 Escalations", "💬 Live Chat (Human Handoff)"
    ])
    
    with tab1:
        st.subheader("ইভেন্টরি এবং প্রোডাক্ট লিস্ট")
        products = session.query(Product).filter_by(shop_id=st.session_state.admin_shop_id).all()
        if products:
            df_products = pd.DataFrame([{
                "ID": p.product_id, "Name": p.name, "Category": p.category,
                "Price ($)": p.price, "Stock": p.stock_count, "Status": p.status
            } for p in products])
            st.dataframe(df_products, use_container_width=True, hide_index=True)
        else:
            st.info("কোনো প্রোডাক্ট পাওয়া যায়নি।")

    with tab2:
        st.subheader("🛒 অর্ডার হিস্ট্রি এবং স্ট্যাটাস আপডেট")
        orders = session.query(Order).filter_by(shop_id=st.session_state.admin_shop_id).order_by(Order.order_id.desc()).all()
        
        if orders:
            df_orders = pd.DataFrame([{
                "Order ID": o.order_id, "Customer ID": o.customer_id, "Product": o.product_name,
                "Quantity": o.quantity, "Price": o.price, "Status": o.status, 
                "Date": o.created_at.strftime("%Y-%m-%d %H:%M") if o.created_at else "N/A"
            } for o in orders])
            st.dataframe(df_orders, use_container_width=True, hide_index=True)
            
            st.markdown("---")
            st.write("**🔄 অর্ডারের স্ট্যাটাস পরিবর্তন করুন:**")
            col_o1, col_o2, col_o3 = st.columns([2, 2, 1])
            
            with col_o1:
                order_options = {f"ORD_{o.order_id} - {o.product_name}": o.order_id for o in orders}
                selected_order_str = st.selectbox("অর্ডার আইডি নির্বাচন করুন:", options=list(order_options.keys()))
                selected_order_id = order_options[selected_order_str]
            
            with col_o2:
                new_status = st.selectbox("নতুন স্ট্যাটাস দিন:", ["Pending", "Processing", "Delivered", "Cancelled"])
            
            with col_o3:
                st.markdown("<br>", unsafe_allow_html=True) 
                if st.button("Update Status 🚀", use_container_width=True):
                    target_order = session.query(Order).filter_by(order_id=selected_order_id).first()
                    if target_order:
                        target_order.status = new_status
                        session.commit()
                        st.success(f"অর্ডার ORD_{selected_order_id} এর স্ট্যাটাস '{new_status}' করা হয়েছে!")
                        st.rerun()
        else:
            st.info("এখনো কোনো অর্ডার আসেনি।")

    with tab3:
        st.subheader("কাস্টমার ডিরেক্টরি")
        customers = session.query(Customer).filter_by(shop_id=st.session_state.admin_shop_id).all()
        if customers:
            df_customers = pd.DataFrame([{
                "Customer ID": c.customer_id, "Name": c.name or "N/A", "Phone": c.phone_number or "N/A",
                "Email": c.email or "N/A", "AI Mode Active": not c.human_intervention
            } for c in customers])
            st.dataframe(df_customers, use_container_width=True, hide_index=True)
        else:
            st.info("কোনো কাস্টমার ডেটা নেই।")

    with tab4:
        st.subheader("ক্রিটিক্যাল ইস্যু / কমপ্লেইন")
        escalations = session.query(Escalation).filter_by(shop_id=st.session_state.admin_shop_id).order_by(Escalation.case_id.desc()).all()
        
        if escalations:
            df_escalations = pd.DataFrame([{
                "Case ID": e.case_id, 
                "Customer ID": e.customer_id, 
                "Issue": e.issue_summary, 
                "Status": "🔴 Pending" if e.status in ["Pending", "Open"] else "✅ Resolved" 
            } for e in escalations])
            st.dataframe(df_escalations, use_container_width=True, hide_index=True)
            
            st.markdown("---")
            st.subheader("✅ টিকিট সমাধান করুন (Resolve Ticket)")
            
            pending_tickets = [e for e in escalations if e.status in ["Pending", "Open"]]
            
            if pending_tickets:
                col_x, col_y = st.columns([3, 1])
                with col_x:
                    ticket_options = {f"Case #{e.case_id} - Customer ID {e.customer_id}": e.case_id for e in pending_tickets}
                    selected_ticket_str = st.selectbox("যে টিকিটটি সমাধান হয়েছে তা নির্বাচন করুন:", options=list(ticket_options.keys()))
                    selected_case_id = ticket_options[selected_ticket_str]
                
                with col_y:
                    st.markdown("<br>", unsafe_allow_html=True) 
                    if st.button("✔️ Mark as Resolved", use_container_width=True):
                        target_escalation = session.query(Escalation).filter_by(case_id=selected_case_id).first()
                        if target_escalation:
                            target_escalation.status = "Resolved"
                            session.commit()
                            st.rerun()
            else:
                st.success("🎉 সব টিকিট সমাধান করা হয়েছে! নতুন কোনো কমপ্লেইন নেই।")
        else:
            st.success("কোনো কমপ্লেইন নেই! সব ঠিক আছে।")

    with tab5:
        st.subheader("👨‍💻 Admin Live Chat & Override")
        
        all_customers = session.query(Customer).filter_by(shop_id=st.session_state.admin_shop_id).order_by(Customer.customer_id.desc()).all()
        if all_customers:
            chat_options = {}
            for c in all_customers:
                status = "🔴 Live Support Needed" if c.human_intervention else "🟢 AI Active"
                chat_options[f"{c.name or 'User'} (ID: {c.customer_id}) - {status}"] = c
                
            selected_chat = st.selectbox("কাস্টমার নির্বাচন করুন:", options=list(chat_options.keys()))
            customer_obj = chat_options[selected_chat]
            
            st.markdown("---")
            col_a, col_b = st.columns(2)
            with col_a:
                if not customer_obj.human_intervention:
                    if st.button("🔴 AI বন্ধ করুন (Take Over Chat)", use_container_width=True):
                        customer_obj.human_intervention = True
                        session.commit()
                        st.rerun()
                else:
                    st.warning("⚠️ বর্তমানে আপনি ম্যানুয়ালি চ্যাট করছেন। এআই বন্ধ আছে।")
                    
            with col_b:
                if customer_obj.human_intervention:
                    if st.button("🟢 AI চালু করুন (Hand back to AI)", use_container_width=True):
                        customer_obj.human_intervention = False
                        session.commit()
                        st.rerun()
                else:
                    st.success("✅ এআই এখন স্বয়ংক্রিয়ভাবে চ্যাট করছে।")
            
            st.markdown("---")
            
            chat_history = session.query(ChatHistory).filter_by(customer_id=customer_obj.customer_id).order_by(ChatHistory.timestamp.asc()).all()
            
            st.markdown(f"### 💬 চ্যাট হিস্ট্রি: User #{customer_obj.customer_id}")
            
            chat_container = st.container(height=400)
            with chat_container:
                if not chat_history:
                    st.info("এই কাস্টমারের কোনো চ্যাট হিস্ট্রি নেই।")
                else:
                    for msg in chat_history:
                        if msg.sender == "user":
                            with st.chat_message("user", avatar="🧑‍💻"):
                                st.write(msg.message_text)
                        elif msg.sender == "ai":
                            with st.chat_message("assistant", avatar="🤖"):
                                st.write(msg.message_text)
                        elif msg.sender == "admin":
                            with st.chat_message("assistant", avatar="👨‍💻"):
                                st.write(f"**Admin:** {msg.message_text}")
            
            if customer_obj.human_intervention:
                with st.form("admin_reply_form", clear_on_submit=True):
                    admin_msg = st.text_input("কাস্টমারকে মেসেজ পাঠান...")
                    submit_btn = st.form_submit_button("Send 🚀")
                    
                    if submit_btn and admin_msg:
                        new_msg = ChatHistory(
                            sender="admin",
                            message_text=admin_msg,
                            customer_id=customer_obj.customer_id,
                            shop_id=st.session_state.admin_shop_id
                        )
                        session.add(new_msg)
                        session.commit()
                        st.rerun()
            else:
                st.info("💡 মেসেজ পাঠাতে হলে আগে উপরের 'AI বন্ধ করুন' বাটনে ক্লিক করে কন্ট্রোল নিন।")