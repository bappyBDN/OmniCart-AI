import os
import json
import re
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv
 
# LangChain Imports
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
 
# Database & Models Imports
from models import Customer, Product, Order, Escalation
from core.db_handler import get_db
 
# Load Environment Variables
load_dotenv()
 
# ==========================================
# ১. ভেক্টর ডাটাবেস লোড করা (Vector DB)
# ==========================================
embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
 
# পলিসি ডাটাবেস
policy_db = Chroma(
    persist_directory="./chroma_db", 
    embedding_function=embeddings, 
    collection_name="policy_collection"
)
 
# প্রোডাক্ট ডাটাবেস
product_db = Chroma(
    persist_directory="./chroma_db", 
    embedding_function=embeddings, 
    collection_name="products_collection"
)
 
# ==========================================
# ২. প্রোডাক্ট এবং পলিসি সার্চ ফাংশন
# ==========================================
 
def search_product(query: str) -> str:
    """Search for products based on a user query."""
    try:
        results = product_db.similarity_search(query, k=3)
        
        if not results:
            return "No matching products found in the database."
 
        found_ids = [doc.metadata.get("product_id") for doc in results if "product_id" in doc.metadata]
        
        # JSON ফাইলটি আছে কি না চেক করা
        if not os.path.exists("data/products.json"):
            return "System Error: 'data/products.json' file is missing. Please notify the admin."
            
        with open("data/products.json", "r", encoding="utf-8") as f:
            all_products = json.load(f)
        
        final_details = [p for p in all_products if p.get("product_id") in found_ids]
        
        if not final_details:
            return "Products found in AI memory, but missing from real-time stock json."
            
        return json.dumps(final_details, ensure_ascii=False)
    except Exception as e:
        return f"Product search failed internally: {str(e)}"
 
@tool
def product_search_tool(query: str) -> str:
    """CRITICAL: You MUST use this tool EVERY TIME the user asks about ANY product, its price, or details."""
    return search_product(query)
 
@tool
def policy_search_tool(query: str) -> str:
    """CRITICAL: You MUST use this tool EVERY TIME the user asks about shop rules, return policy, refund, delivery, shipping, or payment."""
    try:
        results = policy_db.similarity_search(query, k=2)
        if not results:
            return "দুঃখিত, আমাদের পলিসিতে এই বিষয়ে কোনো তথ্য পাওয়া যায়নি।"
        return "\n\n".join([doc.page_content for doc in results])
    except Exception as e:
        return f"Policy search failed: {str(e)}"
 
# ==========================================
# ৩. ইউজার প্রোফাইল টুলস (Config সহ)
# ==========================================
 
@tool
def update_user_info_tool(name: str = None, phone: str = None, email: str = None, address: str = None, config: RunnableConfig = None) -> str:
    """
    Saves the user's provided name, phone, email, and address to the database.
    Call this tool ONLY after the user explicitly types their personal details in the chat.
    """
    safe_config = config or {}
    unique_id = safe_config.get("configurable", {}).get("thread_id", "default_user")
    shop_id = "shop_01"
    
    # 🛡️ THE FIX: প্রম্পটের বদলে পাইথন লজিক দিয়ে ফেক ডাটা ব্লক করা হলো
    dummy_names = ["john", "john doe", "test", "demo", "user", "customer", "fake"]
    dummy_phones = ["1234567890", "0123456789", "0000000000", "12345", "01700000000"]
    
    if name and name.strip().lower() in dummy_names:
        return "❌ ERROR: DO NOT guess names. Ask the user to type their real name."
    
    if phone and ''.join(filter(str.isdigit, phone)) in dummy_phones:
        return "❌ ERROR: DO NOT guess phone numbers. Ask the user for their real phone number."

    db = get_db()
    try:
        customer = db.query(Customer).filter_by(unique_id=unique_id, shop_id=shop_id).first()
        if not customer:
            customer = Customer(unique_id=unique_id, shop_id=shop_id)
            db.add(customer)
        
        if name: customer.name = name
        if phone: customer.phone_number = phone
        if email: customer.email = email
        if address: customer.address = address
        
        db.commit()
        db.refresh(customer)
        print(f"✅ Customer info updated: {customer.name}, {customer.phone_number}, {customer.email}")
        return "✅ তথ্য ডাটাবেসে সফলভাবে সেভ করা হয়েছে।"
    except Exception as e:
        db.rollback()
        return f"❌ Error saving user info: {str(e)}"
    finally:
        db.close()
 
