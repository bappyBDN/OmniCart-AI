from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import update

from backend.database import transaction
from backend.notifications import enqueue
from backend.records import Notification, OrderDraft
from backend.retrieval import product_dict
from backend.schemas import ProfileInput
from models import Customer, Escalation, Order, Product


def money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def profile_dict(customer):
    values = {"name": customer.name, "phone": customer.phone_number, "email": customer.email, "address": customer.address}
    return {**values, "customer_id": customer.customer_id, "human_intervention": bool(customer.human_intervention),
            "missing": [key for key, value in values.items() if not value]}


def get_customer(db, customer_id, shop_id):
    customer = db.query(Customer).filter_by(customer_id=customer_id, shop_id=shop_id).first()
    if not customer:
        raise HTTPException(404, "Customer not found")
    return customer


def save_profile(customer_id, shop_id, values):
    validated = ProfileInput(**values).model_dump(exclude_none=True)
    with transaction() as db:
        customer = get_customer(db, customer_id, shop_id)
        for key, value in validated.items():
            setattr(customer, "phone_number" if key == "phone" else key, value)
        db.flush()
        return profile_dict(customer)


ORDER_CODE_OFFSET = 1000


def order_code(order_id):
    """Customer-facing order ID: ORD-1001, ORD-1002, ... (legacy ORD_1 still accepted)."""
    return f"ORD-{ORDER_CODE_OFFSET + int(order_id)}"


def parse_order_code(text):
    """Return the internal order_id for 'ORD-1001' or legacy 'ORD_1', else None."""
    import re
    match = re.search(r"\bORD-(\d{4,9})\b", text or "", re.I)
    if match and int(match.group(1)) > ORDER_CODE_OFFSET:
        return int(match.group(1)) - ORDER_CODE_OFFSET
    match = re.search(r"\bORD_(\d{1,9})\b", text or "", re.I)
    return int(match.group(1)) if match else None


def order_dict(order, email_status=None):
    return {"order_id": order.order_id, "reference": f"ORD_{order.order_id}", "order_code": order_code(order.order_id),
            "product_id": order.product_id,
            "product_name": order.product_name, "quantity": order.quantity, "unit_price": order.price,
            "total_price": order.total_price, "status": order.status,
            "created_at": order.created_at.isoformat() if order.created_at else None, "email_status": email_status}


def quote_product(shop_id, product_id, quantity):
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 1000:
        raise HTTPException(422, "Quantity must be an integer between 1 and 1000")
    with transaction() as db:
        product = db.query(Product).filter_by(product_id=product_id, shop_id=shop_id).first()
        if not product:
            raise HTTPException(404, "Product not found")
        unit_price = money(product_dict(product)["effective_price"])
        return {"product_id": product_id, "product_name": product.name, "quantity": quantity,
                "unit_price": float(unit_price), "total_price": float(unit_price * quantity),
                "available": product.stock_count >= quantity, "stock_count": product.stock_count,
                "note": "Merchandise subtotal only; this is not a reservation or order."}


def draft_dict(db, draft):
    product = db.get(Product, draft.product_id)
    customer = db.get(Customer, draft.customer_id)
    return {"draft_id": draft.draft_id, "product_id": draft.product_id, "product_name": product.name if product else "Unavailable",
            "quantity": draft.quantity, "unit_price": float(draft.unit_price),
            "total_price": float(money(draft.unit_price) * draft.quantity), "expires_at": draft.expires_at.isoformat(),
            "confirmation_required": draft.order_id is None, "order_id": draft.order_id,
            "phone": customer.phone_number if customer else None,
            "address": customer.address if customer else None,
            "missing_contact": [key for key, value in (("phone", customer.phone_number if customer else None),
                                                       ("address", customer.address if customer else None))
                                if not value],
            "note": "Merchandise subtotal only. Shipping, payment fees and eligibility-based discounts are not collected by this app."}


