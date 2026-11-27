"""Apply merchant-confirmed commercial facts to the active image catalog.

Idempotent and additive: updates only active catalog products, never customers,
orders or the archived demo records. Run with --price/--stock/--currency to
override the defaults (1299, 12, BDT).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import database
from backend.catalog_state import active_product
from backend.config import settings
from backend.records import CatalogEntry
from models import Product


def confirm(price, stock, currency):
    with database.transaction() as db:
        rows = db.query(Product).filter_by(shop_id=settings.shop_id).filter(active_product()).all()
        for product in rows:
            product.price = price
            product.discounted_price = None
            product.stock_count = stock
            product.status = "In Stock" if stock > 0 else "Out of Stock"
            entry = db.get(CatalogEntry, (product.shop_id, product.product_id))
            if entry:
                entry.currency = currency
        return [row.product_id for row in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--price", type=float, default=1299.0)
    parser.add_argument("--stock", type=int, default=12)
    parser.add_argument("--currency", default="BDT")
    args = parser.parse_args()
    ids = confirm(args.price, args.stock, args.currency)
    print(f"Confirmed price {args.currency} {args.price:,.0f} / stock {args.stock} for {len(ids)} active products.")
    print("Updated:", ", ".join(sorted(ids)))


if __name__ == "__main__":
    main()
