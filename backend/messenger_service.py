"""Messenger adapter reusing the existing authenticated chat workflow internally.

Per inbound event (text, photo, quick reply or card postback):

  1. claim the event (dedupe), resolve the PSID to a persistent customer
  2. photo   -> download from Meta's CDN, hand to the visual specialist
     postback -> ORDER_NOW / DETAILS / order-form answers
  3. order flow (backend.order_flow) owns the turn while an order form is open;
     escalation-worthy messages always bypass it and reach the agent
  4. otherwise the shared /api/chat handler runs (memory, sentiment, Graph RAG,
     grounding, escalation) exactly as for the web
  5. reply: clean text (+ quick replies), then product cards; on escalation the
     thread can be passed to a human inbox via the Meta Handover Protocol

BackgroundTasks is in-process, NOT a durable queue. Persisted inbox rows provide
deduplication and failure visibility, but a crash before the first insert can lose
an acknowledged event. See docs/messenger.md for deployment/recovery limits.
"""
import logging
import os
import threading
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from fastapi import BackgroundTasks
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from backend import order_flow
from backend import sentiment as mood
from backend.config import settings
from backend.conversation import product_choices, purchase_request
from backend.conversation_state import record_sentiment, remember_turn
from backend.database import transaction
from backend.catalog_state import active_product
from backend.messenger import (pass_thread_control, send_product_cards, send_product_image, send_quick_replies,
                               send_text_reply)
from backend.messenger_format import format_messenger_response
from backend.records import ChatTurn, MessengerContact, MessengerEvent
from backend.retrieval import product_dict
from backend.schemas import ChatInput
from models import ChatHistory, Customer, Product, Shop

log = logging.getLogger(__name__)
sender_locks = [threading.Lock() for _ in range(64)]
FLOW_PAYLOADS = ("SIZE:", "COLOR:", "QTY:", "ORDER_CONFIRM", "ORDER_EDIT", "ORDER_CANCEL")
IMAGE_PROMPT = "Can you find this product for me?"


def product_display_mode():
    """cards (Generic Template with Order Now / More Details) or image (native photo)."""
    return os.getenv("MESSENGER_PRODUCT_DISPLAY", "cards").strip().lower()


def claim_event(page_id, sender_id, message_id, text):
    """A unique database key prevents concurrent deliveries from replying twice."""
    try:
        with transaction() as db:
            db.add(MessengerEvent(page_id=page_id, message_id=message_id, sender_id=sender_id,
                                  text=text, status="processing"))
        return True
    except IntegrityError:
        # Includes sent, processing AND failed events. Never automatically resend
        # failed messages: an HTTP timeout may mean the reply was actually sent.
        with transaction() as db:
            if db.get(MessengerEvent, (page_id, message_id)):
                return False
        raise


def customer_for_sender(page_id, sender_id):
    try:
        with transaction() as db:
            contact = db.get(MessengerContact, (page_id, sender_id))
            if contact:
                return contact.customer_id
            if not db.get(Shop, settings.shop_id):
                raise RuntimeError("Messenger shop is not configured")
            customer = Customer(unique_id=f"messenger:{page_id}:{sender_id}", shop_id=settings.shop_id)
            db.add(customer)
            db.flush()
            db.add(MessengerContact(page_id=page_id, sender_id=sender_id, customer_id=customer.customer_id))
            return customer.customer_id
    except IntegrityError:
        # A second worker may create the contact while the first is committing.
        with transaction() as db:
            contact = db.get(MessengerContact, (page_id, sender_id))
            if contact:
                return contact.customer_id
        raise


def current_focus(customer_id, shop_id, text):
    """The product an "order this" message refers to, or None if ambiguous."""
    from backend.agent import named_products
    from backend.chat_memory import load_conversation
    named = named_products(text or "", shop_id)
    if len(named) == 1:
        return named[0]
    with transaction() as db:
        _, previous, _ = load_conversation(db, customer_id, shop_id)
        last = db.query(ChatTurn).filter_by(customer_id=customer_id).order_by(ChatTurn.id.desc()).first()
    choices = [source["data"]["product_id"] for source in product_choices(previous)]
    if len(choices) == 1:
        return choices[0]
    focus = (last.response or {}).get("focus_product_id") if last else None
    return focus if focus in choices else None


