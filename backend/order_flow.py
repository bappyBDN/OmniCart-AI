"""Conversational order form for Messenger (state stored per customer).

    start (text "order this" / "Order Now" card button)
      -> size -> color -> quantity            (skipped when known or single-option)
      -> full name -> phone -> delivery address (saved profile values are reused)
      -> summary + [Confirm Order] [Change details] [Cancel]
      -> confirm_order(): live price recheck + atomic stock decrement + Order row
      -> OrderDetail (ORD-1001, variant, phone, address) + admin alert + receipt

The explicit "Confirm Order" reply is the customer's confirmation, the same
safety rule as the web checkout's Confirm button. Off-topic questions during the
flow are answered by the agent, then the customer is reminded where they were.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import HTTPException

from backend.commerce import confirm_order, get_customer, order_code, prepare_order, save_profile
from backend.catalog_state import active_product, orderable
from backend.conversation import purchase_request, stated_name
from backend.conversation_state import get_state, set_order_state
from backend.database import transaction
from backend.records import OrderDetail
from backend.retrieval import SIZE_WORDS, product_dict
from models import Product

FLOW_TTL = timedelta(minutes=30)
MAX_QUICK_REPLIES = 13
STEPS = ("size", "color", "quantity", "name", "phone", "address", "confirm")

CANCEL = re.compile(r"^\s*(?:cancel(?: order| it)?|stop|never ?mind|nevermind|forget it|no thanks|"
                    r"বাতিল|থাক|লাগবে না|cancel koro|bad dao)\s*[.!।]?\s*$", re.I)
YES = re.compile(r"^\s*(?:yes|yep|yeah|yup|ok(?:ay)?|sure|confirm(?: order| it)?|place (?:the |my )?order|go ahead|done|"
                 r"হ্যাঁ|হ্যা|জি|ঠিক আছে|কনফার্ম|confirm koro|ji)\s*[.!।]?\s*$", re.I)
EDIT = re.compile(r"\b(?:change|edit|update|wrong|correct)\b|পরিবর্তন|ভুল", re.I)
QUESTION = re.compile(r"\?|^\s*(?:what|how|when|where|why|which|who|do|does|is|are|can|could|will|would)\b|"
                      r"(?:কি|কী|কেন|কিভাবে|কখন|কোথায়)\s*[?？]?\s*$", re.I)
BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "ekta": 1, "duita": 2, "tinta": 3, "একটি": 1, "একটা": 1, "দুইটি": 2, "দুটো": 2, "তিনটি": 3}


@dataclass
class FlowReply:
    text: str
    quick_replies: list = field(default_factory=list)
    product_id: str | None = None
    done: bool = False           # order placed or flow ended
    passthrough: bool = False    # let the agent answer this message, then append `text` as a reminder
    order: dict | None = None    # receipt for a placed order


def _qr(title, payload):
    return {"content_type": "text", "title": title[:20], "payload": payload[:1000]}


def _bn(locale):
    return (locale or "en").startswith("bn")


def _load_product(db, shop_id, product_id):
    row = db.query(Product).filter_by(product_id=product_id, shop_id=shop_id).filter(active_product()).first()
    return product_dict(row) if row else None


def _match_option(text, options, payload=None, prefix=""):
    if payload and payload.startswith(prefix):
        value = payload[len(prefix):]
        return next((o for o in options if o.casefold() == value.casefold()), None)
    cleaned = (text or "").strip().casefold()
    if not cleaned:
        return None
    for option in options:
        if cleaned == option.casefold() or re.search(rf"(?<!\w){re.escape(option.casefold())}(?!\w)", cleaned):
            return option
    for word, size in SIZE_WORDS.items():  # "medium" -> M
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", cleaned):
            return next((o for o in options if o.casefold() == size.casefold()), None)
    return None


def _quantity(text, payload=None):
    if payload and payload.startswith("QTY:") and payload[4:].isdigit():
        return int(payload[4:])
    value = (text or "").translate(BN_DIGITS).casefold()
    match = re.search(r"(?<![\d.])(\d{1,4})(?![\d.])", value)
    if match:
        return int(match.group(1))
    return next((n for word, n in NUMBER_WORDS.items() if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", value)), None)


def _name(text):
    candidate = stated_name(text) or (text or "").strip()
    candidate = re.sub(r"^(?:it'?s|i am|i'm|this is|name)\s*[:\-]?\s+", "", candidate, flags=re.I).strip(" .!।")
    letters = [c for c in candidate if c.isalpha()]
    if not 2 <= len(candidate) <= 80 or len(letters) < 2 or re.search(r"\d|@|http", candidate) or len(candidate.split()) > 6:
        return None
    return candidate


def _phone(text, payload=None):
    raw = (payload if payload and re.fullmatch(r"\+?[\d\s\-()]{7,20}", payload) else text or "").translate(BN_DIGITS)
    match = re.search(r"\+?\d[\d\s\-()]{5,18}\d", raw)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(0))
    if not 7 <= len(digits) <= 15:
        return None
    # Bangladesh mobile numbers: 01XXXXXXXXX (11 digits) or +8801XXXXXXXXX.
    if digits.startswith("01") and len(digits) != 11:
        return None
    return match.group(0).strip()


def _address(text):
    value = " ".join((text or "").split())
    return value if len(value) >= 12 and sum(c.isalpha() for c in value) >= 6 and len(value) <= 600 else None


def _money(value):
    return f"৳{value:,.2f}"


def _variant_label(state):
    return ", ".join(v for v in (state.get("size"), state.get("color")) if v)


# ------------------------------------------------------------------ prompts

def _ask(state, product, profile, locale):
    """Advance to the next missing field and return its question."""
    bn = _bn(locale)
    name = product["name"]
    sizes, colors = product.get("sizes") or [], product.get("colors") or []
    if len(sizes) == 1 and not state.get("size"):
        state["size"] = sizes[0]
    if len(colors) == 1 and not state.get("color"):
        state["color"] = colors[0]
    if sizes and not state.get("size"):
        state["step"] = "size"
        return FlowReply(f"{name} — কোন সাইজ নেবেন? ({', '.join(sizes)})" if bn else
                         f"Great choice! Which size would you like for the {name}? We have {', '.join(sizes)}.",
                         [_qr(s, f"SIZE:{s}") for s in sizes[:MAX_QUICK_REPLIES]], product["product_id"])
    if colors and not state.get("color"):
        state["step"] = "color"
        return FlowReply(f"কোন রঙটা পছন্দ? ({', '.join(colors)})" if bn else
                         f"And which color? It comes in {', '.join(colors)}.",
                         [_qr(c, f"COLOR:{c}") for c in colors[:MAX_QUICK_REPLIES]], product["product_id"])
    if not state.get("quantity"):
        state["step"] = "quantity"
        options = [n for n in (1, 2, 3) if n <= product["stock_count"]]
        return FlowReply("কয়টি লাগবে?" if bn else "How many would you like?",
                         [_qr(str(n), f"QTY:{n}") for n in options], product["product_id"])
    use_saved = not state.get("editing")
    for key, value in (("name", profile.get("name")), ("phone", profile.get("phone")), ("address", profile.get("address"))):
        if not state.get(key) and use_saved and value:
            state[key] = value
            state.setdefault("prefilled", []).append(key)
    if not state.get("name"):
        state["step"] = "name"
        return FlowReply("অর্ডারটা কার নামে হবে? আপনার পুরো নাম লিখুন।" if bn else
                         "Perfect. What's the full name for the order?", [], product["product_id"])
    if not state.get("phone"):
        state["step"] = "phone"
        return FlowReply("ডেলিভারির জন্য আপনার ফোন নম্বরটা দিন।" if bn else
                         "Thanks! What's the best phone number for delivery updates?",
                         [{"content_type": "user_phone_number"}], product["product_id"])
    if not state.get("address"):
        state["step"] = "address"
        return FlowReply("পুরো ডেলিভারি ঠিকানাটা লিখুন (বাসা, রোড, এলাকা, শহর)।" if bn else
                         "And the full delivery address? (house, road, area, city)", [], product["product_id"])
    state["step"] = "confirm"
    return _summary(state, product, locale)


def _summary(state, product, locale):
    bn = _bn(locale)
    qty, unit = state["quantity"], product["effective_price"]
    variant = _variant_label(state)
    if bn:
        lines = ["আপনার অর্ডারের সারসংক্ষেপ:", f"🛍 {product['name']}" + (f" ({variant})" if variant else ""),
                 f"পরিমাণ: {qty} × {_money(unit)} = {_money(unit * qty)}", f"নাম: {state['name']}",
                 f"ফোন: {state['phone']}", f"ঠিকানা: {state['address']}",
                 "(পণ্যের মোট দাম; ডেলিভারি চার্জ থাকলে দোকান জানিয়ে দেবে।)", "কনফার্ম করব?"]
    else:
        lines = ["Here's your order summary:", f"🛍 {product['name']}" + (f" ({variant})" if variant else ""),
                 f"Qty: {qty} × {_money(unit)} = {_money(unit * qty)}", f"Name: {state['name']}",
                 f"Phone: {state['phone']}", f"Deliver to: {state['address']}",
                 "(Merchandise total; the shop will confirm any delivery charge.)", "Shall I confirm it?"]
    replies = [_qr("✅ Confirm Order" if not bn else "✅ কনফার্ম", "ORDER_CONFIRM"),
               _qr("✏️ Change details" if not bn else "✏️ পরিবর্তন", "ORDER_EDIT"),
               _qr("❌ Cancel" if not bn else "❌ বাতিল", "ORDER_CANCEL")]
    return FlowReply("\n".join(lines), replies, product["product_id"])


def _reminder(state, locale):
    bn = _bn(locale)
    step = state.get("step")
    labels = {"size": ("size", "সাইজ"), "color": ("color", "রঙ"), "quantity": ("quantity", "পরিমাণ"),
              "name": ("full name", "পুরো নাম"), "phone": ("phone number", "ফোন নম্বর"),
              "address": ("delivery address", "ডেলিভারি ঠিকানা")}
    if step == "confirm":
        return "আপনার অর্ডারটা কনফার্মের অপেক্ষায় আছে — তৈরি হলে “কনফার্ম” লিখুন।" if bn else \
            "Your order is still waiting — just tap Confirm Order whenever you're ready."
    en, bangla = labels.get(step, ("details", "তথ্য"))
    return (f"আপনার অর্ডারটা চালু আছে — সুবিধামতো {bangla} পাঠান।" if bn else
            f"(We're still on your order — send your {en} whenever you're ready, or say cancel.)")


# ------------------------------------------------------------------ entry point

def _profile(db, customer_id, shop_id):
    customer = get_customer(db, customer_id, shop_id)
    return {"name": customer.name, "phone": customer.phone_number, "address": customer.address,
            "paused": bool(customer.human_intervention)}


def start(customer_id, shop_id, product_id, text="", locale="en", quantity=None):
    """Begin (or restart) the flow for a product, using anything already in `text`."""
    with transaction() as db:
        product = _load_product(db, shop_id, product_id)
        profile = _profile(db, customer_id, shop_id)
        if not product or profile["paused"]:
            return None
        if not orderable(product):
            return FlowReply("দাম, মুদ্রা ও স্টক বিক্রেতার নিশ্চিত করা প্রয়োজন। এখন অর্ডার করা যাবে না।" if _bn(locale) else
                             "Price, currency and stock need merchant confirmation. Checkout is not available yet.")
        if product["stock_count"] <= 0:
            set_order_state(db, customer_id, shop_id, {})
            return FlowReply(f"দুঃখিত, {product['name']} এখন স্টকে নেই।" if _bn(locale) else
                             f"Sorry, the {product['name']} is sold out right now. Want me to suggest something similar?",
                             [], product_id, done=True)
        now = datetime.utcnow().isoformat()
        state = {"product_id": product_id, "started_at": now, "updated_at": now, "locale": locale,
                 "size": _match_option(text, product.get("sizes") or []),
                 "color": _match_option(text, product.get("colors") or [])}
        named = quantity or _quantity_in_order_text(text)
        if named and 1 <= named <= product["stock_count"]:
            state["quantity"] = named
        reply = _ask(state, product, profile, locale)
        set_order_state(db, customer_id, shop_id, state)
        return reply


def _quantity_in_order_text(text):
    from backend.conversation import named_quantity
    return named_quantity(text or "")


def active_state(customer_id, shop_id):
    with transaction() as db:
        state = dict(get_state(db, customer_id, shop_id).order_state or {})
        if state and datetime.utcnow() - datetime.fromisoformat(state.get("updated_at", "2000-01-01")) > FLOW_TTL:
            set_order_state(db, customer_id, shop_id, {})
            return {}
        return state


def handle(customer_id, shop_id, text, payload=None, focus_product_id=None, locale="en"):
    """Return a FlowReply, or None when the order flow is not involved."""
    payload = (payload or "").strip()
    if payload.startswith("ORDER_NOW:"):
        return start(customer_id, shop_id, payload.split(":", 1)[1], "", locale)
    state = active_state(customer_id, shop_id)
    if not state:
        if focus_product_id and purchase_request(text or ""):
            return start(customer_id, shop_id, focus_product_id, text, locale)
        return None
    locale = state.get("locale", locale)
    bn = _bn(locale)
    if payload == "ORDER_CANCEL" or CANCEL.match(text or ""):
        with transaction() as db:
            set_order_state(db, customer_id, shop_id, {})
        return FlowReply("ঠিক আছে, অর্ডারটা বাতিল করলাম। আর কিছু দেখাতে পারি?" if bn else
                         "No problem — I've cancelled that order. Anything else I can help you find?", [], done=True)
    with transaction() as db:
        product = _load_product(db, shop_id, state["product_id"])
        profile = _profile(db, customer_id, shop_id)
        if not product or profile["paused"]:
            set_order_state(db, customer_id, shop_id, {})
            return None
        step = state.get("step")
        accepted = _accept(step, state, product, text, payload)
        if accepted is None:  # invalid answer
            if QUESTION.search(text or "") and not payload:
                return FlowReply(_reminder(state, locale), passthrough=True, product_id=product["product_id"])
            reply = _invalid(step, state, product, locale)
            set_order_state(db, customer_id, shop_id, {**state, "updated_at": datetime.utcnow().isoformat()})
            return reply
        if accepted == "edit":
            for key in ("name", "phone", "address"):
                state.pop(key, None)
            state["editing"] = True
        if accepted == "confirm":
            set_order_state(db, customer_id, shop_id, state)
        else:
            state["updated_at"] = datetime.utcnow().isoformat()
            reply = _ask(state, product, profile, locale)
            set_order_state(db, customer_id, shop_id, state)
            return reply
    return _place(customer_id, shop_id, state, locale)


def _accept(step, state, product, text, payload):
    """Store a valid answer for `step`. Return True/'edit'/'confirm', or None if invalid."""
    if step == "size":
        value = _match_option(text, product.get("sizes") or [], payload, "SIZE:")
        if value:
            state["size"] = value
            state["color"] = state.get("color") or _match_option(text, product.get("colors") or [])
        return True if value else None
    if step == "color":
        value = _match_option(text, product.get("colors") or [], payload, "COLOR:")
        if value:
            state["color"] = value
        return True if value else None
    if step == "quantity":
        value = _quantity(text, payload)
        if value and 1 <= value <= product["stock_count"]:
            state["quantity"] = value
            return True
        state["bad_quantity"] = value
        return None
    if step == "name":
        value = _name(text)
        if value:
            state["name"] = value
        return True if value else None
    if step == "phone":
        value = _phone(text, payload)
        if value:
            state["phone"] = value
        return True if value else None
    if step == "address":
        value = _address(text)
        if value:
            state["address"] = value
        return True if value else None
    if step == "confirm":
        if payload == "ORDER_CONFIRM" or YES.match(text or ""):
            return "confirm"
        if payload == "ORDER_EDIT" or EDIT.search(text or ""):
            return "edit"
        return None
    return None


def _invalid(step, state, product, locale):
    bn = _bn(locale)
    pid = product["product_id"]
    if step == "size":
        sizes = product.get("sizes") or []
        return FlowReply(f"এই সাইজগুলো আছে: {', '.join(sizes)} — কোনটা নেবেন?" if bn else
                         f"We have it in {', '.join(sizes)} — which one works for you?",
                         [_qr(s, f"SIZE:{s}") for s in sizes[:MAX_QUICK_REPLIES]], pid)
    if step == "color":
        colors = product.get("colors") or []
        return FlowReply(f"এই রঙগুলো আছে: {', '.join(colors)}" if bn else
                         f"It comes in {', '.join(colors)} — which would you like?",
                         [_qr(c, f"COLOR:{c}") for c in colors[:MAX_QUICK_REPLIES]], pid)
    if step == "quantity":
        stock, wanted = product["stock_count"], state.pop("bad_quantity", None)
        if wanted and wanted > stock:
            return FlowReply(f"দুঃখিত, এখন মাত্র {stock}টি আছে। {stock}টি নেবেন?" if bn else
                             f"Sorry, we only have {stock} left right now. Would you like {stock}?",
                             [_qr(str(stock), f"QTY:{stock}"), _qr("Cancel", "ORDER_CANCEL")], pid)
        return FlowReply("কয়টি লাগবে, সংখ্যায় লিখুন (যেমন 2)।" if bn else "Just send me a number, like 2.",
                         [_qr(str(n), f"QTY:{n}") for n in (1, 2, 3) if n <= stock], pid)
    if step == "name":
        return FlowReply("পুরো নামটা লিখবেন? (যেমন: Amina Rahman)" if bn else
                         "Could you type the full name for the order? (e.g. Amina Rahman)", [], pid)
    if step == "phone":
        return FlowReply("নম্বরটা ঠিক মনে হচ্ছে না। ১১ ডিজিটের মোবাইল নম্বর দিন (যেমন 01712345678)।" if bn else
                         "That number doesn't look quite right. Could you send it again? (e.g. 01712345678)",
                         [{"content_type": "user_phone_number"}], pid)
    if step == "address":
        return FlowReply("ঠিকানাটা আরেকটু বিস্তারিত লিখবেন? (বাসা, রোড, এলাকা, শহর)" if bn else
                         "Could you add a bit more detail to the address? House, road, area and city help the courier find you.",
                         [], pid)
    return _summary(state, product, locale)


def _place(customer_id, shop_id, state, locale):
    """Save contact details, create + confirm the order atomically, record details, alert admins."""
    from backend import escalation
    bn = _bn(locale)
    pid = state["product_id"]
    try:
        save_profile(customer_id, shop_id, {"name": state["name"], "phone": state["phone"], "address": state["address"]})
        draft = prepare_order(customer_id, shop_id, pid, int(state["quantity"]), automated=True)
        if draft.get("error") == "insufficient_stock":
            available = draft.get("available", 0)
            with transaction() as db:
                set_order_state(db, customer_id, shop_id, {**state, "step": "quantity", "quantity": None,
                                                           "updated_at": datetime.utcnow().isoformat()})
            return FlowReply(f"দুঃখিত, এর মধ্যে স্টক কমে গেছে — এখন {available}টি আছে। কয়টি নেবেন?" if bn else
                             f"Sorry — stock just changed and only {available} are left. How many would you like?",
                             [_qr(str(n), f"QTY:{n}") for n in range(1, min(available, 3) + 1)], pid)
        if draft.get("error"):
            raise HTTPException(409, draft.get("message", "Order could not be prepared"))
        receipt = confirm_order(customer_id, shop_id, draft["draft_id"], phone=state["phone"], address=state["address"])
    except HTTPException as exc:
        with transaction() as db:
            set_order_state(db, customer_id, shop_id, {})
        detail = str(exc.detail)
        if "Price changed" in detail:
            message = ("দাম এইমাত্র পরিবর্তন হয়েছে, তাই অর্ডারটা কনফার্ম করিনি। নতুন দাম দেখে আবার “Order” লিখুন।" if bn else
                       "The price just changed, so I didn't place the order. Say \"order this\" again and I'll show you the new total.")
        elif "stock" in detail.lower():
            message = "দুঃখিত, পণ্যটি এইমাত্র শেষ হয়ে গেছে।" if bn else "Sorry — that item just sold out, so I couldn't place the order."
        else:
            message = ("অর্ডারটা এই মুহূর্তে সম্পন্ন করা গেল না। আবার চেষ্টা করবেন, বা আমি টিমকে জানাতে পারি।" if bn else
                       "I couldn't complete the order just now. Please try again in a moment, or ask for a human and I'll bring in the team.")
        return FlowReply(message, [], pid, done=True)

    code = order_code(receipt["order_id"])
    variant = {k: state[k] for k in ("size", "color") if state.get(k)}
    with transaction() as db:
        db.add(OrderDetail(order_id=receipt["order_id"], shop_id=shop_id, order_code=code, channel="messenger",
                           variant=variant, customer_name=state["name"], phone=state["phone"],
                           delivery_address=state["address"]))
        set_order_state(db, customer_id, shop_id, {})
        escalation.enqueue_alerts(db, f"order:{receipt['order_id']}", {
            "type": "order", "order_code": code, "customer_id": customer_id, "channel": "messenger",
            "product_name": receipt["product_name"], "quantity": receipt["quantity"],
            "variant_label": f"({_variant_label(state)})" if variant else "", "total_price": receipt["total_price"],
            "customer_name": state["name"], "phone": state["phone"], "address": state["address"]})
    variant_text = f" ({_variant_label(state)})" if variant else ""
    if bn:
        text = (f"🎉 অর্ডার কনফার্ম হয়েছে! অর্ডার নম্বর: {code}\n{receipt['quantity']} × {receipt['product_name']}{variant_text}\n"
                f"মোট: {_money(receipt['total_price'])}\nডেলিভারি: {state['name']}, {state['phone']}\n{state['address']}\n"
                f"শিপ হলে এখানেই জানাব। যেকোনো সময় “track {code}” লিখে আপডেট জানতে পারবেন।")
    else:
        text = (f"🎉 Your order is confirmed! Order ID: {code}\n{receipt['quantity']} × {receipt['product_name']}{variant_text}\n"
                f"Total: {_money(receipt['total_price'])}\nDelivering to: {state['name']}, {state['phone']}\n{state['address']}\n"
                f"We'll message you here when it ships — just say \"track {code}\" anytime for an update.")
    return FlowReply(text, [], pid, done=True, order={**receipt, "order_code": code, "variant": variant,
                                                      "customer_name": state["name"], "address": state["address"]})
