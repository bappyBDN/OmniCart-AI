# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    HF_HOME=/app/cache/huggingface \
    DATABASE_REQUIRE_POSTGRES=true \
    RAG_DENSE_ENABLED=false
WORKDIR /app

COPY backend/requirements.txt backend/requirements-dense.txt ./backend/
ARG INSTALL_DENSE=false
# Dense retrieval is optional. CPU-only Torch avoids CUDA packages in this image.
RUN if [ "$INSTALL_DENSE" = "true" ]; then \
        python -m pip install torch --index-url https://download.pytorch.org/whl/cpu && \
        python -m pip install -r backend/requirements-dense.txt; \
    else \
        python -m pip install -r backend/requirements.txt; \
    fi

RUN groupadd --gid 10001 app && \
    useradd --uid 10001 --gid app --create-home app && \
    mkdir -p /app/data /app/cache && \
    chown -R app:app /app/data /app/cache

COPY backend/ ./backend/
COPY models.py ./
COPY alembic.ini ./
COPY migrations/ ./migrations/
COPY Data/products.json Data/policies.json Data/product_image_catalog.json Data/product_image_rag.json Data/policy_translations.json Data/policy_faqs.json Data/edgecase.json ./Data/
COPY ["product Image", "./product Image"]
COPY scripts/ ./scripts/
COPY --from=frontend-build /build/frontend/dist/ ./frontend/dist/

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3   CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5).close()"
# Keep one worker: chat locks and rate limiting are currently process-local.
CMD ["python", "-m", "backend.serve"]

# Test image: `docker compose --profile test run --rm tests`. Runs against an
# isolated SQLite fixture; .env is never in the build context.
FROM base AS test
USER root
RUN python -m pip install "pytest>=8,<10"
COPY pytest.ini ./
COPY tests/ ./tests/
COPY embedding_service/ ./embedding_service/
RUN chown -R app:app /app/tests
USER app
ENV DATABASE_REQUIRE_POSTGRES=false     DATABASE_URL=sqlite:////tmp/omnicart-test.db     MCP_ENABLED=false
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]

# Last stage = default build target (Render builds this one).
FROM base AS runtime
