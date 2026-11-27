"""Validated, idempotent image-catalog import. No customer/order writes."""
import hashlib
import json
from collections import defaultdict

from sqlalchemy import text

from backend.config import ROOT
from backend.records import CatalogEntry, PolicySource, ProductAlias, ProductImage
from models import Policy, Product, Shop


def load_inputs(root=ROOT):
    from scripts.build_product_image_rag import build_export, validate_catalog
    def load(name):
        return json.loads((root / "Data" / name).read_text(encoding="utf-8"))
    catalog, export = load("product_image_catalog.json"), load("product_image_rag.json")
    validate_catalog(catalog, root)
    if build_export(catalog) != export:
        raise ValueError("RAG export is stale; rebuild it before importing")
    policies, translations, faqs = load("policies.json"), load("policy_translations.json"), load("policy_faqs.json")
    if set(translations) != {p["id"] for p in policies}:
        raise ValueError("Every canonical policy needs translations")
    for value in translations.values():
        if set(value) != {"bn", "banglish"} or not all(value.values()):
            raise ValueError("Missing translation")
    if any(f["source_id"] not in translations for f in faqs):
        raise ValueError("FAQ references a missing source policy")
    return catalog, export, policies, translations, faqs


def import_catalog(db, shop_id, inputs):
    catalog, export, policies, translations, faqs = inputs
    if not db.get(Shop, shop_id):
        raise ValueError("Target shop does not exist")
    if catalog.get("shop_id") not in (None, shop_id):
        raise ValueError("Catalog belongs to another shop")
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(88174213)"))
        db.execute(text("ALTER TABLE products ALTER COLUMN price DROP NOT NULL"))
    docs = defaultdict(list)
    for doc in export["documents"]:
        docs[doc["metadata"]["product_id"]].append({"id": doc["id"], "text": doc["page_content"]})
    ids = {p["product_id"] for p in catalog["products"]}
    # Archive, never delete. Stable IDs and all order FKs survive.
    for row in db.query(Product).filter_by(shop_id=shop_id).all():
        if row.product_id not in ids:
            entry = db.get(CatalogEntry, (shop_id, row.product_id))
            if not entry:
                entry = CatalogEntry(product_id=row.product_id, shop_id=shop_id, currency="BDT", payload={}, documents=[])
                db.add(entry)
            entry.active = False
    for source in catalog["products"]:
        product_id = source["product_id"]
        row = db.get(Product, (shop_id, product_id))
        if not row:
            row = Product(product_id=product_id, shop_id=shop_id, price=source["price"],
                          discounted_price=source["discounted_price"], stock_count=source["stock_count"],
                          colors=source["available_colors"], sizes=source["sizes"])
            db.add(row)
        row.name = source["name"]["en"]
        row.category = source["category"]["code"]
        row.description = "\n".join(source["description"].values()) + "\n" + "\n".join(source["notes"].values())
        row.status = "Needs confirmation" if row.stock_count is None else "In Stock" if row.stock_count > 0 else "Out of Stock"
        db.flush()
        entry = db.get(CatalogEntry, (shop_id, product_id))
        if not entry:
            entry = CatalogEntry(product_id=product_id, shop_id=shop_id, currency=source["currency"] or catalog["currency"])
            db.add(entry)
        entry.active, entry.payload, entry.documents = True, source, docs[product_id]
        image = db.get(ProductImage, (shop_id, product_id))
        if not image:
            image = ProductImage(product_id=product_id, shop_id=shop_id)
            db.add(image)
        primary = next(i for i in source["images"] if i.get("is_primary"))
        image.image_url = "/api/catalog-media/" + primary["path"].rsplit("/", 1)[-1]
        image.alt_text = source["name"]["en"] + " — image observation; merchant verification required"
        existing = {a.alias for a in db.query(ProductAlias).filter_by(product_id=product_id, shop_id=shop_id)}
        aliases = [(locale, alias) for locale, values in source["search_aliases"].items() for alias in values]
        aliases += [("en", image["path"].rsplit("/", 1)[-1]) for image in source["images"]]
        for locale, alias in aliases:
            if alias not in existing:
                db.add(ProductAlias(product_id=product_id, shop_id=shop_id, alias=alias, locale=locale))
                existing.add(alias)
    db.flush()
    for source in policies:
        mapping = db.get(PolicySource, (shop_id, source["id"]))
        row = db.get(Policy, mapping.policy_id) if mapping else None
        if row is None:
            matches = db.query(Policy).filter_by(shop_id=shop_id, policy_type=source["topic"]).all()
            if len(matches) > 1:
                raise ValueError("Ambiguous existing policy topic; review before import")
            row = matches[0] if matches else Policy(shop_id=shop_id)
            db.add(row)
        row.policy_type, row.policy_text = source["topic"], source["content"]
        db.flush()
        if not mapping:
            mapping = PolicySource(shop_id=shop_id, source_id=source["id"], policy_id=row.policy_id)
            db.add(mapping)
        value = {"en": source["content"], **translations[source["id"]]}
        for faq in faqs:
            if faq["source_id"] == source["id"]:
                for locale in ("en", "bn", "banglish"):
                    value[locale] += "\nFAQ: " + faq[locale] + "\n" + (source["content"] if locale == "en" else translations[source["id"]][locale])
        mapping.translations = value
        mapping.source_hash = hashlib.sha256(source["content"].encode()).hexdigest()
    db.flush()
    return {"active_products": len(ids), "images": catalog["image_count"], "product_rag_sections": len(export["documents"]),
            "canonical_policies": len(policies), "derived_faqs": len(faqs)}
