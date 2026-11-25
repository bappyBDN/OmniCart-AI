# Facebook Messenger webhook

The existing FastAPI application exposes:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Customer storefront |
| GET | `/healthz` | `{"status":"active"}` liveness (no SQL/LLM calls) |
| GET | `/webhook` | Meta's plain-text verification challenge |
| POST | `/webhook` | Signed events, immediate `{"status":"ok"}`, then background processing |
| GET | `/app` | Customer storefront alias (existing links still work) |
| GET | `/admin` | Operations console |

Implementation: `backend/messenger.py`, `backend/messenger_service.py`.
Dependencies `httpx` and `python-dotenv` are already in `backend/requirements.txt`.
The website and Messenger share one FastAPI service without competing for `/`:
Meta only calls `/webhook`. Do not point Meta's callback at the storefront or health check.

## 1. Configure secrets

Set these in the local ignored `.env` **and separately in Render → service →
Environment**. Render does not upload your local `.env`.

```dotenv
VERIFY_TOKEN=omnichat_bappys_4001-try
PAGE_ACCESS_TOKEN=YOUR_PAGE_ACCESS_TOKEN
APP_SECRET=YOUR_META_APP_SECRET
FACEBOOK_PAGE_ID=YOUR_NUMERIC_PAGE_ID
```

`APP_SECRET` is from **Meta App settings → Basic → App secret**. It is NOT your
Page access token or verify token. Never use placeholder secrets in production.
`FACEBOOK_PAGE_ID` is recommended to restrict this single-shop integration to one
Page. The verify token has the requested default; a missing/empty Page token or
App secret does not enable an insecure fallback.

POST requests require `X-Hub-Signature-256: sha256=<HMAC-SHA256 of raw body using
APP_SECRET>`. Missing/incorrect signatures return 403. A missing App secret returns
503; GET verification and liveness remain available. Malformed JSON returns 400;
bodies over 1 MiB return 413. Other valid signed event types are acknowledged with
200, including echoes, deliveries, reads, postbacks and non-page test objects.
Only nonempty text messages with a sender ID, Page ID, and message ID are processed.
Postbacks are acknowledged but do not yet invoke the chat bot.

## 2. Deploy and verify the callback

Deploy this code and restart the service. Startup adds `messenger_contacts` and
`messenger_events` to the **configured application database**, without replacing
existing data. Ensure the database role can create those tables.

1. Open `https://YOUR-SERVICE.onrender.com/healthz` and wait for `{"status":"active"}`.
   Open `https://YOUR-SERVICE.onrender.com/` to use the webpage.
2. In the Meta app's Messenger/Webhooks settings, configure the **Page** callback:
   - Callback URL: `https://YOUR-SERVICE.onrender.com/webhook`
   - Verify token: `omnichat_bappys_4001-try` (or your configured value).
3. Click **Verify and save**. There must be no auth proxy, login page, trailing-slash
   redirect, or HTML rewrite in front of `/webhook`.
4. Subscribe the Page object to `messages` and optionally `messaging_postbacks`.

Manual verification (no Page token or App secret needed):

```powershell
curl.exe "https://YOUR-SERVICE.onrender.com/webhook?hub.mode=subscribe&hub.verify_token=omnichat_bappys_4001-try&hub.challenge=123456"
```

Expected: HTTP 200, `Content-Type: text/plain`, body exactly `123456` (no quotes).
An incorrect token returns 403. Avoid sharing access logs containing verify URLs.

**Render free-service cold starts cannot reliably meet Meta's 3-second deadline.**
Waking the service helps initial verification, but production needs an always-on
instance and sufficient capacity. Startup still initializes the application DB;
the liveness handler itself does not query it.

## 3. Check and subscribe the actual Page

Callback verification and Page subscription are separate operations. Use the
generated **Page access token**, not a user/app access token. The Page token needs
the applicable `pages_manage_metadata` and `pages_messaging` permissions and the
Page/account tasks required by Meta. Public users also require the appropriate app
mode, advanced access/App Review and business requirements for your app.
If the token check returns code 100 requesting `pages_read_engagement`, grant that
permission for the Page metadata lookup and regenerate the Page access token.

```powershell
# Read-only token + subscribed-app checks (credentials never appear in the URL):
.\venv\Scripts\python.exe -m backend.messenger_setup

# Explicitly subscribe the Page to this token's app, then check subscriptions:
.\venv\Scripts\python.exe -m backend.messenger_setup --subscribe
```

