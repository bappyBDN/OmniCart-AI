import base64
import io
import logging
import re
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from urllib.parse import urlparse

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from PIL import Image
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from backend import agent, llm
from backend.auth import admin_session, customer_session, current_session, issue_session, verify_password
from backend.commerce import (confirm_order, draft_dict, escalate, get_customer, order_dict,
                              prepare_order, profile_dict, save_profile)
from backend.config import ROOT, settings
from backend.chat_memory import _focused_sources, _previous_sources, load_conversation
from backend.database import initialize, transaction
from backend.notifications import deliver_notifications
from backend.records import AccessSession, ChatTurn, Notification, OrderDraft, ProductImage
from backend.media import generated_product_svg, product_image_record
from backend.voice import transcribe_audio
from backend import sentiment as mood
from backend.conversation_state import record_sentiment, remember_turn, snapshot as conversation_snapshot
from backend.prd_api import router as prd_router
from backend.messenger import router as messenger_router
from backend.language import is_bangla
from backend.support import audit, graph_threads, list_cases, open_case, resume_customer
from backend.usage import aggregate_usage
from backend.retrieval import product_dict, retriever
from backend.schemas import (AdminLogin, CaseResolution, ChatInput, CheckoutConfirm, DraftInput, HandoffInput,
                             ImageInput, OrderStatusInput, PolicyInput, ProductInput, ProfileInput, ReplyInput,
                             VoiceInput)
from models import Admin, ChatHistory, Customer, Escalation, Order, Policy, Product, Shop

log = logging.getLogger(__name__)
chat_locks = [threading.Lock() for _ in range(64)]
rate_lock = threading.Lock()
rate_buckets = defaultdict(deque)


def rate_limit(key, count=40, window=60):
    now = time.monotonic()
    with rate_lock:
        bucket = rate_buckets[key]
        while bucket and bucket[0] < now - window:
            bucket.popleft()
        if len(bucket) >= count:
            raise HTTPException(429, "Too many requests. Please wait a minute.")
        bucket.append(now)
        if len(rate_buckets) > 5000:
            stale = [k for k, v in rate_buckets.items() if not v or v[-1] < now - window]
            for old in stale:
                del rate_buckets[old]


def validate_image(value):
    if value is None:
        return
    match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=\r\n]+)", value)
    if not match:
        raise HTTPException(422, "Upload a PNG, JPEG or WebP image")
    try:
        data = base64.b64decode(match[2], validate=True)
        if len(data) > 3_000_000:
            raise ValueError("Image too large")
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 16_000_000 or image.format not in ("PNG", "JPEG", "WEBP"):
                raise ValueError("Invalid dimensions or format")
            image.verify()
    except Exception:
        raise HTTPException(422, "Invalid image. Maximum 3 MB and 16 megapixels.") from None


@asynccontextmanager
async def lifespan(app):
    initialize()
    # Never seed/overwrite a shop or inventory implicitly.
    retriever.warm(settings.shop_id)
    threading.Thread(target=llm.warm_models, daemon=True).start()
    yield


