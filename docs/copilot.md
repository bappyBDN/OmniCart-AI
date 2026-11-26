# OmniCart Copilot: multimodal shopping, orders and support

This upgrade adds seven modules to the existing FastAPI + LangGraph backend. They are shared by
the web chat (`/api/chat`) and Facebook Messenger (`/webhook`). Every new table is
**additive**: `create_all` creates it on startup, and existing SQLite, PostgreSQL or Neon
databases need no `ALTER`.

| # | Module | Where |
| --- | --- | --- |
| 1 | Human-like persona, browse and no-match fallbacks | `backend/prompts.py`, `catalog.suggest_alternatives`, `agent.retrieve_node` |
| 2 | Photo → attributes → catalogue match | `backend/vision.py`, visual branch of `agent.retrieve_node` |
| 3 | Multi-turn memory and reference resolution | `chat_memory.py` (8 turns), `conversation.rewrite_product_query`, `conversation_states.visual_context` |
| 4 | Clean Messenger text and product cards | `messenger_format.py`, `messenger.send_product_cards` |
| 5 | Conversational order form and tracking | `backend/order_flow.py`, `backend/order_tracking.py` |
| 6 | Sentiment and urgency on every message | `backend/sentiment.py`, `message_sentiments` |
| 7 | Smart escalation and admin handover | `backend/escalation.py`, `support.open_case`, Handover Protocol |

## Request flow

```text
Messenger event ─┬─ photo ──► download (Meta CDN only) ──┐
                 ├─ postback / quick reply ─► order_flow ─┤ (ORDER_NOW, SIZE:, QTY:, ORDER_CONFIRM …)
                 └─ text ────────────────────────────────┤
Web chat ────────────────────────────────────────────────┤
                                                          ▼
                     sentiment.classify ─► message_sentiments + conversation_states
                                                          ▼
   LangGraph: supervisor ─► retrieve ─► reason ⇄ tools ─► ground
              │ handoff_trigger / sentiment escalate      ▲
              └────────► handoff ─► open_case(+triage) ─► alerts (dashboard, Telegram, webhook)
              visual_agent: vision.analyze_image ─► match_products ─► compose_visual_reply
```

## Tables

| Table | Purpose |
| --- | --- |
| `products`, `orders` | Existing catalogue and orders (unchanged) |
| `conversation_states` | One row per customer: `status` (`ACTIVE` / `ESCALATED`), `order_state` JSON, `active_product_id`, `visual_context`, last sentiment, negative streak |
| `message_sentiments` | Label, score, urgency and signals per customer message, linked to `chat_history`. Message text is not duplicated |
| `escalations` + `support_cases` | Existing cases. `support_cases.context.escalation` now holds the trigger, sentiment, product/photo context, summary and last message |
| `order_details` | Order code (`ORD-1001`), size/colour variant, name, phone, delivery address and channel for conversational orders |

## Photo search

1. The image comes from a Messenger attachment (`message.attachments[type=image]`; stickers are
   ignored) or a web upload.
2. Messenger images are fetched only from Meta CDNs (`fbcdn.net`, `fbsbx.com`, …), with
   redirects re-checked, a size cap, and re-encoding to a JPEG of 3 MB or less.
3. The vision model returns `VisualAttributes` JSON: category, colour, pattern, material,
   style, details and summary. Output is sanitised; it is treated as data.
4. `match_products` scores **live** SQL rows: product type ×3, primary colour ×2, material,
   style terms, plus a hybrid-retrieval rank bonus. Quality is `exact`, `close` or `related`.
5. The reply is deterministic and grounded. It acknowledges the photo, then lists up to two
   in-stock matches with description, sizes, colours, price and stock. It also says when the
   closest item is sold out, or politely declines when nothing is close.

Vision providers: Groq `VISION_MODEL` first. GPT-4o-mini (`OPENAI_API_KEY`) and Gemini
(`GEMINI_API_KEY`) are fallbacks; `VISION_PROVIDER` chooses which runs first.

## Messenger order flow

`Order Now` (card button) or text such as "I want to order this" for the focused product
starts the flow. The steps are size → colour → quantity → full name → phone → delivery
address → summary.

When `PUBLIC_SITE_URL` (or Render's external URL) is configured, the native button
is labeled **Order in chat**. **Order now** opens the mobile web delivery form, and
clicking the image/title opens the exact public product page. **More Details**
continues to use the `DETAILS:` postback. Both checkout paths require explicit
customer confirmation; neither opening a link nor starting a form places an order.

- Single-option sizes or colours and any quantity already in the message are filled in
  automatically. Saved profile details are reused; **Change details** re-collects them.
- Phone validation accepts 7–15 digits; Bangladeshi `01…` numbers must have 11 digits. The
  phone step offers Messenger's prefilled-number quick reply.
- Questions mid-flow ("what's your return policy?") are answered by the agent, followed by a
  reminder. `cancel` ends the flow at any step. The flow expires after 30 minutes.
- **Confirm Order** runs the existing `prepare_order` → `confirm_order` transaction. That
  re-checks the live price, decrements stock atomically and creates the order. Nothing is
  reserved before confirmation.
- After confirmation the code stores an `OrderDetail` (`ORD-1001`) and queues a
  new-order admin alert. It then sends a receipt with the order ID.
- Escalation-worthy messages, such as anger or "talk to a human", always bypass the flow.

Tracking: "track ORD-1001", "where is my order?", or an order ID plus phone number.
**A phone number alone never reveals another customer's orders.** Cross-channel lookups
need the order ID and the matching delivery phone.

## Sentiment and escalation

`sentiment.classify` is a fast lexicon and emphasis classifier for English, Bangla and
Banglish; it makes no model call. It returns `POSITIVE`, `NEUTRAL`, `NEGATIVE` or
`FRUSTRATED`, a score in [-1, 1], and urgency.

- `NEGATIVE`: model answers get an empathy instruction, and rule-based answers get a short
  apology prefix.
- Escalation fires on `FRUSTRATED`, on `NEGATIVE` with a score of −0.7 or lower, or after
  three negative turns in a row.
- Keyword triggers also escalate: a human request, refund/payment disputes, damaged or wrong
  items, and bulk/wholesale/custom-logo orders or quantities of `BULK_ORDER_THRESHOLD` or more.

When escalation fires:

1. A case opens with `urgent` priority for anger or disputes; the conversation becomes
   `ESCALATED`, and bot replies pause on both channels.
2. Triage context is stored and shown in **Admin → Overview → Escalation alerts**
   (also `GET /api/admin/escalations`). Telegram or webhook alerts go through the retrying
   notification outbox.
3. The customer gets a warm message that a specialist has been notified with their details.
4. If `META_HANDOVER_TARGET_APP_ID` is set, the thread is passed with `pass_thread_control`.

Resolving the case in the support desk sets the conversation back to `ACTIVE`.

## New admin endpoints

- `GET /api/admin/escalations`: open cases with sentiment, products, photo context, summary
  and last message.
- `GET /api/admin/sentiment?days=7`: label counts, average score and escalated
  conversations.
- `GET /api/admin/orders/{id}/details`: variant, phone and address from the order form.
- The dashboard payload also includes `escalation_alerts` and `sentiment`.

## Limits

- Stock is tracked per product, not per size or colour variant (as in the existing catalogue).
- Visual matching compares attributes with catalogue text. It suggests similar items; it does
  not identify an exact SKU from product photography.
- The Messenger webview order form is not implemented; the chat and quick-reply form covers
  the same fields.
- Per-message sentiment is heuristic. Tune the lexicons in `backend/sentiment.py` for your
  customers' vocabulary.
