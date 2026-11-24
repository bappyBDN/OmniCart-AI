# Deploy OmniCart AI on Render with PostgreSQL

## Fix an existing failing Render service

`sqlite3.OperationalError: unable to open database file` means the service is
using SQLite, not PostgreSQL. Without `DATABASE_URL`, the previous image fell
back to `/app/ecommerce_saas.db`, which the non-root runtime cannot write.
Making that directory writable would not fix persistence: Render's application
filesystem is ephemeral.

1. Create a Render Postgres database, or use your existing one. Put the database
   and web service in the **same region** for internal/private connectivity.
2. Open **Postgres → Info/Connect** and copy its **Internal Database URL**.
3. In **Web Service → Environment**, set:

   ```dotenv
   DATABASE_URL=postgresql://USER:PASSWORD@INTERNAL_HOST:5432/DATABASE_NAME
   DATABASE_REQUIRE_POSTGRES=true
   RAG_DENSE_ENABLED=false
   EMBEDDING_ALLOW_DOWNLOAD=false
   MCP_ENABLED=false
   EMAIL_ENABLED=false
   GROQ_API_KEY=YOUR_KEY
   DEEPSEEK_CHAT_API_KEY=YOUR_KEY
   ```

   Paste the URL Render provides, including its actual database name; do not
   replace it with `/omnichat` if your existing database has another name.
   `postgres://`, `postgresql://`, and `postgresql+psycopg://` all use Psycopg 3.
   Preserve any TLS options in an externally hosted database's URL.
4. Use the repository-root **Dockerfile**. Clear any old Docker Command override,
   or set it to `python -m backend.serve`. It binds to `0.0.0.0` and honors Render's
   `PORT` automatically. Set **Health Check Path** to `/api/health`.
5. Deploy the updated commit. Visit `https://YOUR-SERVICE.onrender.com/api/health`.
   It must report `database.backend: "postgresql"` and the expected database name.

**Important:** Render does not run `docker-compose.yml`. Its `db` hostname,
`.env` file, Compose volumes, `DOCKER_DATABASE_URL`, and Compose environment
settings are not automatically available there. Configure **DATABASE_URL** in
the Render web service itself. Never paste a database password/URL into chat,
source control, screenshots or logs. The new image fails clearly instead of
falling back to SQLite if its PostgreSQL configuration is missing or wrong.

## New deployment with a Blueprint

1. In Render, choose **New → Blueprint** and connect this GitHub repository on
   `main`. Render reads the root `render.yaml`.
2. Review the proposed resources before approving them. The file defines one
   Docker web service and one PostgreSQL 17 database, both in Singapore, with a
   private database connection and `/api/health` monitoring. It prompts for AI keys.
3. The `fromDatabase` reference wires `DATABASE_URL` automatically. No PostgreSQL
   username/password needs to be stored in Git.

The Blueprint explicitly uses **free** plans for evaluation. Render's free
Postgres **expires after 30 days** and has no managed backups; free web services
sleep when idle, have no shell access, and block standard SMTP ports. Choose paid
plans and backups before storing real orders/customer data. Render allows only
one free Postgres instance per workspace; if you already have one, configure the
existing web service as above instead of creating a duplicate.

## First-time catalog and admin setup

Startup creates missing tables but **never seeds data or overwrites an admin**.
An HTTP-healthy empty database will show `shop_ready=false` until setup completes.
Do not run seed if you plan to migrate your existing shop.

On a paid service with Render Shell, run:

```sh
python -m backend.bootstrap --seed
python -m backend.bootstrap --admin-email you@example.com
```

The second command prompts for a new password. Restart the web service after
seeding so its in-memory retrieval cache is rebuilt.

For a free service without Shell, you can run setup from your own computer:

1. Temporarily allow **only your public IP** in the database's external access
   rules. The Blueprint defaults to no external access.
2. Create a private local file `.env.render.local` containing the Render
   **External Database URL**, with `?sslmode=require` (or retain it if supplied):

   ```dotenv
   DATABASE_URL=postgresql://USER:PASSWORD@EXTERNAL_HOST/DATABASE_NAME?sslmode=require
   DATABASE_REQUIRE_POSTGRES=true
   SHOP_ID=shop_01
   ```

3. With Docker installed locally, run:

   ```sh
   docker build -t omnicart-setup .
   docker run --rm --env-file .env.render.local omnicart-setup python -m backend.bootstrap --seed
   docker run --rm -it --env-file .env.render.local omnicart-setup python -m backend.bootstrap --admin-email you@example.com
   ```

4. Remove the temporary external IP rule, securely remove the local credentials
   file when no longer needed, and restart the Render web service.

`.env.render.local` is ignored by Git and Docker builds. A remote Render internal
hostname will not resolve from your computer: use the External Database URL only
for this local setup and use the Internal Database URL in the Render web service.

For existing data, migration is a separate, explicit operation. Follow the
PostgreSQL migration guide in `README.md` before switching databases. The default
migration tool targets a separate database named `omnichat`; do not point it at an
existing populated database or assume that Render roles have `CREATEDB` permission.

## Runtime limits

Keep a single container/worker: chat locks and rate limits are still per-process.
Dense embeddings are disabled for the small image/free plan; SQL/lexical retrieval
works without Torch or model downloads. AI calls still require valid provider
keys and available quota. The security/commerce limitations in `README.md` still
apply. Do not deploy the legacy Streamlit entry point (`main.py`) on this service.