app = FastAPI(title="OmniCart Agentic RAG API", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                   allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type"])
app.include_router(prd_router)
app.include_router(messenger_router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api") or request.url.path in ("/healthz", "/webhook"):
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path not in ("/docs", "/redoc"):
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    return response


@app.get("/api/health")
def health():
    with transaction() as db:
        shop = db.get(Shop, settings.shop_id)
        products = db.query(Product).filter_by(shop_id=settings.shop_id).count()
        policies = db.query(Policy).filter_by(shop_id=settings.shop_id).count()
        database = {"backend": db.bind.dialect.name, "name": Path(db.bind.url.database or "").name}
    import os
    return {"status": "ok", "shop_name": shop.shop_name if shop else "OmniCart", "shop_ready": bool(shop),
            "products": products, "policies": policies, "dense_status": retriever.dense_status,
            "model_client_status": llm.model_status,
            "chat_model": settings.model, "vision_model": settings.vision_model,
            "stt_model": settings.stt_model,
            "embedding_model": settings.embedding_model,
            "rag_min_confidence": settings.rag_min_confidence,
            "rag_dense_min_similarity": settings.rag_dense_min_similarity,
            "mcp_enabled": settings.mcp_enabled,
            "llm_configured": any(os.getenv(k) for k in ["GROQ_API_KEY", "GROQ_API_KEY1", "GROQ_API_KEY2_B", "GROQ_API_KEY3_M", "OPENAI_API_KEY", "HUGGINGFACEHUB_API_TOKEN"]),
            "deepseek_configured": bool(settings.deepseek_api_key),
            "email_enabled": settings.email_enabled, "database": database, "version": "2.0.0",
            "implementation_phase": "prd-2.3-core-1"}


@app.post("/api/sessions", status_code=201)
def create_session(request: Request):
    rate_limit(f"session:{request.client.host}", 15)
    with transaction() as db:
        if not db.get(Shop, settings.shop_id):
            raise HTTPException(503, "No shop configured. Follow README setup instructions.")
        customer = Customer(unique_id=str(uuid4()), shop_id=settings.shop_id)
        db.add(customer)
        db.flush()
        token = issue_session(db, "customer", settings.shop_id, customer_id=customer.customer_id)
        return {"token": token, "profile": profile_dict(customer)}


@app.delete("/api/sessions/current", status_code=204)
def logout(session=Depends(current_session)):
    with transaction() as db:
        db.delete(db.get(AccessSession, session.token_hash))


@app.get("/api/profile")
def get_profile(session=Depends(customer_session)):
    with transaction() as db:
        return profile_dict(get_customer(db, session.customer_id, session.shop_id))


@app.patch("/api/profile")
def patch_profile(payload: ProfileInput, session=Depends(customer_session)):
    return save_profile(session.customer_id, session.shop_id, payload.model_dump(exclude_none=True))


def history_dict(row):
    return {"id": row.message_id, "sender": row.sender, "content": row.message_text,
            "timestamp": row.timestamp.isoformat() if row.timestamp else None}


@app.get("/api/messages")
def messages(session=Depends(customer_session)):
    with transaction() as db:
        rows = db.query(ChatHistory).filter_by(customer_id=session.customer_id, shop_id=session.shop_id).order_by(ChatHistory.message_id.desc()).limit(100).all()
        turns = db.query(ChatTurn).filter_by(customer_id=session.customer_id).order_by(ChatTurn.id.desc()).limit(50).all()
        responses = {t.response.get("message_id"): t.response for t in turns}
        return [{**history_dict(row), **({"metadata": responses[row.message_id]} if row.message_id in responses else {})} for row in reversed(rows)]


@app.post("/api/voice/transcribe")
def voice_transcribe(payload: VoiceInput, request: Request, session=Depends(customer_session)):
    rate_limit(f"voice:{session.customer_id}", 8)
    return transcribe_audio(payload.audio)


@app.post("/api/chat")
def chat(payload: ChatInput, background: BackgroundTasks, session=Depends(customer_session)):
    rate_limit(f"chat:{session.customer_id}", 20)
    validate_image(payload.image)
    query = payload.message.strip()
    if not query:
        raise HTTPException(422, "Please type a message")
    if re.search(r"(.)\1{40,}", query):
        raise HTTPException(422, "Please avoid repeated-character spam")
    # Authorization and transactional tools are the security boundary, not keyword blocking.
    channel = getattr(session, "channel", "web")
    feeling = mood.classify(query)
    with chat_locks[session.customer_id % len(chat_locks)]:
        with transaction() as db:
            existing = db.query(ChatTurn).filter_by(customer_id=session.customer_id, request_id=str(payload.request_id)).first()
            if existing:
                return existing.response
            customer = get_customer(db, session.customer_id, session.shop_id)
            handoff = customer.human_intervention
            history, previous_sources, conversation_context = load_conversation(db, session.customer_id, session.shop_id)
            state = conversation_snapshot(db, session.customer_id)
        streak = state["negative_streak"] + 1 if feeling.negative else 0
        conversation_context.update(
            sentiment={**feeling.as_dict(), "escalate": mood.needs_escalation(feeling, streak)},
            channel=channel, visual_context=state["visual_context"])
        if handoff:
            response = {"answer": "", "status": "queued_for_human",
                        "sources": [], "drafts": [], "trace": [{"node": "handoff", "detail": "Routed to human support"}],
                        "route": "human", "degraded": False, "usage": {}, "latency_ms": 0}
        else:
            try:
                response = agent.run_agent(session.customer_id, session.shop_id, query, history, previous_sources, payload.image, str(payload.request_id), conversation_context)
            except SQLAlchemyError:
                raise HTTPException(503, "The data service is temporarily unavailable. Your request was not confirmed; please try again shortly.") from None
            except Exception:
                log.warning("Agent runtime failed; routing request %s to human recovery", payload.request_id)
                case = open_case(session.customer_id, session.shop_id, query, reason="runtime_failure")
                answer = f"সাপোর্ট কেস #{case['case_id']} খুলেছি। এই বিষয়টি টিম দেখবে; আপাতত AI বন্ধ আছে।" if is_bangla(query) else f"I couldn't safely finish that request. I've opened support case #{case['case_id']} for the team and paused the AI."
                response = {"answer": answer, "handoff": case, "sources": [], "drafts": [],
                    "trace": [{"node": "handoff", "detail": "Runtime recovery queued for human review"}],
                    "route": "human", "degraded": True, "usage": {}, "latency_ms": 0}
        with transaction() as db:
            paused_now = get_customer(db, session.customer_id, session.shop_id).human_intervention
            if paused_now and not response.get("handoff"):
                response.update(answer="", status="queued_for_human", route="human")
            user_row = ChatHistory(sender="user", message_text=query + ("\n[Image attached]" if payload.image else ""), customer_id=session.customer_id, shop_id=session.shop_id)
            db.add(user_row)
            db.flush()
            record_sentiment(db, session.customer_id, session.shop_id, feeling, channel, user_row.message_id)
            remember_turn(db, session.customer_id, session.shop_id, response, channel)
            response.setdefault("sentiment", {**feeling.as_dict(), "escalate": conversation_context["sentiment"]["escalate"]})
            reply = None
            if response["answer"]:
                reply = ChatHistory(sender="ai", message_text=response["answer"], customer_id=session.customer_id, shop_id=session.shop_id)
                db.add(reply)
            db.flush()
            response["message_id"] = reply.message_id if reply else None
            response["human_intervention"] = bool(get_customer(db, session.customer_id, session.shop_id).human_intervention)
            db.add(ChatTurn(customer_id=session.customer_id, request_id=str(payload.request_id), response=response))
        background.add_task(deliver_notifications)
        log.info("chat route=%s latency_ms=%s tokens=%s degraded=%s", response["route"], response["latency_ms"], response["usage"].get("total_tokens", 0), response["degraded"])
        return response


@app.get("/api/products/{product_id}/image")
def product_image(product_id: str):
    product, image = product_image_record(product_id, settings.shop_id)
    if not product:
        raise HTTPException(404, "Product not found")
    if image and image.image_url:
        parsed = urlparse(image.image_url)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return RedirectResponse(image.image_url, status_code=307)
    return Response(generated_product_svg(product), media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/products")
def catalog(q: str = "", category: str = "", session=Depends(customer_session)):
    if q.strip():
        if len(q) > 500:
            raise HTTPException(422, "Search query too long")
        return [s["data"] for s in retriever.search(q, session.shop_id, "product", 30)]
    with transaction() as db:
        query = db.query(Product).filter_by(shop_id=session.shop_id)
        if category:
            query = query.filter_by(category=category)
        return [product_dict(p) for p in query.order_by(Product.name).limit(500)]


@app.get("/api/public/products/{product_id}")
def public_product(product_id: str):
    """Read-only public product detail for Messenger links; no customer/session data."""
    with transaction() as db:
        product = db.query(Product).filter_by(product_id=product_id, shop_id=settings.shop_id).first()
        if not product:
            raise HTTPException(404, "Product not found")
        image = db.query(ProductImage).filter_by(product_id=product_id, shop_id=settings.shop_id).first()
        data = product_dict(product)
        parsed_image = urlparse(image.image_url or "") if image else None
        has_image = bool(parsed_image and parsed_image.scheme in ("http", "https") and parsed_image.netloc)
        representative = bool(has_image and re.search(r"representative|illustration", image.alt_text or "", re.I))
        return {**data, "image_kind": "representative" if representative else "photo" if has_image else "illustration",
                "image_alt": image.alt_text if has_image else f"{product.name} illustration"}


@app.get("/api/policies")
def policies(session=Depends(customer_session)):
    with transaction() as db:
        return [{"policy_id": p.policy_id, "policy_type": p.policy_type, "policy_text": p.policy_text} for p in db.query(Policy).filter_by(shop_id=session.shop_id)]


@app.post("/api/checkout")
def checkout(payload: DraftInput, session=Depends(customer_session)):
    return prepare_order(session.customer_id, session.shop_id, payload.product_id, payload.quantity)


@app.post("/api/checkout/{draft_id}/confirm")
def confirm(draft_id: str, background: BackgroundTasks, payload: CheckoutConfirm | None = None,
            session=Depends(customer_session)):
    contact = payload.model_dump(exclude_none=True) if payload else {}
    receipt = confirm_order(session.customer_id, session.shop_id, draft_id,
                            phone=contact.get("phone"), address=contact.get("address"), name=contact.get("name"))
    background.add_task(deliver_notifications)
    return receipt


@app.get("/api/checkout")
def active_checkout(session=Depends(customer_session)):
    with transaction() as db:
        rows = db.query(OrderDraft).filter_by(customer_id=session.customer_id, shop_id=session.shop_id, order_id=None).filter(OrderDraft.expires_at > datetime.utcnow()).order_by(OrderDraft.expires_at.desc()).limit(10)
        return [draft_dict(db, draft) for draft in rows]


@app.get("/api/orders")
def my_orders(session=Depends(customer_session)):
    with transaction() as db:
        orders = db.query(Order).filter_by(customer_id=session.customer_id, shop_id=session.shop_id).order_by(Order.order_id.desc()).limit(100)
        notices = {n.dedupe_key: n.status for n in db.query(Notification).filter(Notification.dedupe_key.like("order:%"))}
        return [order_dict(o, notices.get(f"order:{o.order_id}", "disabled")) for o in orders]


@app.post("/api/support")
def request_support(payload: ReplyInput, background: BackgroundTasks, session=Depends(customer_session)):
    result = escalate(session.customer_id, session.shop_id, payload.message)
    background.add_task(deliver_notifications)
    return result


@app.post("/api/admin/login")
def admin_login(payload: AdminLogin, request: Request):
    rate_limit(f"login:{request.client.host}", 8)
    with transaction() as db:
        admin = db.query(Admin).filter_by(email=payload.email).first()
        # Do bcrypt work even for unknown users to reduce account enumeration timing.
        dummy = "$2b$12$C6UzMDM.H6dfI/f/IKcEe.yr.RpHcQjH31YcB0TvA7D6OaYpjOF9e"
        valid = verify_password(payload.password, admin.password_hash if admin else dummy)
        if not admin or not valid:
            raise HTTPException(401, "Invalid email or password")
        return {"token": issue_session(db, "admin", admin.shop_id, admin_id=admin.admin_id), "name": admin.name}


@app.get("/api/admin/dashboard")
def dashboard(session=Depends(admin_session)):
    with transaction() as db:
        products = [product_dict(p) for p in db.query(Product).filter_by(shop_id=session.shop_id)]
        orders = [dict(order_dict(o), customer_id=o.customer_id) for o in db.query(Order).filter_by(shop_id=session.shop_id).order_by(Order.order_id.desc()).limit(500)]
        customers = [profile_dict(c) for c in db.query(Customer).filter_by(shop_id=session.shop_id).order_by(Customer.customer_id.desc()).limit(500)]
        cases = list_cases(db, session.shop_id)
        policies = [{"policy_id": p.policy_id, "policy_type": p.policy_type, "policy_text": p.policy_text} for p in db.query(Policy).filter_by(shop_id=session.shop_id)]
        turns = db.query(ChatTurn).join(Customer, ChatTurn.customer_id == Customer.customer_id).filter(
            Customer.shop_id == session.shop_id).order_by(ChatTurn.id.desc()).limit(5000).all()
        ai_usage = aggregate_usage(turn.response.get("usage") for turn in turns if isinstance(turn.response, dict))
        return {"products": products, "orders": orders, "customers": customers, "cases": cases, "policies": policies,
                "ai_usage": ai_usage, "escalation_alerts": escalation_alerts(db, session.shop_id),
                "sentiment": sentiment_overview(db, session.shop_id),
                "metrics": {"orders": db.query(Order).filter_by(shop_id=session.shop_id).count(),
                            "customers": db.query(Customer).filter_by(shop_id=session.shop_id).count(),
                            "open_cases": db.query(Escalation).filter_by(shop_id=session.shop_id).filter(Escalation.status.in_(["Open", "Pending"])).count()}}


def escalation_alerts(db, shop_id, limit=50):
    """Open escalations, newest/urgent first, with the triage context the bot captured."""
    from backend.records import SupportCase
    rows = (db.query(Escalation, SupportCase).join(SupportCase, SupportCase.case_id == Escalation.case_id)
            .filter(Escalation.shop_id == shop_id, Escalation.status.in_(["Open", "Pending"]))
            .order_by(Escalation.case_id.desc()).limit(limit).all())
    alerts = []
    for case, detail in rows:
        context = (detail.context or {}).get("escalation") or {}
        sentiment = context.get("sentiment") or {}
        alerts.append({
            "case_id": case.case_id, "customer_id": case.customer_id, "priority": detail.priority,
            "category": detail.category, "trigger": context.get("trigger_label") or detail.reason,
            "channel": context.get("channel", "web"), "sentiment": sentiment.get("label", "NEUTRAL"),
            "sentiment_score": sentiment.get("score"), "products": context.get("product_context", []),
            "image_context": context.get("visual_context", {}), "conversation_summary": context.get("conversation_summary", ""),
            "last_message": context.get("last_message") or case.issue_summary,
            "created_at": case.created_at.isoformat() if case.created_at else None,
            "sla_due_at": detail.sla_due_at.isoformat() if detail.sla_due_at else None})
    alerts.sort(key=lambda alert: alert["priority"] != "urgent")
    return alerts


def sentiment_overview(db, shop_id, days=7):
    from datetime import timedelta
    from sqlalchemy import func
    from backend.records import ConversationState, MessageSentiment
    since = datetime.utcnow() - timedelta(days=days)
    counts = dict(db.query(MessageSentiment.label, func.count(MessageSentiment.id)).filter(
        MessageSentiment.shop_id == shop_id, MessageSentiment.created_at >= since).group_by(MessageSentiment.label).all())
    average = db.query(func.avg(MessageSentiment.score)).filter(
        MessageSentiment.shop_id == shop_id, MessageSentiment.created_at >= since).scalar()
    escalated = db.query(ConversationState).filter_by(shop_id=shop_id, status="ESCALATED").count()
    return {"window_days": days, "counts": {label: counts.get(label, 0) for label in ("POSITIVE", "NEUTRAL", "NEGATIVE", "FRUSTRATED")},
            "average_score": round(float(average), 3) if average is not None else None,
            "escalated_conversations": escalated}


@app.get("/api/admin/escalations")
def admin_escalations(session=Depends(admin_session)):
    with transaction() as db:
        return escalation_alerts(db, session.shop_id)


@app.get("/api/admin/sentiment")
def admin_sentiment(days: int = 7, session=Depends(admin_session)):
    with transaction() as db:
        return sentiment_overview(db, session.shop_id, max(1, min(days, 90)))


@app.get("/api/admin/orders/{order_id}/details")
def admin_order_details(order_id: int, session=Depends(admin_session)):
    """Variant / phone / delivery address captured by the Messenger order form."""
    from backend.records import OrderDetail
    with transaction() as db:
        order = db.query(Order).filter_by(order_id=order_id, shop_id=session.shop_id).first()
        if not order:
            raise HTTPException(404, "Order not found")
        detail = db.get(OrderDetail, order_id)
        return {**order_dict(order), "details": None if not detail else {
            "order_code": detail.order_code, "channel": detail.channel, "variant": detail.variant,
            "customer_name": detail.customer_name, "phone": detail.phone, "delivery_address": detail.delivery_address}}


@app.put("/api/admin/products/{product_id}")
def upsert_product(product_id: str, payload: ProductInput, session=Depends(admin_session)):
    if payload.discounted_price is not None and payload.discounted_price > payload.price:
        raise HTTPException(422, "Discounted price cannot exceed list price")
    with transaction() as db:
        product = db.get(Product, product_id)
        if product and product.shop_id != session.shop_id:
            raise HTTPException(404, "Product not found")
        if not product:
            product = Product(product_id=product_id, shop_id=session.shop_id)
            db.add(product)
        for key, value in payload.model_dump().items():
            setattr(product, key, value)
        product.status = "In Stock" if product.stock_count else "Out of Stock"
        db.flush()
        return product_dict(product)


@app.put("/api/admin/products/{product_id}/image")
def set_product_image(product_id: str, payload: ImageInput, session=Depends(admin_session)):
    if payload.image_url:
        parsed = urlparse(payload.image_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise HTTPException(422, "Image URL must be an absolute http(s) URL")
    with transaction() as db:
        product = db.query(Product).filter_by(product_id=product_id, shop_id=session.shop_id).first()
        if not product:
            raise HTTPException(404, "Product not found")
        image = db.get(ProductImage, product_id)
        if payload.image_url:
            if not image:
                image = ProductImage(product_id=product_id, shop_id=session.shop_id, image_url=payload.image_url)
                db.add(image)
            image.image_url = payload.image_url
            image.alt_text = payload.alt_text
            image.updated_at = datetime.utcnow()
            audit(db, session.shop_id, session.admin_id, "product.image.updated", product_id, "Product media update", target_type="product")
        elif image:
            db.delete(image)
            audit(db, session.shop_id, session.admin_id, "product.image.removed", product_id, "Product media cleared", target_type="product")
        return {"product_id": product_id, "image_url": payload.image_url}


@app.post("/api/admin/policies", status_code=201)
def add_policy(payload: PolicyInput, session=Depends(admin_session)):
    with transaction() as db:
        policy = Policy(**payload.model_dump(), shop_id=session.shop_id)
        db.add(policy)
        db.flush()
        return {"policy_id": policy.policy_id}


@app.put("/api/admin/policies/{policy_id}")
def edit_policy(policy_id: int, payload: PolicyInput, session=Depends(admin_session)):
    with transaction() as db:
        policy = db.query(Policy).filter_by(policy_id=policy_id, shop_id=session.shop_id).first()
        if not policy:
            raise HTTPException(404, "Policy not found")
        policy.policy_type, policy.policy_text = payload.policy_type, payload.policy_text
        return {"policy_id": policy_id}


@app.patch("/api/admin/orders/{order_id}")
def update_order(order_id: int, payload: OrderStatusInput, session=Depends(admin_session)):
    transitions = {"Pending": {"Processing", "Cancelled"}, "Processing": {"Shipped", "Cancelled"}, "Shipped": {"Delivered"}, "Delivered": set(), "Cancelled": set()}
    with transaction() as db:
        order = db.query(Order).filter_by(order_id=order_id, shop_id=session.shop_id).first()
        if not order:
            raise HTTPException(404, "Order not found")
        if order.status == payload.status:
            return order_dict(order)
        if payload.status not in transitions.get(order.status, set()):
            raise HTTPException(409, f"Cannot change {order.status} to {payload.status}")
        changed = db.execute(update(Order).where(Order.order_id == order_id, Order.shop_id == session.shop_id, Order.status == order.status).values(status=payload.status))
        if changed.rowcount != 1:
            raise HTTPException(409, "Order changed concurrently; refresh and retry")
        if payload.status == "Cancelled" and order.product_id:
            db.execute(update(Product).where(Product.product_id == order.product_id, Product.shop_id == session.shop_id).values(stock_count=Product.stock_count + order.quantity))
        db.refresh(order)
        return order_dict(order)


@app.get("/api/admin/customers/{customer_id}/messages")
def admin_messages(customer_id: int, session=Depends(admin_session)):
    with transaction() as db:
        get_customer(db, customer_id, session.shop_id)
        rows = db.query(ChatHistory).filter_by(customer_id=customer_id, shop_id=session.shop_id).order_by(ChatHistory.message_id.desc()).limit(100).all()
        return [history_dict(row) for row in reversed(rows)]


@app.patch("/api/admin/customers/{customer_id}/handoff")
def handoff(customer_id: int, payload: HandoffInput, session=Depends(admin_session)):
    if payload.enabled:
        # Pause immediately; do not wait behind a slow, in-flight model request.
        # The chat commit path rechecks this flag and suppresses late AI output.
        open_case(customer_id, session.shop_id, payload.note or "Operator took over the conversation", reason="operator_takeover")
        with transaction() as db:
            audit(db, session.shop_id, session.admin_id, "conversation.paused", customer_id, payload.note or "Operator takeover", target_type="customer")
            return profile_dict(get_customer(db, customer_id, session.shop_id))
    with chat_locks[customer_id % len(chat_locks)]:
        for thread in graph_threads(customer_id, session.shop_id):
            agent.resume_handoff(thread, payload.note)
        return resume_customer(customer_id, session.shop_id, session.admin_id, payload.note)


@app.post("/api/admin/customers/{customer_id}/messages", status_code=201)
def admin_reply(customer_id: int, payload: ReplyInput, session=Depends(admin_session)):
    if not payload.message.strip():
        raise HTTPException(422, "Message cannot be blank")
    with transaction() as db:
        customer = get_customer(db, customer_id, session.shop_id)
        if not customer.human_intervention:
            raise HTTPException(409, "Take over this conversation before replying")
        row = ChatHistory(customer_id=customer_id, shop_id=session.shop_id, sender="admin", message_text=payload.message.strip())
        db.add(row)
        audit(db, session.shop_id, session.admin_id, "conversation.reply", customer_id, "Human support reply", target_type="customer")
        db.flush()
        return history_dict(row)


@app.post("/api/admin/cases/{case_id}/resolve")
def resolve_case(case_id: int, payload: CaseResolution = CaseResolution(), session=Depends(admin_session)):
    with transaction() as db:
        case = db.query(Escalation).filter_by(case_id=case_id, shop_id=session.shop_id).first()
        if not case:
            raise HTTPException(404, "Case not found")
        customer_id = case.customer_id
    with chat_locks[customer_id % len(chat_locks)]:
        for thread in graph_threads(customer_id, session.shop_id, case_id):
            agent.resume_handoff(thread, payload.note)
        resume_customer(customer_id, session.shop_id, session.admin_id, payload.note, payload.resolution_code, case_id)
    return {"case_id": case_id, "status": "Resolved"}


@app.post("/api/admin/knowledge/refresh")
def refresh_knowledge(session=Depends(admin_session)):
    retriever.invalidate(session.shop_id)
    retriever.warm(session.shop_id)
    return {"dense_status": retriever.dense_status, "lexical_status": "live"}


@app.post("/api/admin/notifications/retry")
def retry_notifications(background: BackgroundTasks, session=Depends(admin_session)):
    background.add_task(deliver_notifications)
    return {"status": "scheduled"}


# A production build is served by the same origin. No node server is needed then.
DIST = ROOT / "frontend" / "dist"


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "API endpoint not found")
    candidate = (DIST / path).resolve()
    if candidate.is_relative_to(DIST.resolve()) and candidate.is_file():
        return FileResponse(candidate)
    if (path == "admin" or path.startswith("admin/")) and (DIST / "admin.html").exists():
        return FileResponse(DIST / "admin.html")
    if (DIST / "index.html").exists():
        return FileResponse(DIST / "index.html")
    raise HTTPException(404, "React build missing. Run npm --prefix frontend install and npm --prefix frontend run build.")
