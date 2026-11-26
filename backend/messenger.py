"""Meta Messenger HTTP boundary: authenticate, acknowledge, then do slow work."""
import hashlib
import hmac
import json
import logging
import os

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from backend.config import ROOT
from backend.media import public_image_url
from backend.product_links import public_product_url, public_order_url

load_dotenv(ROOT / ".env")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "omnichat_bappys_4001-try").strip()
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN", "").strip()
APP_SECRET = os.getenv("APP_SECRET", "").strip()
FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID", "").strip()
GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
MAX_BODY_BYTES = 1_048_576
log = logging.getLogger(__name__)
router = APIRouter(tags=["Messenger"])


@router.get("/healthz")
async def active():
    """Lightweight liveness probe; does not call the database or an LLM."""
    return {"status": "active"}


@router.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    token = params.get("hub.verify_token", "")
    challenge = params.get("hub.challenge")
    if (params.get("hub.mode") == "subscribe" and VERIFY_TOKEN and
            hmac.compare_digest(token.encode(), VERIFY_TOKEN.encode()) and challenge is not None):
        return Response(content=challenge, media_type="text/plain", status_code=200)
    raise HTTPException(status_code=403, detail="Forbidden")


def _image_url(message):
    """First real photo attachment URL (stickers and other types are ignored)."""
    attachments = message.get("attachments")
    if not isinstance(attachments, list):
        return None
    for attachment in attachments:
        if not isinstance(attachment, dict) or attachment.get("type") != "image":
            continue
        payload = attachment.get("payload")
        if not isinstance(payload, dict) or payload.get("sticker_id"):
            continue
        url = payload.get("url")
        if isinstance(url, str) and url.startswith("https://") and len(url) <= 4000:
            return url
    return None


def _valid_payload(value):
    return isinstance(value, str) and 0 < len(value) <= 1000


def inbound_events(payload):
    """Text, photo, quick-reply and postback events. Tolerates test events,
    unknown fields, receipts, echoes and unsupported attachments.

    Text-only messages yield exactly {page_id, sender_id, message_id, text};
    `image_url` and `postback` keys are added only when present.
    """
    if not isinstance(payload, dict) or payload.get("object") != "page":
        return
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        page_id = entry.get("id")
        if not isinstance(page_id, str) or not page_id.isascii() or not page_id.isdigit():
            continue
        if FACEBOOK_PAGE_ID and page_id != FACEBOOK_PAGE_ID:
            continue
        events = entry.get("messaging")
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            sender, recipient = event.get("sender"), event.get("recipient")
            if not isinstance(sender, dict):
                continue
            sender_id = sender.get("id")
            if isinstance(recipient, dict) and recipient.get("id") != page_id:
                continue
            if (not isinstance(sender_id, str) or not sender_id.isascii() or not sender_id.isdigit()
                    or sender_id == page_id):
                continue
            postback, message = event.get("postback"), event.get("message")
            if isinstance(postback, dict):
                # Card buttons ("Order Now", "More Details"). Newer Graph versions
                # include a mid; otherwise the timestamp makes the event unique.
                data, title = postback.get("payload"), postback.get("title")
                mid = postback.get("mid") or f"postback:{sender_id}:{event.get('timestamp')}:{data}"
                if not _valid_payload(data) or not isinstance(mid, str) or len(mid) > 512 or event.get("timestamp") is None and not postback.get("mid"):
                    continue
                yield {"page_id": page_id, "sender_id": sender_id, "message_id": mid,
                       "text": (title if isinstance(title, str) and title.strip() else data).strip()[:200], "postback": data}
                continue
            if not isinstance(message, dict) or message.get("is_echo"):
                continue
            mid, text = message.get("mid"), message.get("text")
            if not isinstance(mid, str) or not mid or len(mid) > 512:
                continue
            image_url = _image_url(message)
            if text is not None and (not isinstance(text, str) or len(text) > 4000):
                continue
            text = (text or "").strip()
            if not text and not image_url:
                continue
            event_data = {"page_id": page_id, "sender_id": sender_id, "message_id": mid, "text": text}
            quick = message.get("quick_reply")
            if isinstance(quick, dict) and _valid_payload(quick.get("payload")):
                event_data["postback"] = quick["payload"]
            if image_url:
                event_data["image_url"] = image_url
            yield event_data


