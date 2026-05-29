from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Shop, Product
from datetime import datetime, timezone # 🆕 timezone ইমপোর্ট করা হলো

# ১. ডাটাবেসের সাথে কানেক্ট করা
engine = create_engine("sqlite:///ecommerce_saas.db")
SessionLocal = sessionmaker(bind=engine)
session = SessionLocal()

def insert_seed_data():
    print("⏳ ডাটাবেস চেক করা হচ্ছে...")

    try:
        # ২. চেক করা: দোকানটি আগে থেকেই আছে কি না?
        existing_shop = session.query(Shop).filter_by(shop_id="shop_01").first()
        
        if not existing_shop:
            shop_01 = Shop(
                shop_id="shop_01",
                shop_name="Style & Fashion BD",
                bot_tone="Friendly and Professional",
                created_at=datetime.now(timezone.utc) # 🛠️ FIXED: utcnow() এর বদলে এটি
            )
            session.add(shop_01)
            print("✅ নতুন Shop তৈরি হয়েছে!")
        else:
            print("⚠️ Shop ('shop_01') আগে থেকেই ডাটাবেসে আছে।")

        # ৩. চেক করা: প্রোডাক্টটি আগে থেকেই আছে কি না?
        existing_product = session.query(Product).filter_by(product_id="SKU_101").first()
        
        if not existing_product:
            product_01 = Product(
                product_id="SKU_101",
                name="Premium Cotton T-Shirt",
                category="Apparel",
                price=25.00,
                discounted_price=19.99,
                sizes=["M", "L", "XL"],        
                colors=["Black", "Navy Blue"], 
                stock_count=15,
                status="In Stock",
                description="100% pure cotton fabric, perfect for summer comfort.",
                shop_id="shop_01"              
            )
            session.add(product_01)
            print("✅ নতুন Product তৈরি হয়েছে!")
        else:
            print("⚠️ Product ('SKU_101') আগে থেকেই ডাটাবেসে আছে।")

        # ৪. ডাটাবেসে ফাইনালি সেভ (Commit) করা
        session.commit()
        print("🎉 প্রসেস সফলভাবে সম্পন্ন হয়েছে!")

    except Exception as e:
        session.rollback()
        print(f"❌ এরর হয়েছে: {e}")
        
    finally:
        session.close()

if __name__ == "__main__":
    insert_seed_data()