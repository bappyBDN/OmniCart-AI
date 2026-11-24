from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, LargeBinary, String, Text, UniqueConstraint

from models import Base


class AccessSession(Base):
    __tablename__ = "api_access_sessions"
    token_hash = Column(String, primary_key=True)
    role = Column(String, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.customer_id"))
    admin_id = Column(Integer, ForeignKey("admins.admin_id"))
    shop_id = Column(String, ForeignKey("shops.shop_id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)


class ChatTurn(Base):
    __tablename__ = "api_chat_turns"
    __table_args__ = (UniqueConstraint("customer_id", "request_id"),)
    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    request_id = Column(String, nullable=False)
    response = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class OrderDraft(Base):
    __tablename__ = "api_order_drafts"
    draft_id = Column(String, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    shop_id = Column(String, ForeignKey("shops.shop_id"), nullable=False)
    product_id = Column(String, ForeignKey("products.product_id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(String, nullable=False)
    order_id = Column(Integer, ForeignKey("orders.order_id"))
    expires_at = Column(DateTime, nullable=False)


class ProductImage(Base):
    __tablename__ = "api_product_images"
    product_id = Column(String, ForeignKey("products.product_id"), primary_key=True)
    shop_id = Column(String, ForeignKey("shops.shop_id"), nullable=False, index=True)
    image_url = Column(String, nullable=False)
    alt_text = Column(String, nullable=False, default="Product image")
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Notification(Base):
    __tablename__ = "api_notifications"
    id = Column(Integer, primary_key=True)
    dedupe_key = Column(String, nullable=False, unique=True)
    recipient = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    status = Column(String, default="queued", nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class SupportCase(Base):
    __tablename__ = "support_cases"
    case_id = Column(Integer, ForeignKey("escalations.case_id"), primary_key=True)
    shop_id = Column(String, ForeignKey("shops.shop_id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False, index=True)
    category = Column(String, nullable=False, default="support")
    priority = Column(String, nullable=False, default="normal")
    reason = Column(String, nullable=False)
    assigned_admin_id = Column(Integer, ForeignKey("admins.admin_id"))
    context = Column(JSON, nullable=False, default=dict)
    sla_due_at = Column(DateTime, nullable=False)
    resolution_code = Column(String)
    resolved_at = Column(DateTime)


class SupportNote(Base):
    __tablename__ = "support_notes"
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("escalations.case_id"), nullable=False, index=True)
    admin_id = Column(Integer, ForeignKey("admins.admin_id"), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ConversationControl(Base):
    __tablename__ = "conversation_controls"
    customer_id = Column(Integer, ForeignKey("customers.customer_id"), primary_key=True)
    shop_id = Column(String, ForeignKey("shops.shop_id"), nullable=False)
    resume_note = Column(Text, nullable=False, default="")
    unresolved_turns = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AuditEvent(Base):
    __tablename__ = "admin_audit_events"
    id = Column(Integer, primary_key=True)
    shop_id = Column(String, ForeignKey("shops.shop_id"), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey("admins.admin_id"))
    action = Column(String, nullable=False)
    target_type = Column(String, nullable=False)
    target_id = Column(String, nullable=False)
    reason = Column(Text, nullable=False)
    changes = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ProductAlias(Base):
    __tablename__ = "product_aliases"
    __table_args__ = (UniqueConstraint("shop_id", "product_id", "alias"),)
    id = Column(Integer, primary_key=True)
    product_id = Column(String, ForeignKey("products.product_id"), nullable=False, index=True)
    shop_id = Column(String, ForeignKey("shops.shop_id"), nullable=False, index=True)
    alias = Column(String, nullable=False)
    locale = Column(String, nullable=False, default="bn-Latn")


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    document_id = Column(String, primary_key=True)
    shop_id = Column(String, ForeignKey("shops.shop_id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    category = Column(String, nullable=False, default="faq")
    locale = Column(String, nullable=False, default="en")
    published_version = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class KnowledgeVersion(Base):
    __tablename__ = "knowledge_versions"
    document_id = Column(String, ForeignKey("knowledge_documents.document_id"), primary_key=True)
    version = Column(Integer, primary_key=True)
    content = Column(Text, nullable=False)
    checksum = Column(String, nullable=False)
    created_by = Column(Integer, ForeignKey("admins.admin_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class GraphCheckpoint(Base):
    __tablename__ = "agent_checkpoints"
    thread_id = Column(String, primary_key=True)
    namespace = Column(String, primary_key=True, default="")
    checkpoint_id = Column(String, primary_key=True)
    parent_id = Column(String)
    payload_type = Column(String, nullable=False)
    payload = Column(LargeBinary, nullable=False)
    metadata_type = Column(String, nullable=False)
    metadata_blob = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class GraphWrite(Base):
    __tablename__ = "agent_checkpoint_writes"
    thread_id = Column(String, primary_key=True)
    namespace = Column(String, primary_key=True, default="")
    checkpoint_id = Column(String, primary_key=True)
    task_id = Column(String, primary_key=True)
    idx = Column(Integer, primary_key=True)
    channel = Column(String, nullable=False)
    task_path = Column(String, nullable=False, default="")
    payload_type = Column(String, nullable=False)
    payload = Column(LargeBinary, nullable=False)
