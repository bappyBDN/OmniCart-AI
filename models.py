from sqlalchemy import (create_engine, Column, String, Integer, Float, Text, ForeignKey,
                        DateTime, JSON, Boolean, Numeric, ForeignKeyConstraint, CheckConstraint)
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
    __table_args__ = (
        CheckConstraint("price IS NULL OR price >= 0", name="ck_products_price_nonnegative"),
        CheckConstraint("discounted_price IS NULL OR discounted_price >= 0",
                        name="ck_products_discounted_price_nonnegative"),
        CheckConstraint("stock_count IS NULL OR stock_count >= 0",
                        name="ck_products_stock_nonnegative"),
    )

    # Tenant-local identity: two shops may each have SKU_101.
    shop_id = Column(String, ForeignKey("shops.shop_id"), primary_key=True)
    product_id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    category = Column(String)
    price = Column(Numeric(12, 2), nullable=True)  # NULL is unconfirmed, never free.
    discounted_price = Column(Numeric(12, 2))
    stock_count = Column(Integer().evaluates_none(), default=0)

    colors = Column(JSON)
    sizes = Column(JSON)

    status = Column(String, default="In Stock")
    description = Column(Text)

    shop = relationship("Shop", back_populates="products")
    catalog_entry = relationship(
        "CatalogEntry", uselist=False, lazy="joined",
        foreign_keys="[CatalogEntry.shop_id, CatalogEntry.product_id]",
    )
    orders = relationship("Order", back_populates="product", overlaps="orders",
                          foreign_keys="[Order.shop_id, Order.product_id]")
 
# ৪. Order Table - 🔥 FIXED VERSION
class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        ForeignKeyConstraint(["shop_id", "product_id"],
                             ["products.shop_id", "products.product_id"],
                             name="fk_orders_shop_product"),
        CheckConstraint("price >= 0", name="ck_orders_price_nonnegative"),
        CheckConstraint("total_price >= 0", name="ck_orders_total_nonnegative"),
    )

    order_id = Column(Integer, primary_key=True, autoincrement=True)
    product_name = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    price = Column(Numeric(12, 2), nullable=False)
    total_price = Column(Numeric(12, 2), nullable=False)
    currency = Column(String)
    unit_price_snapshot = Column(Numeric(12, 2))
    line_total_snapshot = Column(Numeric(12, 2))
    status = Column(String, default="Pending")
    created_at = Column(DateTime, default=datetime.utcnow)

    customer_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    customer = relationship("Customer", back_populates="orders")

    product_id = Column(String)
    shop_id = Column(String, ForeignKey("shops.shop_id"), nullable=False)
    product = relationship("Product", back_populates="orders", overlaps="orders",
                           foreign_keys=[shop_id, product_id])
    shop = relationship("Shop", back_populates="orders", overlaps="orders,product")
 
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
