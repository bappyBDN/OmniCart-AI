"""Deploy embedding_service/ to a private Hugging Face Docker Space.

NOTE: Hugging Face now requires a PRO subscription to host Docker/Gradio
Spaces, even on CPU basic (create_repo returns 402 otherwise). The CPU basic
hardware (2 vCPU, 16 GB RAM) fits BAAI/bge-m3 + SigLIP base. Free
alternatives: `docker compose --profile embeddings up -d` locally, or the same
image on your own VM (see embedding_service/README.md).

Usage (from the repo root):
    python scripts/deploy_hf_space.py --dry-run
    python scripts/deploy_hf_space.py --wait --write-env

What it does:
  1. Reads HUGGINGFACEHUB_API_TOKEN from .env (never prints it).
  2. Ensures EMBEDDING_API_KEY exists in .env (generates a strong one if empty)
     and mirrors it into EMBEDDING_SERVICE_API_KEY for the main app.
  3. Creates/updates a PRIVATE Docker Space <user>/<name>, stores the API key
     as a Space secret and model settings as Space variables.
  4. Uploads Dockerfile + embedding_service/ (Dockerfile runs as uid 1000).
  5. --wait polls /health until "ready"; --write-env then sets
     EMBEDDING_SERVICE_URL and EMBEDDING_SERVICE_ENABLED=true in .env.

Rollback: set EMBEDDING_SERVICE_ENABLED=false in .env (the app falls back to
keyword search), or delete the Space in the Hugging Face UI.
"""
from __future__ import annotations

import argparse
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
SERVICE_DIR = ROOT / "embedding_service"
SKIP_PARTS = {"__pycache__", ".pytest_cache"}
SKIP_NAMES = {".dockerignore", "Dockerfile"}

SPACE_VARIABLES = {
    "EMBEDDING_TEXT_MODEL": "BAAI/bge-m3",
    "EMBEDDING_TEXT_DIM": "1024",
    "EMBEDDING_TEXT_REVISION": "main",
    "EMBEDDING_IMAGE_MODEL": "google/siglip-base-patch16-224",
    "EMBEDDING_IMAGE_DIM": "768",
    "EMBEDDING_IMAGE_REVISION": "main",
    "EMBEDDING_TORCH_THREADS": "2",
    "EMBEDDING_PRELOAD": "true",
}


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def set_env_values(updates: dict[str, str], path: Path = ENV_PATH) -> None:
    """Replace KEY=... lines in place (first occurrence) or append them."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(updates)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in pending:
            out.append(f"{key}={pending.pop(key)}")
        else:
            out.append(line)
    if pending:
        out.append("")
        out.extend(f"{key}={value}" for key, value in pending.items())
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def space_readme(title: str) -> str:
    return f"""---
title: {title}
emoji: 🔎
colorFrom: blue
colorTo: green
sdk: docker
app_port: 8080
pinned: false
---

Private embedding service for OmniCart AI (bge-m3 text + SigLIP image).
Every request needs the X-API-Key header; see embedding_service/README.md.
"""


def space_dockerfile() -> str:
    text = (SERVICE_DIR / "Dockerfile").read_text(encoding="utf-8")
    if "ARG APP_UID=10001" not in text:
        raise SystemExit("embedding_service/Dockerfile no longer has ARG APP_UID=10001")
    return text.replace("ARG APP_UID=10001", "ARG APP_UID=1000")


def service_files() -> list[Path]:
    files = []
    for path in sorted(SERVICE_DIR.rglob("*")):
        if not path.is_file() or path.name in SKIP_NAMES or path.suffix == ".pyc":
            continue
        if SKIP_PARTS.intersection(path.parts):
            continue
        files.append(path)
    return files


def wait_until_ready(url: str, hf_token: str, minutes: float) -> bool:
    import httpx

    deadline = time.monotonic() + minutes * 60
    last = ""
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{url}/health", timeout=15,
                                 headers={"Authorization": f"Bearer {hf_token}"})
            status = response.json().get("status") if response.status_code == 200 else f"http_{response.status_code}"
        except Exception as exc:  # building/sleeping Spaces return HTML or reset
            status = type(exc).__name__
        if status != last:
            print(f"  health: {status}")
            last = status
        if status == "ready":
            return True
        if status == "error":
            print("  service reported a model load error; check the Space logs")
            return False
        time.sleep(20)
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default="omnicart-embeddings", help="Space name (default: omnicart-embeddings)")
    parser.add_argument("--dry-run", action="store_true", help="show what would be uploaded; change nothing")
    parser.add_argument("--wait", action="store_true", help="wait for /health to report ready")
    parser.add_argument("--wait-minutes", type=float, default=30.0)
    parser.add_argument("--write-env", action="store_true",
                        help="after a ready health check, enable the service in .env")
    args = parser.parse_args(argv)

    env = read_env()
    hf_token = env.get("HUGGINGFACEHUB_API_TOKEN", "")
    if not hf_token:
        print("HUGGINGFACEHUB_API_TOKEN is missing from .env", file=sys.stderr)
        return 2

    from huggingface_hub import CommitOperationAdd, HfApi

    api = HfApi(token=hf_token)
    user = api.whoami()["name"]
    repo_id = f"{user}/{args.name}"
    url = f"https://{user}-{args.name}".replace("_", "-").replace(".", "-").lower() + ".hf.space"
    files = service_files()

    print(f"Space:  {repo_id} (private, docker)")
    print(f"URL:    {url}")
    print(f"Files:  Dockerfile, README.md, " + ", ".join(str(p.relative_to(ROOT)).replace("\\", "/") for p in files))
    print(f"Vars:   {', '.join(f'{k}={v}' for k, v in SPACE_VARIABLES.items())}")
    if args.dry_run:
        print("Dry run: nothing changed.")
        return 0

    api_key = env.get("EMBEDDING_API_KEY", "")
    if not api_key:
        api_key = secrets.token_urlsafe(32)
        print("Generated a new EMBEDDING_API_KEY in .env")
    set_env_values({"EMBEDDING_API_KEY": api_key, "EMBEDDING_SERVICE_API_KEY": api_key})

    api.create_repo(repo_id, repo_type="space", space_sdk="docker", private=True, exist_ok=True)
    api.add_space_secret(repo_id, "EMBEDDING_API_KEY", api_key)
    for key, value in SPACE_VARIABLES.items():
        api.add_space_variable(repo_id, key, env.get(key) or value)

    operations = [
        CommitOperationAdd("README.md", space_readme(args.name).encode("utf-8")),
        CommitOperationAdd("Dockerfile", space_dockerfile().encode("utf-8")),
    ]
    for path in files:
        operations.append(CommitOperationAdd(str(path.relative_to(ROOT)).replace("\\", "/"), str(path)))
    api.create_commit(repo_id, operations, repo_type="space",
                      commit_message="Deploy OmniCart embedding service")
    print("Uploaded. The first build downloads ~3 GB of models and takes 10-20 minutes.")

    set_env_values({"EMBEDDING_SERVICE_URL": url})
    if not args.wait:
        print("Re-run with --wait --write-env to enable it in .env once it is ready.")
        return 0
    if not wait_until_ready(url, hf_token, args.wait_minutes):
        print("Not ready yet; EMBEDDING_SERVICE_ENABLED left unchanged.")
        return 1
    print("Service is ready.")
    if args.write_env:
        set_env_values({"EMBEDDING_SERVICE_ENABLED": "true"})
        print("EMBEDDING_SERVICE_ENABLED=true written to .env")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
