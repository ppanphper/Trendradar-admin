#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Install TrendRadar Admin into an existing TrendRadar directory.

Usage:
  deploy/install.sh [--trendradar-dir /opt/trendradar] [--image local/trendradar:6.10.0] [--port 8081] [--no-start]

Environment overrides:
  TRENDRADAR_DIR=/opt/trendradar
  TRENDRADAR_IMAGE=local/trendradar:6.10.0
  ADMIN_HOST=127.0.0.1
  ADMIN_PORT=8081
  TRENDRADAR_RUN_TIMEOUT=900
  TRENDRADAR_WEB_PUBLIC_URL=
  TRENDRADAR_ADMIN_PUBLIC_URL=
  TRENDRADAR_WEB_PORT=8080
  START_ADMIN=1

The target TrendRadar directory must already contain:
  docs/index.html
  config/config.yaml

This script copies admin_server.py and admin_bridge.js into <TrendRadar>/admin,
generates <TrendRadar>/admin/.admin-token when missing, writes
<TrendRadar>/docker-compose.admin.yml, and optionally starts trendradar-admin.
EOF
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

TRENDRADAR_DIR="${TRENDRADAR_DIR:-/opt/trendradar}"
TRENDRADAR_IMAGE="${TRENDRADAR_IMAGE:-local/trendradar:6.10.0}"
ADMIN_HOST="${ADMIN_HOST:-127.0.0.1}"
TRENDRADAR_ADMIN_ALLOWED_HOSTS="${TRENDRADAR_ADMIN_ALLOWED_HOSTS:-127.0.0.1,localhost,[::1]}"
TRENDRADAR_ADMIN_ENABLE_RUN="${TRENDRADAR_ADMIN_ENABLE_RUN:-0}"
ADMIN_PORT="${ADMIN_PORT:-8081}"
TRENDRADAR_RUN_TIMEOUT="${TRENDRADAR_RUN_TIMEOUT:-900}"
TRENDRADAR_WEB_PUBLIC_URL="${TRENDRADAR_WEB_PUBLIC_URL:-}"
TRENDRADAR_ADMIN_PUBLIC_URL="${TRENDRADAR_ADMIN_PUBLIC_URL:-}"
TRENDRADAR_WEB_PORT="${TRENDRADAR_WEB_PORT:-8080}"
START_ADMIN="${START_ADMIN:-1}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --trendradar-dir)
      TRENDRADAR_DIR="$2"
      shift 2
      ;;
    --image)
      TRENDRADAR_IMAGE="$2"
      shift 2
      ;;
    --port)
      ADMIN_PORT="$2"
      shift 2
      ;;
    --host)
      ADMIN_HOST="$2"
      shift 2
      ;;
    --timeout)
      TRENDRADAR_RUN_TIMEOUT="$2"
      shift 2
      ;;
    --no-start)
      START_ADMIN=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

require_file() {
  if [ ! -f "$1" ]; then
    echo "Missing required file: $1" >&2
    return 1
  fi
}

require_dir() {
  if [ ! -d "$1" ]; then
    echo "Missing required directory: $1" >&2
    return 1
  fi
}

if [ ! -f "$REPO_ROOT/admin/admin_server.py" ] || [ ! -f "$REPO_ROOT/admin/admin_bridge.js" ]; then
  echo "Run this script from a TrendRadar Admin checkout, or keep deploy/ beside admin/." >&2
  exit 1
fi

require_dir "$TRENDRADAR_DIR" || {
  cat >&2 <<EOF

Prepare TrendRadar first, for example:
  git clone https://github.com/sansan0/TrendRadar.git $TRENDRADAR_DIR

Then rerun this installer.
EOF
  exit 1
}

missing=0
require_file "$TRENDRADAR_DIR/docs/index.html" || missing=1
require_file "$TRENDRADAR_DIR/config/config.yaml" || missing=1
mkdir -p "$TRENDRADAR_DIR/output"

if [ "$missing" -ne 0 ]; then
  cat >&2 <<EOF

TrendRadar Admin is an add-on and does not vendor TrendRadar itself.
Fix the missing files above by installing or updating the TrendRadar project at:
  $TRENDRADAR_DIR

