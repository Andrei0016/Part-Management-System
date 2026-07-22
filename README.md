# FTC Parts Management System

Flask app for tracking parts across labeled storage boxes, with a one-way
Google Sheets mirror and an MCP server so Claude can check/manage stock.

## Run with Docker

```
cp .env.example .env        # edit SECRET_KEY, API_TOKEN, GOOGLE_SHEETS_ID, MCP_USER_*
mkdir -p credentials         # put your Google service-account JSON here as
                              # google_service_account.json (leave empty / unset
                              # GOOGLE_SHEETS_ID to run without Sheets sync)
docker compose up --build
docker compose exec web flask create-admin
```

Web UI: http://localhost:5000
MCP server (Streamable-HTTP): http://localhost:8000/mcp

## Google Sheets setup

1. Create a Google Cloud service account, enable the Sheets API, download its
   JSON key to `credentials/google_service_account.json`.
2. Create a spreadsheet with two tabs, `Parts` and `Boxes`, each with a header
   row. Share the sheet with the service account's email (Editor access).
3. Set `GOOGLE_SHEETS_ID` in `.env` to the sheet's id (from its URL).
4. The sheet is a **read-only mirror** — it's overwritten from the database
   every `SYNC_INTERVAL_MINUTES` (default 20) or via the "Sync to Google
   Sheets now" button on the admin log page. Edits made directly in the
   sheet are never read back.

## Local dev (no Docker)

```
python -m venv .venv && .venv/Scripts/activate  # or source .venv/bin/activate
pip install -r requirements.txt
set FLASK_APP=wsgi.py  # or `export FLASK_APP=wsgi.py`
flask create-admin
python wsgi.py
```

## MCP server

The `mcp` container exposes tools (`search_parts`, `get_part`,
`list_low_stock`, `take_stock`, `add_stock`, `register_part`, `edit_part`,
`list_boxes`, `register_box`, `edit_box`) that call the web app's internal
`/api/*` endpoints using `API_TOKEN`. All MCP-driven changes are attributed
in the activity log to the seeded system user (`MCP_USER_FULL_NAME`).

Point a Streamable-HTTP MCP client at `http://<host>:8000/mcp`.
