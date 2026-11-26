"""Read-only database specialist: typed plans, tenant-scoped joins, exact totals.

No SQL supplied by a customer/model is executed. One-to-many aliases are tested
with EXISTS so galleries/aliases cannot multiply stock or inventory values.
"""
import re
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import String, and_, case, cast, func, or_

from backend.catalog_state import active_product
from backend.database import transaction
from backend.records import CatalogEntry, ProductAlias, ProductImage
from models import Product


class SearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(default="", max_length=500)
    product_id: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=100)
    color: str | None = Field(default=None, max_length=40)
    size: str | None = Field(default=None, max_length=20)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    max_price: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    min_price: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    in_stock: bool | None = None
    quantity: int = Field(default=1, ge=1, le=1000, strict=True)
    limit: int = Field(default=20, ge=1, le=50, strict=True)


def search_database(shop_id, **arguments):
    from backend.retrieval import product_dict, tokens
    plan = SearchPlan(**arguments)
    with transaction() as db:
        query = db.query(Product).outerjoin(CatalogEntry, and_(
            CatalogEntry.product_id == Product.product_id, CatalogEntry.shop_id == Product.shop_id)
        ).outerjoin(ProductImage, and_(ProductImage.product_id == Product.product_id,
                                     ProductImage.shop_id == Product.shop_id)
        ).filter(Product.shop_id == shop_id, active_product())
        if plan.product_id:
            query = query.filter(Product.product_id == plan.product_id.upper())
        if plan.category:
            query = query.filter(func.lower(Product.category) == plan.category.casefold())
        if plan.color:
            query = query.filter(cast(Product.colors, String).ilike(f'%"{plan.color}"%'))
        if plan.size:
            query = query.filter(cast(Product.sizes, String).ilike(f'%"{plan.size}"%'))
        if plan.currency:
            # Legacy rows (no metadata) are implicitly BDT; unconfirmed catalog rows stay null.
            query = query.filter(func.coalesce(
                CatalogEntry.currency, case((CatalogEntry.product_id.is_(None), "BDT"), else_=None)) == plan.currency)
        if plan.in_stock is not None:
            query = query.filter(Product.stock_count > 0 if plan.in_stock else Product.stock_count <= 0)
        # Price filters use live confirmed values, never image observations.
        effective = case((and_(Product.discounted_price >= 0, Product.discounted_price < Product.price),
                          Product.discounted_price), else_=Product.price)
        if plan.max_price is not None:
            query = query.filter(effective <= plan.max_price)
        if plan.min_price is not None:
            query = query.filter(effective >= plan.min_price)
        if plan.query:
            exact = re.fullmatch(r"(?:SKU|IMG)_[A-Z0-9_-]+", plan.query, re.I)
            if exact:
                query = query.filter(Product.product_id == plan.query.upper())
            elif re.fullmatch(r"product\d+(?:\.\d+)?\.jpe?g", plan.query, re.I):
                query = query.filter(db.query(ProductAlias.id).filter(
                    ProductAlias.product_id == Product.product_id, ProductAlias.shop_id == shop_id,
                    func.lower(ProductAlias.alias) == plan.query.casefold()).exists())
            else:
                for term in tokens(plan.query)[:12]:
                    # Escape wildcard metacharacters; all SQL values are bound parameters.
                    pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                    alias = db.query(ProductAlias.id).filter(ProductAlias.product_id == Product.product_id,
                        ProductAlias.shop_id == shop_id, ProductAlias.alias.ilike(pattern, escape="\\")).exists()
                    query = query.filter(or_(Product.name.ilike(pattern, escape="\\"),
                        Product.description.ilike(pattern, escape="\\"), Product.category.ilike(pattern, escape="\\"), alias))
        # Aggregations intentionally use ALL matched rows, not the display limit.
        products = [product_dict(p) for p in query.order_by(Product.product_id).all()]
    missing_stock = sum(p["stock_count"] is None for p in products)
    known_stock = sum(p["stock_count"] for p in products if p["stock_count"] is not None)
    currencies = defaultdict(lambda: {"known_inventory_value": Decimal(0), "known_unit_price_sum": Decimal(0),
                                     "missing_price_products": 0, "missing_inventory_products": 0})
    unknown_currency = 0
    for p in products:
        if not p["currency"]:
            unknown_currency += 1
            continue
        group = currencies[p["currency"]]
        price, stock = p["effective_price"], p["stock_count"]
        if price is None:
            group["missing_price_products"] += 1
        else:
            amount = Decimal(str(price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            group["known_unit_price_sum"] += amount
        if price is None or stock is None:
            group["missing_inventory_products"] += 1
        else:
            group["known_inventory_value"] += amount * stock
    for group in currencies.values():
        for key in ("known_inventory_value", "known_unit_price_sum"):
            group[key] = format(group[key], ".2f")
        group["inventory_value"] = group["known_inventory_value"] if not group["missing_inventory_products"] else None
        group["unit_price_sum"] = group["known_unit_price_sum"] if not group["missing_price_products"] else None
    quotes = []
    for p in products[:plan.limit]:
        total = None if p["effective_price"] is None or p["currency"] is None else format(
            Decimal(str(p["effective_price"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * plan.quantity, ".2f")
        quotes.append({"product_id": p["product_id"], "quantity": plan.quantity, "currency": p["currency"],
                       "subtotal": total, "available": None if p["stock_count"] is None else p["stock_count"] >= plan.quantity})
    return {"products": products[:plan.limit], "matched_products": len(products), "returned_products": min(len(products), plan.limit),
            "total_stock": None if missing_stock else known_stock, "known_stock_subtotal": known_stock,
            "missing_stock_products": missing_stock, "missing_price_products": sum(p["effective_price"] is None for p in products),
            "unknown_currency_products": unknown_currency, "currencies": dict(currencies), "quotes": quotes,
            "note": "Merchandise only; no currency conversion, fees, tax calculation, reservation or order. Unknown is not zero."}


def aggregate_question(value):
    return bool(re.search(r"total.{0,30}(?:stock|price|value|amount|cost)|(?:stock|inventory).{0,25}(?:total|value|worth|sum)|"
                         r"মোট.{0,25}(?:স্টক|দাম|মূল্য)|mot.{0,25}(?:stock|dam|daam)|sum of", value, re.I))


def plan_question(value):
    from backend.retrieval import analyse_query
    plan = analyse_query(value)
    identity = re.search(r"\b(?:SKU|IMG)_\d+\b|\bproduct\d+(?:\.\d+)?\.jpe?g\b", value, re.I)
    terms = re.sub(r"\b(?:total|stock|inventory|price|prices|value|worth|amount|cost|sum|of|all|products?|items?|units?|"
                   r"how|much|many|what|is|are|the|and|in|our|your|store|shop|please|tell|me|mot|dam|daam|koto|ache)\b|"
                   r"মোট|স্টক|পণ্য|দাম|মূল্য|কত|আছে", " ", value, flags=re.I).strip(" ?.")
    result = {"query": identity[0] if identity else terms, "category": plan.category,
              "max_price": plan.max_price, "min_price": plan.min_price}
    if plan.colors:
        result["color"] = sorted(plan.colors)[0]
        # Confirmed color filter, not descriptive appearance matches.
        result["query"] = re.sub(r"\b(?:" + "|".join(plan.colors) + r")\b", "", result["query"], flags=re.I).strip()
    return result


def summary_answer(result, bangla=False):
    count, stock = result["matched_products"], result["total_stock"]
    if bangla:
        text = f"মিলেছে {count}টি পণ্য। " + (f"মোট স্টক {stock}টি।" if stock is not None else
            f"{result['missing_stock_products']}টি পণ্যের স্টক নিশ্চিত নয়; সঠিক মোট স্টক বলা যাচ্ছে না।")
        text += f" {result['missing_price_products']}টি পণ্যের দাম ও {result['unknown_currency_products']}টি পণ্যের মুদ্রা নিশ্চিত নয়।"
    else:
        text = f"Matched {count} products. " + (f"Total stock: {stock} units." if stock is not None else
            f"Exact total stock needs confirmation: {result['missing_stock_products']} products have unknown stock (known subtotal: {result['known_stock_subtotal']}).")
        text += f" Unknown prices: {result['missing_price_products']}; unknown currencies: {result['unknown_currency_products']}."
    for currency, group in result["currencies"].items():
        text += f" {currency}: inventory value {group['inventory_value'] if group['inventory_value'] is not None else 'needs confirmation'}; sum of unit prices {group['unit_price_sum'] if group['unit_price_sum'] is not None else 'needs confirmation'}."
    return text
