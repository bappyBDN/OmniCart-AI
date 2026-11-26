# OmniCart AI · v2

React shopping workspace + FastAPI API + a LangChain/LangGraph agentic RAG pipeline.
The original Streamlit files are retained; the new application lives in `backend/`
and `frontend/`. Existing shop data and bcrypt admin accounts are reused.

The earlier GitHub README is preserved in [`docs/legacy-readme.md`](docs/legacy-readme.md)
for reference; follow the instructions below for the current application.

**Copilot upgrade:** human-like persona, photo search (web + Messenger), Messenger product
cards and a conversational order form, sentiment analysis and smart escalation with
admin alerts. See [`docs/copilot.md`](docs/copilot.md).

PRD 2.3 implementation progress and remaining release gates are tracked in
[`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md). The separate
back-office app is **http://localhost:8000/admin**, including the support desk,
versioned knowledge studio and support/knowledge audit trail.

## Run locally (Windows / PowerShell)

For a containerized deployment (React build + API + PostgreSQL), see
[Docker deployment](docs/docker.md) and `docker-compose.yml`. For Render, use
[`render.yaml`](render.yaml) and the [Render deployment guide](docs/render.md).

Python 3.11+ and Node 20.19+ (or Node 22.12+) are recommended. The current Vite 6
build also runs on this project's installed Node 20.11.

```powershell
# Use the existing venv, or create one: py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
npm --prefix frontend install
npm --prefix frontend run build
.\venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open **http://localhost:8000/**. API documentation: **http://localhost:8000/docs**.
`GET /healthz` returns `{"status":"active"}` for lightweight Messenger/Render liveness.
The storefront is also available at `/app`; Messenger uses `/webhook` independently.
After the first build, `.\run.ps1` starts the same app. Use one API worker for
the local SQLite deployment (chat locks/rate limits are per process).

For frontend hot reload, run these in separate terminals:

```powershell
.\venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
npm --prefix frontend run dev
```

The development UI is http://localhost:5173 and proxies `/api` to port 8000.

### Configuration and first-time setup

For Facebook Page verification, signed Messenger events, and Page subscription,
see [Messenger setup](docs/messenger.md). The callback is **`/webhook`** (GET/POST).

Keep your existing `.env`, or copy `.env.example` and fill in your own values.
Set `GROQ_API_KEY` (and optional backup keys), plus `DEEPSEEK_CHAT_API_KEY` for the
complex-task tier. The current model strategy is:

- **Small/routine:** `openai/gpt-oss-20b` via Groq, used for routing, common shopping
  and short synthesis.
- **Complex synthesis:** `deepseek-chat` (the account currently reports the served
  model as `deepseek-flash`) first, with Groq 120B/OpenAI/Hugging Face fallbacks.
- **Vision:** `qwen/qwen3.8-27b` via Groq.
- **Voice:** `whisper-large-v3-turbo` via Groq.

The key name `DEEPSEEK_CHAT_API_KEY` avoids a collision with any older
`Deepseek_API_Key` entry in the same environment. Override `CHAT_MODEL`,
`DEEPSEEK_MODEL`, `VISION_MODEL` or `STT_MODEL` when your account offers different
models. No provider keys are exposed to React.

### Conversation continuity and focused answers

Context is assembled in explicit priority order:

1. **Current user request** — authoritative.
2. **Recent chat** — up to eight user/assistant turns (16 messages) inside a bounded character budget.
3. **Older history** — a compact, lower-priority summary (bounded), never allowed
   to override the current turn.

Referential follow-ups (`“what size is it?”`, `“show me the image”`, `“is it in blue?”`)
reuse the previously cited product. A specific product name or SKU stays focused: the
retriever does not inject unrelated products, and tool results are filtered against
the focus. A new product request, comparison or alternatives request clears the focus.
Product images/cards appear only for browse/show/see/compare/find/visual requests;
price/stock/policy answers stay text-only. Token costs stay bounded by recent-window
trimming, older-history summarisation and the small/large model split.

Quantity-only follow-ups (`“i need 6”`, `“6 pcs”`, `“ok i need to buy 3”`) are answered deterministically from
the focused SKU's live stock with no model call, so a provider or validator outage can
never replace them with an unrelated catalogue list. If the previous turn cited several
products, the assistant asks which product instead of guessing. A bare quantity with no
focused product asks for the product name rather than scanning the catalogue.

Product context now survives source-free clarifications and policy detours. Banglish
references such as `“ata kano khabo?”` stay on the selected item. A new explicit
product name/SKU overrides older context, while ambiguous multi-product references
ask which item rather than choosing the first search result. Short quantity replies
inherit the conversation language.

`“I wanna order this”` asks for the missing quantity; an explicit quantity then
prepares a reviewable draft, never a confirmed order. Required name collection uses
only a name literally supplied by the customer. Active human review blocks automated
draft creation. Repeated no-match questions clarify and offer support; they no longer
silently create a support case merely because an unresolved-turn counter increased.

BDT questions preserve the selected product but do **not** fabricate exchange rates.
The assistant asks for an explicit `1 USD = [rate] BDT`, labels conversions as estimates,
and leaves the USD checkout unchanged. Health-product follow-ups explain verified
catalogue details without inventing benefits, safety, personal suitability or dosage.

Regression coverage: `tests/test_conversation_context.py`. To exercise the same flow
with configured live model providers in a **disposable seeded database**, run:

```powershell
.\venv\Scripts\python.exe -X utf8 scripts/smoke_conversation.py --live-model
```

This optional check can incur model-provider costs. It never confirms an order,
uses the real shop database, sends email or starts an MCP child process.

### Token optimisation and cost dashboard

Every model call records input/output/cached tokens, provider, served model and an
estimated USD cost in the chat turn. Admin → Settings shows total spend, cache reads
and a provider/model breakdown; Overview shows the aggregate AI cost. Rates are
configurable through the `*_COST_PER_M` values in `.env.example` and are estimates,
not provider invoices. DeepSeek peak/off-peak rates are not inferred automatically.

Optimisations in the current path:

- deterministic greetings, thanks, catalogue counts and English policy quotation do
  not call an LLM;
- small-model routing/synthesis, with the larger DeepSeek tier reserved for
  comparisons and complex tasks;
- last-eight-turn context trimming (16 messages) with a character budget;
- maximum five source chunks and short output limits;
- Graph RAG uses exact SQL rows before embeddings;
- citation/grounding checks prevent retrying invented facts;
- provider prompt-cache tokens are recorded when the provider reports them.

Voice transcription is per-audio-minute/Whisper pricing and is not included in the
token-cost dashboard.

If the database is empty, explicitly import the supplied catalog:

```powershell
.\venv\Scripts\python.exe -m backend.bootstrap --seed
.\venv\Scripts\python.exe -m backend.bootstrap --admin-email you@example.com
```

The second command prompts for a new admin password. There are no hard-coded
admin credentials and existing passwords are never overwritten. The seed command
only seeds a shop with **no products and no policies**; it refuses partial or
already-populated catalogs. It never resets inventory, customers or orders.

### PostgreSQL database

The API supports PostgreSQL through Psycopg 3. All current application records use
the same configured database: shops, inventory, policies, admin credentials,
customers, chat history, hashed access sessions, chat turn metadata, checkout
drafts, orders, support cases, rejected orders, and the notification outbox.
Derived embeddings can be rebuilt from PostgreSQL; the old Chroma/checkpoint
SQLite files are legacy backups and are not used by the new API.

#### Existing Neon database: `omnichat-db`

Set these values in your private `.env` (never commit credentials):

```dotenv
DATABASE_URL='postgresql://USER:PASSWORD@HOST/omnichat-db?sslmode=require&channel_binding=require'
DATABASE_REQUIRE_POSTGRES=true
```

Check the connection, then **stop all API/Streamlit writers** and migrate existing
local records into the empty remote database:

```powershell
.\venv\Scripts\python.exe -m backend.migrate_postgres --use-database-url --check
.\venv\Scripts\python.exe -m backend.migrate_postgres --use-database-url --migrate --source-paused
```

This uses the database named in `DATABASE_URL`, not `POSTGRES_ADMIN_URL` or
`POSTGRES_DATABASE`. It never creates another database or overwrites populated
tables. All imported row counts are verified, and the original SQLite is retained.
Restart with `run.ps1`; `/api/health` should report `database.backend=postgresql`
and `database.name=omnichat-db`. For Docker Compose, separately set
`DOCKER_DATABASE_URL` to the same private connection string. For Render, configure
`DATABASE_URL` in the service environment; local `.env` files are not uploaded.

#### Creating a separate database from an administrative connection

1. Install the current `backend/requirements.txt` (includes `psycopg[binary]`).
2. Put the administrative connection in `.env` as `POSTGRES_ADMIN_URL`, and set
   `POSTGRES_DATABASE=omnichat`. Keep credentials out of source control.
3. Check connectivity without changing databases:

   ```powershell
   .\venv\Scripts\python.exe -m backend.migrate_postgres --check
   ```

4. **Stop the API and any legacy Streamlit writer** before the actual import:

   ```powershell
   .\venv\Scripts\python.exe -m backend.migrate_postgres --migrate --source-paused
   ```

   This creates **only the separate `omnichat` database** if permitted, imports a
   consistent SQLite snapshot including committed WAL data, preserves IDs and
   session tokens, verifies all row counts, and resets PostgreSQL ID sequences.
   It refuses to merge into a populated target. Existing SQLite is not deleted;
   tables/data in the original administrative database are never modified.
   The role must have `CREATEDB`, or an administrator must create `omnichat` first.

5. Only after import succeeds, set the active application URL in `.env`:

   ```dotenv
   DATABASE_URL=postgresql+psycopg://USER:PASSWORD@REACHABLE_HOST:5432/omnichat
   ```

   URL-encode special password characters. Preserve required SSL query options
   (for example `?sslmode=require`) for a remote endpoint. Restart with `run.ps1`.
   `/api/health` must show `database.backend=postgresql` and
   `database.name=omnichat`; it never exposes passwords or the full URL.

Container-only service names generally cannot resolve from Windows localhost.
Use a reachable endpoint, an SSH tunnel, or run the API inside the database's
container network. Do not expose PostgreSQL publicly without TLS and restricted
network access. Until connectivity and migration are verified, leave
`DATABASE_URL` unchanged: the existing application continues using SQLite.

### RAG pipeline and measured retrieval

The runtime retrieval path is implemented in `backend/retrieval.py`:

```text
live SQL/published knowledge
→ Bangla/Banglish query analysis
→ SQL-first product filters (category/colour/size/budget/stock)
→ structure-aware policy chunks
→ BM25 + multilingual dense embeddings
→ reciprocal-rank fusion
→ deterministic reranker + strict relevance gate
→ cited source records
```

Dense vectors are built only from searchable text, not price/stock, so inventory
changes never require re-embedding. Product price and stock are hydrated from SQL
for every result. Product names, sizes, colours and descriptions are indexed; a
product alias added in the admin API is searchable immediately. Published knowledge
versions are indexed; drafts and rolled-back versions are not.

The evaluator is deterministic and read-only:

```powershell
.\venv\Scripts\python.exe -m scripts.eval_rag
.\venv\Scripts\python.exe -m scripts.eval_rag --dense
```

Current local evaluation (50 policy topics, 25 product/Banglish queries,
15 no-answer probes): **100% policy recall@1/@3, 100% product recall@1/@3,
100% no-answer accuracy** for both lexical and hybrid modes on this corpus. This is
a corpus-specific regression result, not a claim of universal 100% accuracy.
`RAG_MIN_CONFIDENCE` and `RAG_DENSE_MIN_SIMILARITY` are configurable. The dense
model loads from the local Hugging Face cache by default; set
`EMBEDDING_ALLOW_DOWNLOAD=true` only when a first download is acceptable. The first
cold load can take roughly 90 seconds on this CPU; lexical retrieval is available
immediately and the UI reports the actual mode.

Install optional dense dependencies with:

```powershell
.\venv\Scripts\python.exe -m pip install -r backend\requirements-dense.txt
```

Lexical fallback supports curated Bangla/Banglish aliases; dense embeddings provide
broader cross-language semantic matching. The UI and `/api/health` report the actual
mode; **Hybrid** appears only when the dense index is ready.

### Graph RAG + MCP database gateway

`backend/graph_rag.py` is a small LangGraph retrieval graph:

```text
analyse → structured SQL → relationship expansion → hybrid retrieval → fuse
```

It reads authoritative live rows through `backend/gateway.py`; when an exact
product is unavailable it can add same-category in-stock alternatives. The same
gateway is exposed by `backend/mcp_server.py` as a local **MCP stdio server** with
14 typed, read-only tools: catalogue summary/search/get/aliases, policies, published
knowledge, customer profile/orders/messages, recent orders, order lookup, support
cases and metrics. There is deliberately **no arbitrary SQL tool** and no write
tool; all mutations remain behind authenticated FastAPI admin/customer endpoints.

```powershell
# Prove the MCP process and tools work end to end
.\venv\Scripts\python.exe -m scripts.mcp_smoke
```

The local `.env` enables `MCP_ENABLED=true`, so Graph RAG routes its structured
database reads through the MCP child process. Set it to `false` to use the same
gateway in-process. A transport failure falls back to the local gateway/hybrid
retrieval. Example MCP client configuration is in `docs/mcp.md`.

### Voice notes and the visual product agent

The composer now records browser audio and posts it to
`POST /api/voice/transcribe`. The backend validates MIME/size (16 MB max), sends the
audio to the configured Groq Whisper model, and returns a transcript. Audio is not
persisted by this service; the transcript is treated as untrusted user text and goes
through the normal supervisor, Graph RAG and grounding checks. Live smoke test:
`"Hello, I am looking for a red shirt in medium size."` transcribed correctly.

Image turns are handled by a **visual specialist** selected by the supervisor. The
vision model extracts visible attributes, Graph RAG searches live catalogue rows, and
the chat returns up to four labelled product cards. Every product now has an image
URL: by default it is a deterministic category **illustration** generated by
`/api/products/{id}/image` (the supplied catalogue has no product photography). An
admin can attach a real image URL from **Admin → Catalogue → Image**; the same endpoint
then redirects to that image. Visual matches are similarity suggestions, not exact
product identification or authenticity proof.

The supervisor also routes shopping, support and order turns to named specialist
profiles with scoped tool authority. This is a supervisor/specialist execution model
inside one LangGraph runtime, not yet separate deployable agent services.

### Product images

The supplied catalogue had no photography. `scripts/seed_product_images.py` attaches
**real online Unsplash images** as type/category representatives to the existing
database (100 image records, verified HTTP URLs):

```powershell
.\venv\Scripts\python.exe -m scripts.seed_product_images
# Overwrite existing image records:
.\venv\Scripts\python.exe -m scripts.seed_product_images --force
```

The public `/api/products/{id}/image` endpoint serves/redirects to the configured
image. Admin → Catalogue → **Image** can replace any representative with the
merchant's own licensed product photo, or blank it to restore the generated
illustration. Representative images are for UI testing and must not be treated as
exact SKU photography or an authenticity claim.

## What changed

```text
React → FastAPI authentication + input validation
      → LangGraph: route → retrieve → reason ⇄ tools → evidence critic → answer
                            ↑ live SQL + BM25 / dense RRF ↓
                     cited LangChain Documents
```

- SQL is the source of truth for products, stock, discounted prices and policies.
  JSON/legacy Chroma indexes are not used for runtime facts.
- Shop-scoped retrieval, relevance gating, explicit product-type constraints,
  budget filtering, policy chunks, and source cards instead of unconditional
  nearest-neighbor matches. Dense search uses reciprocal-rank fusion with BM25.
- Up to four tool rounds; tool validation errors are returned to the agent.
  Tools cover product/policy search, decimal-safe quantity quotes, profile validation/update, checkout proposals,
  real order history, image-driven matching and support escalation.
- A second model pass removes unsupported answer claims; citation IDs, product/order
  IDs, product names, stock counts and monetary amounts are checked in code.
  Provider/critic failures return original retrieved records and queue human recovery
  rather than inventing an answer. This reduces, not eliminates, model errors.
- SQL chat history persists across refreshes and process restarts. Each turn is
  supplied to the graph once; the new app does not reuse legacy in-memory graph
  checkpoints, avoiding duplicate histories. Request UUIDs deduplicate retries.
- Checkout drafts expire after 15 minutes. **Only a user-confirmed checkout API call
  creates an order.** Atomic inventory decrement, live price recheck, transactional
  order creation and draft idempotency prevent negative stock and duplicate orders.
  The review screen collects an editable phone number and full delivery address;
  both are validated and saved to the profile before the order is created.
- Notification outbox reports disabled / not configured / queued / sent / failed
  honestly. `EMAIL_ENABLED=false` by default prevents accidental real emails.
  Enable it to reuse `EMAIL_SENDER`, `EMAIL_PASSWORD`, `ADMIN_EMAIL` and SMTP settings.
  Admins can retry failures through `/api/admin/notifications/retry` (max 3 attempts).
- React pages: chat with operational traces/citations/image upload; live catalog
  and quantity selection; profile; checkout review; orders; searchable policies;
  bcrypt admin login; inventory/policy editing; order transitions; support cases;
  human takeover/replies/hand-back. Chats poll for human replies every 5 seconds.

### Privacy and boundaries

Customers receive a random, hashed-at-rest access token. Entering somebody else's
email **does not log you into their account**. The guest token is stored in browser
local storage and expires after seven days; losing it means losing access to that
guest session. Administrator tokens are tab-scoped and expire after one day.
Legacy email-only customer sessions are not auto-linked, because that would expose
other customers' conversations and orders. Admins can still view legacy data.

Images are verified (PNG/JPEG/WebP, 3 MB, 16 megapixels), sent to the configured
vision provider and not saved. Vision matching is descriptive, not an authenticity
or exact-SKU guarantee. Uploaded images may be processed by your external AI provider.
Profile details and recent conversation are also sent to the selected AI provider.

This app records **merchandise-only orders**, not paid transactions. No payment
gateway, tax/shipping calculator, conditional discount eligibility verification,
SMS/WhatsApp gateway, or carrier tracking API is connected. Policies mentioning
those services are informational; the assistant is instructed not to claim they
were performed. Use a production identity provider, HTTPS, shared rate limiting,
PostgreSQL/Redis locks, an independently supervised outbox worker and audited
commerce/payment integrations before exposing this local app publicly.

## Tests

Tests use isolated temporary SQLite databases; they never modify your real shop.

```powershell
.\venv\Scripts\python.exe -m pytest
npm --prefix frontend test
npm --prefix frontend run build
```

Browser integration tests (Microsoft Edge installed):

```powershell
npm --prefix frontend run test:e2e
```

Playwright starts an isolated test app on port 8001 with a disposable database,
synthetic admin and deterministic AI replies. It covers chat citations/reload,
profile → catalog → confirmation → orders, and human support takeover/reply/resume.
It does not modify the running shop on port 8000. You can also run
`.\venv\Scripts\python.exe -m scripts.smoke_live` for optional real-provider checks;
those use a separate synthetic database and consume API quota.

Coverage includes English/Bangla/Banglish retrieval targets, every stored policy
topic, attribute-only queries, irrelevant-query rejection, live inventory updates,
tenant/session isolation, profile grounding, bounded tool loops, invalid citations,
invented product names/stock/amounts, provider failures, checkout
confirmation/expiry/price changes, concurrent overselling/double submission,
cancellation restocking, admin handoff and token revocation. The RAG evaluation is
separately runnable with `python -m scripts.eval_rag`. Automated LLM tests are
deterministic fakes; they do not certify every real model response or external
provider's availability.

## Key files

| Path | Purpose |
| --- | --- |
| `backend/main.py` | API, validation, authorization, SPA hosting |
| `backend/agent.py` | LangGraph workflow, LangChain tools and grounding critic |
| `backend/retrieval.py` | Live Documents, SQL filters, BM25, dense RRF, reranker, citations |
| `backend/graph_rag.py` | LangGraph Graph RAG: analyse → SQL → expand → hybrid → fuse |
| `backend/gateway.py` | Typed, shop-scoped read-only database tools |
| `backend/mcp_server.py` | MCP stdio server exposing the read-only gateway |
| `backend/mcp_client.py` | Optional MCP transport for Graph RAG |
| `backend/voice.py` | Validated Groq Whisper voice-note transcription |
| `backend/media.py` | Labelled product illustrations and real-image fallback |
| `backend/language.py` | Deterministic Bangla/Banglish normalisation |
| `scripts/eval_rag.py` | Read-only lexical and hybrid retrieval evaluation |
| `scripts/mcp_smoke.py` | End-to-end MCP transport/tool smoke test |
| `backend/commerce.py` | Profiles, safe checkout, support escalation |
| `backend/records.py` | Additive session/turn/draft/notification tables |
| `backend/llm.py` | Provider configuration and correctly bound fallbacks |
| `frontend/src/` | React application |
| `tests/` | Isolated backend regression tests |
