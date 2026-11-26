"""Order status tracking by Order ID (ORD-1001) or phone number.

Privacy rule: a phone number alone never reveals ANOTHER customer's orders.
  * Order ID owned by the requester            -> full status
  * Order ID + matching delivery phone          -> status only (cross-channel, e.g. web order asked on Messenger)
  * Phone matching the requester's own orders   -> their recent orders
  * anything else                               -> ask for the missing identifier
"""
import re

from backend.commerce import order_code, parse_order_code
from backend.database import transaction
from backend.records import OrderDetail
from models import Customer, Order

STATUS_TEXT = {
    "Pending": "received and waiting to be processed",
    "Processing": "being packed",
    "Shipped": "on its way",
    "Delivered": "delivered",
    "Cancelled": "cancelled",
}
STATUS_TEXT_BN = {
    "Pending": "গ্রহণ করা হয়েছে, প্রসেসিংয়ের অপেক্ষায়", "Processing": "প্যাক করা হচ্ছে",
    "Shipped": "পথে আছে", "Delivered": "ডেলিভারি হয়েছে", "Cancelled": "বাতিল হয়েছে",
}
TRACK_INTENT = re.compile(
    r"\b(?:track|tracking|status|where(?:'s| is) my|update on my|did my|has my|when will my)\b.{0,30}\b(?:order|parcel|package|delivery)\b|"
    r"\border (?:status|update)\b|\bORD[-_]\d+\b|অর্ডার.*(?:কোথায়|স্ট্যাটাস|অবস্থা)|order (?:kothay|koi)", re.I)
PHONE = re.compile(r"(?<!\w)\+?\d[\d \-()]{6,18}\d(?!\w)")


def digits(value):
    return re.sub(r"\D", "", value or "")[-10:]


def is_tracking_request(text):
    return bool(TRACK_INTENT.search(text or ""))


def _line(order, detail, bangla):
    code = detail.order_code if detail else order_code(order.order_id)
    when = order.created_at.strftime("%d %b") if order.created_at else ""
    variant = ""
    if detail and detail.variant:
        variant = " (" + ", ".join(str(v) for v in detail.variant.values() if v) + ")"
    if bangla:
        return f"{code}: {order.quantity} × {order.product_name}{variant} — {STATUS_TEXT_BN.get(order.status, order.status)} ({when})"
    return f"{code}: {order.quantity} × {order.product_name}{variant} — {STATUS_TEXT.get(order.status, order.status.lower())} ({when})"


def track(customer_id, shop_id, text, locale="en"):
    """Return a reply string, or None when the message is not a tracking request."""
    if not is_tracking_request(text) and not (PHONE.search(text or "") and re.search(r"\border\b|অর্ডার", text or "", re.I)):
        return None
    bangla = (locale or "en").startswith("bn")
    order_id = parse_order_code(text)
    phones = {digits(match) for match in PHONE.findall(text or "") if 7 <= len(re.sub(r"\D", "", match)) <= 15}
    with transaction() as db:
        if order_id:
            order = db.query(Order).filter_by(order_id=order_id, shop_id=shop_id).first()
            detail = db.get(OrderDetail, order_id) if order else None
            customer = db.get(Customer, order.customer_id) if order else None
            owned = bool(order and order.customer_id == customer_id)
            phone_match = bool(order and phones and phones & {digits(detail.phone if detail else ""),
                                                              digits(customer.phone_number if customer else "")} - {""})
            if owned or phone_match:
                head = "আপনার অর্ডারের আপডেট:" if bangla else "Here's the latest on your order:"
                return head + "\n" + _line(order, detail, bangla)
            if order:
                return ("নিরাপত্তার জন্য, এই অর্ডারে ব্যবহৃত ফোন নম্বরটিও পাঠাবেন?" if bangla else
                        "For your security, could you also send the phone number used for that order?")
            return ("এই অর্ডার নম্বরে কোনো অর্ডার পাচ্ছি না। নম্বরটা আরেকবার দেখে নেবেন? (যেমন ORD-1001)" if bangla else
                    "I couldn't find an order with that ID. Could you double-check it? It looks like ORD-1001.")
        mine = db.query(Order).filter_by(customer_id=customer_id, shop_id=shop_id).order_by(Order.order_id.desc())
        if phones:
            customer = db.get(Customer, customer_id)
            own_phone = digits(customer.phone_number if customer else "")
            rows = []
            for order in mine.limit(20):
                detail = db.get(OrderDetail, order.order_id)
                if phones & {digits(detail.phone if detail else ""), own_phone} - {""}:
                    rows.append((order, detail))
            if not rows:
                return ("এই নম্বরে আপনার কোনো অর্ডার পাচ্ছি না। অর্ডার নম্বরটা (যেমন ORD-1001) পাঠালে খুঁজে দেখব।" if bangla else
                        "I don't see any orders from you under that number. If you send the order ID (like ORD-1001), I'll look it up.")
        else:
            rows = [(order, db.get(OrderDetail, order.order_id)) for order in mine.limit(3)]
            if not rows:
                return ("এই চ্যাট থেকে কোনো অর্ডার পাচ্ছি না। অর্ডার নম্বর বা অর্ডারে দেওয়া ফোন নম্বরটা পাঠাবেন?" if bangla else
                        "I can't see any orders from this chat yet. Could you send your order ID or the phone number you ordered with?")
        head = "আপনার সাম্প্রতিক অর্ডার:" if bangla else ("Here's your latest order:" if len(rows) == 1 else "Here are your latest orders:")
        return head + "\n" + "\n".join(_line(order, detail, bangla) for order, detail in rows[:5])