@tool
def check_user_status_tool(config: RunnableConfig = None) -> str:
    """ইউজারের প্রোফাইল চেক করার জন্য। অর্ডার করার আগে এটি কল করা বাধ্যতামূলক।"""
    safe_config = config or {}
    unique_id = safe_config.get("configurable", {}).get("thread_id", "default_user")
    shop_id = "shop_01"
    
    db = get_db()
    try:
        customer = db.query(Customer).filter_by(unique_id=unique_id, shop_id=shop_id).first()
        if not customer:
            return "STOP! প্রোফাইল নেই। DO NOT guess. Use plain text to ask the user for their Name, Phone, Email, and Address."
            
        missing = []
        if not customer.name: missing.append("নাম (Name)")
        if not customer.phone_number: missing.append("ফোন নম্বর (Phone)")
        if not customer.email: missing.append("ইমেইল (Email)")
        if not customer.address: missing.append("ঠিকানা (Address)")
        
        if missing:
            return f"⚠️ প্রোফাইল অসম্পূর্ণ। ইউজারের কাছে এই তথ্যগুলো জিজ্ঞেস করুন: {', '.join(missing)}"
        
        print(f"✅ Profile Complete: {customer.name}, {customer.email}, {customer.phone_number}")
        return f"✅ প্রোফাইল সম্পূর্ণ আছে। Customer: {customer.name}, Email: {customer.email}। এখন অর্ডার প্রসেস করা যাবে।"
    finally:
        db.close()
 
# ==========================================
# ৪. অর্ডার প্রসেসিং এবং অটো-ইমেইল টুল - 🔥 COMPLETELY FIXED
# ==========================================
 
