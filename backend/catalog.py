"""SQL aggregates are not similarity search: never count a top-k result list."""
import re

from sqlalchemy import case, func

from backend.database import transaction
from backend.language import normalize_query
from models import Product


def catalog_summary(shop_id, category=None):
    with transaction() as db:
        query = db.query(
            func.count(Product.product_id),
            func.coalesce(func.sum(case((Product.stock_count > 0, 1), else_=0)), 0),
            func.coalesce(func.sum(case((Product.stock_count > 0, Product.stock_count), else_=0)), 0),
        ).filter(Product.shop_id == shop_id)
        if category:
            query = query.filter(func.lower(Product.category) == category.strip().lower())
        total, available, units = query.one()
        return {"total_products": int(total), "in_stock_products": int(available),
                "out_of_stock_products": int(total - available), "available_units": int(units),
                "category": category}


def is_catalog_count_question(value):
    """Rules handle unqualified catalog totals only, never a filtered count.

    Queries naming a product, category, budget or variant stay with the agent so
    a global count is not incorrectly presented as the count for that filter.
    """
    text = normalize_query(value).original.casefold()
    if re.search(r"\d|sku_", text):
        return False
    counting = re.search(r"how many|number of|\bcount\b|\btotal\b|কতগুলো|কতগুলি|কতটি|কয়টি|কয়টি|মোট|\b(?:koyta|koyti|kotogulo|koto|koy)\b", text)
    subject = re.search(r"\b(?:products?|items?|catalog|catalogue|inventory|units?|pieces?|stock)\b|পণ্য|প্রোডাক্ট|পন্য|স্টক|\b(?:ponno|ponnyo|product)\b", text)
    if not counting or not subject:
        return False
    filler = set("how many number of count total product products item items catalog catalogue inventory units unit pieces piece stock do does you your have has are is in the store shop currently available out please tell me can could would what there now at all and a an what's whats us with carry sell we our different types type altogether apnar apnader kache mot koyta koyti kotogulo koto koy ponno ponnyo ache ase apni tomader".split())
    filler.update("আপনার আপনাদের কাছে দোকানে দোকানের কত কতগুলো কতগুলি কতটি কয়টি কয়টি মোট পণ্য পন্য প্রোডাক্ট আছে রয়েছে রয়েছে এখন বর্তমানে স্টক স্টকে পাওয়া যায় যায় আমাকে বলুন আপনি কি সব ধরনের প্রকার সংখ্যা মজুদ".split())
    words = re.findall(r"[a-z]+|[\u0980-\u09ff]+", text)
    return bool(words) and all(word in filler for word in words)
