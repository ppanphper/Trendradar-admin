# Security model

The admin can rewrite TrendRadar's live configuration, including notification webhooks and the
hot-list API endpoint. Anyone who can call its write API can redirect pushes or feed the crawler
from a server they control. This document records the threats this fork defends against and why
each control exists.

## Threats and controls

| Threat | Control |
|---|---|
| Other pages on the same machine send requests with the admin cookie. Browsers treat every port on `127.0.0.1` as the same site, so `SameSite` alone does not stop a page on `127.0.0.1:8089` (TrendRadar reports) or any other local service from posting to `127.0.0.1:8081`. | All state-changing requests must pass a same-origin check (`Sec-Fetch-Site` is `same-origin`/`none`, and `Origin`, when present, equals the admin's own origin). API writes must also use `Content-Type: application/json`, which forces a CORS preflight that the server never answers. |
| DNS rebinding: a remote site resolves its own name to `127.0.0.1` and talks to the admin. | Requests whose `Host` header is not in `TRENDRADAR_ADMIN_ALLOWED_HOSTS` (default `127.0.0.1,localhost,[::1]`) get `403`. |
| Token leakage through URLs, logs, history and cookies. | Login is a `POST` form. The `?token=` link is gone, query strings are redacted from access logs, and the cookie carries a random server-side session id (`HttpOnly`, `SameSite=Strict`, `Max-Age` 12 h). The token itself never reaches the browser after login. Sessions live in memory and end on logout or restart. |
| Third-party scripts running with full admin rights. The official editor loads Tailwind (unversioned), Font Awesome, js-yaml and SortableJS from public CDNs. | Those files are vendored under `admin/vendor/` (see `SOURCES.md`) and the editor page is served with `script-src 'self'`, `connect-src 'self'`, `frame-ancestors 'none'`. If the upstream editor adds a new external resource, CSP blocks it and the server logs a warning. |
| Untrusted report content executing in the admin origin. TrendRadar reports contain titles and links from external feeds, and link schemes are not validated. | The `/report` route is removed; the toolbar links to the TrendRadar web server, a different origin. |
| Manual runs racing the scheduled crawler or running without the main container's secrets, which can corrupt incremental push state. | `POST /api/run` is disabled unless `TRENDRADAR_ADMIN_ENABLE_RUN=1`; the button is hidden when disabled. |
| Exposure beyond the machine. | Default bind is `127.0.0.1`. Containers listen on `0.0.0.0` internally and publish to `127.0.0.1` only. |
| Oversized request bodies. | Bodies over 2 MiB are rejected with `413`; unauthenticated requests are rejected before the body is read. |

Kept from upstream: constant-time token comparison, `yaml.safe_load`, path confinement for static files
and backup restores, a backup before every write, and atomic replacement of config files.

## Editor data integrity

Older versions of the official editor rewrite `platforms.sources` with only `id` and `name`,
silently dropping `expected_domain`, which disables TrendRadar's link-domain check. The bridge wraps
`updatePlatformsInYaml` and writes back every scalar field the platform had before; it adds nothing
when the editor already kept the field.

The editor itself was fixed in ppanphper/TrendRadar#1 (`docs/assets/yaml-block.js`): list edits keep
unchanged items verbatim, keep comments and unknown fields, handle block-style standalone lists, and
remove standalone references to deleted feeds and platforms.

Remaining limitation: the page needs `'unsafe-inline'` scripts because the editor uses inline event
handlers, so an HTML injection inside the editor would still run. Treat config files from untrusted
sources accordingly.

## Deployment notes

- Atomic writes replace the file (new inode). Mount the config directory, not single files, into
  containers that must see updates without a restart.
- The admin container needs no TrendRadar secrets. Run it as a non-root user with a read-only root
  filesystem and `output/` mounted read-only.