def _product_source(shop_id, product_id):
    with transaction() as db:
        row = db.query(Product).filter_by(product_id=product_id, shop_id=shop_id).filter(active_product()).first()
        if not row:
            return None
        data = product_dict(row)
    return {"id": f"product:{product_id}", "kind": "product", "title": data["name"],
            "content": data["name"], "data": data, "retrieval": "order_flow"}


def record_flow_turn(customer_id, request_id, user_text, flow, feeling):
    """Persist an order-flow turn like any chat turn (memory, admin view, dedupe)."""
    source = _product_source(settings.shop_id, flow.product_id) if flow.product_id else None
    response = {"answer": flow.text, "sources": [source] if source else [], "drafts": [],
                "trace": [{"node": "order_flow", "detail": "Messenger order form step"}],
                "route": "order_flow", "degraded": False, "usage": {}, "latency_ms": 0,
                "focus_product_id": flow.product_id, "show_images": False, "quick_replies": flow.quick_replies,
                "order": flow.order, "sentiment": feeling.as_dict()}
    with transaction() as db:
        existing = db.query(ChatTurn).filter_by(customer_id=customer_id, request_id=str(request_id)).first()
        if existing:
            return existing.response
        user_row = ChatHistory(sender="user", message_text=user_text, customer_id=customer_id, shop_id=settings.shop_id)
        db.add(user_row)
        db.flush()
        record_sentiment(db, customer_id, settings.shop_id, feeling, "messenger", user_row.message_id)
        reply = ChatHistory(sender="ai", message_text=flow.text, customer_id=customer_id, shop_id=settings.shop_id)
        db.add(reply)
        db.flush()
        response["message_id"] = reply.message_id
        remember_turn(db, customer_id, settings.shop_id, response, "messenger")
        db.add(ChatTurn(customer_id=customer_id, request_id=str(request_id), response=response))
    return response


def _bypass_flow(text):
    """Escalation-worthy messages must reach the agent even mid-order."""
    from backend.agent import handoff_trigger
    feeling = mood.classify(text)
    return feeling.label == mood.FRUSTRATED or bool(handoff_trigger(text or ""))


def _details_text(shop_id, product_id):
    source = _product_source(shop_id, product_id)
    return f"Tell me more about the {source['title']}" if source else None


def generate_reply(page_id, sender_id, message_id, text, background, image=None, postback=None):
    # Runtime import avoids a module initialization cycle; the existing handler
    # provides history, idempotent graph execution, support handoff and SQL writes.
    from backend.main import chat
    with sender_locks[hash((page_id, sender_id)) % len(sender_locks)]:
        customer_id = customer_for_sender(page_id, sender_id)
        request_id = uuid5(NAMESPACE_URL, f"messenger:{page_id}:{message_id}")
        session = SimpleNamespace(customer_id=customer_id, shop_id=settings.shop_id, channel="messenger")
        with transaction() as db:
            paused = bool(db.get(Customer, customer_id).human_intervention)
        feeling = mood.classify(text)
        flow = None
        if not paused and not image and not _bypass_flow(text):
            if postback and postback.startswith("DETAILS:"):
                text = _details_text(settings.shop_id, postback.split(":", 1)[1]) or text
            else:
                focus = None
                if (not postback and purchase_request(text or "")
                        and not order_flow.active_state(customer_id, settings.shop_id)):
                    focus = current_focus(customer_id, settings.shop_id, text)
                flow = order_flow.handle(customer_id, settings.shop_id, text, payload=postback, focus_product_id=focus)
                if flow is None and postback and postback.startswith(FLOW_PAYLOADS):
                    flow = order_flow.FlowReply("That order form has expired — tap Order Now on the product to start again.",
                                                done=True)
        if flow and not flow.passthrough:
            return record_flow_turn(customer_id, request_id, text, flow, feeling)
        response = chat(ChatInput(message=text or IMAGE_PROMPT, request_id=request_id, image=image), background, session)
        answer = response.get("answer", "")
        if flow and flow.passthrough and answer:
            # The agent answered a side question; remind the customer where the order stands.
            return {**response, "answer": answer + "\n\n" + flow.text, "quick_replies": flow.quick_replies}
        if response.get("drafts") and answer and not paused:
            # The agent proposed a quantity; continue in the Messenger order form.
            draft = response["drafts"][-1]
            started = order_flow.start(customer_id, settings.shop_id, draft["product_id"], text,
                                       response.get("locale", "en"), quantity=draft.get("quantity"))
            if started and not started.done:
                return {**response, "answer": started.text, "quick_replies": started.quick_replies}
        return {**response, "answer": answer}


