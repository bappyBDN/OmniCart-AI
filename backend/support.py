"""Deterministic human-handoff workflow. Internal notes never enter customer chat."""
from datetime import datetime, timedelta, timezone
import os

from fastapi import HTTPException
from sqlalchemy import text

from backend.commerce import get_customer, order_dict, profile_dict
from backend.database import transaction
from backend.notifications import enqueue
from backend.records import AuditEvent, ConversationControl, Notification, SupportCase, SupportNote
from models import Admin, ChatHistory, Escalation, Order


def audit(db, shop_id, actor_id, action, target_id, reason, changes=None, target_type="case"):
    db.add(AuditEvent(shop_id=shop_id, actor_id=actor_id, action=action, target_type=target_type,
                      target_id=str(target_id), reason=reason, changes=changes or {}))


def context_snapshot(db, customer_id, shop_id, summary=""):
    customer = get_customer(db, customer_id, shop_id)
    messages = list(reversed(db.query(ChatHistory).filter_by(customer_id=customer_id, shop_id=shop_id).order_by(ChatHistory.message_id.desc()).limit(12).all()))
    orders = db.query(Order).filter_by(customer_id=customer_id, shop_id=shop_id).order_by(Order.order_id.desc()).limit(5).all()
    return {"summary": summary[:1500], "profile": profile_dict(customer), "recent_orders": [order_dict(o) for o in orders],
            "recent_messages": [{"sender": m.sender, "text": m.message_text[:1500]} for m in messages]}


def case_dict(case, detail):
    return {"case_id": case.case_id, "customer_id": case.customer_id, "issue_summary": case.issue_summary,
            "status": case.status, "category": detail.category if detail else "support",
            "priority": detail.priority if detail else "normal", "reason": detail.reason if detail else "legacy",
            "assigned_admin_id": detail.assigned_admin_id if detail else None,
            "sla_due_at": detail.sla_due_at.replace(tzinfo=timezone.utc).isoformat() if detail else None,
            "sla_breached": bool(detail and not detail.resolved_at and detail.sla_due_at < datetime.utcnow()),
            "resolution_code": detail.resolution_code if detail else None,
            # Structured triage data for the dashboard's escalation alerts.
            "escalation": (detail.context or {}).get("escalation") if detail else None}


def open_case(customer_id, shop_id, summary, reason="customer_request", priority="normal", category="support",
              graph_thread_id=None, extra_context=None):
    """Open (or reuse) the customer's case, pause the bot and alert the admins.

    extra_context (see backend.escalation.build_context) carries the sentiment,
    product/photo context, conversation summary and last message for triage.
    """
    from backend import escalation
    from backend.conversation_state import ESCALATED, set_status
    with transaction() as db:
        if db.bind.dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        customer = get_customer(db, customer_id, shop_id)
        # Lock the customer when PostgreSQL is in use so concurrent requests reuse
        # the same open case, rather than opening duplicate tickets for one thread.
        from models import Customer
        db.query(Customer).filter_by(customer_id=customer_id, shop_id=shop_id).with_for_update().first()
        case = db.query(Escalation).filter_by(customer_id=customer_id, shop_id=shop_id).filter(Escalation.status.in_(["Open", "Pending"])).order_by(Escalation.case_id.desc()).first()
        created = not case
        if not case:
            case = Escalation(customer_id=customer_id, shop_id=shop_id, issue_summary=summary[:3000], status="Open")
            db.add(case)
            db.flush()
        detail = db.get(SupportCase, case.case_id)
        if not detail:
            detail = SupportCase(case_id=case.case_id, customer_id=customer_id, shop_id=shop_id,
                reason=reason, category=category, priority=priority,
                context=context_snapshot(db, customer_id, shop_id, summary),
                sla_due_at=datetime.utcnow() + timedelta(minutes=5 if priority == "urgent" else 15))
            db.add(detail)
        if graph_thread_id or extra_context:
            data = dict(detail.context)
            if graph_thread_id:
                data["graph_thread_ids"] = list(dict.fromkeys(data.get("graph_thread_ids", []) + [graph_thread_id]))
            if extra_context:
                # Newest escalation details win; earlier triggers stay in history.
                data["escalation"] = extra_context
                data["escalation_history"] = (data.get("escalation_history", []) + [
                    {"trigger": extra_context.get("trigger"), "at": datetime.utcnow().isoformat()}])[-10:]
            detail.context = data
        if priority == "urgent":
            detail.priority = "urgent"
        customer.human_intervention = True
        set_status(db, customer_id, shop_id, ESCALATED)
        if created:
            audit(db, shop_id, None, "handoff.created", case.case_id, reason)
        notification_key = f"escalation:{case.case_id}"
        notice = db.query(Notification).filter_by(dedupe_key=notification_key).first()
        email_body = summary
        if extra_context:
            email_body = escalation.alert_text(escalation.alert_payload(
                {"case_id": case.case_id, "priority": detail.priority}, customer_id, extra_context))
        email_status = notice.status if notice else enqueue(db, notification_key, os.getenv("ADMIN_EMAIL"),
            f"Support case #{case.case_id}", email_body)
        result = {**case_dict(case, detail), "human_intervention": True, "email_status": email_status}
        alerts = escalation.enqueue_alerts(db, notification_key, escalation.alert_payload(
            result, customer_id, extra_context or escalation.build_context(trigger=reason, query=summary)))
        db.flush()
        return {**result, "alert_channels": alerts}