@tool
def process_order_tool(product_id: str, quantity: int, config: RunnableConfig = None) -> str:
    """
    🔥 CRITICAL: ফাইনাল অর্ডার ডাটাবেসে সেভ করে, স্টক আপডেট করে এবং ইউজারের কাছে অটোমেটিক ইমেইল পাঠায়।
    """
    safe_config = config or {}
    unique_id = safe_config.get("configurable", {}).get("thread_id", "default_user")
    shop_id = "shop_01"
    
    print(f"\n🔥 [DEBUG] process_order_tool called! Product: {product_id}, Qty: {quantity}")
    
    db = get_db()
    try:
        p_id_str = str(product_id).strip()
        
        # Quantity ভ্যালিডেশন
        try:
            qty = int(quantity)
        except:
            return "❌ Error: Invalid quantity. Quantity must be a number."
        
        # কাস্টমার প্রোফাইল চেক
        customer = db.query(Customer).filter_by(unique_id=unique_id, shop_id=shop_id).first()
        if not customer or not all([customer.name, customer.phone_number, customer.email, customer.address]):
            return "❌ Error: ইউজারের সব তথ্য নেই। 'check_user_status_tool' ব্যবহার করুন।"
        
        # ডাটাবেস থেকে প্রোডাক্ট খোঁজা
        product = db.query(Product).filter_by(product_id=p_id_str, shop_id=shop_id).first()
        if not product:
            return f"❌ Error: প্রোডাক্ট (ID: {p_id_str}) পাওয়া যায়নি।"
        
        # ==========================================
        # 🚨 স্মার্ট স্টক চেক লজিক (NEW FIX)
        # ==========================================
        if product.stock_count <= 0:
            return "⚠️ INSTRUCTION FOR AI: This product is completely out of stock. Apologize and tell the user it is currently unavailable."
            
        if qty > product.stock_count:
            return (f"⚠️ INSTRUCTION FOR AI: The user requested {qty}, but we only have {product.stock_count} "
                    f"in stock. Apologize and explicitly ask the user: 'আমাদের স্টকে এই মুহূর্তে মাত্র "
                    f"{product.stock_count}টি {product.name} আছে। আপনি কি {product.stock_count}টি অর্ডার করতে চান?'")
        # ==========================================
        
        total_price = float(qty * product.price)
        
        # ১. ডাটাবেসে অর্ডার সেভ করা
        new_order = Order(
            customer_id=customer.customer_id,
            product_id=product.product_id,
            product_name=product.name,
            quantity=qty,
            price=product.price,
            total_price=total_price,
            shop_id=shop_id,
            status="Pending"
        )
        product.stock_count -= qty
        db.add(new_order)
        db.commit()
        db.refresh(new_order) 
        order_id = f"ORD_{new_order.order_id}"
        print(f"   ✅ Order Saved! Order ID: {order_id}")
        # ==========================================
        # ২. অটোমেটিক ইমেইল সেন্ডিং লজিক (NEW FIX)
        # ==========================================
        sender_email = os.getenv("EMAIL_SENDER") 
        app_password = os.getenv("EMAIL_PASSWORD") 
        
        if sender_email and app_password and customer.email:
            try:
                msg = MIMEMultipart()
                msg['From'] = sender_email
                msg['To'] = customer.email
                msg['Subject'] = f"🛍️ Your Order is Confirmed! - {order_id}"
                
                html_body = f"""
                <html>
                    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: auto; border: 1px solid #ddd; padding: 20px; border-radius: 10px;">
                        <h2 style="color: #28a745;">🎉 Order Successfully Placed!</h2>
                        <p>Hello <b>{customer.name}</b>,</p>
                        <p>Thank you for shopping with us. Your order <b>{order_id}</b> has been confirmed.</p>
                        
                        <div style="background-color: #f9f9f9; padding: 15px; border-radius: 5px; margin-bottom: 20px;">
                            <h3 style="margin-top: 0;">📦 Order Details:</h3>
                            <ul>
                                <li><b>Product:</b> {product.name}</li>
                                <li><b>Quantity:</b> {qty}</li>
                                <li><b>Total Price:</b> ${total_price}</li>
                            </ul>
                        </div>
                        
                        <p><b>Delivery Address:</b> {customer.address}</p>
                        <p><b>Phone Number:</b> {customer.phone_number}</p>
                        
                        <br>
                        <p>We will notify you once your order is shipped.</p>
                        <p>Regards,<br><b>🤖 AI Shop Assistant</b></p>
                    </body>
                </html>
                """
                msg.attach(MIMEText(html_body, 'html'))
                
                server = smtplib.SMTP('smtp.gmail.com', 587)
                server.starttls()
                server.login(sender_email, app_password)
                server.send_message(msg)
                server.quit()
                print(f"   📧 [DEBUG] Email successfully sent to {customer.email}")
            except Exception as email_err:
                print(f"   ⚠️ [DEBUG] Failed to send email: {str(email_err)}")
        else:
            print("   ⚠️ [DEBUG] Email credentials missing or customer has no email. Skipped email.")
        # ==========================================
        
        # ৩. এআই-কে কনফার্মেশন দেওয়া
        return f"✅ অর্ডার সফলভাবে প্রসেস হয়েছে। Order ID: {order_id}. কাস্টমারকে একটি কনফার্মেশন ইমেইল পাঠানো হয়েছে।"
        
    except Exception as e:
        db.rollback()
        return f"❌ অর্ডার প্রসেস করতে সমস্যা হয়েছে: {str(e)}"
    finally:
        db.close()
@tool
def send_email_tool(customer_email: str, customer_name: str, order_id: str, product_name: str, price: str) -> str:
    """
    DEPRECATED: This tool is now integrated into process_order_tool.
    Use process_order_tool instead - it handles both order creation and email sending.
    """
    return "⚠️ This tool is deprecated. The email is now automatically sent by process_order_tool."
 
# ==========================================
# ৫. অ্যাডমিন নোটিফিকেশন এবং ভিশন টুলস
# ==========================================
 
