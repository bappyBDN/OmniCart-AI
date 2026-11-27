# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
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
COPY Data/products.json Data/policies.json Data/product_image_catalog.json Data/product_image_rag.json Data/policy_translations.json Data/policy_faqs.json ./Data/
COPY ["product Image", "./product Image"]
COPY scripts/ ./scripts/
COPY --from=frontend-build /build/frontend/dist/ ./frontend/dist/

USER app
EXPOSE 8000
# Keep one worker: chat locks and rate limiting are currently process-local.
CMD ["python", "-m", "backend.serve"]