Expected target layout:
  $TRENDRADAR_DIR/docs/index.html
  $TRENDRADAR_DIR/docs/assets/...
  $TRENDRADAR_DIR/config/config.yaml
  $TRENDRADAR_DIR/output/
EOF
  exit 1
fi

mkdir -p "$TRENDRADAR_DIR/admin"
install -m 0755 "$REPO_ROOT/admin/admin_server.py" "$TRENDRADAR_DIR/admin/admin_server.py"
install -m 0644 "$REPO_ROOT/admin/admin_bridge.js" "$TRENDRADAR_DIR/admin/admin_bridge.js"
rm -rf "$TRENDRADAR_DIR/admin/vendor"
cp -R "$REPO_ROOT/admin/vendor" "$TRENDRADAR_DIR/admin/vendor"

if [ ! -s "$TRENDRADAR_DIR/admin/.admin-token" ]; then
  if command -v openssl >/dev/null 2>&1; then
    umask 077
    openssl rand -base64 32 > "$TRENDRADAR_DIR/admin/.admin-token"
  else
    umask 077
    python3 - <<'PY' > "$TRENDRADAR_DIR/admin/.admin-token"
import secrets
print(secrets.token_urlsafe(32))
PY
  fi
fi
chmod 600 "$TRENDRADAR_DIR/admin/.admin-token"

cat > "$TRENDRADAR_DIR/docker-compose.admin.yml" <<EOF
services:
  trendradar-admin:
    image: ${TRENDRADAR_IMAGE}
    container_name: trendradar-admin
    restart: unless-stopped
    entrypoint: [python, /app/admin/admin_server.py]
    environment:
      TZ: Asia/Shanghai
      TRENDRADAR_ADMIN_HOST: 0.0.0.0
      TRENDRADAR_ADMIN_PORT: 8081
      TRENDRADAR_ADMIN_TOKEN_FILE: /app/admin/.admin-token
      TRENDRADAR_ADMIN_ALLOWED_HOSTS: "${TRENDRADAR_ADMIN_ALLOWED_HOSTS}"
      TRENDRADAR_ADMIN_ENABLE_RUN: "${TRENDRADAR_ADMIN_ENABLE_RUN}"
      TRENDRADAR_BASE_DIR: /app
      TRENDRADAR_CONFIG_DIR: /app/config
      TRENDRADAR_OUTPUT_DIR: /app/output
      TRENDRADAR_DOCS_DIR: /app/docs
      TRENDRADAR_ADMIN_DIR: /app/admin
      TRENDRADAR_RUN_TIMEOUT: ${TRENDRADAR_RUN_TIMEOUT}
      TRENDRADAR_WEB_PORT: ${TRENDRADAR_WEB_PORT}
      TRENDRADAR_WEB_PUBLIC_URL: "${TRENDRADAR_WEB_PUBLIC_URL}"
      TRENDRADAR_ADMIN_PUBLIC_URL: "${TRENDRADAR_ADMIN_PUBLIC_URL}"
    ports:
      - "${ADMIN_HOST}:${ADMIN_PORT}:8081"
    volumes:
      - ./admin:/app/admin:ro
      - ./docs:/app/docs:ro
      - ./config:/app/config
      - ./output:/app/output
EOF

if [ "$START_ADMIN" = "1" ]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "docker not found. Files installed, but container was not started." >&2
  else
    (cd "$TRENDRADAR_DIR" && docker compose -f docker-compose.admin.yml up -d)
  fi
fi

host_for_url="$ADMIN_HOST"
if [ "$host_for_url" = "0.0.0.0" ]; then
  host_for_url="<server-ip>"
fi

cat <<EOF

TrendRadar Admin installed.

Target: $TRENDRADAR_DIR
Compose: $TRENDRADAR_DIR/docker-compose.admin.yml
Token file: $TRENDRADAR_DIR/admin/.admin-token

Open:
  http://${host_for_url}:${ADMIN_PORT}/
and paste the token from:
  $TRENDRADAR_DIR/admin/.admin-token

Keep the token private. It is not stored in this repository.
EOF
