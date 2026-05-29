# core/db_handler.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Customer

# কানেকশন সেটিংস
engine = create_engine("sqlite:///ecommerce_saas.db", connect_args={'check_same_thread': False})
SessionLocal = sessionmaker(bind=engine)

# সরাসরি গ্লোবাল সেশন না রেখে, একটি সেশন-ফ্যাক্টরি তৈরি করুন
def get_db():
    engine = create_engine("sqlite:///ecommerce_saas.db")
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()

# আপনার ফাংশনটিকে আপডেট করুন সেশন পাস করার জন্য
def get_or_create_customer(unique_id, shop_id="shop_01"):
    db = SessionLocal() # প্রতিবার ফাংশন কলের সময় ফ্রেশ সেশন
    try:
        customer = db.query(Customer).filter_by(unique_id=unique_id, shop_id=shop_id).first()
        if not customer:
            customer = Customer(unique_id=unique_id, shop_id=shop_id)
            db.add(customer)
            db.commit()
        return customer
    finally:
        db.close() # কাজ শেষে সেশন ক্লোজ হবে