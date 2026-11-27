"""Multilingual edge-case support Q&A ingested into RAG.

Each entry is one self-contained semantic chunk (a query/response pair) and is
tagged with category + intent. Language mirroring indexes every query form
(English, Bangla, Banglish) so a customer question in any script retrieves the
same answer; the agent then replies in the customer's language.
"""
import json

from langchain_core.documents import Document

from backend.config import ROOT
from backend.records import EdgeCaseQA

LOCALES = ("en", "bn", "banglish")


def load_edgecases(root=ROOT):
    entries = json.loads((root / "Data" / "edgecase.json").read_text(encoding="utf-8"))
    for entry in entries:
        if not entry.get("id") or not entry.get("category") or not entry.get("intent"):
            raise ValueError("Edge case missing id/category/intent")
        for locale in LOCALES:
            if not entry.get("queries", {}).get(locale) or not entry.get("responses", {}).get(locale):
                raise ValueError(f"Edge case {entry.get('id')} is missing {locale}")
    return entries


def import_edgecases(db, shop_id, entries):
    existing = {row.source_id for row in db.query(EdgeCaseQA).filter_by(shop_id=shop_id)}
    for entry in entries:
        row = db.get(EdgeCaseQA, (shop_id, entry["id"]))
        if row and row.shop_id != shop_id:
            raise ValueError(f"Edge case {entry['id']} belongs to another shop")
        if not row:
            row = EdgeCaseQA(source_id=entry["id"], shop_id=shop_id)
            db.add(row)
        row.category = entry["category"]
        row.intent = entry["intent"]
        row.queries = entry["queries"]
        row.responses = entry["responses"]
        existing.discard(entry["id"])
    return len(entries)


def edgecase_documents(db, shop_id):
    documents = []
    for row in db.query(EdgeCaseQA).filter_by(shop_id=shop_id).all():
        query_text = " ".join(row.queries.get(locale, "") for locale in LOCALES)
        intent_text = f"{row.category} {row.intent}".replace("_", " ")
        embedding_text = f"{query_text} {intent_text}"
        documents.append(Document(
            page_content=json.dumps({"queries": row.queries, "responses": row.responses}, ensure_ascii=False),
            metadata={
                "id": f"edgecase:{row.source_id}", "kind": "policy", "title": row.queries.get("en", row.source_id),
                "shop_id": shop_id, "source_type": "edgecase", "locale": "multi",
                "category": row.category, "intent": row.intent,
                "embedding_text": embedding_text, "keywords": embedding_text,
                "data": {"queries": row.queries, "responses": row.responses, "category": row.category, "intent": row.intent},
            },
        ))
    return documents
