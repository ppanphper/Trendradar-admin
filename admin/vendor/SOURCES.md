# Vendored editor assets

The official TrendRadar editor (`docs/index.html`) loads these files from public CDNs.
`admin_server.py` rewrites those URLs to `/vendor/...` so no third-party script runs
in the admin origin, and the editor CSP only allows `'self'`.

When the upstream editor bumps a dependency, the server logs
`WARNING: editor still references external resources blocked by CSP`; download the new
version here, verify it, and update `CDN_REWRITES` in `admin_server.py`.

| Local file | Source | Verification |
|---|---|---|
| `tailwindcss-play-3.4.17.js` | https://cdn.tailwindcss.com/3.4.17 (target of the unversioned `cdn.tailwindcss.com` redirect on 2026-11-06) | no published SRI; sha256 below |
| `js-yaml-4.1.0.min.js` | https://cdnjs.cloudflare.com/ajax/libs/js-yaml/4.1.0/js-yaml.min.js | matches cdnjs SRI |
| `Sortable-1.15.0.min.js` | https://cdnjs.cloudflare.com/ajax/libs/Sortable/1.15.0/Sortable.min.js | matches cdnjs SRI |
| `fontawesome/css/all.min.css` | https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css | matches cdnjs SRI |
| `fontawesome/webfonts/*.woff2` | https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/webfonts/ | cdnjs publishes no SRI for fonts; sha256 below. The `.ttf` fallbacks are not vendored |

sha256:

```
176e894661aa9cdc9a5cba6c720044cbbf7b8bd80d1c9a142a7c24b1b6c50d15  tailwindcss-play-3.4.17.js
45dc3dd03dc07a06705a2c2989b8c7f709013f04bd5386e3279d4e447f07ebd7  js-yaml-4.1.0.min.js
8a9889aecc2f011e15031fed87eeb35ac75e62655a7b4889ba247ee8ea872474  Sortable-1.15.0.min.js
1edb1725a9ea8ca4dcf2f5508cee183218aa1685e47c1b23056717f754f58ebf  fontawesome/css/all.min.css
748332090c4b8e20f95d0ff59f0be20fa9c889359d3b36d4b886d73376054207  fontawesome/webfonts/fa-brands-400.woff2
8e7e5ea1b15f62ab14dbd41768e8fbcd21cc859a4ea5da812457ee714299fb35  fontawesome/webfonts/fa-regular-400.woff2
7152a6933ee3d690ec2af3d09da9d701723d16aa3410a6d80f28ff8866f3b880  fontawesome/webfonts/fa-solid-900.woff2
694a17c3d9d6c05f8aac63c544615552a4b220e9a4de863d87341a6bcfc1bc8d  fontawesome/webfonts/fa-v4compatibility.woff2
```
