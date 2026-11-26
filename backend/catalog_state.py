"""Publication and commercial-fact invariants shared by catalog consumers."""
from sqlalchemy import select

from backend.records import CatalogEntry
from models import Product


def active_product():
    """NOT EXISTS filter: active unless an explicit inactive catalog entry exists.

    `correlate_except` pins CatalogEntry inside the subquery's own FROM even when
    the enclosing query also joins CatalogEntry, so correlation never collapses.
    """
    sub = select(CatalogEntry.product_id).where(
        CatalogEntry.product_id == Product.product_id,
        CatalogEntry.shop_id == Product.shop_id,
        CatalogEntry.active.is_(False),
    ).correlate_except(CatalogEntry)
    return ~sub.exists()


def orderable(data):
    return (data.get("active", True) and data.get("effective_price") is not None
            and data.get("currency") is not None and data.get("stock_count") is not None)


def commercial_text(data):
    price, currency = data.get("effective_price"), data.get("currency")
    pricing = f"{price:.2f} {currency}" if price is not None and currency else "Price/currency needs confirmation"
    stock = data.get("stock_count")
    inventory = f"{stock} in stock" if stock is not None else "Stock needs confirmation"
    return f"{pricing}; {inventory}"
