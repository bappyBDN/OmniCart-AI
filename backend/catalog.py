"""SQL aggregates are not similarity search: never count a top-k result list."""
import re

from sqlalchemy import case, func

from backend.database import transaction
from backend.catalog_state import active_product
from backend.language import normalize_query
from models import Product


def is_catalog_browse_question(value):
    """Unfiltered discovery only; product names, budgets and variants stay in RAG."""
    text = normalize_query(value).original.casefold()
    if re.search(r"\d|sku_|[$€£৳]", text) or is_catalog_count_question(value):
        return False
    subject = re.search(r"\b(?:products?|items?|catalog|catalogue|collection|sell|selling|carry)\b|পণ্য|প্রোডাক্ট|পন্য", text)
    if not subject:
        return False
    filler = set("hi hello hey ai please can could would will you your yours me us i we our do does are is a an the some any few available have has sell selling carry about tell give show see look list names name product products item items catalog catalogue collection what whats s which of at in store shop for browse introduce introduce me let lets to want like know may today now offer offerings and stock in-stock".split())
    filler.update("আপনার আপনাদের তোমাদের কি কী পণ্য পন্য প্রোডাক্ট আছে দেখান দেখাও কিছু নাম বলুন বলো আমাকে কীভাবে কি কি সব ধরনের পণ্যের সম্পর্কে জানতে চাই".split())
    words = re.findall(r"[a-z]+|[\u0980-\u09ff]+", text)
    return bool(words) and all(word in filler for word in words)


def browse_catalog(shop_id, limit=5):
    """Stable examples from live inventory, not an invented popularity ranking."""
    from backend.retrieval import product_dict
    with transaction() as db:
        rows = db.query(Product).filter(active_product(), Product.shop_id == shop_id, Product.stock_count > 0).order_by(
            Product.product_id).limit(max(1, min(int(limit), 5))).all()
        return [product_dict(row) for row in rows]


def suggest_alternatives(shop_id, query, limit=3):
    """In-stock products of the same TYPE (or category) the customer asked about.

    Only used after retrieval found no direct match. Returns [] for queries that
    name no known product type or category ("dragon eggs"), so the assistant
    asks a clarifying question instead of pushing unrelated items.
    """
    from backend.retrieval import analyse_query, product_dict, tokens
    plan = analyse_query(query)
    wanted = set().union(*plan.groups) if plan.groups else set()
    if not wanted and not plan.category:
        return []
    with transaction() as db:
        rows = [product_dict(row) for row in db.query(Product).filter(
            active_product(), Product.shop_id == shop_id, Product.stock_count > 0).order_by(Product.product_id).all()]
    same_type = [row for row in rows if wanted and wanted & set(tokens(row["name"]))]
    if same_type:
        colors = {c.casefold() for c in plan.colors}
        # Prefer the requested colour when we have it in another variant/product.
        same_type.sort(key=lambda row: (not colors & {c.casefold() for c in row["colors"]}, row["product_id"]))
        return same_type[:limit]
    category = plan.category or next((row["category"] for row in rows if wanted & set(tokens(row["name"]))), None)
    return [row for row in rows if category and row["category"] == category][:limit]


def catalog_summary(shop_id, category=None):
    with transaction() as db:
        query = db.query(
            func.count(Product.product_id),
            func.coalesce(func.sum(case((Product.stock_count > 0, 1), else_=0)), 0),
            func.coalesce(func.sum(case((Product.stock_count > 0, Product.stock_count), else_=0)), 0),
        ).filter(Product.shop_id == shop_id, active_product())
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
