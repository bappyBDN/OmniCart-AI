# components/login_screen.py
import streamlit as st
import uuid
from models import Customer
from core.db_handler import get_db  # নতুন ইমপোর্ট

def show_login():
    st.title("🛍️ AI Shop Assistant")
    st.markdown("---")
    st.subheader("👋 স্বাগতম! চ্যাট শুরু করতে আপনার তথ্য দিন")
    
    with st.form("login_form"):
        contact_info = st.text_input("আপনার ইমেইল অথবা ফোন নম্বর দিন:")
        submit_btn = st.form_submit_button("চ্যাট শুরু করুন 🚀")
        
        if submit_btn: 
            if contact_info:
                contact_info = contact_info.strip()
                db = get_db() # ডাটাবেস সেশন ওপেন করা হলো
                
                try:
                    existing_customer = db.query(Customer).filter(
                        (Customer.email == contact_info) | (Customer.phone_number == contact_info)
                    ).first()
                    
                    if existing_customer:
                        st.session_state.thread_id = existing_customer.unique_id
                    else:
                        new_thread_id = str(uuid.uuid4())
                        is_email = "@" in contact_info
                        
                        new_customer = Customer(
                            unique_id=new_thread_id,
                            email=contact_info if is_email else None,
                            phone_number=contact_info if not is_email else None,
                            shop_id="shop_01"
                        )
                        db.add(new_customer)
                        db.commit()
                        st.session_state.thread_id = new_thread_id
                    
                    st.session_state.user_logged_in = True
                    st.rerun() # সাকসেসফুল হলে রিরান হবে
                except Exception as e:
                    st.error(f"ডাটাবেস এরর: {str(e)}")
                finally:
                    db.close()
            else:
                st.warning("দয়া করে ইমেইল বা ফোন নম্বর দিন।")