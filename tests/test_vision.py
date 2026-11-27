"""Phase 1 regressions for visual search: NULL stock and similarity wording."""
from backend import agent, vision
from backend.database import transaction
from backend.retrieval import product_dict
from models import Product


def tshirt_attributes():
    return vision.VisualAttributes(category="T-shirt", color="navy blue", pattern="solid",
                                   material="cotton", style="crew-neck")


def product_source(product_id, shop_id="shop_01"):
    with transaction() as db:
        row = db.query(Product).filter_by(product_id=product_id, shop_id=shop_id).one()
        return agent.catalog_product_source(product_dict(row))


def ids(items):
    return [item["product"]["product_id"] for item in items]


def test_null_stock_is_unknown_never_sold_out(store):
    with transaction() as db:
        db.query(Product).filter_by(product_id="SKU_101", shop_id="shop_01").update({"stock_count": None})

    matches = vision.match_products(tshirt_attributes(), "shop_01")

    assert "SKU_101" not in ids(matches["sold_out"])
    assert "SKU_101" in ids(matches["unknown_stock"])
    assert "SKU_101" not in ids(matches["in_stock"])

    answer, sources, focus = vision.compose_visual_reply(tshirt_attributes(), matches, "en")
    assert focus == "SKU_101"
    assert sources[0]["data"]["product_id"] == "SKU_101"
    assert "stock needs merchant confirmation" in answer.lower()
    assert "sold out" not in answer.lower()


def test_quality_is_strong_match_and_reply_never_claims_exact_identity(store):
    matches = vision.match_products(tshirt_attributes(), "shop_01")
    assert matches["in_stock"][0]["product"]["product_id"] == "SKU_101"
    assert matches["in_stock"][0]["quality"] == "strong_match"
    assert all(item["quality"] != "exact" for group in ("in_stock", "sold_out", "unknown_stock")
               for item in matches[group])

    answer, _, _ = vision.compose_visual_reply(tshirt_attributes(), matches, "en")
    lowered = answer.lower()
    assert "looks very similar" in lowered
    for forbidden in ("exact", "identical", "authentic"):
        assert forbidden not in lowered


def test_out_of_stock_purchase_attaches_similar_product_cards(store):
    with transaction() as db:
        db.query(Product).filter_by(product_id="SKU_101", shop_id="shop_01").update({"stock_count": 0})
    source = product_source("SKU_101")
    state = {"query": "I want this", "locale": "en", "intent": "purchase",
             "previous_sources": [source]}

    result = agent.focused_conversation(state, source, "shop_01", store["customer_id"])

    returned = [row["data"]["product_id"] for row in result["sources"]]
    assert "SKU_101" in returned
    assert len(returned) > 1
    assert result["show_images"] is True
    assert all(row["kind"] == "product" for row in result["sources"])


def test_null_stock_purchase_asks_for_confirmation_instead_of_sold_out(store):
    with transaction() as db:
        db.query(Product).filter_by(product_id="SKU_101", shop_id="shop_01").update({"stock_count": None})
    source = product_source("SKU_101")
    state = {"query": "I want this", "locale": "en", "intent": "purchase",
             "previous_sources": [source]}

    result = agent.focused_conversation(state, source, "shop_01", store["customer_id"])

    assert "stock needs merchant confirmation" in result["answer"].lower()
    assert "out of stock" not in result["answer"].lower()
    assert [row["data"]["product_id"] for row in result["sources"]] == ["SKU_101"]
