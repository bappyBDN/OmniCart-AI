# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/huggingface \
    TRANSFORMERS_CACHE=/models/huggingface \
    EMBEDDING_ALLOW_DOWNLOAD=true \
    EMBEDDING_PRELOAD=true \
    EMBEDDING_TORCH_THREADS=2

WORKDIR /app

# CPU-only torch keeps the image small and avoids CUDA downloads.
RUN python -m pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY embedding_service/requirements.txt ./embedding_service/requirements.txt
RUN python -m pip install --no-cache-dir -r embedding_service/requirements.txt

COPY embedding_service/ ./embedding_service/

# Download both models at build time. Runtime stays offline by default.
ARG EMBEDDING_TEXT_MODEL=BAAI/bge-m3
ARG EMBEDDING_TEXT_DIM=1024
ARG EMBEDDING_TEXT_REVISION=main
ARG EMBEDDING_IMAGE_MODEL=google/siglip-base-patch16-224
ARG EMBEDDING_IMAGE_DIM=768
ARG EMBEDDING_IMAGE_REVISION=main
ENV EMBEDDING_TEXT_MODEL=$EMBEDDING_TEXT_MODEL \
    EMBEDDING_TEXT_DIM=$EMBEDDING_TEXT_DIM \
    EMBEDDING_TEXT_REVISION=$EMBEDDING_TEXT_REVISION \
    EMBEDDING_IMAGE_MODEL=$EMBEDDING_IMAGE_MODEL \
    EMBEDDING_IMAGE_DIM=$EMBEDDING_IMAGE_DIM \
    EMBEDDING_IMAGE_REVISION=$EMBEDDING_IMAGE_REVISION

RUN python -m embedding_service.download_models

ENV EMBEDDING_ALLOW_DOWNLOAD=false \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1

RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app \
    && chown -R app:app /app /models
USER app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"

CMD ["uvicorn", "embedding_service.app:app", "--host", "0.0.0.0", "--port", "8080"]
