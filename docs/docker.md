# Docker deployment

This deploys the current React + FastAPI app, not the legacy Streamlit app.
Docker builds React and serves it through FastAPI on port 8000. Install Docker
Engine/Desktop with the Compose v2 plugin; on Windows use Linux containers.

## First deployment

Run these commands from the repository root:

```powershell
# Only if .env does not already exist:
Copy-Item .env.example .env
```

Edit `.env` and set `GROQ_API_KEY` and, for the complex-task model,
`DEEPSEEK_CHAT_API_KEY`. Keep `.env` private; it is supplied at runtime and never
copied into the image. Keep `EMAIL_ENABLED=false` unless real email is intended.

```powershell
docker compose build
# First-time setup only: imports the supplied catalog into an empty database.
docker compose run --rm app python -m backend.bootstrap --seed
# Prompts for a new password; existing admins are not overwritten.
docker compose run --rm app python -m backend.bootstrap --admin-email you@example.com
docker compose up -d --wait
```

On Linux/macOS use `cp .env.example .env` instead of `Copy-Item`.

- Storefront: http://localhost:8000
- Admin: http://localhost:8000/admin
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/api/health

Seeding is explicit, never performed on startup, and refuses a populated or
partially populated catalog. Skip it when using an existing database. After
changing the catalog with a separate bootstrap process, restart a running app
to rebuild its retrieval cache.

## Storage and existing data

The default database is `/app/data/ecommerce_saas.db` in the persistent Compose
`app-data` volume. Model downloads use the separate `model-cache` volume.
Rebuilds and `docker compose down` retain these volumes. **Do not run
`docker compose down -v` unless you intend to delete the stored data.** Back up
the database before upgrades.

The database files in your working folder are **not** bundled into the image or
automatically imported. This default starts a separate, empty database. To move
an existing SQLite shop, stop all writers, create a consistent snapshot with
SQLite's backup API (including committed WAL data), and restore that snapshot
as `/app/data/ecommerce_saas.db` in the volume while the app is stopped. Ensure
the database and directory are writable by UID/GID `10001:10001`. Do not copy a
live `.db` file alone or seed over an existing shop.

For an existing PostgreSQL deployment, add this to `.env` **only after any
required migration has succeeded**:

```dotenv
DOCKER_DATABASE_URL=postgresql+psycopg://USER:URL_ENCODED_PASSWORD@REACHABLE_HOST:5432/omnichat
```

Preserve any required TLS options, such as `?sslmode=require`. `localhost` inside
the container means the container itself, not the host; Docker Desktop can reach
host services via `host.docker.internal`. This Compose file does not provision or
migrate PostgreSQL. Follow the PostgreSQL migration section in `README.md` first.
The host's ordinary `DATABASE_URL` is intentionally ignored by Compose so a
Windows SQLite path or host-only PostgreSQL address cannot accidentally be reused.

## Optional dense retrieval

The default image uses lexical/SQL retrieval to keep builds small and avoid model
downloads. To install CPU-only dense dependencies and allow the first model
download, add to `.env`:

```dotenv
DOCKER_INSTALL_DENSE=true
EMBEDDING_ALLOW_DOWNLOAD=true
```

Then run `docker compose up -d --build --wait`. This needs more build time, disk
space and RAM. The Hugging Face model cache persists across container recreation;
once downloaded, you can set `EMBEDDING_ALLOW_DOWNLOAD=false` and recreate the app.
Health/UI reports lexical mode until the dense index is ready. `MCP_ENABLED=true`
also works inside the same container; it needs no separately exposed service.

## Network and operations

By default the published port binds only to `127.0.0.1`. For a remote server,
prefer an HTTPS reverse proxy on the same host. To deliberately publish on all
interfaces, set `DOCKER_BIND_HOST=0.0.0.0` in `.env`, restrict access with a firewall,
and configure HTTPS. Browser microphone access needs HTTPS outside localhost.
Change the host port with `DOCKER_PORT=8080` if needed.

```powershell
docker compose ps
docker compose logs -f app
docker compose up -d --build --wait
docker compose down
```

Run a single application container/worker. This setup does not add shared rate
limiting, cross-process chat locks, production identity, payment integrations or
an independent notification worker. The production/privacy boundaries documented
in `README.md` still apply; containerization alone does not remove them.