text_events = inbound_events  # backwards-compatible name


@router.post("/webhook")
async def receive_webhook(request: Request, background: BackgroundTasks):
    # Verification tokens only protect GET. POST requires Meta's raw-body HMAC.
    if not APP_SECRET:
        log.error("Messenger POST unavailable: configure APP_SECRET in the server environment")
        raise HTTPException(503, "Messenger signature verification is not configured")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, "Webhook body too large")
    expected = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    supplied = request.headers.get("X-Hub-Signature-256", "")
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        log.warning("Rejected Messenger webhook: missing or invalid signature")
        raise HTTPException(403, "Invalid webhook signature")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HTTPException(400, "Invalid JSON") from None
    # Starlette sends the response BEFORE awaiting these background tasks. No SQL,
    # LLM or Send API operation runs on the request's acknowledgement path.
    from backend.messenger_service import process_message
    for event in inbound_events(payload):
        background.add_task(process_message, **event)
    return {"status": "ok"}


class MessengerSendError(RuntimeError):
    """Safe-to-log error: never includes access tokens or Graph response bodies."""


class MessengerConfigurationError(MessengerSendError):
    """Invalid local credentials; no Send API request was attempted."""


def page_authorization_headers():
    """Trim copy/paste padding, but never silently rewrite a damaged token.

    HTTPX/h11 rejects CR/LF in header values with LocalProtocolError. Check
    credentials first so errors are actionable without logging the secret.
    """
    token = PAGE_ACCESS_TOKEN.strip()
    if not token:
        message = "PAGE_ACCESS_TOKEN is not configured"
    elif (not token.isascii() or any(ord(char) <= 32 or ord(char) >= 127 for char in token)
          or '"' in token or "'" in token):
        message = ("PAGE_ACCESS_TOKEN must be one ASCII token without internal whitespace, "
                   "control characters, quotes, or a Bearer prefix. "
                   "Paste only the raw Page token in the server environment.")
    else:
        return {"Authorization": f"Bearer {token}"}
    log.error("Messenger configuration error: %s", message)
    raise MessengerConfigurationError(message)


async def send_text_reply(sender_id: str, text: str):
    """Send a RESPONSE within Meta's messaging window; split at the 2,000-char limit.

    Do not blindly retry timeouts: Meta may already have delivered the message.
    """
    await _send_messages(sender_id, [{"text": text[offset:offset + 2000]} for offset in range(0, len(text), 2000)])


async def send_product_image(sender_id: str, image_url: str):
    """Send actual stored photography as a native Messenger image attachment."""
    url = public_image_url(image_url)
    if not url:
        raise MessengerConfigurationError("Product image must use a public HTTPS photography URL")
    await _send_messages(sender_id, [{"attachment": {"type": "image", "payload": {"url": url, "is_reusable": True}}}])


async def send_quick_replies(sender_id: str, text: str, quick_replies: list):
    """Text with tappable quick replies (max 13) attached to the LAST chunk."""
    chunks = [text[offset:offset + 2000] for offset in range(0, len(text), 2000)] or [" "]
    messages = [{"text": chunk} for chunk in chunks]
    if quick_replies:
        messages[-1]["quick_replies"] = quick_replies[:13]
    await _send_messages(sender_id, messages)


