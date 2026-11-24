import hashlib
from uuid import uuid4

from fastapi import HTTPException

from backend.database import transaction
from backend.records import KnowledgeDocument, KnowledgeVersion
from backend.support import audit


def document_dict(db, document):
    versions = db.query(KnowledgeVersion).filter_by(document_id=document.document_id).order_by(KnowledgeVersion.version.desc()).all()
    return {"document_id": document.document_id, "title": document.title, "category": document.category,
            "locale": document.locale, "published_version": document.published_version,
            "versions": [{"version": v.version, "content": v.content, "checksum": v.checksum, "created_at": v.created_at.isoformat()} for v in versions]}


def save_version(shop_id, actor_id, values, document_id=None):
    with transaction() as db:
        document = db.query(KnowledgeDocument).filter_by(document_id=document_id, shop_id=shop_id).with_for_update().first() if document_id else None
        if document_id and not document:
            raise HTTPException(404, "Knowledge document not found")
        if not document:
            document = KnowledgeDocument(document_id=str(uuid4()), shop_id=shop_id,
                title=values["title"], category=values["category"], locale=values["locale"])
            db.add(document)
            db.flush()
        elif (document.title, document.category, document.locale) != (values["title"], values["category"], values["locale"]):
            raise HTTPException(409, "Document identity is immutable; create another document to change title/category/locale")
        digest = hashlib.sha256(values["content"].encode()).hexdigest()
        latest = db.query(KnowledgeVersion).filter_by(document_id=document.document_id).order_by(KnowledgeVersion.version.desc()).first()
        if not latest or latest.checksum != digest:
            db.add(KnowledgeVersion(document_id=document.document_id, version=(latest.version + 1 if latest else 1),
                content=values["content"], checksum=digest, created_by=actor_id))
            db.flush()
            audit(db, shop_id, actor_id, "knowledge.draft.saved", document.document_id, values["reason"], target_type="knowledge")
        return document_dict(db, document)


def publish(shop_id, actor_id, document_id, version, reason):
    with transaction() as db:
        document = db.query(KnowledgeDocument).filter_by(document_id=document_id, shop_id=shop_id).with_for_update().first()
        if not document or not db.get(KnowledgeVersion, (document_id, version)):
            raise HTTPException(404, "Knowledge version not found")
        before = document.published_version
        document.published_version = version
        audit(db, shop_id, actor_id, "knowledge.published", document_id, reason,
              {"before": before, "after": version}, target_type="knowledge")
        return document_dict(db, document)
