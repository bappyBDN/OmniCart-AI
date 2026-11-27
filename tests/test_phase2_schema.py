"""Phase 2 schema contracts that must hold in SQLite tests and PostgreSQL."""
from pathlib import Path

from sqlalchemy import Numeric

from backend.database import transaction
from backend.records import (EdgeCaseQA, KnowledgeDocument, KnowledgeVersion,
                             OrderDraft, ProductImage)
from models import Order, Product


def column_names(table):
    return list(table.primary_key.columns.keys())


def test_product_primary_key_is_tenant_local():
    assert column_names(Product.__table__) == ["shop_id", "product_id"]
    assert column_names(ProductImage.__table__) == ["shop_id", "product_id"]
    assert column_names(EdgeCaseQA.__table__) == ["shop_id", "source_id"]
    assert column_names(KnowledgeDocument.__table__) == ["shop_id", "document_id"]
    assert column_names(KnowledgeVersion.__table__) == ["shop_id", "document_id", "version"]


def test_money_columns_are_exact_numeric_and_snapshots_exist():
    for table, columns in (
        (Product.__table__, ("price", "discounted_price")),
        (Order.__table__, ("price", "total_price", "unit_price_snapshot", "line_total_snapshot")),
        (OrderDraft.__table__, ("unit_price", "unit_price_snapshot", "line_total_snapshot")),
    ):
        for column in columns:
            assert isinstance(table.c[column].type, Numeric), f"{table.name}.{column}"
    assert "currency" in Order.__table__.c
    assert "currency" in OrderDraft.__table__.c


def test_two_shops_can_reuse_the_same_product_and_knowledge_ids(store):
    with transaction() as db:
        db.add(Product(product_id="SKU_101", shop_id="other", name="Other shop shirt",
                       category="Apparel", price=5, stock_count=1))
        db.add(EdgeCaseQA(source_id="size_001", shop_id="other", category="size",
                          intent="fit", queries={"en": "size"}, responses={"en": "answer"}))
        db.add(KnowledgeDocument(document_id="doc-1", shop_id="other", title="Other policy"))
        db.flush()
        db.add(KnowledgeVersion(shop_id="other", document_id="doc-1", version=1,
                                content="text", checksum="abc", created_by=1))

    with transaction() as db:
        assert db.get(Product, ("shop_01", "SKU_101")).name == "Premium Cotton T-Shirt"
        assert db.get(Product, ("other", "SKU_101")).name == "Other shop shirt"
        assert db.get(EdgeCaseQA, ("other", "size_001")).category == "size"
        assert db.get(KnowledgeDocument, ("other", "doc-1")).title == "Other policy"
        assert db.get(KnowledgeVersion, ("other", "doc-1", 1)).checksum == "abc"


def test_phase2_migration_contains_required_postgres_objects():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "migrations" / "versions" / "0001_phase2_production_schema.py").read_text(encoding="utf-8")
    for statement in (
        "CREATE EXTENSION IF NOT EXISTS vector",
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        "CREATE TABLE IF NOT EXISTS shop_config",
        "CREATE TABLE IF NOT EXISTS shop_channels",
        "CREATE TABLE IF NOT EXISTS product_variants",
        "CREATE TABLE IF NOT EXISTS knowledge_chunks",
        "CREATE TABLE IF NOT EXISTS product_image_embeddings",
        "CREATE TABLE IF NOT EXISTS shop_synonyms",
        "CREATE TABLE IF NOT EXISTS image_assets",
        "CREATE TABLE IF NOT EXISTS job_queue",
        "CREATE TABLE IF NOT EXISTS outbound_messages",
        "CREATE TABLE IF NOT EXISTS failed_queries",
        "CREATE TABLE IF NOT EXISTS eval_cases",
        "CREATE TABLE IF NOT EXISTS eval_runs",
        "USING hnsw (embedding vector_cosine_ops)",
        "USING gin (tsv)",
        "ix_job_queue_ready",
        "PRIMARY KEY (shop_id, product_id)",
    ):
        assert statement in migration
    assert (root / "alembic.ini").is_file()
