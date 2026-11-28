#!/usr/bin/env bash
# Deploy embedding_service/ to an existing Google Compute Engine VM with HTTPS.
#
#   bash scripts/deploy_gce_embeddings.sh INSTANCE ZONE [PROJECT]
#
# Requirements: gcloud logged in (gcloud auth login), a VM with >= 4 GB RAM and
# >= 15 GB free disk, Debian/Ubuntu image. Run from the repo root (Git Bash ok).
#
# What it does:
#   1. Reads EMBEDDING_API_KEY from .env (generates one if empty) and mirrors
#      it into EMBEDDING_SERVICE_API_KEY. The key is never printed or put on a
#      command line; it travels in a chmod-600 file over gcloud scp.
#   2. Opens tcp:80,443 for the VM via a tagged firewall rule.
#   3. Uploads embedding_service/, installs Docker if missing, builds the image
#      (models are baked in at build time: ~3 GB, 10-20 minutes the first time)
#      and starts it behind Caddy on https://<ip-with-dashes>.sslip.io.
#   4. Waits for /health = ready, then writes EMBEDDING_SERVICE_URL and
#      EMBEDDING_SERVICE_ENABLED=true into .env.
#
# Rollback: set EMBEDDING_SERVICE_ENABLED=false in .env (keyword search), or on
# the VM: cd ~/omnicart/embedding_service/deploy && docker compose -f docker-compose.vm.yml down
set -euo pipefail

INSTANCE="${1:?usage: deploy_gce_embeddings.sh INSTANCE ZONE [PROJECT]}"
ZONE="${2:?usage: deploy_gce_embeddings.sh INSTANCE ZONE [PROJECT]}"
PROJECT_ARGS=()
if [[ -n "${3:-}" ]]; then PROJECT_ARGS=(--project "$3"); fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
TAG="omnicart-embeddings"
RULE="omnicart-embeddings-https"
REMOTE_DIR="omnicart"

env_get() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r"' || true; }
env_set() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  if grep -qE "^$key=" "$ENV_FILE"; then
    awk -v k="$key" -v v="$value" 'BEGIN{done=0} { if (!done && index($0, k"=")==1) { print k"="v; done=1 } else print }' "$ENV_FILE" > "$tmp"
  else
    cat "$ENV_FILE" > "$tmp"; printf '%s=%s\n' "$key" "$value" >> "$tmp"
  fi
  cat "$tmp" > "$ENV_FILE"; rm -f "$tmp"
}

[[ -f "$ENV_FILE" ]] || { echo ".env not found at $ENV_FILE" >&2; exit 2; }
gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q . \
  || { echo "gcloud is not logged in. Run: gcloud auth login" >&2; exit 2; }

API_KEY="$(env_get EMBEDDING_API_KEY)"
if [[ -z "$API_KEY" ]]; then
  API_KEY="$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n')"
  echo "Generated a new EMBEDDING_API_KEY in .env"
fi
env_set EMBEDDING_API_KEY "$API_KEY"
env_set EMBEDDING_SERVICE_API_KEY "$API_KEY"

IP="$(gcloud compute instances describe "$INSTANCE" --zone "$ZONE" "${PROJECT_ARGS[@]}" \
      --format='value(networkInterfaces[0].accessConfigs[0].natIP)')"
[[ -n "$IP" ]] || { echo "VM $INSTANCE has no external IP" >&2; exit 2; }
DOMAIN="${IP//./-}.sslip.io"
URL="https://$DOMAIN"
echo "VM:     $INSTANCE ($ZONE) $IP"
echo "URL:    $URL"
echo "Tip:    reserve this IP as static in GCP, or the URL changes when the VM restarts."

echo "==> Firewall (tcp:80,443 -> tag $TAG)"
if ! gcloud compute firewall-rules describe "$RULE" "${PROJECT_ARGS[@]}" >/dev/null 2>&1; then
  gcloud compute firewall-rules create "$RULE" "${PROJECT_ARGS[@]}" \
    --allow tcp:80,tcp:443 --target-tags "$TAG" --direction INGRESS \
    --description "OmniCart embedding service (Caddy HTTPS)" >/dev/null
fi
gcloud compute instances add-tags "$INSTANCE" --zone "$ZONE" "${PROJECT_ARGS[@]}" --tags "$TAG" >/dev/null

echo "==> Upload"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
tar -C "$ROOT" --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
    --exclude='embedding_service/deploy/vm.env' -czf "$WORK/embedding_service.tgz" embedding_service
umask 077
printf 'EMBEDDING_API_KEY=%s\nEMBED_DOMAIN=%s\n' "$API_KEY" "$DOMAIN" > "$WORK/vm.env"
# The remote steps travel as a file: on Windows, gcloud's PuTTY client writes
# a host-key answer into stdin, so piping a script to `bash -s` is unreliable.
cat > "$WORK/omnicart_remote.sh" <<REMOTE
set -euo pipefail
MEM_MB=\$(awk '/MemTotal/ {print int(\$2/1024)}' /proc/meminfo)
echo "VM memory: \${MEM_MB} MB"
if [ "\$MEM_MB" -lt 3500 ]; then
  echo "WARNING: under 4 GB RAM. bge-m3 + SigLIP may not fit; see embedding_service/README.md (E5 fallback)."
fi
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi
mkdir -p ~/$REMOTE_DIR
tar -xzf ~/embedding_service.tgz -C ~/$REMOTE_DIR
# Keep the secrets file out of the Docker build context (and out of the image).
printf 'embedding_service/deploy/vm.env\n**/__pycache__\n**/*.pyc\n' > ~/$REMOTE_DIR/.dockerignore
install -m 600 ~/vm.env ~/$REMOTE_DIR/embedding_service/deploy/vm.env
rm -f ~/vm.env ~/embedding_service.tgz ~/omnicart_remote.sh
cd ~/$REMOTE_DIR/embedding_service/deploy
sudo docker compose -f docker-compose.vm.yml up -d --build
sudo docker compose -f docker-compose.vm.yml ps
REMOTE
gcloud compute scp --quiet --zone "$ZONE" "${PROJECT_ARGS[@]}" \
  "$WORK/embedding_service.tgz" "$WORK/vm.env" "$WORK/omnicart_remote.sh" "$INSTANCE:." >/dev/null

echo "==> Install Docker (if needed), build and start (first build: 10-20 min)"
gcloud compute ssh "$INSTANCE" --quiet --zone "$ZONE" "${PROJECT_ARGS[@]}" \
  --command "bash ~/omnicart_remote.sh" </dev/null

echo "==> Waiting for $URL/health (models load after start; certificate on first request)"
for _ in $(seq 1 60); do
  STATUS="$(curl -s -m 10 "$URL/health" | sed -n 's/.*"status":"\([a-z]*\)".*/\1/p' || true)"
  echo "  health: ${STATUS:-unreachable}"
  if [[ "$STATUS" == "ready" ]]; then
    env_set EMBEDDING_SERVICE_URL "$URL"
    env_set EMBEDDING_SERVICE_ENABLED "true"
    echo "Ready. .env updated: EMBEDDING_SERVICE_URL=$URL, EMBEDDING_SERVICE_ENABLED=true"
    exit 0
  fi
  [[ "$STATUS" == "error" ]] && { echo "Model load failed. On the VM: sudo docker compose -f docker-compose.vm.yml logs embeddings" >&2; exit 1; }
  sleep 20
done
env_set EMBEDDING_SERVICE_URL "$URL"
echo "Not ready after 20 minutes. EMBEDDING_SERVICE_URL is set; ENABLED left false. Re-check $URL/health." >&2
exit 1
