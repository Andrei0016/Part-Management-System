# FTC Parts Management System

A parts inventory system for an FTC robotics team: a Flask web app backed by
SQLite, with a one-way mirror to a Google Sheet and a separate MCP server so
Claude can check and manage stock directly. Built to run on small
self-hosted hardware (a spare mini PC, a Raspberry Pi, a shelf server) — no
Celery, Redis, Elasticsearch, or JS build step required.

- **Web UI** — search parts, adjust stock, register boxes/parts, admin user
  management, activity log.
- **Google Sheets mirror** — a read-only, periodically-refreshed view of
  current stock for anyone who prefers spreadsheets.
- **MCP server** — lets Claude search parts, check low-stock items, and
  adjust/register stock on your behalf, fully logged and attributed to a
  dedicated system account.

## Quick start (recommended)

On the machine that will host it (Linux, with [Docker](https://docs.docker.com/engine/install/)
already installed):

```bash
wget -qO- https://raw.githubusercontent.com/Andrei0016/Part-Management-System/master/install.sh | bash
```

This single command:

1. Downloads the latest code into `./pms` (no `git` required).
2. On a **first run**: generates `.env` with fresh random `SECRET_KEY` /
   `API_TOKEN` values, optionally asks for your Google Sheets ID, builds and
   starts the Docker stack, then walks you through creating the first admin
   user.
3. On any **later run**: re-downloads the latest code and rebuilds the
   containers, leaving your `.env`, database, and Google service-account
   credentials untouched. Run the same command again any time you want to
   update.

Override the install directory with `PMS_DIR=/opt/pms wget -qO- ... | bash`.

If you already have a local clone or extracted copy, you can run the same
script in place instead: `bash install.sh` (it detects it's already inside
the project and skips the download step).

Once it's up:

- Web UI: http://localhost:5000
- MCP server (Streamable-HTTP): http://localhost:8000/mcp

## Manual setup

If you'd rather do it by hand instead of via `install.sh`:

```bash
git clone https://github.com/Andrei0016/Part-Management-System.git pms
cd pms
cp .env.example .env          # edit SECRET_KEY, API_TOKEN, GOOGLE_SHEETS_ID
mkdir -p instance credentials  # credentials/ holds the Google service-account JSON, if used
docker compose up -d --build
docker compose exec web flask create-admin
```

## Configuration

All configuration lives in `.env` (see `.env.example` for the full list with
comments). The ones you're most likely to touch:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Flask session signing key. Random per install; never share it. |
| `API_TOKEN` | Bearer token the `mcp` container uses to call the `web` container's internal API. Rotate with `docker compose exec web flask regenerate-api-token`, then update `.env` and restart both containers. |
| `GOOGLE_SHEETS_ID` | Target spreadsheet id (from its URL). Leave blank to run without Sheets sync. |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Path (inside the container) to the service-account JSON. Defaults to `/run/secrets/google_service_account.json`, mounted from `./credentials/`. |
| `SYNC_INTERVAL_MINUTES` | How often the background thread pushes DB state to Sheets. |
| `MCP_USER_USERNAME` / `MCP_USER_FULL_NAME` | Identity used to attribute MCP-driven changes in the activity log. |

## Google Sheets setup

1. Create a Google Cloud service account, enable the Sheets API, download
   its JSON key to `credentials/google_service_account.json`.
2. Create a spreadsheet with two tabs, `Parts` and `Boxes`, each with a
   header row. Share the sheet with the service account's email (Editor
   access).
3. Set `GOOGLE_SHEETS_ID` in `.env` to the sheet's id, then restart:
   `docker compose up -d --build`.
4. The sheet is a **one-way, read-only mirror** — it's overwritten from the
   database every `SYNC_INTERVAL_MINUTES` (default 20), or immediately via
   the "Sync to Google Sheets now" button on the admin MCP page. Edits made
   directly in the sheet are never read back or synced into the database.

## MCP server

The `mcp` container exposes tools (`search_parts`, `get_part`,
`list_low_stock`, `take_stock`, `add_stock`, `register_part`, `edit_part`,
`list_boxes`, `register_box`, `edit_box`) that call the web app's internal
`/api/*` endpoints using `API_TOKEN`. Every MCP-driven change is attributed
in the activity log to the seeded system user (`MCP_USER_FULL_NAME`), not to
whichever human is talking to Claude.

Point a Streamable-HTTP MCP client at `http://<host>:8000/mcp`. The current
token and connection details are shown on the admin "MCP" page in the web UI
(admin login required).

## Local development (no Docker)

```bash
python -m venv .venv && .venv/Scripts/activate   # or source .venv/bin/activate
pip install -r requirements.txt
flask --app wsgi.py create-admin
python wsgi.py                                    # runs on :5000 with debug=True
```

## Updating

Re-run the same install command — it re-downloads the code and rebuilds the
containers, and never touches `.env`, `instance/` (the SQLite database), or
`credentials/`:

```bash
wget -qO- https://raw.githubusercontent.com/Andrei0016/Part-Management-System/master/install.sh | bash
```

Or manually, if you cloned with `git`:

```bash
cd pms && git pull --ff-only && docker compose up -d --build
```

There are no database migrations — schema changes are additive
(`db.create_all()` on startup only creates tables that don't exist yet). If
a future change ever requires an actual migration, it'll be called out
separately.

## Backups

The whole system's state is the SQLite file at `instance/parts.db` (plus its
`-wal`/`-shm` companions while the app is running) and, if you use Sheets
sync, `credentials/google_service_account.json`. Back up the `instance/` and
`credentials/` directories; everything else is reproducible from this repo.

## Architecture

- **Two runtime processes, one source of truth.** `web` (Flask + gunicorn,
  1 worker only) owns the SQLite database. `mcp` never touches the DB
  directly — it calls `web`'s internal token-authed API over the compose
  network, so activity logging and the Sheets dirty-flag stay centralized
  regardless of whether a change came from a human or from Claude.
- **Why single-worker matters:** the Sheets sync loop runs as an in-process
  background thread started from the app factory. Running gunicorn with
  more than 1 worker would spawn multiple competing sync threads.
- **All writes funnel through `app/services.py`** (`adjust_stock`,
  `register_part`, `edit_part`, `register_box`, `edit_box`) — both the web
  blueprints and the internal API call these same functions, so the UI and
  MCP paths can't drift apart.
- **IDs encode location.** A `Part.id` is `box_id-part_id` (e.g.
  `001-001`); part *names* are not unique across boxes.

## Security notes

- The internal `/api/*` used by the MCP server fails closed: if `API_TOKEN`
  is unset, every request is rejected.
- This app has no CSRF protection on its forms (no Flask-WTF/token
  middleware). It's designed to run on a trusted LAN behind admin-gated
  login, not exposed to the open internet. If you do expose it beyond a
  trusted network, put it behind a reverse proxy with its own access
  controls, or add CSRF protection first.
- Rotate `SECRET_KEY` and `API_TOKEN` if `.env` is ever leaked
  (`docker compose exec web flask regenerate-api-token` handles the latter).
