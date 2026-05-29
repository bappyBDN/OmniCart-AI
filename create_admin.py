import bcrypt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Admin

# ডাটাবেস কানেকশন
engine = create_engine("sqlite:///ecommerce_saas.db")
SessionLocal = sessionmaker(bind=engine)
session = SessionLocal()

def create_super_admin():
    # চেক করা হচ্ছে আগে থেকে অ্যাডমিন আছে কি না
    admin = session.query(Admin).filter_by(email="admin@shop.com").first()
    
    if not admin:
        # পাসওয়ার্ড এনক্রিপ্ট করা
        hashed_pw = bcrypt.hashpw("123456".encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        # নতুন অ্যাডমিন তৈরি
        new_admin = Admin(
            name="Super Admin",
            email="admin@shop.com",
            password_hash=hashed_pw,
            role="Owner",
            shop_id="shop_01"
        )
        session.add(new_admin)
        session.commit()
        print("✅ ম্যাজিক সফল! অ্যাডমিন অ্যাকাউন্ট ডাটাবেসে তৈরি হয়ে গেছে।")
    else:
        print("⚠️ অ্যাডমিন অ্যাকাউন্ট আগে থেকেই আছে!")

if __name__ == "__main__":
    create_super_admin()
    session.close()