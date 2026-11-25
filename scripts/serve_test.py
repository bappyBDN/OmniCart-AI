"""Playwright-only isolated application. Never use this server for real traffic."""
import json
import os
import tempfile
from pathlib import Path


def main():
    temp_root = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Temp" / "opencode"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="omnicart-browser-", dir=temp_root) as directory:
        os.environ["DATABASE_URL"] = f"sqlite:///{(Path(directory) / 'browser.db').as_posix()}"
        os.environ["DATABASE_REQUIRE_POSTGRES"] = "false"
        os.environ["VERIFY_TOKEN"] = "browser-test-verify"
        os.environ["APP_SECRET"] = "browser-test-app-secret"
        os.environ["PAGE_ACCESS_TOKEN"] = ""
        os.environ["FACEBOOK_PAGE_ID"] = ""
        os.environ["RAG_DENSE_ENABLED"] = "false"
        os.environ["EMAIL_ENABLED"] = "false"
        os.environ["MCP_ENABLED"] = "false"
        import bcrypt
        import uvicorn
        from backend import agent, llm
        from backend.config import ROOT
        from backend.database import initialize, transaction
        from backend.main import app
        from backend.retrieval import retriever
        from models import Admin, Policy, Product, Shop
        initialize()
        with transaction() as db:
            db.add(Shop(shop_id="shop_01", shop_name="Browser test shop"))
            db.flush()
            for p in json.loads((ROOT / "Data" / "products.json").read_text(encoding="utf-8")):
                db.add(Product(**p, shop_id="shop_01"))
            for p in json.loads((ROOT / "Data" / "policies.json").read_text(encoding="utf-8")):
                db.add(Policy(policy_type=p["topic"], policy_text=p["content"], shop_id="shop_01"))
            db.add(Admin(email="browser-test@example.com", name="Browser test admin", shop_id="shop_01",
                         password_hash=bcrypt.hashpw(b"isolated-test-only", bcrypt.gensalt(rounds=4)).decode()))

        def fake_agent(customer_id, shop_id, query, history, previous_sources=None, image=None, request_id=None, conversation_context=None):
            sources = retriever.search("cotton tshirt", shop_id, "product", 1)
            return {"answer": "Premium Cotton T-Shirt is $19.99, with 15 in stock. [product:SKU_101]",
                    "sources": sources, "trace": [{"node": "retrieve", "detail": "Retrieved live catalog"}],
                    "drafts": [], "degraded": False, "usage": {}, "latency_ms": 12, "route": "shopping"}
        agent.run_agent = fake_agent
        llm.warm_models = lambda: None
        uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning")


if __name__ == "__main__":
    main()
