# CLAUDE.md

Local cyber-security news aggregator: a Python stdlib HTTP server plus a vanilla JS front end.

## Commands
- Run: `python3 server.py [--host IP] [--port N] [--allowed-host NAME] [--db PATH]` (default 127.0.0.1:8000, DB `data/pulse.db`, gitignored). Settings may also come from `.env` (see `.env.example`), loaded by `pulse/envfile.py` before other imports.
- Docker: `docker compose up -d --build` (publishes on `PULSE_BIND_IP`, default 192.168.3.100). Data is a **bind mount of host `./data`**, shared with the Python run; the container runs as `PULSE_UID:PULSE_GID`. Don't reintroduce a named volume. `docker compose config` validates without building.
- Scheduler: `pulse/scheduler.py` `DailyScheduler` (thread started in `server.py`). State lives in meta key `auto_refresh` (`slot` = last slot taken). `initialize()` keeps catch-up; `settings_changed()` rebaselines (never runs immediately). Settings `auto_refresh` / `auto_refresh_time` are in `DEFAULT_SETTINGS`. Tests use a fake clock.
- Persistence: `server.py` installs a SIGTERM handler (Python as PID 1 would otherwise ignore `docker stop`), waits for `Jobs.wait()`, then `Store.checkpoint()`. `--backup FILE` uses the SQLite backup API.
- Test: `python3 -m unittest discover -s tests -t .` (no pytest; tests must stay offline, using `tests/fixtures.FakeNet` and `NoAI`)

## Architecture
- `pulse/netguard.py`: the **only** way to fetch user-supplied URLs (SSRF checks, DNS pinning, redirect re-validation, size caps). Never use `urllib.request` for source URLs.
- `pulse/collector.py`: `Collector` resolves feeds (stored feed URL → the URL itself → `<link rel=alternate>` → common paths → Google News), summarises, classifies and upserts. `Jobs` runs one background refresh at a time; the UI polls `/api/refresh/<id>`.
- `pulse/store.py`: SQLite + FTS5 (`articles_fts` is kept in sync by triggers). All user search text goes through `fts_query()`.
- `pulse/ai.py`: optional `anthropic` SDK use (structured outputs, `fallbacks: "default"`). Every failure must fall back silently to the keyword pipeline.
- `pulse/web.py`: routes, validation (bad input raises `ApiError(400)`), a static-file allowlist (`STATIC_FILES`), Host/Origin/Content-Type checks, and CSP. `server.allowed_hosts` holds hostnames only (no ports) = loopback + the bound IP + `--allowed-host`/`PULSE_ALLOWED_HOSTS`. A new static file must be added to `STATIC_FILES`, and new runtime files to the Dockerfile `COPY` lines.
- `static/app.js`: build DOM with `el()` and `textContent` only. Never use `innerHTML` with article data. The CSP forbids inline scripts and `style=""` attributes; set styles through `node.style.setProperty`.

## Conventions
- Core code must remain stdlib-only; `anthropic` is the single optional dependency.
- Summary kinds rank `headline < page < feed < ai`. Upserts only upgrade a summary, never downgrade it.
- User-facing changes go in `RELEASE_NOTES.md`; bump `pulse/__init__.py` `VERSION`.