def prepare_order(customer_id, shop_id, product_id, quantity, *, automated=False):
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 1000:
        raise HTTPException(422, "Quantity must be an integer between 1 and 1000")
    with transaction() as db:
        customer = get_customer(db, customer_id, shop_id)
        if automated and customer.human_intervention:
            return {"error": "human_intervention", "human_intervention": True,
                    "message": "The assistant is paused for human review; no draft was created."}
        # Contact details can be supplied on the review screen. Only the
        # customer's name must exist before a proposal is created.
        missing = [key for key in profile_dict(customer)["missing"] if key == "name"]
        if missing:
            return {"error": "profile_incomplete", "missing": missing, "message": "Complete your profile before checkout."}
        product = db.query(Product).filter_by(product_id=product_id, shop_id=shop_id).first()
        if not product:
            raise HTTPException(404, "Product not found")
        if product.stock_count < quantity:
            return {"error": "insufficient_stock", "available": product.stock_count}
        price = str(money(product_dict(product)["effective_price"]))
        # Repeated agent calls reuse an existing unexpired proposal.
        draft = db.query(OrderDraft).filter_by(customer_id=customer_id, shop_id=shop_id, product_id=product_id,
            quantity=quantity, unit_price=price, order_id=None).filter(OrderDraft.expires_at > datetime.utcnow()).first()
        if not draft:
            draft = OrderDraft(draft_id=str(uuid4()), customer_id=customer_id, shop_id=shop_id,
                product_id=product_id, quantity=quantity, unit_price=price, expires_at=datetime.utcnow() + timedelta(minutes=15))
            db.add(draft)
            db.flush()
        return draft_dict(db, draft)


def confirm_order(customer_id, shop_id, draft_id, phone=None, address=None):
    contact = {}
    if phone is not None or address is not None:
        try:
            contact = ProfileInput(phone=phone, address=address).model_dump(exclude_none=True)
        except ValidationError:
            raise HTTPException(422, "Enter a valid phone number and full delivery address") from None
    with transaction() as db:
        # SQLite serializes checkout writers before checking the idempotency marker.
        if db.bind.dialect.name == "sqlite":
            from sqlalchemy import text
            db.execute(text("BEGIN IMMEDIATE"))
        draft = db.query(OrderDraft).filter_by(draft_id=draft_id, customer_id=customer_id, shop_id=shop_id).with_for_update().first()
        if not draft:
            raise HTTPException(404, "Checkout not found")
        if draft.order_id:
            notice = db.query(Notification).filter_by(dedupe_key=f"order:{draft.order_id}").first()
            return order_dict(db.get(Order, draft.order_id), notice.status if notice else "disabled")
        if draft.expires_at < datetime.utcnow():
            raise HTTPException(409, "Checkout expired. Please request a new quote.")
        customer = get_customer(db, customer_id, shop_id)
        for key, value in contact.items():
            setattr(customer, "phone_number" if key == "phone" else key, value)
        db.flush()
        if any(key != "email" for key in profile_dict(customer)["missing"]):
            raise HTTPException(409, "Enter a phone number and full delivery address before confirming")
        # PostgreSQL locks the product price and stock together until checkout
        # commits. SQLite already holds its BEGIN IMMEDIATE write reservation.
        product = db.query(Product).filter_by(product_id=draft.product_id, shop_id=shop_id).with_for_update().first()
        if not product or money(product_dict(product)["effective_price"]) != money(draft.unit_price):
            raise HTTPException(409, "Price changed. Please request a new quote.")
        result = db.execute(update(Product).where(Product.product_id == draft.product_id, Product.shop_id == shop_id,
            Product.stock_count >= draft.quantity).values(stock_count=Product.stock_count - draft.quantity))
        if result.rowcount != 1:
            raise HTTPException(409, "Insufficient stock. Please request a new quote.")
        order = Order(customer_id=customer_id, product_id=product.product_id, product_name=product.name,
            quantity=draft.quantity, price=float(draft.unit_price), total_price=float(money(draft.unit_price) * draft.quantity),
            status="Pending", shop_id=shop_id)
        db.add(order)
        db.flush()
        draft.order_id = order.order_id
        email_status = enqueue(db, f"order:{order.order_id}", customer.email, f"Order ORD_{order.order_id} received",
            f"Hello {customer.name},\nOrder ORD_{order.order_id}: {order.quantity} × {order.product_name}.\n"
            f"Merchandise subtotal: ${order.total_price:.2f}. Status: Pending.\nDelivery address: {customer.address}")
        return order_dict(order, email_status)


def escalate(customer_id, shop_id, summary):
    from backend.support import open_case
    return open_case(customer_id, shop_id, summary)
