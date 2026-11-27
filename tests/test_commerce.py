from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from backend.commerce import confirm_order, prepare_order
from backend.database import transaction
from backend.records import OrderDraft
from models import Order, Product


def test_checkout_is_proposal_until_confirmed(store):
    draft = prepare_order(store["customer_id"], "shop_01", "SKU_101", 2)
    with transaction() as db:
        assert db.query(Order).count() == 0
        assert db.get(Product, ("shop_01", "SKU_101")).stock_count == 15
    receipt = confirm_order(store["customer_id"], "shop_01", draft["draft_id"])
    assert receipt["unit_price"] == 19.99
    assert receipt["total_price"] == 39.98
    assert receipt["email_status"] == "disabled"
    assert confirm_order(store["customer_id"], "shop_01", draft["draft_id"])["order_id"] == receipt["order_id"]
    with transaction() as db:
        assert db.query(Order).count() == 1
        assert db.get(Product, ("shop_01", "SKU_101")).stock_count == 13


@pytest.mark.parametrize("quantity", [0, -1, 1.5, True, 1001])
def test_reject_invalid_quantity(store, quantity):
    with pytest.raises(HTTPException):
        prepare_order(store["customer_id"], "shop_01", "SKU_101", quantity)


def test_stock_and_tenant_checks(store):
    assert prepare_order(store["customer_id"], "shop_01", "SKU_102", 1)["error"] == "insufficient_stock"
    with pytest.raises(HTTPException):
        prepare_order(store["customer_id"], "shop_01", "OTHER_SECRET", 1)


def test_expiry_and_price_change(store):
    draft = prepare_order(store["customer_id"], "shop_01", "SKU_101", 1)
    with transaction() as db:
        db.get(Product, ("shop_01", "SKU_101")).discounted_price = 10
    with pytest.raises(HTTPException, match="Price changed"):
        confirm_order(store["customer_id"], "shop_01", draft["draft_id"])
    with transaction() as db:
        db.get(OrderDraft, draft["draft_id"]).expires_at = datetime.utcnow() - timedelta(seconds=1)
    with pytest.raises(HTTPException, match="expired"):
        confirm_order(store["customer_id"], "shop_01", draft["draft_id"])


def test_concurrent_double_confirm_is_idempotent(store):
    draft = prepare_order(store["customer_id"], "shop_01", "SKU_101", 2)
    def confirm(_):
        return confirm_order(store["customer_id"], "shop_01", draft["draft_id"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(confirm, range(4)))
    assert len({r["order_id"] for r in receipts}) == 1
    with transaction() as db:
        assert db.get(Product, ("shop_01", "SKU_101")).stock_count == 13


def test_concurrent_orders_cannot_oversell(store):
    draft1 = prepare_order(store["customer_id"], "shop_01", "SKU_103", 2)
    draft2 = prepare_order(store["customer_id"], "shop_01", "SKU_103", 1)
    def confirm(draft):
        try:
            return confirm_order(store["customer_id"], "shop_01", draft["draft_id"])
        except HTTPException:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm, [draft1, draft2]))
    assert len([r for r in results if r]) == 1
    with transaction() as db:
        assert db.get(Product, ("shop_01", "SKU_103")).stock_count >= 0
