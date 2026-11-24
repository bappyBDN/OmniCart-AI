from fastapi import APIRouter, Depends, HTTPException

from backend.auth import admin_session
from backend.database import transaction
from backend.knowledge import document_dict, publish, save_version
from backend.language import normalize_query
from backend.records import AuditEvent, KnowledgeDocument, ProductAlias
from backend.retrieval import retriever
from backend.schemas import AliasInput, CaseUpdate, KnowledgeInput, KnowledgePublish, RetrievalPreview
from backend.support import audit, case_context, update_case
from models import Admin, Product

router = APIRouter(prefix="/api")


@router.get("/admin/cases/{case_id}/context")
def support_context(case_id: int, session=Depends(admin_session)):
    return case_context(case_id, session.shop_id, session.admin_id)


@router.patch("/admin/cases/{case_id}")
def patch_case(case_id: int, payload: CaseUpdate, session=Depends(admin_session)):
    return update_case(case_id, session.shop_id, session.admin_id, payload.model_dump(exclude_unset=True))


@router.get("/admin/team")
def team(session=Depends(admin_session)):
    with transaction() as db:
        return [{"admin_id": a.admin_id, "name": a.name, "role": a.role} for a in db.query(Admin).filter_by(shop_id=session.shop_id)]


@router.get("/admin/knowledge/documents")
def knowledge_documents(session=Depends(admin_session)):
    with transaction() as db:
        return [document_dict(db, d) for d in db.query(KnowledgeDocument).filter_by(shop_id=session.shop_id)]


@router.post("/admin/knowledge/documents", status_code=201)
def add_document(payload: KnowledgeInput, session=Depends(admin_session)):
    return save_version(session.shop_id, session.admin_id, payload.model_dump())


@router.post("/admin/knowledge/documents/{document_id}/versions")
def add_version(document_id: str, payload: KnowledgeInput, session=Depends(admin_session)):
    return save_version(session.shop_id, session.admin_id, payload.model_dump(), document_id)


@router.post("/admin/knowledge/documents/{document_id}/publish")
def publish_version(document_id: str, payload: KnowledgePublish, session=Depends(admin_session)):
    return publish(session.shop_id, session.admin_id, document_id, payload.version, payload.reason)


@router.post("/admin/knowledge/preview")
def preview_retrieval(payload: RetrievalPreview, session=Depends(admin_session)):
    query = normalize_query(payload.query)
    return {"normalized": query.text, "locale": query.locale, "sources": retriever.search(payload.query, session.shop_id, payload.kind, 5)}


@router.post("/admin/products/{product_id}/aliases", status_code=201)
def add_alias(product_id: str, payload: AliasInput, session=Depends(admin_session)):
    with transaction() as db:
        if not db.query(Product).filter_by(product_id=product_id, shop_id=session.shop_id).first():
            raise HTTPException(404, "Product not found")
        alias = payload.alias.strip()
        if not alias:
            raise HTTPException(422, "Alias cannot be blank")
        record = db.query(ProductAlias).filter_by(product_id=product_id, shop_id=session.shop_id, alias=alias).first()
        if not record:
            record = ProductAlias(product_id=product_id, shop_id=session.shop_id, alias=alias, locale=payload.locale)
            db.add(record)
            audit(db, session.shop_id, session.admin_id, "product.alias.added", product_id, "Search alias curation", target_type="product")
            db.flush()
        return {"id": record.id, "alias": alias}


@router.get("/admin/audit")
def audit_events(session=Depends(admin_session)):
    with transaction() as db:
        return [{"id": row.id, "actor_id": row.actor_id, "action": row.action, "target_type": row.target_type,
                 "target_id": row.target_id, "reason": row.reason, "changes": row.changes, "created_at": row.created_at.isoformat()}
                for row in db.query(AuditEvent).filter_by(shop_id=session.shop_id).order_by(AuditEvent.id.desc()).limit(300)]
