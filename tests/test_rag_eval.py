"""Measured retrieval evaluation.

These are deterministic lexical evaluations so they run in CI without downloading
a model or spending provider tokens. The multilingual dense path is evaluated by
`python -m scripts.eval_rag --dense` against the real local corpus.
"""
import json
from pathlib import Path

from backend.retrieval import HybridRetriever

ROOT = Path(__file__).resolve().parents[1]
POLICY_TOPICS = [item["topic"] for item in json.loads((ROOT / "Data" / "policies.json").read_text(encoding="utf-8"))]

PRODUCT_CASES = [
    ("premium cotton t-shirt", "Premium Cotton T-Shirt"),
    ("cotton t-shirt", "Premium Cotton T-Shirt"),
    ("polo shirt", "Polo Shirt"),
    ("wireless earbuds", "Wireless Earbuds"),
    ("bluetooth earbuds", "Wireless Earbuds"),
    ("yoga mat", "Yoga Mat"),
    ("running shoes", "Running Shoes"),
    ("sneakers", "Sneakers"),
    ("smart watch", "Smart Watch Pro 2"),
    ("gaming keyboard", "Gaming Keyboard"),
    ("coffee mug", "Coffee Mug"),
    ("water bottle", "Water Bottle"),
    ("desk lamp", "Desk Lamp"),
    ("leather wallet", "Leather Wallet"),
    ("backpack", "Backpack"),
    ("phone case", "Phone Case"),
    ("laptop stand", "Laptop Stand"),
    ("SKU_102", "Smart Watch Pro 2"),
    ("black hoodie", "Hoodie"),
    ("blue water bottle", "Water Bottle"),
    ("size M t-shirt", "Premium Cotton T-Shirt"),
    ("lal polo shirt", "Polo Shirt"),
    ("kalo hoodie", "Hoodie"),
    ("juta", "Running Shoes"),
    ("suti jama", "Premium Cotton T-Shirt"),
    ("show shirts under 30", "Polo Shirt"),
    ("show shirts under 30", "Premium Cotton T-Shirt"),
]

NO_ANSWER_PRODUCT = [
    "quantum fusion reactor", "dragon eggs", "pet insurance", "car loan",
    "saree", "laptop", "phone", "how to cook biryani", "weather tomorrow",
]
NO_ANSWER_POLICY = [
    "gift wrapping", "antimatter policy", "how to cook biryani",
    "what is the weather tomorrow", "pet insurance", "car loan",
]


def test_every_store_policy_topic_is_retrievable(store):
    retriever = HybridRetriever(False)
    hits = {}
    for topic in POLICY_TOPICS:
        titles = [item["title"] for item in retriever.search(topic, "shop_01", "policy", 3)]
        hits[topic] = topic in titles
        assert hits[topic], f"Policy topic missed: {topic!r}; top 3={titles}"
    recall_at_1 = sum(
        retriever.search(topic, "shop_01", "policy", 1)[0]["title"] == topic
        for topic in POLICY_TOPICS
    ) / len(POLICY_TOPICS)
    assert recall_at_1 >= 0.90, f"policy recall@1={recall_at_1:.2%}"


def test_product_evaluation_set_hits_top_three(store):
    retriever = HybridRetriever(False)
    misses = []
    for query, expected in PRODUCT_CASES:
        titles = [item["title"] for item in retriever.search(query, "shop_01", "product", 3)]
        if expected not in titles:
            misses.append((query, expected, titles))
    assert not misses, f"Product misses: {misses}"
    assert (len(PRODUCT_CASES) - len(misses)) / len(PRODUCT_CASES) >= 0.90


def test_no_answer_gate_prevents_unrelated_nearest_neighbour_answers(store):
    retriever = HybridRetriever(False)
    product_misses = {
        query: [item["title"] for item in retriever.search(query, "shop_01", "product", 3)]
        for query in NO_ANSWER_PRODUCT
    }
    policy_misses = {
        query: [item["title"] for item in retriever.search(query, "shop_01", "policy", 3)]
        for query in NO_ANSWER_POLICY
    }
    assert all(not titles for titles in product_misses.values()), product_misses
    assert all(not titles for titles in policy_misses.values()), policy_misses


def test_sources_expose_explainable_ranking_metadata(store):
    result = HybridRetriever(False).search("cotton t-shirt", "shop_01", "product", 1)[0]
    assert result["id"] == "product:SKU_101"
    assert result["kind"] == "product"
    assert result["source_type"] == "product"
    assert 0 < result["confidence"] <= 1
    assert result["lexical_score"] > 0
    assert result["dense_score"] == 0
    assert result["retrieval"] == "lexical"
    assert result["data"]["product_id"] == "SKU_101"
