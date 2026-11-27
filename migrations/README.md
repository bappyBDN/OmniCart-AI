# OmniCart database migrations

Phase 2 uses Alembic. The migration is a delta for an existing OmniCart
PostgreSQL database (or a Neon branch cloned from it). It is PostgreSQL-only
because it installs `vector`/`pg_trgm`, creates HNSW and GIN indexes, and uses
`NUMERIC`/generated `tsvector` columns.

## Safety rules

- Never run against `postgres`, `template0` or `template1`.
- Run `--check` first. It refuses on duplicate tenant ids, orphan knowledge
  versions, negative prices or non-numeric draft prices.
- Run on a Neon branch, verify, then apply to the production branch.
- Historic order amounts are copied verbatim into `unit_price_snapshot` and
  `line_total_snapshot`. Currency is filled only when `catalog_entries` already
  recorded it. No FX conversion and no guessed relabelling happen.
- Downgrade refuses if two shops now reuse a product, edge-case or knowledge id.
  That is a real conflict and must be resolved by a human.

## Neon branch procedure

```powershell
# 1. Create/choose a Neon branch from production and copy its connection string.
$env:DATABASE_URL = "postgresql+psycopg://.../neondb?sslmode=require"

# 2. Preflight (read-only checks).
.\venv\Scripts\python.exe scripts/migrate_phase2.py --check

# 3. Apply on the branch.
.\venv\Scripts\python.exe scripts/migrate_phase2.py --apply

# 4. Verify tables, columns, indexes and Alembic revision.
.\venv\Scripts\python.exe scripts/migrate_phase2.py --verify

# 5. Inspect the generated SQL without connecting.
.\venv\Scripts\python.exe -m alembic upgrade head --sql
.\venv\Scripts\python.exe -m alembic downgrade 0001_phase2:base --sql

# 6. Rehearse rollback on the branch only.
.\venv\Scripts\python.exe scripts/migrate_phase2.py --downgrade
```

After the branch passes, point the same commands at the production branch during
a maintenance window. Keep the previous branch until the application has run
through its checkout and vision smoke tests.

## Fresh database note

`0001_phase2` is a **delta** migration: it expects the pre-Phase-2 tables to
exist. A brand-new database should be created from the production snapshot or
from the application's legacy bootstrap, then upgraded with Alembic. Do not run
this delta against an empty database.
