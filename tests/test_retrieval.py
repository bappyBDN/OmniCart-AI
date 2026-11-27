import pytest

from backend.database import transaction
from backend.retrieval import HybridRetriever, expand, split_policy, tokens
from models import Product


@pytest.mark.parametrize("query,kind,expected", [
    ("cotton t-shirt", "product", "Premium Cotton T-Shirt"),
    ("টিশার্ট", "product", "Premium Cotton T-Shirt"),
    ("শার্ট", "product", "Polo Shirt"),
    ("wireless earbuds", "product", "Wireless Earbuds"),
    ("যোগা ম্যাট", "product", "Yoga Mat"),
    ("running shoes", "product", "Running Shoes"),
    ("SKU_102", "product", "Smart Watch Pro 2"),
    ("refund policy", "policy", "Refund Policy"),
    ("রিফান্ড", "policy", "Refund Policy"),
    ("return window", "policy", "Return Window"),
    ("shipping charges time", "policy", "Shipping Charges & Time"),
    ("ডেলিভারি", "policy", "Shipping Charges & Time"),
    ("warranty coverage", "policy", "Warranty Coverage"),
    ("damaged item", "policy", "Damaged Items"),
    ("payment methods", "policy", "Payment Methods"),
    ("order cancellation", "policy", "Order Cancellation"),
    ("international shipping", "policy", "International Shipping"),
    ("cash on delivery terms", "policy", "Cash on Delivery Terms"),
])
def test_expected_source_in_top_three(store, query, kind, expected):
    results = HybridRetriever(False).search(query, "shop_01", kind, 3)
    assert expected in [r["title"] for r in results]


def test_no_irrelevant_nearest_neighbor(store):
    assert HybridRetriever(False).search("quantum fusion reactor", "shop_01") == []


def test_bangla_shipping_phrase_retrieves_the_delivery_policy(store):
    query = "শহরের ভিতরে এবং বাইরে ডেলিভারি চার্জ কত?"
    results = HybridRetriever(False).search(query, "shop_01", "policy", 3)
    assert [item["title"] for item in results] == ["Shipping Charges & Time"]
    assert "ের" not in expand(query)


def test_multi_topic_query_preserves_refund_and_return_policies(store):
    results = HybridRetriever(False).search("What is the difference between your refund policy and return window?", "shop_01", "policy", 4)
    titles = [r["title"] for r in results]
    assert "Refund Policy" in titles
    assert "Return Window" in titles


def test_strict_shirt_matching_and_budget(store):
    results = HybridRetriever(False).search("show shirts under $30", "shop_01", "product")
    assert results
    assert all("shirt" in result["title"].lower() for result in results)
    assert all(result["data"]["effective_price"] <= 30 for result in results)


def test_exact_color_and_size_constraints(store):
    results = HybridRetriever(False).search("red shirt size M", "shop_01", "product")
    assert [r["title"] for r in results] == ["Polo Shirt"]
    assert HybridRetriever(False).search("red shirt size XS", "shop_01", "product") == []


def test_accessory_is_not_substituted_for_the_product(store):
    results = HybridRetriever(False).search("gaming keyboard", "shop_01", "product")
    assert [r["title"] for r in results] == ["Gaming Keyboard"]
    assert HybridRetriever(False).search("laptop", "shop_01", "product") == []
    assert HybridRetriever(False).search("laptop stand", "shop_01", "product")[0]["title"] == "Laptop Stand"


def test_attribute_only_queries_use_sql_filters_not_empty_search(store):
    retriever = HybridRetriever(False)
    under_thirty = retriever.search("products under $30", "shop_01", "product")
    assert under_thirty and all(item["data"]["effective_price"] <= 30 for item in under_thirty)
    out_of_stock = retriever.search("out of stock products", "shop_01", "product")
    assert out_of_stock and all(item["data"]["stock_count"] <= 0 for item in out_of_stock)
    electronics = retriever.search("electronics", "shop_01", "product")
    assert electronics and all(item["data"]["category"] == "Electronics" for item in electronics)


def test_live_sql_stock_and_prices_not_json(store):
    with transaction() as db:
        p = db.get(Product, ("shop_01", "SKU_101"))
        p.stock_count, p.price, p.discounted_price = 3, 15, 12
    product = HybridRetriever(False).search("SKU_101", "shop_01", "product", 1)[0]["data"]
    assert product["stock_count"] == 3
    assert product["effective_price"] == 12


def test_shop_isolation(store):
    results = HybridRetriever(False).search("cotton shirt refund", "shop_01")
    assert all("OTHER_SECRET" not in r["content"] and "999 days" not in r["content"] for r in results)


def test_chunking_preserves_policy_content():
    text = "Shipping is subject to location. " * 100
    chunks = split_policy(text)
    assert len(chunks) > 1
    assert all(len(chunk) <= 650 for chunk in chunks)
    assert text[-50:].strip() in chunks[-1]


def test_bangla_tokens_are_not_discarded():
    assert "refund" in tokens("রিফান্ড পলিসি")


def test_dense_rrf_uses_live_documents_and_relevance_threshold(store, monkeypatch):
    retriever = HybridRetriever(True)
    class Embedding:
        def embed_query(self, query): return [1.0, 0.0]
    retriever.embedding = Embedding()
    monkeypatch.setattr(retriever, 'warm', lambda shop: ('current', {'product:SKU_101': [0.9, 0.1], 'OTHER_SECRET': [1.0, 0.0]}))
    result = retriever.search('comfortable summer clothing', 'shop_01', 'product', 1)
    assert result[0]['id'] == 'product:SKU_101'
    assert result[0]['retrieval'] == 'hybrid'
    assert result[0]['data']['stock_count'] == 15
    # Cosine similarity of this vector with the mock query is ~0.39, below the
    # configured dense threshold. Do not assume stored vectors are pre-normalized.
    monkeypatch.setattr(retriever, 'warm', lambda shop: ('current', {'product:SKU_101': [0.39, 0.921]}))
    assert retriever.search('quantum fusion reactor', 'shop_01', 'product') == []
