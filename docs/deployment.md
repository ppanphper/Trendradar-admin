# Deployment Guide

TrendRadar Admin is an add-on for an existing TrendRadar deployment. It does not include TrendRadar's upstream source, generated reports, or private configuration.

## What Must Exist First

The target machine needs a TrendRadar root directory, usually:

```text
/opt/trendradar
```

It must contain:

```text
/opt/trendradar/docs/index.html
/opt/trendradar/docs/assets/...
/opt/trendradar/config/config.yaml
/opt/trendradar/output/
```

If those files are missing, install TrendRadar first:

```bash
sudo mkdir -p /opt
sudo git clone https://github.com/sansan0/TrendRadar.git /opt/trendradar
```

Then prepare the normal TrendRadar image/config according to the upstream project.

## Install Admin Into an Existing TrendRadar Directory

Clone this repository:

```bash
git clone https://github.com/tantanwu/Trendradar-admin.git /opt/trendradar-admin-src
cd /opt/trendradar-admin-src
```

Run the installer:

```bash
sudo bash deploy/install.sh --trendradar-dir /opt/trendradar --image local/trendradar:6.10.0 --port 8081
```

The script will:

- verify that `/opt/trendradar/docs/index.html` exists;
- verify that `/opt/trendradar/config/config.yaml` exists;
- copy `admin/admin_server.py` and `admin/admin_bridge.js` into `/opt/trendradar/admin/`;
- generate `/opt/trendradar/admin/.admin-token` if missing;
- write `/opt/trendradar/docker-compose.admin.yml`;
- start or recreate `trendradar-admin` unless `--no-start` is passed;
- print the admin URL and the token file path.

Open the printed URL:

```text
http://127.0.0.1:8081/
```

Paste the token from the token file into the login form. Login creates a server-side session cookie; the token itself is not stored in the browser. To reach the admin from another machine, publish the port on that interface and set `TRENDRADAR_ADMIN_ALLOWED_HOSTS` to the host name you use.

## Existing Production Layout

For a two-container layout:

```text
trendradar       -> 8080, main report site and scheduled runs
trendradar-admin -> 8081, config admin (manual run only with TRENDRADAR_ADMIN_ENABLE_RUN=1)
```

Both containers share:

```text
/opt/trendradar/config
/opt/trendradar/output
/opt/trendradar/docs
```

The UI text is deployment-neutral. It says that config is saved to the current TrendRadar deployment rather than naming a particular host. If you want the status API to show explicit public URLs, set:

```text
TRENDRADAR_WEB_PUBLIC_URL=https://trendradar.example.com/
TRENDRADAR_ADMIN_PUBLIC_URL=https://trendradar-admin.example.com/
```

If these are omitted, URLs are inferred from the request `Host`; `TRENDRADAR_WEB_PORT` controls the inferred main web port and defaults to `8080`.

Recommended mount permissions:

```text
main trendradar:  config read-only, output read-write
trendradar-admin: config read-write, output read-write, docs read-only
```

## Manual Compose

If you do not want to use `deploy/install.sh`, copy the admin files manually:

```bash
mkdir -p /opt/trendradar/admin
cp admin/admin_server.py admin/admin_bridge.js /opt/trendradar/admin/
openssl rand -base64 32 > /opt/trendradar/admin/.admin-token
chmod 600 /opt/trendradar/admin/.admin-token
```

Then adapt one of these examples:

```text
deploy/docker-compose.admin.yml       # admin service only
deploy/docker-compose.full.example.yml # main + admin example
```

The example compose files assume they are placed under the TrendRadar root's `deploy/` directory, so paths like `../config` resolve to the real TrendRadar config directory. The installer avoids this ambiguity by writing `/opt/trendradar/docker-compose.admin.yml` directly.

## Updating an Existing Install

Pull this repository and rerun the installer:

```bash
cd /opt/trendradar-admin-src
git pull
sudo bash deploy/install.sh --trendradar-dir /opt/trendradar --image local/trendradar:6.10.0
```

The installer preserves an existing `/opt/trendradar/admin/.admin-token`.

## Missing File Handling

The installer intentionally fails if these are missing:

```text
docs/index.html
config/config.yaml
```

Reason: those files belong to TrendRadar itself. Generating placeholders would produce a broken admin where the visual editor or real config save path does not match the main service.

If `output/` is missing, the installer creates it. Reports will appear there after TrendRadar runs.

If `.admin-token` is missing, the installer generates it locally.

## Security

- Do not commit `.admin-token`, `.env`, `config/`, `output/`, or generated report files.
- Keep port `8081` on localhost, LAN/VPN, or behind HTTPS and IP allowlisting; see [security.md](security.md).
- Use `TRENDRADAR_ADMIN_TOKEN_FILE`; avoid putting the token directly in compose environment variables.
- If the token is missing, `admin_server.py` exits instead of starting without auth.

## Verification

```bash
cd /opt/trendradar
docker compose -f docker-compose.admin.yml ps

TOKEN=$(cat admin/.admin-token)
curl -sS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8081/api/state | python3 -m json.tool | head
```

Expected:

```text
"success": true
```
