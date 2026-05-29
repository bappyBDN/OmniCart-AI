# app.py
import streamlit as st
from dotenv import load_dotenv

from components.login_screen import show_login
from components.chat_interface import show_chat
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", module="transformers")
from core.llm_provider import verify_llm_keys
verify_llm_keys()

# পরিবেশ ভেরিয়েবল লোড করা
load_dotenv()

# পেজ কনফিগারেশন
st.set_page_config(page_title="AI Shop Assistant", page_icon="🛍️", layout="centered")

# সেশন স্টেট ইনিশিয়ালাইজেশন
if "user_logged_in" not in st.session_state:
    st.session_state.user_logged_in = False
if "thread_id" not in st.session_state:
    st.session_state.thread_id = None

# মেইন রাউটিং লজিক
if not st.session_state.user_logged_in:
    show_login()
else:
    show_chat()