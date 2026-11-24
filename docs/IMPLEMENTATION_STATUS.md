# PRD 2.3 implementation status

Source: `PRD - Agentic Commerce Assistant (LangChain + LangGraph).md` (2,324 lines),
cross-checked against the accompanying DOCX. This is a phased implementation, **not
a declaration that the full production PRD is complete**.

## This increment: web agent core + human recovery

| PRD requirement | Implementation / verification |
|---|---|
| FR-SH-01, FR-BN-01/02/04/05 | Deterministic English/Bangla/Banglish normalization, curated product aliases, SQL-first color/size/budget filters, live price/stock hydration. `backend/language.py`, `retrieval.py`; multilingual retrieval tests. |
| RAG-01/02/04/05/06/09/12/13 | Live SQL corpus, structure-aware policy chunks, BM25 + optional multilingual dense retrieval, reciprocal-rank fusion, deterministic reranking, strict relevance acceptance, SQL-first attribute filtering, live price/stock hydration, cited source metadata. Unknown product/policy questions do not invoke a model to guess; knowledge drafts, immutable versions, atomic publish and rollback. Dense vectors exclude volatile price/stock. |
| §12.3 / §23 evaluation | `scripts/eval_rag.py` and `tests/test_rag_eval.py` measure 50 policy topics, 25 product/Banglish queries and 15 no-answer probes. Current local result: 100% policy/product recall@1/@3 and 100% no-answer for lexical and hybrid modes. This is corpus-specific evidence, not universal accuracy. The 150+ policy/120 Banglish PRD-scale independent eval set is still open. |
| FR-SU-04, FR-HI-01 | Rules-first explicit-human/strong-distress/authority escalation, no LLM needed. Repeated unresolved retrieval asks for clarification and offers opt-in human help, not automatic enrollment. Provider failures with usable evidence return verified records and offer help. Two consecutive operational tool failures and unrecoverable runtime failures still use human recovery. Routine shopping routes cannot invoke the handoff tool without an eligible request/consent. |
| FR-HI-02/03/04/05 | Support desk with transcript/profile/live recent orders, handoff snapshot, priority, assignment, internal notes, SLA deadline/overdue display, resolution codes and operator resume note. There is **no staffed-agent availability guarantee or external SLA alert worker yet**. |
| NFR-04, §9.7 | Typed LangGraph checkpoints and pending writes in the **configured application database**, including durable `interrupt()`/`Command(resume=...)` for human handoff. One checkpoint thread per request, bounded conversation history supplied once; retries resume pending work, not replay completed side effects. This is a custom full-snapshot SQLAlchemy saver, not the PRD's native PostgresSaver + Redis hot cache. |
| §9.5 / §9.8 | Route-scoped tool allowlists; read-only policy/order routes; tools validate ownership, inputs and authoritative amounts. Checkout proposals still require UI confirmation. |
| §9.9, §24 | Natural, concise AI persona; no impersonation of a human; Banglish → Bangla reply instruction; rule-only greetings/thanks; small model for routine synthesis, larger model for comparisons; last eight messages within a character budget; at most five source chunks. Current request > recent chat > bounded older-history summary. Referential product follow-ups use a focused SKU; specific-product answers/tool results do not introduce unrelated products. Product images are opt-in for browse/show/see/compare/find/visual requests, not forced into factual answers. Quantity-only follow-ups are answered from the focused SKU's live stock deterministically (no model/validator dependency); ambiguous multi-product lists and bare quantities without focus ask for clarification instead of listing the catalogue. |
| FR-CO-05 | Email optional. Name is required before a checkout proposal; an editable, validated phone number and full delivery address are collected on the review screen and saved before confirmation. |
| FR-AD-02/08/14, partial FR-AD-01 | Separate operations app at `/admin`: overview dashboard with live revenue/order pipeline/RAG health, catalogue search and editing, alias creation, order search/filter/state changes, customer inbox, support desk, knowledge studio, policy editor, audit trail and system health. Initial legacy-role-compatible server authorization remains; full role matrix, mandatory 2FA/SSO, step-up and four-eyes workflows remain open. |
| §9 agentic retrieval / §8.3 tool layer | `backend/graph_rag.py` LangGraph retrieval graph (analyse → structured SQL → relation expansion → hybrid retrieval → fuse). `backend/gateway.py` provides typed, shop-scoped read-only database tools; `backend/mcp_server.py` exposes 14 tools over MCP stdio; `backend/mcp_client.py` optionally routes Graph RAG through that server with local fallback. No arbitrary SQL or write tools. |
| §9.5 supervisor / §9.8 specialist agents | A supervisor node selects `shopping_agent`, `support_agent`, `order_agent`, `visual_agent` or human handoff. Each profile has scoped tools, prompt and trace metadata. The runtime is one LangGraph process; separately deployed agent services are still open. |
| FR-CH-11/12, FR-VO-01/02/03/06 | Web voice-note recording, 16 MB/MIME validation, Groq Whisper transcription (`STT_MODEL`), transcript-only processing and no audio persistence. Audio is not stored in object storage, no self-hosted STT fallback exists, and Messenger/WhatsApp voice notes are not implemented. |
| FR-SH-06 / visual product cards | Image-to-attributes vision pass, Graph RAG product lookup and labelled similar-product cards. The supplied catalogue has no photography, so a deterministic category illustration is used; admins can attach a real image URL. Matching is similarity, not exact identity/authenticity. |
| FR-AD-01 / product media | Admin “Image” action stores a validated http(s) product image URL and the public image endpoint redirects to it; blank restores the generated illustration. The local database was seeded with 100 real online representative images for testing (`scripts/seed_product_images.py`). These are not exact SKU photography and must be replaced with licensed merchant media for production. |
| §24 cost / token monitoring | DeepSeek is the complex-task provider with Groq small/vision/STT models and fallbacks. Every model response records input, output, cached tokens, provider, served model and estimated cost. Admin Overview/Settings aggregate spend by model; rates are configurable estimates. Whisper audio cost and DeepSeek peak/off-peak billing are not automatically inferred. |
| §23 / §19 | Isolated regression tests and browser workflows; CI runs backend/frontend/build/browser gates. Target p95/accuracy/containment metrics are **not certified** by this test suite. |

