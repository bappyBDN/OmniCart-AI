"""Image catalog import, active/archive scoping and join-based stock/price totals."""
import json

import pytest

from backend import database
from backend.catalog_import import import_catalog, load_inputs
from backend.catalog_state import active_product, orderable
from backend.commerce import prepare_order, quote_product
from backend.database_search import aggregate_question, plan_question, search_database
from backend.records import CatalogEntry, PolicySource, ProductImage
from models import Policy, Product


def test_load_inputs_are_consistent_and_complete():
    catalog, export, policies, translations, faqs = load_inputs()
    assert len(catalog["products"]) == 17
    assert catalog["publication_status"] == "draft_requires_merchant_verification"
    assert all(p["price"] is None for p in catalog["products"])
    assert len(policies) == 50
    assert set(translations) == {p["id"] for p in policies}
    assert all(set(v) == {"bn", "banglish"} and all(v.values()) for v in translations.values())
    assert all(f["source_id"] in translations for f in faqs)
    assert len(export["documents"]) == 68


def test_import_archives_demo_catalog_and_adds_active_products(store):
    catalog, export, policies, translations, faqs = load_inputs()
    with database.transaction() as db:
        result = import_catalog(db, "shop_01", (catalog, export, policies, translations, faqs))
    assert result["active_products"] == 17
    with database.transaction() as db:
        # 17 active new products + 100 archived demo products still present.
        assert db.query(Product).filter_by(shop_id="shop_01").count() == 117
        active_ids = {p.product_id for p in db.query(Product).filter_by(shop_id="shop_01").filter(active_product())}
        assert len(active_ids) == 17
        assert all(pid.startswith("IMG_") for pid in active_ids)
        # Archived products keep their rows and order references.
        archived = db.query(CatalogEntry).filter_by(shop_id="shop_01", active=False).count()
        assert archived == 100
        # Every policy maps to a source with Bangla + Banglish translations.
        sources = db.query(PolicySource).filter_by(shop_id="shop_01").all()
        assert len(sources) == 50
        for source in sources:
            assert "bn" in source.translations and "banglish" in source.translations
        # A catalog product has a local photo record, never an invented price.
        image = db.get(ProductImage, sorted(active_ids)[0])
        assert image and image.image_url.startswith("/api/catalog-media/")


def test_import_is_idempotent(store):
    inputs = load_inputs()
    with database.transaction() as db:
        import_catalog(db, "shop_01", inputs)
    with database.transaction() as db:
        before = db.query(Product).filter_by(shop_id="shop_01").count()
        import_catalog(db, "shop_01", inputs)
    with database.transaction() as db:
        assert db.query(Product).filter_by(shop_id="shop_01").count() == before
        assert db.query(CatalogEntry).filter_by(shop_id="shop_01", active=False).count() == 100


def test_search_database_returns_exact_totals_for_confirmed_products(store):
    result = search_database("shop_01", query="", category="Apparel")
    assert result["matched_products"] > 0
    assert result["total_stock"] is not None  # every legacy row has numeric stock
    assert result["total_stock"] == sum(p["stock_count"] for p in result["products"] if p["stock_count"] is not None) or result["total_stock"] == result["known_stock_subtotal"]
    assert "BDT" in result["currencies"]
    assert result["missing_price_products"] == 0


def test_search_database_reports_unknown_not_zero(store):
    with database.transaction() as db:
        db.add(Product(product_id="IMG_001", name="Unconfirmed item", category="Apparel",
                       price=None, stock_count=None, shop_id="shop_01"))
        db.add(CatalogEntry(product_id="IMG_001", shop_id="shop_01", active=True, currency=None, payload={}, documents=[]))
    result = search_database("shop_01", query="IMG_001")
    assert result["matched_products"] == 1
    assert result["total_stock"] is None
    assert result["missing_stock_products"] == 1
    assert result["missing_price_products"] == 1
    assert result["quotes"][0]["subtotal"] is None


def test_unconfirmed_products_are_not_orderable_or_quoted(store):
    with database.transaction() as db:
        db.add(Product(product_id="IMG_002", name="Draft item", category="Apparel",
                       price=None, stock_count=None, shop_id="shop_01"))
        db.add(CatalogEntry(product_id="IMG_002", shop_id="shop_01", active=True, currency=None, payload={}, documents=[]))
    quote = quote_product("shop_01", "IMG_002", 1)
    assert quote["error"] == "needs_confirmation" and quote["unit_price"] is None
    draft = prepare_order(store["customer_id"], "shop_01", "IMG_002", 1, automated=True)
    assert draft["error"] == "needs_confirmation"


def test_aggregate_question_detection_and_planning():
    assert aggregate_question("what is the total stock of all products")
    assert aggregate_question("মোট পণ্যের দাম কত")
    assert not aggregate_question("show me t-shirts")
    plan = plan_question("total price of blue shirts")
    assert plan["color"] == "blue"
    assert "blue" not in plan["query"].casefold()
