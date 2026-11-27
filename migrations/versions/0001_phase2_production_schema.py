"""Phase 2: tenant-local keys, numeric money, vectors and durable queues.

Revision ID: 0001_phase2
Revises:
Create Date: 2026-11-28

This migration is intentionally PostgreSQL-only. It is non-destructive for
existing business rows: identifiers are preserved, money values are cast to
NUMERIC without currency conversion, and new columns are backfilled from the
catalog where a currency was actually recorded. Historic values are never
relabelled with a guessed currency.

Downgrade refuses to run if two shops now reuse the same product/document id;
that is a real data conflict and must be resolved by a human, not silently
merged.
"""
from alembic import op

revision = "0001_phase2"
down_revision = None
branch_labels = None
depends_on = None


def _require_postgres() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("Phase 2 migrations require PostgreSQL with pgvector and pg_trgm.")


def upgrade() -> None:
    _require_postgres()

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ---------------------------------------------------------------- tenant config
    op.execute("""
        CREATE TABLE IF NOT EXISTS shop_config (
            shop_id VARCHAR PRIMARY KEY REFERENCES shops(shop_id) ON DELETE CASCADE,
            default_language VARCHAR(16) NOT NULL DEFAULT 'en',
            reply_script_policy JSONB NOT NULL DEFAULT
                '{"bn":"bn","bn-Latn":"bn","en":"en"}'::jsonb,
            bot_tone VARCHAR(64) NOT NULL DEFAULT 'warm',
            currency CHAR(3) NOT NULL DEFAULT 'BDT',
            categories JSONB NOT NULL DEFAULT '[]'::jsonb,
            attribute_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
            vision_prompt_hints JSONB NOT NULL DEFAULT '{}'::jsonb,
            policy_precedence JSONB NOT NULL DEFAULT '{}'::jsonb,
            thresholds JSONB NOT NULL DEFAULT
                jsonb_build_object('high', 0.75, 'medium', 0.50, 'low', 0.35),
            style_guide JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        INSERT INTO shop_config (shop_id, bot_tone, currency)
        SELECT s.shop_id,
               COALESCE(NULLIF(s.bot_tone, ''), 'warm'),
               COALESCE((
                   SELECT ce.currency
                   FROM catalog_entries ce
                   WHERE ce.shop_id = s.shop_id AND ce.currency IS NOT NULL
                   GROUP BY ce.currency
                   ORDER BY count(*) DESC
                   LIMIT 1
               ), 'BDT')
        FROM shops s
        ON CONFLICT (shop_id) DO NOTHING
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS shop_channels (
            page_id VARCHAR(64) PRIMARY KEY,
            shop_id VARCHAR NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
            page_access_token_encrypted TEXT,
            app_secret_encrypted TEXT,
            verify_token_hash TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_shop_channels_shop_active ON shop_channels (shop_id, active)")

    # ---------------------------------------------------------------- money columns
    op.execute("ALTER TABLE products ALTER COLUMN price TYPE NUMERIC(12,2) USING price::numeric(12,2)")
    op.execute("ALTER TABLE products ALTER COLUMN discounted_price TYPE NUMERIC(12,2) USING discounted_price::numeric(12,2)")
    op.execute("ALTER TABLE orders ALTER COLUMN price TYPE NUMERIC(12,2) USING price::numeric(12,2)")
    op.execute("ALTER TABLE orders ALTER COLUMN total_price TYPE NUMERIC(12,2) USING total_price::numeric(12,2)")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS currency CHAR(3)")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS unit_price_snapshot NUMERIC(12,2)")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS line_total_snapshot NUMERIC(12,2)")
    op.execute("ALTER TABLE api_order_drafts ALTER COLUMN unit_price TYPE NUMERIC(12,2) USING unit_price::numeric(12,2)")
    op.execute("ALTER TABLE api_order_drafts ADD COLUMN IF NOT EXISTS currency CHAR(3)")
    op.execute("ALTER TABLE api_order_drafts ADD COLUMN IF NOT EXISTS unit_price_snapshot NUMERIC(12,2)")
    op.execute("ALTER TABLE api_order_drafts ADD COLUMN IF NOT EXISTS line_total_snapshot NUMERIC(12,2)")

    # Historic numbers are copied verbatim into immutable snapshots. Currency is
    # attached only when the catalog actually recorded one.
    op.execute("""
        UPDATE orders o
        SET unit_price_snapshot = o.price,
            line_total_snapshot = o.total_price
        WHERE o.unit_price_snapshot IS NULL OR o.line_total_snapshot IS NULL
    """)
    op.execute("""
        UPDATE orders o
        SET currency = ce.currency
        FROM catalog_entries ce
        WHERE ce.shop_id = o.shop_id AND ce.product_id = o.product_id
          AND o.currency IS NULL AND ce.currency IS NOT NULL
    """)
    op.execute("""
        UPDATE api_order_drafts d
        SET unit_price_snapshot = d.unit_price,
            line_total_snapshot = ROUND(d.unit_price * d.quantity, 2)
        WHERE d.unit_price_snapshot IS NULL OR d.line_total_snapshot IS NULL
    """)
    op.execute("""
        UPDATE api_order_drafts d
        SET currency = ce.currency
        FROM catalog_entries ce
        WHERE ce.shop_id = d.shop_id AND ce.product_id = d.product_id
          AND d.currency IS NULL AND ce.currency IS NOT NULL
    """)

    for table, constraint in (
        ("products", "ck_products_price_nonnegative"),
        ("products", "ck_products_discounted_price_nonnegative"),
        ("products", "ck_products_stock_nonnegative"),
        ("orders", "ck_orders_price_nonnegative"),
        ("orders", "ck_orders_total_nonnegative"),
        ("api_order_drafts", "ck_api_order_drafts_unit_price_nonnegative"),
        ("api_order_drafts", "ck_api_order_drafts_quantity_positive"),
    ):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}")
    op.execute("ALTER TABLE products ADD CONSTRAINT ck_products_price_nonnegative CHECK (price IS NULL OR price >= 0)")
    op.execute("ALTER TABLE products ADD CONSTRAINT ck_products_discounted_price_nonnegative CHECK (discounted_price IS NULL OR discounted_price >= 0)")
    op.execute("ALTER TABLE products ADD CONSTRAINT ck_products_stock_nonnegative CHECK (stock_count IS NULL OR stock_count >= 0)")
    op.execute("ALTER TABLE orders ADD CONSTRAINT ck_orders_price_nonnegative CHECK (price >= 0)")
    op.execute("ALTER TABLE orders ADD CONSTRAINT ck_orders_total_nonnegative CHECK (total_price >= 0)")
    op.execute("ALTER TABLE api_order_drafts ADD CONSTRAINT ck_api_order_drafts_unit_price_nonnegative CHECK (unit_price >= 0)")
    op.execute("ALTER TABLE api_order_drafts ADD CONSTRAINT ck_api_order_drafts_quantity_positive CHECK (quantity > 0)")

    # ---------------------------------------------------------------- tenant-local keys
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_product_id_fkey")
    op.execute("ALTER TABLE catalog_entries DROP CONSTRAINT IF EXISTS catalog_entries_product_id_fkey")
    op.execute("ALTER TABLE api_order_drafts DROP CONSTRAINT IF EXISTS api_order_drafts_product_id_fkey")
    op.execute("ALTER TABLE api_product_images DROP CONSTRAINT IF EXISTS api_product_images_product_id_fkey")
    op.execute("ALTER TABLE product_aliases DROP CONSTRAINT IF EXISTS product_aliases_product_id_fkey")

    op.execute("ALTER TABLE catalog_entries DROP CONSTRAINT IF EXISTS catalog_entries_pkey")
    op.execute("ALTER TABLE catalog_entries ADD CONSTRAINT catalog_entries_pkey PRIMARY KEY (shop_id, product_id)")
    op.execute("ALTER TABLE api_product_images DROP CONSTRAINT IF EXISTS api_product_images_pkey")
    op.execute("ALTER TABLE api_product_images ADD CONSTRAINT api_product_images_pkey PRIMARY KEY (shop_id, product_id)")

    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS products_pkey")
    op.execute("ALTER TABLE products ADD CONSTRAINT products_pkey PRIMARY KEY (shop_id, product_id)")

    for table, constraint in (
        ("orders", "fk_orders_shop_product"),
        ("catalog_entries", "fk_catalog_entries_shop_product"),
        ("api_order_drafts", "fk_api_order_drafts_shop_product"),
        ("api_product_images", "fk_api_product_images_shop_product"),
        ("product_aliases", "fk_product_aliases_shop_product"),
    ):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}")
    op.execute("""
        ALTER TABLE orders ADD CONSTRAINT fk_orders_shop_product
        FOREIGN KEY (shop_id, product_id) REFERENCES products(shop_id, product_id)
    """)
    op.execute("""
        ALTER TABLE catalog_entries ADD CONSTRAINT fk_catalog_entries_shop_product
        FOREIGN KEY (shop_id, product_id) REFERENCES products(shop_id, product_id)
    """)
    op.execute("""
        ALTER TABLE api_order_drafts ADD CONSTRAINT fk_api_order_drafts_shop_product
        FOREIGN KEY (shop_id, product_id) REFERENCES products(shop_id, product_id)
    """)
    op.execute("""
        ALTER TABLE api_product_images ADD CONSTRAINT fk_api_product_images_shop_product
        FOREIGN KEY (shop_id, product_id) REFERENCES products(shop_id, product_id)
    """)
    op.execute("""
        ALTER TABLE product_aliases ADD CONSTRAINT fk_product_aliases_shop_product
        FOREIGN KEY (shop_id, product_id) REFERENCES products(shop_id, product_id)
    """)

    op.execute("ALTER TABLE edgecase_qa DROP CONSTRAINT IF EXISTS edgecase_qa_pkey")
    op.execute("ALTER TABLE edgecase_qa ADD CONSTRAINT edgecase_qa_pkey PRIMARY KEY (shop_id, source_id)")

    # knowledge_versions must gain shop_id before the parent key can change.
    op.execute("ALTER TABLE knowledge_versions DROP CONSTRAINT IF EXISTS knowledge_versions_document_id_fkey")
    op.execute("ALTER TABLE knowledge_versions ADD COLUMN IF NOT EXISTS shop_id VARCHAR")
    op.execute("""
        UPDATE knowledge_versions v
        SET shop_id = d.shop_id
        FROM knowledge_documents d
        WHERE d.document_id = v.document_id AND v.shop_id IS NULL
    """)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM knowledge_versions WHERE shop_id IS NULL) THEN
                RAISE EXCEPTION 'knowledge_versions has orphan document_id rows; resolve before migrating';
            END IF;
        END $$;
    """)
    op.execute("ALTER TABLE knowledge_documents DROP CONSTRAINT IF EXISTS knowledge_documents_pkey")
    op.execute("ALTER TABLE knowledge_documents ADD CONSTRAINT knowledge_documents_pkey PRIMARY KEY (shop_id, document_id)")
    op.execute("ALTER TABLE knowledge_versions DROP CONSTRAINT IF EXISTS knowledge_versions_pkey")
    op.execute("ALTER TABLE knowledge_versions ALTER COLUMN shop_id SET NOT NULL")
    op.execute("ALTER TABLE knowledge_versions ADD CONSTRAINT knowledge_versions_pkey PRIMARY KEY (shop_id, document_id, version)")
    op.execute("ALTER TABLE knowledge_versions DROP CONSTRAINT IF EXISTS fk_knowledge_versions_shop_document")
    op.execute("ALTER TABLE knowledge_versions DROP CONSTRAINT IF EXISTS fk_knowledge_versions_shop")
    op.execute("""
        ALTER TABLE knowledge_versions ADD CONSTRAINT fk_knowledge_versions_shop_document
        FOREIGN KEY (shop_id, document_id)
        REFERENCES knowledge_documents(shop_id, document_id)
    """)
    op.execute("ALTER TABLE knowledge_versions ADD CONSTRAINT fk_knowledge_versions_shop FOREIGN KEY (shop_id) REFERENCES shops(shop_id)")

    # ---------------------------------------------------------------- variants
    op.execute("""
        CREATE TABLE IF NOT EXISTS product_variants (
            variant_id VARCHAR PRIMARY KEY,
            shop_id VARCHAR NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
            product_id VARCHAR NOT NULL,
            sku VARCHAR,
            size VARCHAR(64),
            color VARCHAR(64),
            price NUMERIC(12,2),
            currency CHAR(3),
            stock_count INTEGER,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_product_variants_shop_product
                FOREIGN KEY (shop_id, product_id) REFERENCES products(shop_id, product_id),
            CONSTRAINT ck_product_variants_price_nonnegative CHECK (price IS NULL OR price >= 0),
            CONSTRAINT ck_product_variants_stock_nonnegative CHECK (stock_count IS NULL OR stock_count >= 0),
            CONSTRAINT uq_product_variants_shop_sku UNIQUE (shop_id, sku)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_product_variants_shop_product ON product_variants (shop_id, product_id, active)")
    op.execute("""
        INSERT INTO product_variants
            (variant_id, shop_id, product_id, sku, price, currency, stock_count, active)
        SELECT 'legacy:' || p.shop_id || ':' || p.product_id,
               p.shop_id, p.product_id, p.product_id, p.price,
               ce.currency, p.stock_count, TRUE
        FROM products p
        LEFT JOIN catalog_entries ce
          ON ce.shop_id = p.shop_id AND ce.product_id = p.product_id
        ON CONFLICT (variant_id) DO NOTHING
    """)

    # ---------------------------------------------------------------- vectors
    op.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            chunk_id BIGSERIAL PRIMARY KEY,
            shop_id VARCHAR NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
            kind VARCHAR(32) NOT NULL,
            source_id VARCHAR(128) NOT NULL,
            locale VARCHAR(16) NOT NULL DEFAULT 'en',
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            parent_text TEXT,
            embedding vector(1024),
            tsv tsvector GENERATED ALWAYS AS (
                to_tsvector('simple'::regconfig,
                    COALESCE(title, '') || ' ' || COALESCE(content, ''))
            ) STORED,
            content_hash CHAR(64) NOT NULL,
            model_name VARCHAR(128),
            model_version VARCHAR(64),
            status VARCHAR(16) NOT NULL DEFAULT 'draft',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_knowledge_chunks_status
                CHECK (status IN ('draft', 'published', 'archived')),
            CONSTRAINT uq_knowledge_chunks_content
                UNIQUE (shop_id, kind, source_id, locale, content_hash)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_shop_kind_status ON knowledge_chunks (shop_id, kind, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_tsv ON knowledge_chunks USING gin (tsv)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS product_image_embeddings (
            id BIGSERIAL PRIMARY KEY,
            shop_id VARCHAR NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
            product_id VARCHAR NOT NULL,
            image_id VARCHAR(128) NOT NULL,
            embedding vector(768) NOT NULL,
            model_name VARCHAR(128) NOT NULL,
            model_version VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_product_image_embeddings_shop_product
                FOREIGN KEY (shop_id, product_id) REFERENCES products(shop_id, product_id),
            CONSTRAINT uq_product_image_embeddings_version
                UNIQUE (shop_id, product_id, image_id, model_name, model_version)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_product_image_embeddings_shop_product ON product_image_embeddings (shop_id, product_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_product_image_embeddings_embedding ON product_image_embeddings USING hnsw (embedding vector_cosine_ops)")

    # ---------------------------------------------------------------- shop vocabulary
    op.execute("""
        CREATE TABLE IF NOT EXISTS shop_synonyms (
            id BIGSERIAL PRIMARY KEY,
            shop_id VARCHAR NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
            term VARCHAR(128) NOT NULL,
            canonical VARCHAR(128) NOT NULL,
            lang VARCHAR(16) NOT NULL DEFAULT 'en',
            kind VARCHAR(32) NOT NULL DEFAULT 'product',
            status VARCHAR(16) NOT NULL DEFAULT 'draft',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_shop_synonyms_status CHECK (status IN ('draft', 'published', 'archived')),
            CONSTRAINT uq_shop_synonyms_term UNIQUE (shop_id, term, lang, kind)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_shop_synonyms_shop_status ON shop_synonyms (shop_id, status, kind)")

    # ---------------------------------------------------------------- images, jobs, outbox
    op.execute("""
        CREATE TABLE IF NOT EXISTS image_assets (
            asset_id VARCHAR PRIMARY KEY,
            shop_id VARCHAR NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
            customer_id INTEGER REFERENCES customers(customer_id) ON DELETE SET NULL,
            url TEXT NOT NULL,
            sha256 CHAR(64) NOT NULL,
            mime VARCHAR(64) NOT NULL,
            bytes BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            delete_after TIMESTAMPTZ,
            CONSTRAINT uq_image_assets_shop_hash UNIQUE (shop_id, sha256)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_image_assets_retention ON image_assets (delete_after)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS job_queue (
            job_id BIGSERIAL PRIMARY KEY,
            shop_id VARCHAR NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
            kind VARCHAR(64) NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(16) NOT NULL DEFAULT 'queued',
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 5,
            run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
            locked_by VARCHAR(128),
            locked_at TIMESTAMPTZ,
            last_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_job_queue_status
                CHECK (status IN ('queued', 'running', 'done', 'dead')),
            CONSTRAINT ck_job_queue_attempts CHECK (attempts >= 0 AND max_attempts > 0)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_job_queue_ready ON job_queue (status, run_after)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_job_queue_shop ON job_queue (shop_id, status)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS outbound_messages (
            outbox_id BIGSERIAL PRIMARY KEY,
            shop_id VARCHAR NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
            customer_id INTEGER REFERENCES customers(customer_id) ON DELETE SET NULL,
            channel VARCHAR(32) NOT NULL,
            payload JSONB NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'queued',
            attempts INTEGER NOT NULL DEFAULT 0,
            provider_message_id VARCHAR(128),
            last_error TEXT,
            idempotency_key VARCHAR(128) NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            sent_at TIMESTAMPTZ,
            CONSTRAINT ck_outbound_messages_status
                CHECK (status IN ('queued', 'sending', 'sent', 'failed', 'dead'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_outbound_messages_ready ON outbound_messages (status, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_outbound_messages_shop ON outbound_messages (shop_id, status)")

    # ---------------------------------------------------------------- review + eval
    op.execute("""
        CREATE TABLE IF NOT EXISTS failed_queries (
            id BIGSERIAL PRIMARY KEY,
            shop_id VARCHAR NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
            query TEXT NOT NULL,
            language VARCHAR(16),
            reason VARCHAR(128) NOT NULL,
            retrieved_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            reviewed BOOLEAN NOT NULL DEFAULT FALSE
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_failed_queries_review ON failed_queries (shop_id, reviewed, created_at)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS eval_cases (
            id BIGSERIAL PRIMARY KEY,
            shop_id VARCHAR NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
            query TEXT NOT NULL,
            locale VARCHAR(16) NOT NULL DEFAULT 'en',
            intent VARCHAR(64),
            expected_product_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            expected_no_match BOOLEAN NOT NULL DEFAULT FALSE,
            image_path TEXT,
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_eval_cases_shop_locale ON eval_cases (shop_id, locale)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS eval_runs (
            run_id BIGSERIAL PRIMARY KEY,
            shop_id VARCHAR NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
            git_sha VARCHAR(64),
            model_version VARCHAR(128),
            metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
            tolerance JSONB NOT NULL DEFAULT '{}'::jsonb,
            passed BOOLEAN NOT NULL DEFAULT FALSE,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_eval_runs_shop_started ON eval_runs (shop_id, started_at DESC)")


def downgrade() -> None:
    _require_postgres()

    # New tables are removed first so their foreign keys stop depending on the
    # tenant-local primary keys.
    for table in ("eval_runs", "eval_cases", "failed_queries", "outbound_messages",
                  "job_queue", "image_assets", "shop_synonyms", "product_image_embeddings",
                  "knowledge_chunks", "product_variants", "shop_channels", "shop_config"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    # Real data conflicts cannot be merged automatically.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM products GROUP BY product_id HAVING count(*) > 1) THEN
                RAISE EXCEPTION 'downgrade blocked: product_id is reused across shops; resolve duplicates first';
            END IF;
            IF EXISTS (SELECT 1 FROM edgecase_qa GROUP BY source_id HAVING count(*) > 1) THEN
                RAISE EXCEPTION 'downgrade blocked: edgecase source_id is reused across shops; resolve duplicates first';
            END IF;
            IF EXISTS (SELECT 1 FROM knowledge_documents GROUP BY document_id HAVING count(*) > 1) THEN
                RAISE EXCEPTION 'downgrade blocked: knowledge document_id is reused across shops; resolve duplicates first';
            END IF;
        END $$;
    """)

    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS fk_orders_shop_product")
    op.execute("ALTER TABLE catalog_entries DROP CONSTRAINT IF EXISTS fk_catalog_entries_shop_product")
    op.execute("ALTER TABLE api_order_drafts DROP CONSTRAINT IF EXISTS fk_api_order_drafts_shop_product")
    op.execute("ALTER TABLE api_product_images DROP CONSTRAINT IF EXISTS fk_api_product_images_shop_product")
    op.execute("ALTER TABLE product_aliases DROP CONSTRAINT IF EXISTS fk_product_aliases_shop_product")

    op.execute("ALTER TABLE knowledge_versions DROP CONSTRAINT IF EXISTS fk_knowledge_versions_shop_document")
    op.execute("ALTER TABLE knowledge_versions DROP CONSTRAINT IF EXISTS fk_knowledge_versions_shop")
    op.execute("ALTER TABLE knowledge_versions DROP CONSTRAINT IF EXISTS knowledge_versions_pkey")
    op.execute("ALTER TABLE knowledge_versions ADD CONSTRAINT knowledge_versions_pkey PRIMARY KEY (document_id, version)")
    op.execute("ALTER TABLE knowledge_versions DROP COLUMN IF EXISTS shop_id")
    op.execute("ALTER TABLE knowledge_documents DROP CONSTRAINT IF EXISTS knowledge_documents_pkey")
    op.execute("ALTER TABLE knowledge_documents ADD CONSTRAINT knowledge_documents_pkey PRIMARY KEY (document_id)")

    op.execute("ALTER TABLE edgecase_qa DROP CONSTRAINT IF EXISTS edgecase_qa_pkey")
    op.execute("ALTER TABLE edgecase_qa ADD CONSTRAINT edgecase_qa_pkey PRIMARY KEY (source_id)")

    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS products_pkey")
    op.execute("ALTER TABLE products ADD CONSTRAINT products_pkey PRIMARY KEY (product_id)")
    op.execute("ALTER TABLE api_product_images DROP CONSTRAINT IF EXISTS api_product_images_pkey")
    op.execute("ALTER TABLE api_product_images ADD CONSTRAINT api_product_images_pkey PRIMARY KEY (product_id)")
    op.execute("ALTER TABLE catalog_entries DROP CONSTRAINT IF EXISTS catalog_entries_pkey")
    op.execute("ALTER TABLE catalog_entries ADD CONSTRAINT catalog_entries_pkey PRIMARY KEY (product_id)")

    op.execute("ALTER TABLE orders ADD CONSTRAINT orders_product_id_fkey FOREIGN KEY (product_id) REFERENCES products(product_id)")
    op.execute("ALTER TABLE catalog_entries ADD CONSTRAINT catalog_entries_product_id_fkey FOREIGN KEY (product_id) REFERENCES products(product_id)")
    op.execute("ALTER TABLE api_order_drafts ADD CONSTRAINT api_order_drafts_product_id_fkey FOREIGN KEY (product_id) REFERENCES products(product_id)")
    op.execute("ALTER TABLE api_product_images ADD CONSTRAINT api_product_images_product_id_fkey FOREIGN KEY (product_id) REFERENCES products(product_id)")
    op.execute("ALTER TABLE product_aliases ADD CONSTRAINT product_aliases_product_id_fkey FOREIGN KEY (product_id) REFERENCES products(product_id)")

    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS ck_products_price_nonnegative")
    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS ck_products_discounted_price_nonnegative")
    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS ck_products_stock_nonnegative")
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS ck_orders_price_nonnegative")
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS ck_orders_total_nonnegative")
    op.execute("ALTER TABLE api_order_drafts DROP CONSTRAINT IF EXISTS ck_api_order_drafts_unit_price_nonnegative")
    op.execute("ALTER TABLE api_order_drafts DROP CONSTRAINT IF EXISTS ck_api_order_drafts_quantity_positive")

    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS currency")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS unit_price_snapshot")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS line_total_snapshot")
    op.execute("ALTER TABLE api_order_drafts DROP COLUMN IF EXISTS currency")
    op.execute("ALTER TABLE api_order_drafts DROP COLUMN IF EXISTS unit_price_snapshot")
    op.execute("ALTER TABLE api_order_drafts DROP COLUMN IF EXISTS line_total_snapshot")

    op.execute("ALTER TABLE products ALTER COLUMN price TYPE DOUBLE PRECISION USING price::double precision")
    op.execute("ALTER TABLE products ALTER COLUMN discounted_price TYPE DOUBLE PRECISION USING discounted_price::double precision")
    op.execute("ALTER TABLE orders ALTER COLUMN price TYPE DOUBLE PRECISION USING price::double precision")
    op.execute("ALTER TABLE orders ALTER COLUMN total_price TYPE DOUBLE PRECISION USING total_price::double precision")
    op.execute("ALTER TABLE api_order_drafts ALTER COLUMN unit_price TYPE VARCHAR USING unit_price::text")
