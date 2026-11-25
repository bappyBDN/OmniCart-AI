"""Copy the app's SQLite tables safely into PostgreSQL.

Credentials are read from .env, never command-line arguments or printed output.
The original administrative database is queried but its tables are never changed.
Use --use-database-url for an existing application database (for example Neon).
The app's DATABASE_URL is deliberately not changed by this utility.
"""
import argparse
import os
import re
import socket
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

from backend.config import ROOT
from backend.database import make_engine, normalize_url
from models import Base
import backend.records  # noqa: F401: register session, conversation and outbox tables


class MigrationError(RuntimeError):
    pass


def application_url(value):
    url = normalize_url(value)
    if url.drivername != "postgresql+psycopg" or not url.host or not url.database:
        raise MigrationError("DATABASE_URL must be a full PostgreSQL connection URL.")
    if url.database in ("postgres", "template0", "template1"):
        raise MigrationError("DATABASE_URL must select an application database, not a system database.")
    return url


def connection_urls(admin_value, database):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", database):
        raise MigrationError("Database name must use lowercase letters, digits or underscores (max 63 characters).")
    admin = normalize_url(admin_value)
    if admin.drivername != "postgresql+psycopg" or not admin.host or not admin.database:
        raise MigrationError("POSTGRES_ADMIN_URL must be a full PostgreSQL connection URL.")
    if database in (admin.database, "postgres", "template0", "template1"):
        raise MigrationError("Target must be a separate application database, not the administrative database.")
    return admin, admin.set(database=database)


def ensure_database(admin_engine, name, create=False):
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        existing = connection.execute(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}).scalar()
        if existing:
            return True
        if not create:
            return False
        permitted = connection.execute(text("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user")).scalar()
        if not permitted:
            raise MigrationError("This account has no CREATEDB privilege. Ask the PostgreSQL administrator to create the target database.")
        # Validated name plus dialect identifier quoting: no interpolated SQL input.
        quoted = connection.dialect.identifier_preparer.quote_identifier(name)
        connection.exec_driver_sql(f"CREATE DATABASE {quoted} ENCODING 'UTF8' TEMPLATE template0")
        return True


def copy_tables(source_engine, target_engine):
    """Only an empty target is accepted; insert + verification are one transaction."""
    present = set(inspect(source_engine).get_table_names())
    tables = [table for table in Base.metadata.sorted_tables if table.name in present]
    if not tables or "shops" not in present:
        raise MigrationError("Source is not an OmniCart database.")
    # DDL is additive and transactional on PostgreSQL. Never truncate/overwrite.
    with target_engine.begin() as target, source_engine.connect() as source:
        Base.metadata.create_all(target)
        for table in Base.metadata.sorted_tables:
            if target.execute(select(func.count()).select_from(table)).scalar_one():
                raise MigrationError(f"Target table '{table.name}' is not empty. Refusing to merge or replace data.")
        copied = {}
        for table in tables:
            rows = source.execute(select(table)).mappings()
            total = 0
            while batch := rows.fetchmany(500):
                target.execute(table.insert(), [dict(row) for row in batch])
                total += len(batch)
            copied[table.name] = total
        for table in tables:
            actual = target.execute(select(func.count()).select_from(table)).scalar_one()
            if actual != copied[table.name]:
                raise MigrationError(f"Row-count verification failed for '{table.name}'. Import was rolled back.")
        if target_engine.dialect.name == "postgresql":
            # Explicit primary keys are preserved; advance SERIAL sequences so
            # the next new customer/order/message does not collide after import.
            for table in tables:
                column = table._autoincrement_column
                if column is None:
                    continue
                sequence = target.execute(text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
                    {"table_name": table.name, "column_name": column.name}).scalar()
                if sequence:
                    maximum = target.execute(select(func.max(column))).scalar()
                    target.execute(text("SELECT setval(CAST(:sequence AS regclass), :value, :called)"),
                        {"sequence": sequence, "value": max(maximum or 1, 1), "called": maximum is not None and maximum >= 1})
        return copied


