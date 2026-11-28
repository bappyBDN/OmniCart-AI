# OmniCart embedding service

A small, authenticated FastAPI service that owns the only two vector models the
shop needs:

| Purpose | Default model | Dimension |
|---|---|---|
| Text (queries and documents) | `BAAI/bge-m3` | 1024 |
| Product photos | `google/siglip-base-patch16-224` | 768 |

Both are open-source, CPU-friendly, and downloaded at Docker build time. The
service never calls a paid embedding or rerank API. Chat/vision still use the
existing Groq/DeepSeek wiring in `backend/llm.py`.

## Endpoints

- `GET /health` — unauthenticated, returns `loading`/`ready`/`error`, model
  names/revisions/dimensions and process RSS.
- `POST /embed/text` — authenticated, JSON:
  ```json
  {"texts": ["nil kurti"], "kind": "query"}
  ```
  `kind` is `query` (512-token cap) or `document` (1024-token cap).
- `POST /embed/image` — authenticated, raw image bytes with
  `Content-Type: image/jpeg|png|webp|gif`; returns one normalized vector.

Every response includes `model_name` and `model_version`; callers store both
with each vector.

## Environment

| Variable | Default | Notes |
|---|---|---|
| `EMBEDDING_API_KEY` | empty | Required. Use a long random value; clients send `X-API-Key`. |
| `EMBEDDING_TEXT_MODEL` | `BAAI/bge-m3` | Any sentence-transformers-compatible model. |
| `EMBEDDING_TEXT_DIM` | `1024` | Must match the model output. |
| `EMBEDDING_TEXT_REVISION` | `main` | Pin a commit/tag for reproducibility. |
| `EMBEDDING_QUERY_MAX_TOKENS` | `512` | Queries are truncated before encoding. |
| `EMBEDDING_DOCUMENT_MAX_TOKENS` | `1024` | Document chunks are truncated before encoding. |
| `EMBEDDING_IMAGE_MODEL` | `google/siglip-base-patch16-224` | SigLIP image tower. |
| `EMBEDDING_IMAGE_DIM` | `768` | Must match the model output. |
| `EMBEDDING_IMAGE_REVISION` | `main` | Pin for reproducibility. |
| `EMBEDDING_ALLOW_DOWNLOAD` | `false` | `false` sets Hugging Face offline mode. |
| `EMBEDDING_PRELOAD` | `true` | Load models in a background thread at startup. |
| `EMBEDDING_REQUEST_TIMEOUT` | `20` | Per-request inference timeout. |
| `EMBEDDING_MAX_BATCH` | `64` | Text batch cap. |
| `EMBEDDING_MAX_IMAGE_BYTES` | `8000000` | Image cap. |
| `EMBEDDING_TORCH_THREADS` | `2` | Keep low on small VMs. |

## Build and run (4 GB Google Compute Engine VM)

One command from your PC (gcloud logged in; HTTPS via Caddy + sslip.io):

```bash
bash scripts/deploy_gce_embeddings.sh INSTANCE ZONE [PROJECT]
```

Manual equivalent (run from the repo root; the Dockerfile copies `embedding_service/`):

```bash
docker build -f embedding_service/Dockerfile -t omnicart-embeddings .
docker run --rm -p 8080:8080 \
  -e EMBEDDING_API_KEY='replace-with-a-long-random-key' \
  omnicart-embeddings
```

On Cloud Run, request **4 GiB** memory, CPU always allocated, concurrency 1–2,
and a startup probe on `/health`:

```bash
gcloud run deploy omnicart-embeddings \
  --source ./embedding_service \
  --region asia-south1 \
  --memory 4Gi \
  --cpu 2 \
  --timeout 300 \
  --allow-unauthenticated=false \
  --set-env-vars EMBEDDING_API_KEY='replace-me'
```

`/health` is intentionally unauthenticated for the platform probe; protect the
service URL with Cloud Run IAM or a private VPC when possible. Embedding routes
always require `X-API-Key`.

## 2 GB server fallback

If the VM has only 2 GB, use the 768-dim E5 model instead of BGE-M3:

```bash
EMBEDDING_TEXT_MODEL=intfloat/multilingual-e5-base
EMBEDDING_TEXT_DIM=768
```

The service adds the required asymmetric prefixes automatically:
`query: ` for queries and `passage: ` for documents.

Because Phase 2 created `knowledge_chunks.embedding` as `vector(1024)`, a 768-dim
model requires a one-time schema change **and re-embedding**:

```sql
DROP INDEX IF EXISTS ix_knowledge_chunks_embedding;
ALTER TABLE knowledge_chunks ALTER COLUMN embedding TYPE vector(768);
CREATE INDEX ix_knowledge_chunks_embedding
  ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
```

Do not mix vector dimensions in one column.

## Benchmark before choosing a VM

```bash
EMBEDDING_SERVICE_URL=http://127.0.0.1:8080 \
EMBEDDING_SERVICE_API_KEY=... \
python -m embedding_service.benchmark --count 100
```

It reports p50/p95 latency, throughput, vector dimension, model revision and the
service's RSS. Run it after `/health` is `ready`.

## Failure behaviour

The main app's `backend/embedding_client.py` has a 3-second query timeout, two
retries, a circuit breaker and a structured fallback result. When the service is
stopped or overloaded, retrieval receives `EmbeddingFallback(available=False)`
and stays on keyword search; customer replies continue.
