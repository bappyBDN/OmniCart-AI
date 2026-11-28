"""Safely apply and verify the Phase 2 PostgreSQL schema on a Neon branch.

Usage:
    python scripts/migrate_phase2.py --check
    python scripts/migrate_phase2.py --apply
    python scripts/migrate_phase2.py --verify
    python scripts/migrate_phase2.py --downgrade

DATABASE_URL must point at the branch database. The URL is never printed.
"""
from __future__ import annotations

import argparse
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from backend.config import ROOT, settings
from backend.database import make_engine, normalize_url

REVISION = "0001_phase2"

REQUIRED_TABLES = {
    "shop_config", "shop_channels", "product_variants", "knowledge_chunks",
    "product_image_embeddings", "shop_synonyms", "image_assets", "job_queue",
    "outbound_messages", "failed_queries", "eval_cases", "eval_runs",
}
REQUIRED_COLUMNS = {
    "orders": {"currency", "unit_price_snapshot", "line_total_snapshot"},
    "api_order_drafts": {"currency", "unit_price_snapshot", "line_total_snapshot"},
    "knowledge_versions": {"shop_id"},
}
REQUIRED_INDEXES = {
    "ix_knowledge_chunks_embedding", "ix_knowledge_chunks_tsv",
    "ix_knowledge_chunks_shop_kind_status", "ix_product_image_embeddings_embedding",
    "ix_job_queue_ready", "ix_outbound_messages_ready",
}


def alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    return config


def require_postgres(url):
    parsed = normalize_url(url)
    if parsed.drivername != "postgresql+psycopg":
        raise SystemExit("DATABASE_URL must be a PostgreSQL URL for Phase 2.")


def scalar(connection, sql, **params):
    return connection.execute(text(sql), params).scalar_one()


def preflight(connection):
    checks = {
        "duplicate_product_ids_across_shops": """
            SELECT count(*) FROM (
                SELECT product_id FROM products GROUP BY product_id HAVING count(*) > 1
            ) duplicates
        """,
        "duplicate_edgecase_ids_across_shops": """
            SELECT count(*) FROM (
                SELECT source_id FROM edgecase_qa GROUP BY source_id HAVING count(*) > 1
            ) duplicates
        """,
        "duplicate_document_ids_across_shops": """
            SELECT count(*) FROM (
                SELECT document_id FROM knowledge_documents GROUP BY document_id HAVING count(*) > 1
            ) duplicates
        """,
        "orphan_knowledge_versions": """
            SELECT count(*) FROM knowledge_versions v
            LEFT JOIN knowledge_documents d ON d.document_id = v.document_id
            WHERE d.document_id IS NULL
        """,
        "negative_or_null_product_prices": """
            SELECT count(*) FROM products WHERE price < 0 OR discounted_price < 0
        """,
        "non_numeric_draft_unit_prices": """
            SELECT count(*) FROM api_order_drafts
            WHERE unit_price IS NULL OR btrim(unit_price::text) !~ '^-?[0-9]+(\\.[0-9]+)?$'
        """,
    }
    failures = []
    for name, sql in checks.items():
        count = scalar(connection, sql)
        print(f"{name}: {count}")
        if count:
            failures.append(f"{name} ({count})")
    if failures:
        raise SystemExit("Preflight failed; no migration was run. Resolve: " + ", ".join(failures))


def check():
    require_postgres(settings.database_url)
    engine = make_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            preflight(connection)
            print("Preflight passed. Safe to run --apply on this branch.")
    finally:
        engine.dispose()


def apply():
    require_postgres(settings.database_url)
    engine = make_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            preflight(connection)
    finally:
        engine.dispose()
    command.upgrade(alembic_config(), "head")
    print(f"Upgraded to {REVISION}. Run --verify next.")


def downgrade():
    require_postgres(settings.database_url)
    # "rev:base" ranges are only valid with --sql; an online downgrade takes a target.
    command.downgrade(alembic_config(), "base")
    print("Downgraded Phase 2. New-only tables/columns were removed; check application version.")


def verify():
    require_postgres(settings.database_url)
    engine = make_engine(settings.database_url)
    missing = []
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        missing.extend(sorted(REQUIRED_TABLES - tables))
        for table, columns in REQUIRED_COLUMNS.items():
            if table not in tables:
                missing.append(f"{table} (table)")
                continue
            present = {column["name"] for column in inspector.get_columns(table)}
            missing.extend(f"{table}.{column}" for column in sorted(columns - present))
        with engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
            if version != REVISION:
                missing.append(f"alembic_version={version!r}")
            indexes = {row[0] for row in connection.execute(text(
                "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
            ))}
            missing.extend(sorted(REQUIRED_INDEXES - indexes))
            if not missing:
                for table in sorted(REQUIRED_TABLES | {"products", "orders", "api_order_drafts"}):
                    count = scalar(connection, f"SELECT count(*) FROM {table}")
                    print(f"{table}: {count} rows")
                print("Schema verification passed.")
    finally:
        engine.dispose()
    if missing:
        print("Schema verification failed. Missing: " + ", ".join(missing))
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--apply", action="store_true")
    action.add_argument("--verify", action="store_true")
    action.add_argument("--downgrade", action="store_true")
    args = parser.parse_args()
    if args.check:
        check()
    elif args.apply:
        apply()
    elif args.downgrade:
        downgrade()
    else:
        verify()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        # Never echo driver errors: they can contain hostnames or credentials.
        print(f"Phase 2 operation failed: {type(exc).__name__}. No secrets were printed.")
        sys.exit(1)
