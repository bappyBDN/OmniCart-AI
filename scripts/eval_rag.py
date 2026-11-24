"""Offline retrieval evaluation against the real local corpus.

Default: deterministic lexical evaluation (no model, no network, no writes).
--dense: also warm the cached multilingual embedding model and evaluate the
hybrid ranking. This consumes local CPU but no provider tokens.

Run:
    .\\venv\\Scripts\\python.exe -m scripts.eval_rag
    .\\venv\\Scripts\\python.exe -m scripts.eval_rag --dense
"""
import argparse
import json
import time
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
]
NO_ANSWER_PRODUCT = [
    "quantum fusion reactor", "dragon eggs", "pet insurance", "car loan",
    "saree", "laptop", "phone", "how to cook biryani", "weather tomorrow",
]
NO_ANSWER_POLICY = [
    "gift wrapping", "antimatter policy", "how to cook biryani",
    "what is the weather tomorrow", "pet insurance", "car loan",
]


def evaluate(retriever):
    policy_hits_1 = policy_hits_3 = 0
    for topic in POLICY_TOPICS:
        titles = [item["title"] for item in retriever.search(topic, "shop_01", "policy", 3)]
        policy_hits_1 += bool(titles and titles[0] == topic)
        policy_hits_3 += topic in titles
    product_hits_1 = product_hits_3 = 0
    product_misses = []
    for query, expected in PRODUCT_CASES:
        titles = [item["title"] for item in retriever.search(query, "shop_01", "product", 3)]
        product_hits_1 += bool(titles and titles[0] == expected)
        product_hits_3 += expected in titles
        if expected not in titles:
            product_misses.append((query, expected, titles))
    no_answer_total = len(NO_ANSWER_PRODUCT) + len(NO_ANSWER_POLICY)
    no_answer_ok = sum(not retriever.search(query, "shop_01", "product", 3) for query in NO_ANSWER_PRODUCT)
    no_answer_ok += sum(not retriever.search(query, "shop_01", "policy", 3) for query in NO_ANSWER_POLICY)
    return {
        "policy_recall_at_1": policy_hits_1 / len(POLICY_TOPICS),
        "policy_recall_at_3": policy_hits_3 / len(POLICY_TOPICS),
        "product_recall_at_1": product_hits_1 / len(PRODUCT_CASES),
        "product_recall_at_3": product_hits_3 / len(PRODUCT_CASES),
        "no_answer_accuracy": no_answer_ok / no_answer_total,
        "product_misses": product_misses,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dense", action="store_true")
    args = parser.parse_args()
    print("Lexical evaluation")
    lexical = evaluate(HybridRetriever(False))
    for key, value in lexical.items():
        print(f"  {key}: {value:.2%}" if key.endswith(("1", "3", "cy")) else f"  {key}: {value}")
    if not args.dense:
        return
    print("\nMultilingual dense evaluation")
    dense = HybridRetriever(True)
    dense.warm("shop_01")
    # First model load on this machine can take ~90s (torch/sentence-transformers);
    # subsequent process starts use the OS cache and are much faster.
    for _ in range(600):
        if dense.dense_status in ("ready", "unavailable", "disabled"):
            break
        time.sleep(0.5)
    print("  dense_status:", dense.dense_status)
    if dense.dense_status != "ready":
        print("  Dense index unavailable; lexical results remain the production fallback.")
        return
    hybrid = evaluate(dense)
    for key, value in hybrid.items():
        print(f"  {key}: {value:.2%}" if key.endswith(("1", "3", "cy")) else f"  {key}: {value}")
    print("  dense_misses:", hybrid["product_misses"])


if __name__ == "__main__":
    main()
