# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A parts inventory system for an FTC robotics team: a Flask web app backed by SQLite, with a one-way mirror to a Google Sheet and a separate MCP server so Claude can check/manage stock directly. Designed to run on small self-hosted hardware — deliberately avoids heavier infra (no Celery/Redis/Elasticsearch, no JS build step, no ORM beyond Flask-SQLAlchemy).

## Commands

Local dev (no Docker):
```
python -m venv .venv && .venv/Scripts/activate   # or source .venv/bin/activate
pip install -r requirements.txt
flask --app wsgi.py create-admin                 # create an admin user (prompts for username/full name/password)
python wsgi.py                                   # runs on :5000 with debug=True
```

Docker (matches production topology — two containers, `web` + `mcp`):
```
cp .env.example .env         # fill in SECRET_KEY, API_TOKEN, GOOGLE_SHEETS_ID
docker compose up --build
docker compose exec web flask create-admin
```

There is no test suite yet (`app.test_client()` is the pattern used for manual verification during development — see git history / plan file for examples of exercising routes and the `/api/*` endpoints this way).

## Architecture

**Two runtime processes, one source of truth.** `web` (Flask + gunicorn, 1 worker only) owns the SQLite database. `mcp` (`mcp_server/server.py`, Streamable-HTTP transport on :8000) is a separate container that never touches the DB directly — it calls `web`'s internal token-authed API (`/api/*`, see `app/api.py`) over the compose network. This keeps activity logging and the Sheets dirty-flag centralized in one writer regardless of whether a change came from a human (UI) or Claude (MCP).

**Why single-worker matters:** the Google Sheets sync loop (`app/sheets_sync.py`) runs as an in-process background thread started from the app factory (`app/__init__.py`). Running gunicorn with more than 1 worker would spawn multiple competing sync threads. If you ever need to scale workers, the sync loop must move out-of-process first.

**Sheets sync is one-way and lossy-safe, not bidirectional.** `SyncState` (in `app/models.py`) tracks a `dirty` bool; every write path (`app/services.py`) sets it. On each timer tick or the admin "sync now" button, `sync_once()` in `app/sheets_sync.py` overwrites a fixed range (`Parts!A2:H`, `Boxes!A2:C`) in the target sheet — it does not clear the whole tab (so headers/formatting survive) and does not read the sheet back. Edits made directly in Google Sheets are never imported. Sync failures are logged and leave `dirty=True` so the next tick retries; they never block a request.

**All writes funnel through `app/services.py`.** `adjust_stock`, `register_part`, `edit_part`, `register_box`, `edit_box` are the only place stock changes happen — each one writes a `LogEntry`, calls `SyncState.mark_dirty()`, and commits, in that order. Both the web blueprints (`app/parts.py`, `app/boxes.py`) and the internal API (`app/api.py`) call these same functions, so the UI and MCP paths can't drift apart. Don't bypass this module to mutate `Part`/`Box` rows directly.

**IDs encode location.** A `Part.id` is `box_id-part_id` (e.g. `001-001`) and is a foreign-key-adjacent reference to `Box.box_id`. Part *names* are not unique across boxes — search (`search_parts` in `app/services.py`) is expected to return multiple rows for the same name in different boxes, each with its own location.

**MCP auth model:** every MCP-driven write is attributed to a single seeded system user (`MCP_USER_USERNAME`/`MCP_USER_FULL_NAME` env vars, seeded on first run in `app/__init__.py::_seed_mcp_user`), not to whichever human is talking to Claude. The internal API (`app/api.py`) rejects all requests if `API_TOKEN` is unset (fail closed) and otherwise checks a static `Authorization: Bearer <token>` header — there's no per-user MCP auth.

**Styling:** Pico CSS is vendored into `app/static/pico.min.css` (not loaded from a CDN) so the app works fully offline on a LAN. Small overrides live in `app/static/custom.css`. Templates are classless Pico markup (`app/templates/`) — avoid adding component class names that assume a different CSS framework.

**Naming gotcha:** the entry point is `wsgi.py`, not `app.py` — the Flask application package is `app/`, and Python cannot have both `app.py` and `app/` resolve to the same import name in one directory.
