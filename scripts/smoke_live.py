r"""Optional live-provider evaluation; uses only synthetic data in a temporary DB.

Run: .\venv\Scripts\python.exe -m scripts.smoke_live
This makes real provider calls and may consume your API quota. Never sends email.
"""
import json
import os
import tempfile
from pathlib import Path


def main():
    temp_root = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Temp" / "opencode"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="omnicart-live-", dir=temp_root) as directory:
        os.environ["DATABASE_URL"] = f"sqlite:///{(Path(directory) / 'smoke.db').as_posix()}"
        os.environ["RAG_DENSE_ENABLED"] = "false"
        os.environ["EMAIL_ENABLED"] = "false"
        os.environ["MCP_ENABLED"] = "false"
        from backend.agent import run_agent
        from backend.config import ROOT
        from backend.database import engine, initialize, transaction
        from models import ChatHistory, Customer, Policy, Product, Shop
        initialize()
        with transaction() as db:
            db.add(Shop(shop_id="shop_01", shop_name="Synthetic evaluation shop"))
            db.flush()
            for p in json.loads((ROOT / "Data" / "products.json").read_text(encoding="utf-8")):
                db.add(Product(**p, shop_id="shop_01"))
            for p in json.loads((ROOT / "Data" / "policies.json").read_text(encoding="utf-8")):
                db.add(Policy(policy_type=p["topic"], policy_text=p["content"], shop_id="shop_01"))
            customer = Customer(unique_id="evaluation-only", shop_id="shop_01")
            db.add(customer)
            db.flush()
            customer_id = customer.customer_id
        cases = [
            ("What is the Premium Cotton T-Shirt price and stock?", ["19.99", "15", "product:SKU_101"]),
            ("What is the difference between your refund policy and return window?", ["7", "14", "policy:1:0", "policy:5:0"]),
            ("শহরের ভিতরে এবং বাইরে ডেলিভারি চার্জ কত?", ["2", "5", "policy:2:0"]),
            ("Do you sell quantum fusion reactors?", []),
            ("how many products do you have?", ["100"]),
        ]
        passed = 0
        for query, expected in cases:
            result = run_agent(customer_id, "shop_01", query, [])
            valid = not result["degraded"] and all(value in result["answer"].translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")) for value in expected)
            print(json.dumps({"query": query, "answer": result["answer"], "passed": valid, "degraded": result["degraded"],
                              "latency_ms": result["latency_ms"], "tools": [t["detail"] for t in result["trace"] if t["node"] == "tool"]}, ensure_ascii=False), flush=True)
            passed += int(valid)
        print(f"Live smoke checks passed: {passed}/{len(cases)}", flush=True)
        engine.dispose()


if __name__ == "__main__":
    main()
