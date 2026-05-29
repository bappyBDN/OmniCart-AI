import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Product, Policy 

# ১. ডাটাবেস কানেকশন সেটআপ
engine = create_engine("sqlite:///ecommerce_saas.db")
SessionLocal = sessionmaker(bind=engine)
session = SessionLocal()

def load_products(json_file_path, target_shop_id):
    """JSON থেকে প্রোডাক্ট ডাটাবেসে লোড করার ফাংশন"""
    try:
        with open(json_file_path, 'r', encoding='utf-8') as file:
            products_data = json.load(file)
            
            added_count = 0
            for item in products_data:
                # চেক করা যে প্রোডাক্টটি আগে থেকেই ডাটাবেসে আছে কি না
                existing_product = session.query(Product).filter_by(product_id=item["product_id"]).first()
                
                if not existing_product:
                    new_product = Product(
                        product_id=item["product_id"],
                        name=item["name"],
                        category=item["category"],
                        price=item["price"],
                        discounted_price=item.get("discounted_price"), 
                        sizes=item["sizes"],
                        colors=item["colors"],
                        stock_count=item["stock_count"],
                        status=item["status"],
                        description=item["description"],
                        shop_id=target_shop_id # 🔒 Multi-tenant Lock
                    )
                    session.add(new_product)
                    added_count += 1
                    
            session.commit()
            print(f"📦 প্রোডাক্ট: {added_count} টি নতুন প্রোডাক্ট সফলভাবে ডাটাবেসে সেভ হয়েছে!")
            
    except Exception as e:
        session.rollback()
        print(f"❌ প্রোডাক্ট লোড করতে এরর: {e}")

def load_policies(json_file_path, target_shop_id):
    """JSON থেকে পলিসি ডাটাবেসে লোড করার ফাংশন"""
    try:
        with open(json_file_path, 'r', encoding='utf-8') as file:
            policies_data = json.load(file)
            
            added_count = 0
            for item in policies_data:
                # 🛠️ FIXED: JSON-এর "topic" এবং "content" কে ডাটাবেসের কলামের সাথে ম্যাপ করা হলো
                new_policy = Policy(
                    policy_type=item["topic"],
                    policy_text=item["content"],
                    shop_id=target_shop_id
                )
                session.add(new_policy)
                added_count += 1
                
            session.commit()
            print(f"📜 পলিসি: {added_count} টি নতুন পলিসি সফলভাবে ডাটাবেসে সেভ হয়েছে!")
            
    except Exception as e:
        session.rollback()
        print(f"❌ পলিসি লোড করতে এরর: {e}")

if __name__ == "__main__":
    # আমরা এই ডেটাগুলো "shop_01" (Style & Fashion BD) এর জন্য সেভ করছি
    target_shop = "shop_01" 
    
    print("⏳ ডাটাবেসে JSON ডেটা ইমপোর্ট শুরু হচ্ছে...")
    
    # ফাংশন কল করা
    load_products("E:\Agentic_Chat_BotV-0.1.0\Data\products.json", target_shop)
    load_policies("E:\Agentic_Chat_BotV-0.1.0\Data\policies.json", target_shop)
    
    session.close()
    print("✅ ম্যাজিক সম্পন্ন! সব ডেটা ডাটাবেসে রেডি।")