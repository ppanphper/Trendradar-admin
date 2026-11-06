# TrendRadar Admin

Server-backed admin layer for [TrendRadar](https://github.com/sansan0/TrendRadar).

It reuses TrendRadar's official visual config editor, adds authenticated server-side save/run APIs, and writes directly to the same `config/` and `output/` volumes used by the main TrendRadar container.

This fork hardens the admin for a localhost deployment: session login, same-origin checks against CSRF, a Host allowlist, locally vendored editor assets behind a strict CSP, and a fix for the official editor dropping `expected_domain` when platforms are edited. See [docs/security.md](docs/security.md).

## Features

- Token-protected admin UI on port `8081`, bound to `127.0.0.1` by default.
- Loads the local official TrendRadar editor from `docs/index.html`.
- Saves real server files instead of browser-local storage:
  - `config/config.yaml`
  - `config/frequency_words.txt`
  - `config/timeline.yaml`
  - optional AI prompt/interest files.
- Validates YAML before writes.
- Creates timestamped backups under `config/backups/` before writes/restores.
- Can trigger a manual `python -m trendradar` run without restarting the main TrendRadar container (opt-in via `TRENDRADAR_ADMIN_ENABLE_RUN=1`).
- Removes official static-editor actions that are unsafe for server mode, such as loading remote defaults or copying local-only config.
- Keeps every platform field (`expected_domain`, `enabled`, ...) when platforms are deleted or reordered in the visual editor.
- Synchronizes module navigation: clicking a config module number jumps both the YAML editor and the visual module card.

## Layout

```text
admin/admin_server.py   # Python HTTP admin backend
admin/admin_bridge.js   # Browser-side bridge injected into official editor
deploy/install.sh       # Installer for an existing TrendRadar directory
deploy/                 # Compose examples and env template
docs/deployment.md      # Full deployment guide
```

This repo does not vendor TrendRadar itself. Deploy it beside a TrendRadar checkout or release tree containing:

```text
config/
docs/index.html
docs/assets/...
output/
```

## Quick Deployment

For a normal install into an existing TrendRadar directory:

```bash
git clone https://github.com/tantanwu/Trendradar-admin.git /opt/trendradar-admin-src
cd /opt/trendradar-admin-src
sudo bash deploy/install.sh --trendradar-dir /opt/trendradar --image local/trendradar:6.10.0 --port 8081
```

The installer checks required TrendRadar files, copies the admin code into `/opt/trendradar/admin`, creates `/opt/trendradar/admin/.admin-token` if missing, writes `/opt/trendradar/docker-compose.admin.yml`, and starts `trendradar-admin`.

Full guide: [docs/deployment.md](docs/deployment.md).

## Required TrendRadar Files

This repository is not a full TrendRadar fork. It expects TrendRadar itself to provide:

```text
docs/index.html
docs/assets/...
config/config.yaml
config/frequency_words.txt
config/timeline.yaml
output/
```

If `docs/index.html` or `config/config.yaml` is missing, `deploy/install.sh` stops and tells you to install/fix TrendRadar first. It only creates files that should be machine-local, such as `admin/.admin-token` and `output/`.

## Manual Deployment

Create an admin token file outside git:

```bash
mkdir -p admin
openssl rand -base64 32 > admin/.admin-token
chmod 600 admin/.admin-token
```

Mount this repo's `admin/` directory into the TrendRadar image and mount the existing TrendRadar `config/`, `docs/`, and `output/` directories as shown in `deploy/docker-compose.admin.yml`.

Example service:

```bash
docker compose -f deploy/docker-compose.admin.yml up -d
```

Then open `http://127.0.0.1:8081/` and paste the contents of `admin/.admin-token` into the login form.

A successful login creates a server-side session (`HttpOnly`, `SameSite=Strict`, 12 hours by default); the token itself is never stored in the cookie. Scripts can call the API with `Authorization: Bearer <token>`.

## Security Notes

- Do not commit `.admin-token`, `.env`, generated configs, or output reports.
- Keep `8081` on a trusted network, VPN, or reverse proxy with HTTPS if exposed beyond LAN, and add the host name to `TRENDRADAR_ADMIN_ALLOWED_HOSTS`.
- The admin process needs write access to `config/` because it edits live TrendRadar configuration. The main TrendRadar service can keep `config/` read-only.
- If `TRENDRADAR_ADMIN_TOKEN` or `TRENDRADAR_ADMIN_TOKEN_FILE` is missing, the server fails closed and exits.

## Runtime Environment

Required environment variables for the admin service:

```text
TRENDRADAR_ADMIN_TOKEN_FILE=/app/admin/.admin-token
TRENDRADAR_CONFIG_DIR=/app/config
TRENDRADAR_OUTPUT_DIR=/app/output
TRENDRADAR_DOCS_DIR=/app/docs
TRENDRADAR_ADMIN_DIR=/app/admin
```

Useful optional variables:

```text
TRENDRADAR_ADMIN_HOST=127.0.0.1          # use 0.0.0.0 inside a container and bind the published port to 127.0.0.1
TRENDRADAR_ADMIN_PORT=8081
TRENDRADAR_ADMIN_ALLOWED_HOSTS=127.0.0.1,localhost,[::1]   # Host header allowlist (DNS rebinding); * disables it
TRENDRADAR_ADMIN_SESSION_TTL=43200       # seconds
TRENDRADAR_ADMIN_ENABLE_RUN=0            # 1 enables the manual run button and POST /api/run
TRENDRADAR_RUN_TIMEOUT=900
TRENDRADAR_WEB_PORT=8080
TRENDRADAR_WEB_PUBLIC_URL=https://trendradar.example.com/
TRENDRADAR_ADMIN_PUBLIC_URL=https://trendradar-admin.example.com/
```

If public URL variables are omitted, the admin API infers display URLs from the current request `Host`. The top navigation intentionally uses generic wording, so the UI remains portable across machines.

## Local Check

```bash
python3 -m unittest discover -s tests
```

`tests/e2e/editor_check.mjs` drives the real editor in headless Chrome against a running admin (CSP violations, hidden buttons, platform field preservation). Rerun it after upstream editor updates.
