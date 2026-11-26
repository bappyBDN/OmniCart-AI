"""Messenger adapter reusing the existing authenticated chat workflow internally.

BackgroundTasks is in-process, NOT a durable queue. Persisted inbox rows provide
deduplication and failure visibility, but a crash before the first insert can lose
an acknowledged event. See docs/messenger.md for deployment/recovery limits.
"""
import logging
import threading
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from fastapi import BackgroundTasks
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from backend.config import settings
from backend.database import transaction
from backend.messenger import send_product_cards, send_product_image, send_text_reply
from backend.messenger_format import format_messenger_response
from backend.records import MessengerContact, MessengerEvent
from backend.schemas import ChatInput
from models import Customer, Shop

log = logging.getLogger(__name__)
sender_locks = [threading.Lock() for _ in range(64)]


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


def generate_reply(page_id, sender_id, message_id, text, background):
    # Runtime import avoids a module initialization cycle; the existing handler
    # provides history, idempotent graph execution, support handoff and SQL writes.
    from backend.main import chat
    with sender_locks[hash((page_id, sender_id)) % len(sender_locks)]:
        customer_id = customer_for_sender(page_id, sender_id)
        request_id = uuid5(NAMESPACE_URL, f"messenger:{page_id}:{message_id}")
        session = SimpleNamespace(customer_id=customer_id, shop_id=settings.shop_id)
        response = chat(ChatInput(message=text, request_id=request_id), background, session)
        answer = response.get("answer", "")
        if response.get("drafts") and answer:
            # A Messenger text message cannot confirm a checkout draft in the UI.
            answer += "\nThis is only a proposal, not a confirmed order. Please contact the shop to complete checkout."
        return {**response, "answer": answer}


def mark_event(page_id, message_id, status, error=None):
    with transaction() as db:
        db.execute(update(MessengerEvent).where(
            MessengerEvent.page_id == page_id, MessengerEvent.message_id == message_id,
        ).values(status=status, error=error))


async def process_message(page_id: str, sender_id: str, message_id: str, text: str):
    """Offload synchronous SQL/LLM work so it never blocks the ASGI event loop."""
    claimed = False
    try:
        claimed = await run_in_threadpool(claim_event, page_id, sender_id, message_id, text)
        if not claimed:
            return
        notifications = BackgroundTasks()
        response = await run_in_threadpool(generate_reply, page_id, sender_id, message_id, text, notifications)
        reply = await run_in_threadpool(format_messenger_response, response, settings.shop_id, text)
        if reply.text:
            await send_text_reply(sender_id, reply.text)
        linked = [product for product in reply.products if product.get("product_url")]
        if linked:
            await send_product_cards(sender_id, linked)
        for product in reply.products:
            if not product.get("product_url"):
                log.warning("Messenger product link unavailable; configure PUBLIC_SITE_URL or RENDER_EXTERNAL_URL")
                await send_product_image(sender_id, product["image_url"])
        await run_in_threadpool(mark_event, page_id, message_id, "sent" if reply.text or reply.products else "human_handoff")
        try:
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
