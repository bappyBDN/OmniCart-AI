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


def text_events(payload):
    """Tolerate test events, unknown fields, receipts and attachments."""
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
            message, sender = event.get("message"), event.get("sender")
            if not isinstance(message, dict) or not isinstance(sender, dict) or message.get("is_echo"):
                continue
            sender_id, text, mid = sender.get("id"), message.get("text"), message.get("mid")
            recipient = event.get("recipient")
            if isinstance(recipient, dict) and recipient.get("id") != page_id:
                continue
            if (not isinstance(sender_id, str) or not sender_id.isascii() or not sender_id.isdigit()
                    or sender_id == page_id or not isinstance(mid, str) or not mid or len(mid) > 512
                    or not isinstance(text, str) or not text.strip() or len(text) > 4000):
                continue
            yield {"page_id": page_id, "sender_id": sender_id, "message_id": mid, "text": text.strip()}


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
    for event in text_events(payload):
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
