import logging
import os
import smtplib
from email.message import EmailMessage

from sqlalchemy import update

from backend.config import settings
from backend.database import transaction
from backend.records import Notification

log = logging.getLogger(__name__)


def enqueue(db, key, recipient, subject, body):
    if not settings.email_enabled or not recipient:
        return "disabled"
    if not os.getenv("EMAIL_SENDER") or not os.getenv("EMAIL_PASSWORD"):
        return "not_configured"
    db.add(Notification(dedupe_key=key, recipient=recipient, subject=subject, body=body))
    return "queued"


def deliver_notifications():
    """Bounded outbox delivery. A failed email never rolls back a real order."""
    with transaction() as db:
        ids = [n.id for n in db.query(Notification).filter(Notification.status.in_(["queued", "failed"]), Notification.attempts < 3).limit(10)]
    for notification_id in ids:
        with transaction() as db:
            claimed = db.execute(update(Notification).where(Notification.id == notification_id,
                Notification.status.in_(["queued", "failed"])).values(status="sending", attempts=Notification.attempts + 1))
            if claimed.rowcount != 1:
                continue
            item = db.get(Notification, notification_id)
            recipient, body = item.recipient, item.body
            email = None
            if not recipient.startswith("alert:"):
                email = EmailMessage()
                email["From"] = os.getenv("EMAIL_SENDER")
                email["To"] = item.recipient
                email["Subject"] = item.subject
                email.set_content(item.body)
        try:
            if email is None:
                # Admin escalation / order alerts (Telegram, webhook). See backend.escalation.
                from backend.escalation import deliver_alert
                deliver_alert(recipient, body)
            else:
                with smtplib.SMTP(os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "587")), timeout=15) as smtp:
                    smtp.starttls()
                    smtp.login(os.environ["EMAIL_SENDER"], os.environ["EMAIL_PASSWORD"])
                    smtp.send_message(email)
            status = "sent"
        except Exception:
            log.warning("Notification %s delivery failed", notification_id)
            status = "failed"
        with transaction() as db:
            db.get(Notification, notification_id).status = status