def migrate_snapshot(sqlite_path, target_engine):
    """SQLite's backup API includes committed WAL data; original file is untouched."""
    source_path = Path(sqlite_path).resolve()
    if not source_path.is_file():
        raise MigrationError("SQLite source file does not exist.")
    temp_root = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Temp" / "opencode"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="omnichat-migration-", dir=temp_root) as folder:
        snapshot_path = Path(folder) / "source.db"
        with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(snapshot_path)) as snapshot:
                source.backup(snapshot)
        snapshot_engine = make_engine(f"sqlite:///{snapshot_path.as_posix()}")
        try:
            with snapshot_engine.connect() as source:
                violations = source.exec_driver_sql("PRAGMA foreign_key_check").fetchmany(1)
                if violations:
                    raise MigrationError("Source contains broken foreign-key references. Repair these before migrating; no target data was imported.")
            return copy_tables(snapshot_engine, target_engine)
        finally:
            snapshot_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="Only check DNS, authentication, and target availability")
    action.add_argument("--create", action="store_true", help="Create the separate target database, without copying records")
    action.add_argument("--migrate", action="store_true", help="Create target if needed and copy all SQLite app tables")
    parser.add_argument("--use-database-url", action="store_true",
                        help="Use the existing application DATABASE_URL directly; never create a database")
    parser.add_argument("--source-paused", action="store_true", help="Confirm you stopped ALL SQLite app writers before importing")
    parser.add_argument("--sqlite", type=Path, default=ROOT / "ecommerce_saas.db")
    args = parser.parse_args()
    if args.migrate and not args.source_paused:
        parser.error("Stop the API and legacy Streamlit app, then pass --source-paused. This prevents losing writes at cutover.")
    if args.use_database_url and args.create:
        parser.error("--create cannot be used with --use-database-url; the database must already exist.")
    variable = "DATABASE_URL" if args.use_database_url else "POSTGRES_ADMIN_URL"
    value = os.getenv(variable, "")
    if not value:
        parser.error(f"Set {variable} in .env (do not put passwords on the command line).")
    admin_engine = target_engine = None
    try:
        if args.use_database_url:
            target_url = application_url(value)
        else:
            admin_url, target_url = connection_urls(value, os.getenv("POSTGRES_DATABASE", "omnichat"))
        try:
            socket.getaddrinfo(target_url.host, target_url.port or 5432, type=socket.SOCK_STREAM)
        except OSError:
            raise MigrationError(f"Host '{target_url.host}' cannot be resolved from this machine. Supply a reachable PostgreSQL host/port or use the server's container network/SSH tunnel.") from None
        if not args.use_database_url:
            admin_engine = make_engine(admin_url)
            exists = ensure_database(admin_engine, target_url.database, create=args.create or args.migrate)
            print("Administrative PostgreSQL connection verified (credentials hidden).")
            if not exists:
                print(f"Target '{target_url.database}' does not exist yet. Use --create or --migrate after checking access.")
                return
        target_engine = make_engine(target_url)
        with target_engine.connect() as connection:
            actual_name = connection.execute(text("SELECT current_database()")).scalar_one()
            if actual_name != target_url.database:
                raise MigrationError("Connected to an unexpected database. No import performed.")
        print(f"Target database '{target_url.database}' is reachable.")
        if args.migrate:
            counts = migrate_snapshot(args.sqlite, target_engine)
            for table, count in counts.items():
                print(f"  {table}: {count} records verified")
            print(f"Import committed. Original SQLite retained. Restart the API with DATABASE_URL selecting '{target_url.database}'.")
        else:
            print("No app records were copied. Active DATABASE_URL was not changed.")
    except (MigrationError, SQLAlchemyError, ValueError) as exc:
        # Never print driver exception strings: they may contain URLs or PII.
        message = str(exc) if isinstance(exc, MigrationError) else "PostgreSQL operation failed. Check reachability, credentials, SSL settings, and database permissions. Active database was not changed."
        parser.exit(1, message + "\n")
    finally:
        if target_engine:
            target_engine.dispose()
        if admin_engine:
            admin_engine.dispose()


if __name__ == "__main__":
    main()
