"""Attach representative online product images for local UI testing.

These are real Unsplash photos used as category/type representatives. They are
not guaranteed to depict the exact SKU. For production, replace them with the
merchant's own licensed product photography via Admin → Catalogue → Image.

Run:
    python -m scripts.seed_product_images
    python -m scripts.seed_product_images --force
"""
import argparse
from datetime import datetime

from backend.config import settings
from backend.database import initialize, transaction
from backend.records import ProductImage
from models import Product

BASE = "https://images.unsplash.com/photo-{id}?auto=format&fit=crop&w=720&q=80"
# Longer/more specific keys win because the matcher sorts by key length.
BY_TYPE = {
    "t-shirt": "1521572163474-6864f9cf17ab", "tshirt": "1521572163474-6864f9cf17ab",
    "polo shirt": "1596755094514-f87e34085b2c", "shirt": "1596755094514-f87e34085b2c",
    "hoodie": "1556821840-3a63f95609a7", "jacket": "1551028719-00167b16eac5",
    "sweatpants": "1506629082955-511b1aa562c8", "shorts": "1591195853828-11db59a44f6b",
    "dress": "1595777457583-95e059d581b8", "leggings": "1506629082955-511b1aa562c8",
    "socks": "1586350977771-b3b0abd50c82", "shoes": "1542291026-7eec264c27ff",
    "sneakers": "1542291026-7eec264c27ff", "sandals": "1603487742131-4160ec999306",
    "watch": "1523275335684-37898b6baf30", "wallet": "1627123424574-724758594e93",
    "backpack": "1553062407-98eeb64c6a62", "sunglasses": "1511499767150-a48a237f0083",
    "belt": "1624222247344-550fb60583dc", "cap": "1588850561407-ed78c282e89b",
    "scarf": "1601924994987-69e26d50dc26", "gloves": "1601924994987-69e26d50dc26",
    "headphone": "1505740420928-5e560c06d30e", "earbud": "1606220945770-b5b6c2c55bf1",
    "speaker": "1608043152269-423dbba4e7e1", "keyboard": "1587829741301-dc798b83add3",
    "mouse": "1527864550417-7fd91fc51a46", "monitor": "1527443224154-c4a3942d3acf",
    "laptop": "1496181133206-80ce9b88a853", "phone": "1511707171634-5f897ff02aa9",
    "mug": "1577937927133-66ef06acdf18", "bottle": "1602143407151-7111542de6e8",
    "lamp": "1507473885765-e6ed057f782c", "chair": "1503602642458-232111445657",
    "mat": "1592432678016-e910b452f9a2", "dumbbells": "1583454110551-21f2fa2afe61",
    "resistance bands": "1592432678016-e910b452f9a2", "notebook": "1531346878377-a5be20888e57",
    "pens": "1583485088034-697b5bc54ccd", "calculator": "1564466809058-bf4114d55352",
    "blender": "1570222094114-d054a817e56b", "vacuum": "1558317374-067fb5f30001",
    "candle": "1603006905003-be475563bc59", "pot": "1485955900006-10f4d324d411",
    "soap": "1600857544200-b2f666a9a2ec", "face wash": "1596462502278-27bfdc403348",
    "shampoo": "1608248543803-ba4f8c70ae0b", "makeup": "1596462502278-27bfdc403348",
    "coffee": "1447933601403-0c6688de566e", "puzzle": "1586165368502-1bad197a6461",
    "tools": "1530124566582-a618bc2615dc",
}
BY_CATEGORY = {
    "Apparel": "1521572163474-6864f9cf17ab",
    "Electronics": "1505740420928-5e560c06d30e",
    "Accessories": "1553062407-98eeb64c6a62",
    "Home": "1503602642458-232111445657",
    "Sports": "1592432678016-e910b452f9a2",
    "Stationery": "1531346878377-a5be20888e57",
    "Health": "1600857544200-b2f666a9a2ec",
    "Beauty": "1596462502278-27bfdc403348",
    "Grocery": "1447933601403-0c6688de566e",
    "Toys": "1586165368502-1bad197a6461",
    "Tools": "1530124566582-a618bc2615dc",
}


def image_for(product):
    name = (product.name or "").casefold()
    for keyword in sorted(BY_TYPE, key=len, reverse=True):
        if keyword in name:
            return BASE.format(id=BY_TYPE[keyword])
    return BASE.format(id=BY_CATEGORY.get(product.category, BY_CATEGORY["Home"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Overwrite existing image records")
    args = parser.parse_args()
    initialize()
    updated = skipped = 0
    with transaction() as db:
        products = db.query(Product).filter_by(shop_id=settings.shop_id).order_by(Product.product_id).all()
        for product in products:
            existing = db.get(ProductImage, product.product_id)
            if existing and not args.force:
                skipped += 1
                continue
            url = image_for(product)
            if existing:
                existing.image_url = url
                existing.alt_text = f"{product.name} representative image"
                existing.updated_at = datetime.utcnow()
            else:
                db.add(ProductImage(product_id=product.product_id, shop_id=settings.shop_id,
                                    image_url=url, alt_text=f"{product.name} representative image"))
            updated += 1
    print(f"Product images updated: {updated}; preserved existing: {skipped}")


if __name__ == "__main__":
    main()