Set `FACEBOOK_PAGE_ID` to the returned Page ID. Match the returned subscribed app
ID with your dashboard. Meta permissions, expired tokens, missing review and app
mode restrictions cannot be fixed by returning a successful webhook response.
The script never sends a message to a real user. It only changes a subscription
when you explicitly pass `--subscribe`.

## Processing, safety and operational limits

- The HTTP route checks the raw-body signature, decodes JSON, schedules
  `BackgroundTasks`, and returns 200 **before** SQL, LLM or outbound HTTP work.
  Synchronous chat/SQL work runs in the thread pool, not on the event loop.
- Page-scoped senders get persistent customer identities. Existing chat context,
  agent checkpoints, message history and handoffs use the configured database.
- Echoes cannot trigger replies. Page ID plus message ID deduplicates events in SQL,
  including across workers. Do not delete deduplication rows while Meta might retry.
- Replies use `POST https://graph.facebook.com/v21.0/me/messages`, a Bearer token,
  `messaging_type=RESPONSE`, explicit timeouts and 2,000-character chunks. Respect
  Meta's messaging window and policies; this does not enable unsolicited messaging.
- No automatic checkout confirmation is possible through text. Drafts remain
  proposals. Admin-console replies are not yet forwarded to Messenger.
- Inspect `messenger_events.status`: `processing`, `sent`, `human_handoff`, `failed`.
  Logs omit payload text, tokens and raw Graph errors. `sent` means the Send API
  accepted the reply, not proof that the recipient read it.
- Send errors/timeouts are not automatically retried: Meta might already have sent
  a reply or some chunks. Reconcile failed/stale events with Meta before any manual
  replay. Repeated webhook deliveries of those events are safely ignored.
- **BackgroundTasks is best-effort, not a durable job queue.** A crash after the ACK
  but before the inbox insert can lose an event. A crash after Send API acceptance
  can leave an uncertain `processing` row. Strict no-loss delivery requires a
  durable queue/inbox write before ACK and a supervised worker; that changes the
  requested no-database-on-ACK design. Do not claim exactly-once delivery.
- Use one API worker/instance for the current in-process conversation locks. These
  serialize reply generation but do not guarantee Meta event/delivery ordering.
  Multiple workers need distributed per-conversation serialization and a durable
  worker queue. Set monitoring/alerts for background failures, token expiration,
  backlog and resource exhaustion. Adopt a retention policy for inbox message text.

## Troubleshooting `LocalProtocolError`

This is a local HTTP protocol failure, not a Meta permissions response. A common
cause is an `Authorization` token copied with spaces, line breaks or other invalid
header characters. The exception alone does not prove the exact cause.

- Credentials are loaded with `.strip()` to remove surrounding whitespace. Internal
  whitespace, control characters, non-ASCII characters and pasted quotes are
  rejected before sending a request. Do not remove internal line breaks to guess
  a repaired token: paste a fresh, intact Page token instead.
- In Render **Environment**, set `PAGE_ACCESS_TOKEN` to the raw token on one line,
  without quotes, `Bearer `, or `PAGE_ACCESS_TOKEN=`. `python-dotenv` removes normal
  `.env` quotes, but Render's environment field is not a `.env` parser.
- Both Graph clients explicitly use `http2=False`. HTTPX already defaults to
  HTTP/1.1, so that setting alone does not repair an invalid header.
- Save the Render environment and restart/redeploy. Existing process environment
  variables take precedence over `.env`; changing a local file cannot repair a
  malformed token configured on Render.
- If validation passes but the protocol error persists, inspect outbound proxy
  configuration (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`) without sharing proxy
  credentials. The code does not disable required proxies or TLS verification.
- Never print the full `LocalProtocolError`: it may contain the Authorization
  header. Logs deliberately provide sanitized diagnostics instead.
- After restarting, run `python -m backend.messenger_setup` for a **read-only**
  token/subscription check, then send a **new** test message to the Page. Previously
  failed events remain deduplicated and are not automatically replayed.

The `/api/checkout`, `/api/profile`, and `/api/messages` HTTP 200 access logs are
successful website requests, unrelated to this outbound Messenger failure.

## Tests

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_messenger.py -q
```

Tests use an isolated SQLite fixture and a mocked Send API: they never use the
real Page token, call an LLM, send a message, or change a Meta subscription.
