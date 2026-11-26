"""Shared SQL conversation memory for web customer sessions and Page-scoped PSIDs."""
import re

from backend.records import ChatTurn
from backend.database import transaction
from models import ChatHistory, Customer, Product

RECENT_TURNS = 8
RECENT_MESSAGES = RECENT_TURNS * 2


def _focused_sources(response):
    """Prefer what the final answer actually cited over retrieval candidates."""
    response = response or {}
    products = [source for source in response.get("sources", [])
                if source.get("kind") == "product" and source.get("data")]
    cited_ids = set(re.findall(r"\[product:([^\]\s]+)\]", response.get("answer", "")))
    cited = [source for source in products if source["data"].get("product_id") in cited_ids]
    if cited:
        return cited
    focus = response.get("focus_product_id")
    focused = [source for source in products if source.get("id") == f"product:{focus}"]
    return focused or None


def _previous_sources(recent_turns):
    """Preserve identities across greetings/policy detours, but honor topic resets."""
    if not recent_turns:
        return []
    previous = recent_turns[0].response or {}
    sources = previous.get("sources", [])
    if previous.get("context_reset"):
        return _focused_sources(previous) or sources
    if not previous.get("degraded") and any(s.get("kind") == "product" for s in sources):
        return _focused_sources(previous) or sources
    for turn in recent_turns[1:]:
        response = turn.response or {}
        recovered = _focused_sources(response)
        if recovered:
            return recovered
        if response.get("context_reset"):
            break
        products = [s for s in response.get("sources", []) if s.get("kind") == "product" and s.get("data")]
        if products and not response.get("degraded"):
            return products
    return sources


def load_conversation(db, customer_id, shop_id):
    """Caller has already authorized the customer; never share memory across IDs.

    Read up to eight recent turns plus older message rows for the bounded summary.
    Messenger contacts and web access sessions both resolve to a persistent customer.
    """
    history = list(reversed(db.query(ChatHistory).filter_by(
        customer_id=customer_id, shop_id=shop_id,
    ).order_by(ChatHistory.message_id.desc()).limit(RECENT_MESSAGES + 14).all()))
    turns = db.query(ChatTurn).join(Customer, ChatTurn.customer_id == Customer.customer_id).filter(
        ChatTurn.customer_id == customer_id, Customer.shop_id == shop_id,
    ).order_by(ChatTurn.id.desc()).limit(RECENT_TURNS).all()
    last_response = (turns[0].response or {}) if turns else {}
    context = {key: last_response[key] for key in ("pending_action", "locale") if key in last_response}
    context["allow_history_recovery"] = not bool(turns)
    return history, _previous_sources(turns), context


def history_product_sources(history, shop_id):
    """Recover legacy assistant identities when structured turn metadata is absent.

    Never accept a user-supplied citation as identity. Never resurrect a missing
    SKU by falling back further into older dialogue. No historical facts are used.
    """
    with transaction() as db:
        from backend.catalog_state import active_product
        products = db.query(Product.product_id, Product.name).filter_by(shop_id=shop_id).filter(active_product()).all()
    for row in reversed((history or [])[-RECENT_MESSAGES:]):
        if row.sender != "ai":
            continue
        text = row.message_text or ""
        cited = set(re.findall(r"\[product:([^\]\s]+)\]", text))
        if cited:
            matches = [(pid, name) for pid, name in products if pid in cited]
        else:
            matches = [(pid, name) for pid, name in products if re.search(
                rf"(?<!\w){re.escape(name.casefold())}(?!\w)", text.casefold())]
            matches = [(pid, name) for pid, name in matches if not any(
                name != other and name.casefold() in other.casefold() for _, other in matches)]
        if matches or cited:
            return [{"id": f"product:{pid}", "kind": "product", "title": name,
                     "content": name, "data": {"product_id": pid, "name": name}} for pid, name in matches]
    return []