def list_cases(db, shop_id):
    cases = db.query(Escalation).filter_by(shop_id=shop_id).order_by(Escalation.case_id.desc()).limit(500).all()
    details = {d.case_id: d for d in db.query(SupportCase).filter_by(shop_id=shop_id)}
    return [case_dict(case, details.get(case.case_id)) for case in cases]


def get_case(db, case_id, shop_id):
    case = db.query(Escalation).filter_by(case_id=case_id, shop_id=shop_id).first()
    if not case:
        raise HTTPException(404, "Support case not found")
    return case, db.get(SupportCase, case_id)


def case_context(case_id, shop_id, actor_id):
    with transaction() as db:
        case, detail = get_case(db, case_id, shop_id)
        notes = db.query(SupportNote).filter_by(case_id=case_id).order_by(SupportNote.id).all()
        audit(db, shop_id, actor_id, "case.context.read", case_id, "Support investigation")
        return {**case_dict(case, detail), "context": context_snapshot(db, case.customer_id, shop_id, case.issue_summary),
                "handoff_snapshot": detail.context if detail else {},
                "notes": [{"id": n.id, "admin_id": n.admin_id, "content": n.content, "created_at": n.created_at.isoformat()} for n in notes]}


def update_case(case_id, shop_id, actor_id, values):
    with transaction() as db:
        case, detail = get_case(db, case_id, shop_id)
        if not detail:
            raise HTTPException(409, "Take over this conversation to initialize the support case first")
        if case.status == "Resolved":
            raise HTTPException(409, "This case is already resolved")
        before = case_dict(case, detail)
        if "assigned_admin_id" in values:
            assigned = values["assigned_admin_id"]
            if assigned is not None and not db.query(Admin).filter_by(admin_id=assigned, shop_id=shop_id).first():
                raise HTTPException(404, "Assignee not found in this shop")
            detail.assigned_admin_id = assigned
        if values.get("priority"):
            detail.priority = values["priority"]
        if values.get("note"):
            db.add(SupportNote(case_id=case_id, admin_id=actor_id, content=values["note"].strip()))
        audit(db, shop_id, actor_id, "case.updated", case_id, values["reason"], {"before": before, "after": case_dict(case, detail)})
        return case_dict(case, detail)


def graph_threads(customer_id, shop_id, case_id=None):
    with transaction() as db:
        query = db.query(SupportCase).filter_by(customer_id=customer_id, shop_id=shop_id)
        if case_id is not None:
            query = query.filter_by(case_id=case_id)
        return [thread for detail in query
                for thread in detail.context.get("graph_thread_ids", []) if not detail.resolved_at]


def resume_customer(customer_id, shop_id, actor_id, note, resolution_code="resolved", case_id=None):
    with transaction() as db:
        customer = get_customer(db, customer_id, shop_id)
        query = db.query(Escalation).filter_by(customer_id=customer_id, shop_id=shop_id).filter(Escalation.status.in_(["Open", "Pending"]))
        if case_id is not None:
            query = query.filter_by(case_id=case_id)
        cases = query.all()
        for case in cases:
            case.status = "Resolved"
            detail = db.get(SupportCase, case.case_id)
            if detail:
                detail.resolved_at = datetime.utcnow()
                detail.resolution_code = resolution_code
            audit(db, shop_id, actor_id, "case.resolved", case.case_id, note or resolution_code)
        db.flush()
        customer.human_intervention = bool(db.query(Escalation).filter_by(customer_id=customer_id, shop_id=shop_id).filter(Escalation.status.in_(["Open", "Pending"])).count())
        from backend.conversation_state import ACTIVE, ESCALATED, set_status
        set_status(db, customer_id, shop_id, ESCALATED if customer.human_intervention else ACTIVE)
        control = db.get(ConversationControl, customer_id) or ConversationControl(customer_id=customer_id, shop_id=shop_id)
        control.resume_note, control.unresolved_turns, control.updated_at = note, 0, datetime.utcnow()
        db.add(control)
        audit(db, shop_id, actor_id, "conversation.resumed", customer_id, note or resolution_code, target_type="customer")
        return profile_dict(customer)
