"""Alembic environment for OmniCart.

The database URL comes from the same environment variable as the application
(`DATABASE_URL`), never from a checked-in credential. Offline SQL generation
requires a PostgreSQL URL; production upgrades must run on PostgreSQL because
the schema uses pgvector, pg_trgm and generated tsvector columns.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import backend.records  # noqa: F401: register the additive application tables
from backend.config import settings
from models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ConfigParser treats percent signs specially in URLs (passwords often contain
# them). Alembic expects the value escaped when set through the config object.
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
