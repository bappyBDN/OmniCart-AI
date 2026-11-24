import json
import os
from pathlib import Path

# Tests operate on an isolated in-process SQLite fixture. They must never route
# Graph RAG through the MCP child process (which would use the production DB).
os.environ["MCP_ENABLED"] = "false"

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend import database
from backend import llm
from backend.main import app, rate_buckets
from backend.retrieval import retriever
from models import Admin, Base, Customer, Policy, Product, Shop


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A separate disposable database. Tests NEVER touch the existing shop database."""
    engine = database.make_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(retriever, "dense_enabled", False)
    monkeypatch.setattr(retriever, "dense_status", "disabled")
    monkeypatch.setattr(llm, "warm_models", lambda: None)
    Base.metadata.create_all(engine)
    with database.transaction() as db:
        db.add_all([Shop(shop_id="shop_01", shop_name="Test shop"), Shop(shop_id="other", shop_name="Other shop")])
        db.flush()
        root = Path(__file__).resolve().parents[1]
        for p in json.loads((root / "Data" / "products.json").read_text(encoding="utf-8")):
            db.add(Product(**p, shop_id="shop_01"))
        for p in json.loads((root / "Data" / "policies.json").read_text(encoding="utf-8")):
            db.add(Policy(policy_type=p["topic"], policy_text=p["content"], shop_id="shop_01"))
        db.add(Product(product_id="OTHER_SECRET", name="Private Cotton Shirt", category="Apparel", price=1,
                       stock_count=50, shop_id="other", description="Only available to another shop"))
        db.add(Policy(policy_type="Private policy", policy_text="Private cotton refunds last 999 days", shop_id="other"))
        db.add(Admin(email="manager@example.com", name="Manager", password_hash=bcrypt.hashpw(b"safe-test-pass", bcrypt.gensalt(rounds=4)).decode(), shop_id="shop_01"))
        customer = Customer(unique_id="test_customer", name="Amina Rahman", email="amina@example.com", phone_number="01712345678", address="12 Garden Road, Dhaka", shop_id="shop_01")
        db.add(customer)
        db.flush()
        customer_id = customer.customer_id
    rate_buckets.clear()
    yield {"customer_id": customer_id, "engine": engine}
    engine.dispose()


@pytest.fixture
def client(store):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def headers(client):
    response = client.post("/api/sessions")
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture
def complete_headers(client, headers):
    response = client.patch("/api/profile", headers=headers, json={"name": "Amina Rahman", "email": "amina@example.com", "phone": "01712345678", "address": "12 Garden Road, Dhaka"})
    assert response.status_code == 200
    return headers


@pytest.fixture
def admin_headers(client):
    response = client.post("/api/admin/login", json={"email": "manager@example.com", "password": "safe-test-pass"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}
