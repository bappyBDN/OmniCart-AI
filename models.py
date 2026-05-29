from sqlalchemy import create_engine, Column, String, Integer, Float, Text, ForeignKey, DateTime, JSON, Boolean
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
 
# SQLAlchemy-এর বেস অবজেক্ট (এর ওপর ভিত্তি করেই সব টেবিল তৈরি হবে)
Base = declarative_base()
 
# ১. Shop Table
class Shop(Base):
    __tablename__ = "shops"
    
    shop_id = Column(String, primary_key=True)
    shop_name = Column(String, nullable=False)
    bot_tone = Column(String, default="Professional")
    created_at = Column(DateTime, default=datetime.utcnow)
    
    customers = relationship("Customer", back_populates="shop")
    products = relationship("Product", back_populates="shop")
    policies = relationship("Policy", back_populates="shop")
    admins = relationship("Admin", back_populates="shop")
    orders = relationship("Order", back_populates="shop")
    escalations = relationship("Escalation", back_populates="shop")
    rejected_orders = relationship("RejectedOrder", back_populates="shop")
 
# ২. Customer Table
class Customer(Base):
    __tablename__ = "customers"
    
    customer_id = Column(Integer, primary_key=True, autoincrement=True)
    unique_id = Column(String, nullable=False) # Website Session বা Facebook PSID
    name = Column(String)
    phone_number = Column(String)
    email = Column(String)
    address = Column(Text)
    # 🆕 ম্যাজিক সুইচ: ডিফল্টভাবে False থাকবে (মানে এআই কথা বলবে)
    human_intervention = Column(Boolean, default=False)
    
    shop_id = Column(String, ForeignKey("shops.shop_id"), nullable=False)
    shop = relationship("Shop", back_populates="customers")
    
    orders = relationship("Order", back_populates="customer")
 
# ৩. Product Table (Updated for Real-world JSON)
class Product(Base):
    __tablename__ = "products"
    
    product_id = Column(String, primary_key=True)  # প্রাইমারি কী
    name = Column(String, nullable=False)
    category = Column(String)
    price = Column(Float, nullable=False)
    discounted_price = Column(Float)             # 🆕 নতুন: ডিসকাউন্ট প্রাইস
    stock_count = Column(Integer, default=0)     # 🔄 আপডেট: stock এর বদলে stock_count
    
    # 🆕 ম্যাজিক: JSON টাইপ ব্যবহার করলে সরাসরি ["M", "L"] লিস্ট সেভ করা যাবে!
    colors = Column(JSON)                        
    sizes = Column(JSON)                         
    
    status = Column(String, default="In Stock")  # 🆕 নতুন: ইন স্টক নাকি আউট অফ স্টক
    description = Column(Text)                   # 🆕 নতুন: প্রোডাক্টের বিস্তারিত বিবরণ
    
    shop_id = Column(String, ForeignKey("shops.shop_id"), nullable=False)
    shop = relationship("Shop", back_populates="products")
    
    # Relationship with Orders
    orders = relationship("Order", back_populates="product")
 
# ৪. Order Table - 🔥 FIXED VERSION
class Order(Base):
    __tablename__ = "orders"
    
    order_id = Column(Integer, primary_key=True, autoincrement=True)  # Auto-increment করা হয়েছে
    product_name = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False, default=1)  # 🆕 যোগ করা হয়েছে
    price = Column(Float, nullable=False)
    total_price = Column(Float, nullable=False)  # 🆕 যোগ করা হয়েছে
    status = Column(String, default="Pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 🛠️ FIXED: customers.id এর বদলে customers.customer_id হবে
    customer_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    customer = relationship("Customer", back_populates="orders")
    
    product_id = Column(String, ForeignKey("products.product_id"))  # String হবে কারণ Product এ String আছে
    product = relationship("Product", back_populates="orders")
    
    shop_id = Column(String, ForeignKey("shops.shop_id"), nullable=False)
    shop = relationship("Shop", back_populates="orders")
 
# ৫. Policy Table
class Policy(Base):
    __tablename__ = "policies"
    
    policy_id = Column(Integer, primary_key=True, autoincrement=True)
    policy_type = Column(String)
    policy_text = Column(Text, nullable=False)
    
    shop_id = Column(String, ForeignKey("shops.shop_id"), nullable=False)
    shop = relationship("Shop", back_populates="policies")
 
# ৬. Admin Table (উইথ পাসওয়ার্ড হ্যাশ)
class Admin(Base):
    __tablename__ = "admins"
    
    admin_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String)
    email = Column(String, nullable=False, unique=True)
    password_hash = Column(String, nullable=False) # এনক্রিপ্ট করা পাসওয়ার্ড
    phone = Column(String)
    role = Column(String, default="Manager")
    
    shop_id = Column(String, ForeignKey("shops.shop_id"), nullable=False)
    shop = relationship("Shop", back_populates="admins")
 
# ৭. Escalation Table
class Escalation(Base):
    __tablename__ = "escalations"
    
    case_id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("customers.customer_id"))
    issue_summary = Column(Text, nullable=False)
    status = Column(String, default="Open")
    created_at = Column(DateTime, default=datetime.utcnow)
    
    shop_id = Column(String, ForeignKey("shops.shop_id"), nullable=False)
    shop = relationship("Shop", back_populates="escalations")
 
# ৮. Rejected Orders Table
class RejectedOrder(Base):
    __tablename__ = "rejected_orders"
    
    reject_id = Column(Integer, primary_key=True, autoincrement=True)
    original_order_id = Column(String)
    customer_id = Column(Integer, ForeignKey("customers.customer_id"))
    product_name = Column(String)
    reason_for_rejection = Column(Text, nullable=False)
    rejected_at = Column(DateTime, default=datetime.utcnow)
    
    shop_id = Column(String, ForeignKey("shops.shop_id"), nullable=False)
    shop = relationship("Shop", back_populates="rejected_orders")
 
# ৯. ChatHistory Table (For Human Handoff & Memory)
class ChatHistory(Base):
    __tablename__ = "chat_history"
    
    message_id = Column(Integer, primary_key=True, autoincrement=True)
    sender = Column(String, nullable=False) # এখানে সেভ হবে: 'user', 'ai', অথবা 'admin'
    message_text = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    # কোন কাস্টমারের মেসেজ?
    customer_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    customer = relationship("Customer", backref="chat_messages")
    
    # 🔒 Multi-tenant Lock: কোন দোকানের মেসেজ?
    shop_id = Column(String, ForeignKey("shops.shop_id"), nullable=False)
    shop = relationship("Shop")
 
# ==========================================
# ডাটাবেস ইঞ্জিন তৈরি করা (Creation Logic)
# ==========================================
def init_db():
    # 'ecommerce_saas.db' নামে একটি SQLite ফাইল তৈরি করবে
    engine = create_engine("sqlite:///ecommerce_saas.db")
    
    # Base অবজেক্টে থাকা সব টেবিল ডাটাবেসে পুশ করবে
    Base.metadata.create_all(engine)
    print("✅ Database and all tables created successfully!")
 
if __name__ == "__main__":
    init_db()
 