### Operational behavior

- Explicit human request: open/reuse ticket → capture context → pause AI → acknowledge
  accurately as **queued**, not already assigned → persist graph interrupt.
- Unverifiable policy/product questions: explain the limitation, ask a focused
  clarification and offer human help. Repeated misses alone do not create a case;
  an explicit request/accepted offer or an operational authority/failure condition
  is required. A subsequent purchase request is not consent to escalation.
- Source-free answers preserve the last product identity; explicit topic resets stop
  recovery. Price/stock are freshly hydrated, not copied from conversation memory.
  Banglish purpose questions, BDT clarification and quantity collection have deterministic
  guarded paths. Multiple candidate products require a selection, never top-1 guessing.
- While paused: save inbound messages only; **no LLM call or automated chat reply**.
  Immediate admin takeover suppresses a late in-flight AI answer at commit time.
- Resolving a case resumes its interrupted graph, records a resolution note and audit,
  and returns automation only when no other open case remains. Private investigation
  notes are never inserted into customer history or model context.
- Original SQL policies are still authoritative. The PRD's example default return
  window is not used to overwrite the store's existing 14-day return policy.
- English policy clauses are rendered verbatim with citations, without generative
  additions. Live evaluation caught a critic inventing an inspection/approval step;
  that case now has a no-model regression test. Translated answers still require
  evaluation and human review; they are not guaranteed infallible.
- Raw uploaded image bytes are held only for the in-flight request, never embedded
  in graph checkpoints. A retry after a process crash must reattach the image.
- Existing sample catalog currency remains USD; this increment does not relabel
  existing amounts as BDT or fabricate Bangladesh courier rates.

## Still open / next milestones

1. **P0 infrastructure:** reachable PostgreSQL endpoint, actual `omnichat` migration,

   pgvector/pg_trgm/FTS indexes and native production database integration tests;
   Redis-backed locks/cache/idempotency, Alembic deployment migrations, production
   retention/backup/restore and encrypted PII. The supplied internal PostgreSQL
   hostname has not resolved from Windows; SQLite is intentionally still active.
2. **RAG hardening still open:** a separately hosted cross-encoder reranker
   (the current reranker is deterministic and explainable), heading/token-aware
   ingestion for PDF/DOCX/HTML sources, effective-date filtering, semantic FAQ cache,
   independently curated PRD-scale 150-policy/120-Banglish evaluation, and calibrated
   probability scores. Current normalized confidence is a heuristic relevance measure,
   not a calibrated probability. Dense cold-load on this CPU is ~90 seconds; lexical
   retrieval is available immediately and the UI reports the mode.
3. **P1 voice/widget still open:** web voice transcription is implemented, but audio
   object-storage retention, a self-hosted STT fallback, a Bangla/English WER accuracy
   corpus, Messenger/WhatsApp voice media, the embeddable Shadow DOM widget, true SSE
   streaming, message debouncing, feedback, preferences with consent and rolling
   summaries remain.
4. **P2 commerce:** durable multi-item cart/variants/coupons, NUMERIC/minor-unit schema
   migration, confirmation hash including address/payment/totals, deterministic
   cancellation/returns, configurable authority thresholds and approval flows.
5. **P2 fulfilment/payments:** provider-selected hosted payments and signed webhooks;
   COD risk states and reviews, canonical Bangladesh location data, live courier
   adapters/sync/book/track/failover. Requires verified merchant credentials and
   sanctioned sandbox tests. No mocked integration is labelled a live payment or parcel.
6. **Admin hardening:** full PRD role matrix, assigned-conversation scopes, mandatory
   2FA/SSO, step-up, four-eyes approvals, IP allowlists, immutable database-enforced
   audit, reason codes on *all* legacy mutations, bulk import/export and manual
   finance/fulfilment recovery. Audit added here covers new support/knowledge actions;
   it does **not** claim every legacy endpoint is audited.
   Input/output injection classifiers, comprehensive PII redaction, encrypted fields
   and adversarial evaluation also remain production release gates.
7. **P3–P5:** independent security/accessibility/load tests, SLO/DR game days, Meta
   approval/signatures, channel identity, opt-in and 24-hour templates. Messenger
   and WhatsApp remain disabled/not implemented pending their readiness gates.

## Useful operator paths

- Customer UI: `/`
- Separate operations application: `/admin` (`/admin.html` during Vite development)
- Admin → **Support desk**: queue, investigate, assign, note, reply, resolve/resume
- Admin → **Knowledge studio**: draft, preview retrieval, publish, roll back
- Admin → **Audit trail**: support/knowledge events (read only)
- `RAG_MIN_CONFIDENCE` (default 0.45), `CONTEXT_MAX_CHARS` (12,000),
  `LLM_SMALL_MODEL` / `CHAT_MODEL`: environment configuration, restart required