def product_card(product):
    """Keep native chat ordering and the mobile form within Meta's three-button limit."""
    stock = product.get("stock_count")
    currency = product.get("currency") or "USD"
    price = product.get("effective_price")
    if price is not None and currency:
        details = [f"${float(price):.2f}" if currency == "USD" else f"{float(price):.2f} {currency}"]
    else:
        details = ["Price needs confirmation"]
    if stock is not None:
        details.append(f"{stock} in stock" if stock > 5 else f"only {stock} left" if stock > 0 else "Sold out")
    else:
        details.append("Stock needs confirmation")
    if product.get("sizes"):
        details.append("Sizes " + "/".join(product["sizes"][:5]))
    if "representative" in (product.get("alt_text") or "").lower():
        details.append("Representative image")
    element = {"title": product["name"][:80], "subtitle": " · ".join(details)[:80]}
    image = public_image_url(product.get("image_url")) if product.get("image_url") else None
    if image:
        element["image_url"] = image
    if "orderable" in product:
        orderable = product["orderable"] is True
    else:
        orderable = price is not None and (stock or 0) > 0 and bool(currency)
    buttons = [{"type": "postback", "title": "More Details", "payload": f"DETAILS:{product['product_id']}"}]
    if orderable and stock > 0:
        buttons.insert(0, {"type": "postback", "title": "Order Now", "payload": f"ORDER_NOW:{product['product_id']}"})
    url = public_product_url(product["product_id"])
    if url:
        element["default_action"] = {"type": "web_url", "url": url}
        if orderable and stock > 0:
            buttons[0]["title"] = "Order in chat"
            buttons.insert(0, {"type": "web_url", "title": "Order now",
                               "url": public_order_url(product["product_id"]), "webview_height_ratio": "full"})
        else:
            buttons.append({"type": "web_url", "title": "View product", "url": url})
    element["buttons"] = buttons
    return element


async def send_product_cards(sender_id: str, products: list):
    """One horizontally scrollable carousel (max 10 cards)."""
    elements = [product_card(product) for product in products[:10]]
    if not elements:
        return
    await _send_messages(sender_id, [{"attachment": {"type": "template", "payload": {
        "template_type": "generic", "image_aspect_ratio": "square", "elements": elements}}}])


async def pass_thread_control(sender_id: str, target_app_id: str, metadata: str = ""):
    """Meta Handover Protocol: give the thread to a human inbox app (e.g. Page Inbox)."""
    headers = page_authorization_headers()
    async with httpx.AsyncClient(http2=False, timeout=15.0) as client:
        try:
            response = await client.post(f"{GRAPH_API_BASE}/me/pass_thread_control", headers=headers,
                                         json={"recipient": {"id": sender_id}, "target_app_id": target_app_id,
                                               "metadata": metadata[:1000]})
        except httpx.RequestError as exc:
            log.error("Handover pass_thread_control transport failure (%s)", type(exc).__name__)
            raise MessengerSendError("Handover transport failure") from None
    if not response.is_success:
        log.error("Handover pass_thread_control rejected (HTTP %s)", response.status_code)
        raise MessengerSendError(f"Handover returned HTTP {response.status_code}")


async def _send_messages(sender_id, messages):
    headers = page_authorization_headers()
    if not messages:
        return
    # HTTP/1.1 is already HTTPX's default; keep it explicit for this integration.
    async with httpx.AsyncClient(http2=False, timeout=15.0) as client:
        for message in messages:
            try:
                response = await client.post(
                    f"{GRAPH_API_BASE}/me/messages",
                    headers=headers,
                    json={"recipient": {"id": sender_id}, "messaging_type": "RESPONSE",
                          "message": message},
                )
            except httpx.LocalProtocolError:
                # Never stringify this exception: h11 may include Authorization.
                log.error("Messenger Send API LocalProtocolError: check local HTTP/header and proxy configuration; request not retried")
                raise MessengerSendError("Local HTTP protocol error; check headers and proxy configuration; delivery uncertain") from None
            except httpx.RequestError as exc:
                log.error("Messenger Send API transport failure (%s); delivery may be uncertain", type(exc).__name__)
                raise MessengerSendError("Send API transport failure; delivery uncertain") from None
            if not response.is_success:
                # Never log response.text or exception URLs: these can contain PII.
                log.error("Messenger Send API rejected reply (HTTP %s)", response.status_code)
                raise MessengerSendError(f"Send API returned HTTP {response.status_code}")
