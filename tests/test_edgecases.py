"""Edge-case RAG ingestion, language mirroring, push-sell and translation."""
import json

from backend import database
from backend.catalog import suggest_similar
from backend.edgecases import edgecase_documents, import_edgecases, load_edgecases
from backend.records import EdgeCaseQA
from backend.retrieval import retriever
from backend.translate import localize_answer, to_english
from models import Product


def test_edgecases_load_and_validate():
    entries = load_edgecases()
    assert len(entries) >= 19
    for entry in entries:
        assert {"en", "bn", "banglish"} <= set(entry["queries"]) <= {"en", "bn", "banglish"}
        assert {"en", "bn", "banglish"} <= set(entry["responses"])


def test_edgecase_import_and_language_mirroring(store):
    entries = load_edgecases()
    with database.transaction() as db:
        import_edgecases(db, "shop_01", entries)
    with database.transaction() as db:
        docs = edgecase_documents(db, "shop_01")
        assert any(d.metadata["id"] == "edgecase:size_001" for d in docs)
        assert all(d.metadata["source_type"] == "edgecase" for d in docs)
        assert all(d.metadata["locale"] == "multi" for d in docs)
        size = next(d for d in docs if d.metadata["id"] == "edgecase:size_001")
        # Language mirroring: the Bangla and Banglish query forms are indexed.
        assert "৩৮" in size.metadata["keywords"]
        assert "heavier hips" in size.metadata["keywords"]


def test_edgecase_is_retrievable_in_bangla(store):
    entries = load_edgecases()
    with database.transaction() as db:
        import_edgecases(db, "shop_01", entries)
    results = retriever.search("বডি সাইজ ৩৮ কিন্তু হিপ ভারী", "shop_01", "policy", 3)
    assert any(s["source_type"] == "edgecase" for s in results)


def test_translator_normalises_banglish_and_bangla(store):
    assert "green kurti tunic" in to_english("sobuj kurti")
    assert "saree" in to_english("শাড়ি")
    assert "gown dress" in to_english("গাউন")


def test_localize_answer_translates_english_to_bangla_for_bn_customers():
    class Model:
        def invoke(self, messages):
            return {"content": "এটি একটি বাংলা উত্তর।"}
    model = Model()
    assert localize_answer("Hello world", "bn", model) == "এটি একটি বাংলা উত্তর।"
    assert localize_answer("ইতিমধ্যে বাংলা", "bn", model) == "ইতিমধ্যে বাংলা"
    assert localize_answer("Already English", "en", model) == "Already English"
    assert localize_answer("English", "bn", None) == "English"  # no model -> unchanged


def test_push_sell_suggests_similar_out_of_stock(store):
    with database.transaction() as db:
        db.query(Product).filter_by(shop_id="shop_01").update({"stock_count": 0})
        db.query(Product).filter_by(shop_id="shop_01", product_id="SKU_161").update({"stock_count": 12})  # Polo Shirt
        db.query(Product).filter_by(shop_id="shop_01", product_id="SKU_153").update({"stock_count": 8})   # Dress
    from backend.retrieval import product_dict
    with database.transaction() as db:
        target = product_dict(db.query(Product).filter_by(product_id="SKU_101", shop_id="shop_01").first())
    similar = suggest_similar("shop_01", target, 3)
    assert similar and all(row["stock_count"] > 0 for row in similar)
    assert target["product_id"] not in [row["product_id"] for row in similar]