@tool
def send_email_to_admin(issue_summary: str, user_exact_message: str, customer_email: str, customer_phone: str, config: RunnableConfig = None) -> str:
    """CRITICAL: Call this tool to notify the admin about a refund, return, cancellation, or complex issue."""
    
    print("\n🚀 [DEBUG] send_email_to_admin Tool কল হয়েছে!")
    
    # 🛡️ THE FIX: বর্তমান চ্যাটিং কাস্টমারকে বের করার লজিক
    safe_config = config or {}
    unique_id = safe_config.get("configurable", {}).get("thread_id", "default_user")
    shop_id = "shop_01"
    
    db = get_db()
    try:
        # লেটেস্ট কাস্টমারের বদলে, নির্দিষ্ট unique_id (Session) দিয়ে কাস্টমার খোঁজা হচ্ছে
        customer = db.query(Customer).filter_by(unique_id=unique_id, shop_id=shop_id).first()
        
        if customer:
            # যদি কাস্টমারের ইমেইল বা ফোন না থাকে, তবে এআই যা পেয়েছে তা দিয়ে আপডেট করা
            if customer_email and customer_email != "unknown": customer.email = customer_email
            if customer_phone and customer_phone != "unknown": customer.phone_number = customer_phone
            
            customer.human_intervention = True # AI বন্ধ করার কমান্ড
            customer_id = customer.customer_id
            print(f"✅ [DEBUG] Current Customer Found! ID: {customer_id}")
        else:
            print("⚠️ [DEBUG] No Customer Found for this thread! Using fallback ID 1")
            customer_id = 1
            
        # কমপ্লেইন তৈরি
        new_escalation = Escalation(
            customer_id=customer_id,
            issue_summary=f"User Message: {user_exact_message} | AI Notes: {issue_summary}",
            status="Pending",
            shop_id=shop_id
        )
        
        db.add(new_escalation)
        db.commit()
        print(f"✅ [DEBUG] Escalation ডাটাবেসে সফলভাবে সেভ হয়েছে!")
        
    except Exception as db_error:
        db.rollback()
        print(f"❌ [DEBUG] Database Error: {str(db_error)}")
    finally:
        db.close()
 
    sender_email = os.getenv("EMAIL_SENDER") 
    app_password = os.getenv("EMAIL_PASSWORD") 
    admin_email = os.getenv("ADMIN_EMAIL") 
    
    if not sender_email or not app_password or not admin_email:
        print("⚠️ [DEBUG] Email credentials missing. Skipping email.")
        return "অ্যাডমিন প্যানেলে অ্যালার্ট পাঠানো হয়েছে কিন্তু ইমেইল ক্রেডেনশিয়াল নেই।"
 
    try:
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = admin_email
        msg['Subject'] = "🚨 Action Required: Customer Request (Refund/Return/Cancel)"
 
        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: auto; border: 1px solid #ddd; padding: 20px; border-radius: 10px;">
                <h2 style="color: #d9534f; border-bottom: 2px solid #d9534f; padding-bottom: 10px;">🚨 Action Required</h2>
                <p>Hello Admin,</p>
                <p>A customer requires manual intervention. <b>The AI has paused the chat for this user.</b></p>
                
                <div style="background-color: #f9f9f9; padding: 15px; border-radius: 5px; margin-bottom: 20px;">
                    <h3 style="margin-top: 0; color: #0056b3;">💬 Conversation Section:</h3>
                    <p><b>User's Exact Query:</b><br> <i>"{user_exact_message}"</i></p>
                    <hr style="border: 0; border-top: 1px solid #ccc;">
                    <p><b>Issue Summary:</b><br> {issue_summary}</p>
                </div>
                
                <p>Please log in to the <b>Admin Panel</b> -> <b>Live Chat</b> to reply to the customer.</p>
                <br>
                <p>Regards,<br><b>🤖 AI Shop Assistant</b></p>
            </body>
        </html>
        """
        
        msg.attach(MIMEText(html_body, 'html'))
 
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, app_password)
        server.send_message(msg)
        server.quit()
        
        print("✅ [DEBUG] Email successfully sent!")
        return "অ্যাডমিনকে সফলভাবে জানানো হয়েছে। একজন প্রতিনিধি শীঘ্রই আপনার সাথে যোগাযোগ করবেন।"
 
    except Exception as e:
        print(f"❌ [DEBUG] Email Error: {str(e)}")
        return "ড্যাশবোর্ডে অ্যালার্ট পাঠানো হয়েছে কিন্তু ইমেইল পাঠাতে এরর হয়েছে।"
    
@tool
def vision_to_product_search_tool(image_description: str) -> str:
    """
    CRITICAL: Use this tool ONLY when you have analyzed an image and found a product.
    Pass a short search query (e.g., 'Blue full sleeve shirt') to find matching products in the shop.
    """
    return search_product(image_description)