def mark_event(page_id, message_id, status, error=None):
    with transaction() as db:
        db.execute(update(MessengerEvent).where(
            MessengerEvent.page_id == page_id, MessengerEvent.message_id == message_id,
        ).values(status=status, error=error))


async def _deliver(sender_id, reply, quick_replies):
    cards = reply.cards if product_display_mode() == "cards" else []
    if quick_replies:
        # Quick replies only show on the latest message, so cards go first.
        if cards:
            await send_product_cards(sender_id, cards)
        await send_quick_replies(sender_id, reply.text or "👇", quick_replies)
        return
    if reply.text:
        await send_text_reply(sender_id, reply.text)
    if cards:
        await send_product_cards(sender_id, cards)
    else:
        for product in reply.products:
            await send_product_image(sender_id, product["image_url"])


async def _handover(sender_id, response):
    target = os.getenv("META_HANDOVER_TARGET_APP_ID", "").strip()
    case = response.get("handoff")
    if not target or not case:
        return
    try:
        await pass_thread_control(sender_id, target, f"case:{case.get('case_id')}")
    except Exception:
        log.error("Messenger handover failed; the bot stays paused and the case is on the dashboard")


async def process_message(page_id: str, sender_id: str, message_id: str, text: str,
                          image_url: str | None = None, postback: str | None = None):
    """Offload synchronous SQL/LLM work so it never blocks the ASGI event loop."""
    claimed = False
    try:
        claimed = await run_in_threadpool(claim_event, page_id, sender_id, message_id,
                                          text or ("[image]" if image_url else "[event]"))
        if not claimed:
            return
        image = None
        if image_url:
            from backend.vision import fetch_image_data_url
            try:
                image = await fetch_image_data_url(image_url)
            except Exception as exc:
                log.warning("Messenger photo could not be fetched (%s)", type(exc).__name__)
                await send_text_reply(sender_id, "Sorry, I couldn't open that photo. Could you try sending it again, "
                                                 "or tell me what you're looking for?")
                await run_in_threadpool(mark_event, page_id, message_id, "sent", "image_unavailable")
                return
        notifications = BackgroundTasks()
        response = await run_in_threadpool(generate_reply, page_id, sender_id, message_id, text, notifications,
                                           image, postback)
        reply = await run_in_threadpool(format_messenger_response, response, settings.shop_id, text)
        quick_replies = response.get("quick_replies") or []
        await _deliver(sender_id, reply, quick_replies)
        await _handover(sender_id, response)
        sent = reply.text or reply.products or reply.cards
        await run_in_threadpool(mark_event, page_id, message_id, "sent" if sent else "human_handoff")
        try:
            from backend.notifications import deliver_notifications
            notifications.add_task(deliver_notifications)
            await notifications()
        except Exception:
            # A separate email/outbox error must not turn a sent reply into a
            # failed event that an operator might mistakenly replay.
            log.error("Messenger reply handled, but notification delivery failed; inspect the notification outbox")
    except Exception as exc:
        # Log types, not exception text: database/HTTP exceptions may contain PII.
        kind = type(exc).__name__
        log.error("Messenger background processing failed (%s); inspect messenger_events", kind)
        if claimed:
            try:
                await run_in_threadpool(mark_event, page_id, message_id, "failed", kind)
            except Exception:
                log.error("Could not persist Messenger failure status; inspect processing events")
