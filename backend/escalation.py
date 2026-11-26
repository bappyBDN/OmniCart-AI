"""Smart escalation: rich hand-over context + instant admin alerts.

When the agent hands a conversation to a person, `support.open_case` stores the
context built here on the support case (visible in the admin dashboard) and
queues alerts through the durable notification outbox:

  * recipient "alert:telegram"  -> Telegram Bot API (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)
  * recipient "alert:webhook"   -> JSON POST to ADMIN_ALERT_WEBHOOK_URL (Slack/n8n/Zapier ...)

Both are optional; the dashboard always shows the case. Alert bodies contain
the customer's recent words for triage, so only configure trusted endpoints.
"""
import json
import logging
import os

import httpx

log = logging.getLogger(__name__)

# Human-readable labels for the admin dashboard and alerts.
TRIGGER_LABELS = {
    "customer_request": "Asked for a human",
    "customer_distress": "Frustrated / angry customer",
    "negative_sentiment": "Persistent negative sentiment",
    "authority_exceeded": "Payment / refund dispute or account action",
    "after_sales_request": "Damaged item / return / refund request",
    "complex_request": "Bulk or custom order",
    "tool_failure": "Automation failed repeatedly",
    "provider_unavailable": "AI provider unavailable",
    "validation_failed": "Answer could not be verified",
    "runtime_failure": "Runtime failure",
    "operator_takeover": "Operator took over",
}
CATEGORY_BY_TRIGGER = {
    "complex_request": "sales", "after_sales_request": "after_sales",
    "authority_exceeded": "billing", "customer_distress": "complaint", "negative_sentiment": "complaint",
}
URGENT_TRIGGERS = {"customer_distress", "authority_exceeded", "negative_sentiment"}


def conversation_summary(user_messages, limit=4):
    """Compact, factual summary: the customer's own recent words, oldest first."""
    recent = [" ".join(str(text).split())[:160] for text in user_messages if text and str(text).strip()][-limit:]
    return " → ".join(recent)


def build_context(*, trigger, query, sentiment=None, products=None, visual=None, user_messages=None, channel="web"):
    return {
        "trigger": trigger,
        "trigger_label": TRIGGER_LABELS.get(trigger, trigger.replace("_", " ").capitalize()),
        "channel": channel,
        "sentiment": sentiment or {"label": "NEUTRAL", "score": 0.0, "urgency": "normal"},
        "product_context": [{"product_id": p.get("product_id"), "name": p.get("name")}
                            for p in (products or []) if p.get("product_id")][:5],
        "visual_context": {k: v for k, v in (visual or {}).items()
                           if k in ("description", "summary", "category", "color", "pattern", "material", "style",
                                    "matched_product_ids")},
        "conversation_summary": conversation_summary(list(user_messages or []) + [query]),
        "last_message": " ".join((query or "").split())[:500],
    }


def alert_payload(case, customer_id, context):
    sentiment = context.get("sentiment") or {}
    return {
        "type": "escalation",
        "case_id": case["case_id"],
        "priority": case.get("priority", "normal"),
        "customer_id": customer_id,
        "channel": context.get("channel", "web"),
        "trigger": context.get("trigger_label") or context.get("trigger"),
        "sentiment": sentiment.get("label", "NEUTRAL"),
        "sentiment_score": sentiment.get("score", 0.0),
        "products": [p.get("name") for p in context.get("product_context", [])],
        "image_context": (context.get("visual_context") or {}).get("summary", ""),
        "conversation_summary": context.get("conversation_summary", ""),
        "last_message": context.get("last_message", ""),
    }


def alert_text(payload):
    lines = [f"🚨 Escalation #{payload['case_id']} ({payload['priority']})" if payload.get("type") == "escalation"
             else f"🛒 New order {payload.get('order_code', '')}",
             f"Customer: {payload.get('customer_id')} via {payload.get('channel', 'web')}"]
    if payload.get("type") == "escalation":
        lines += [f"Reason: {payload['trigger']}",
                  f"Sentiment: {payload['sentiment']} ({payload['sentiment_score']})"]
        if payload.get("products"):
            lines.append("Products: " + ", ".join(filter(None, payload["products"])))
        if payload.get("image_context"):
            lines.append("Photo: " + payload["image_context"])
        if payload.get("conversation_summary"):
            lines.append("Summary: " + payload["conversation_summary"])
        lines.append("Last message: " + payload.get("last_message", ""))
    else:
        lines += [f"{payload.get('quantity')} × {payload.get('product_name')} {payload.get('variant_label', '')}".rstrip(),
                  f"Total: ৳{payload.get('total_price', 0):,.2f}",
                  f"Name: {payload.get('customer_name')} · Phone: {payload.get('phone')}",
                  f"Address: {payload.get('address')}"]
    return "\n".join(lines)


def alert_channels():
    channels = []
    if os.getenv("TELEGRAM_BOT_TOKEN", "").strip() and os.getenv("TELEGRAM_CHAT_ID", "").strip():
        channels.append("alert:telegram")
    if os.getenv("ADMIN_ALERT_WEBHOOK_URL", "").strip().startswith("https://"):
        channels.append("alert:webhook")
    return channels


def enqueue_alerts(db, key, payload):
    """Queue one outbox row per configured channel. Idempotent per key."""
    from backend.records import Notification
    queued = []
    for channel in alert_channels():
        dedupe = f"{key}:{channel}"
        if db.query(Notification).filter_by(dedupe_key=dedupe).first():
            continue
        db.add(Notification(dedupe_key=dedupe, recipient=channel, subject=f"OmniCart {payload.get('type', 'alert')}",
                            body=json.dumps(payload, ensure_ascii=False, default=str)))
        queued.append(channel)
    return queued


def deliver_alert(recipient, body):
    """Send one queued alert. Raises on failure so the outbox can retry."""
    payload = json.loads(body)
    with httpx.Client(http2=False, timeout=10.0) as client:
        if recipient == "alert:telegram":
            token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
            response = client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                   json={"chat_id": os.environ["TELEGRAM_CHAT_ID"].strip(), "text": alert_text(payload)[:4000]})
        elif recipient == "alert:webhook":
            response = client.post(os.environ["ADMIN_ALERT_WEBHOOK_URL"].strip(),
                                   json={**payload, "text": alert_text(payload)})
        else:
            raise ValueError("Unknown alert channel")
    if not response.is_success:
        # Never log the response body or URL: both may contain secrets.
        raise RuntimeError(f"Alert channel returned HTTP {response.status_code}")


def customer_message(locale="en"):
    """Warm hand-over reply. Honest: a person has been notified, not 'is typing'."""
    if (locale or "en").startswith("bn"):
        return ("আপনার বিষয়টা বুঝতে পারছি, আর এটা ঠিকভাবে সমাধান হওয়া দরকার। আমাদের একজন সাপোর্ট বিশেষজ্ঞকে "
                "আপনার সব তথ্যসহ জানিয়ে দিয়েছি — তিনি শিগগিরই এই চ্যাটে যোগ দেবেন। আবার সব খুলে বলতে হবে না।")
    return ("I completely understand, and I want to make sure this is handled properly. I've passed everything "
            "to one of our support specialists, along with the details you've shared, so you won't need to "
            "repeat yourself. They'll join this chat shortly.")
