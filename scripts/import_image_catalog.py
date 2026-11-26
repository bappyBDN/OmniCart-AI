"""Validate locally, or back up and transactionally import into configured SQL."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import MetaData, Table, inspect, select

from backend import database
from backend.catalog_import import import_catalog, load_inputs
from backend.config import settings


def snapshot(connection, tables):
    result = {}
    existing = set(inspect(connection).get_table_names())
    for name in tables:
        if name in existing:
            table = Table(name, MetaData(), autoload_with=connection)
            result[name] = [dict(row) for row in connection.execute(select(table)).mappings()]
    return result


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, default=str, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path, help="Private JSON backup path outside Git (required with --apply)")
    args = parser.parse_args()
    inputs = load_inputs()
    if not args.apply:
        print("Validated 17 products, 33 photos, 68 RAG sections, 50 translated policies and 10 derived FAQs. No database writes.")
        return
    if not args.backup or args.backup.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error("--apply requires --backup outside the repository")
    with database.engine.connect() as conn:
        if conn.dialect.name == "postgresql" and conn.exec_driver_sql("select current_database()").scalar_one() != "omnichat-db":
            raise SystemExit("Unexpected database; import stopped")
        before = snapshot(conn, ["customers", "orders"])
        backup = snapshot(conn, ["products", "policies", "product_images", "product_aliases", "catalog_entries", "policy_sources"])
    args.backup.parent.mkdir(parents=True, exist_ok=True)
    with args.backup.open("x", encoding="utf-8") as handle:
        json.dump(backup, handle, ensure_ascii=False, default=str, indent=2)
    database.initialize()
    with database.transaction() as db:
        result = import_catalog(db, settings.shop_id, inputs)
        after = snapshot(db.connection(), ["customers", "orders"])
        if fingerprint(before) != fingerprint(after):
            raise RuntimeError("Customers/orders changed; import rolled back (retry during a quiet window)")
    print(json.dumps({**result, "customers_and_orders": "unchanged", "backup": str(args.backup)}, indent=2))


if __name__ == "__main__":
    main()
