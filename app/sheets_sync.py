"""One-way DB -> Google Sheets mirror.

The database is always the source of truth. This module periodically
(re)writes the current Parts/Boxes tables into fixed data ranges on a
Google Sheet so team members who prefer spreadsheets can see current
stock. Sheet edits are never read back. A `dirty` flag on SyncState avoids
pointless API calls when nothing changed since the last push.
"""
import logging
import os
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger("sheets_sync")

PARTS_RANGE = "Parts!A2:H"
BOXES_RANGE = "Boxes!A2:C"

_sync_lock = threading.Lock()


def _get_client(app):
    import gspread

    creds_file = app.config["GOOGLE_SERVICE_ACCOUNT_FILE"]
    if not os.path.exists(creds_file):
        raise RuntimeError(f"Google service account file not found: {creds_file}")
    return gspread.service_account(filename=creds_file)


def _parts_rows():
    from app.models import Part

    rows = []
    for part in Part.query.order_by(Part.id).all():
        rows.append(
            [
                part.id,
                part.name,
                part.box_id,
                part.box.shelf,
                part.box.row,
                part.quantity,
                part.minimum_quantity,
                part.tags,
            ]
        )
    return rows


def _boxes_rows():
    from app.models import Box

    return [[b.box_id, b.shelf, b.row] for b in Box.query.order_by(Box.box_id).all()]


def sync_once(app, force=False):
    """Push current DB state to the configured Google Sheet.

    Returns (ok, message). Never raises — callers (background loop, CLI,
    admin "sync now" button) all just want a pass/fail + reason.
    """
    with app.app_context():
        from app.extensions import db
        from app.models import SyncState

        state = SyncState.get()
        if not state.dirty and not force:
            return True, "Nothing to sync — no changes since last push."

        if not app.config.get("GOOGLE_SHEETS_ID"):
            return False, "Sync skipped: GOOGLE_SHEETS_ID is not configured."

        with _sync_lock:
            try:
                client = _get_client(app)
                sheet = client.open_by_key(app.config["GOOGLE_SHEETS_ID"])
                sheet.values_update(
                    PARTS_RANGE,
                    params={"valueInputOption": "RAW"},
                    body={"values": _parts_rows() or [[]]},
                )
                sheet.values_update(
                    BOXES_RANGE,
                    params={"valueInputOption": "RAW"},
                    body={"values": _boxes_rows() or [[]]},
                )
            except Exception as e:  # noqa: BLE001 - never let sync crash the app
                logger.exception("Google Sheets sync failed")
                return False, f"Sync failed: {e}"

            state.dirty = False
            state.last_synced_at = datetime.now(timezone.utc)
            db.session.commit()

        return True, f"Synced at {state.last_synced_at.isoformat()}"


def start_sync_thread(app):
    interval_seconds = app.config["SYNC_INTERVAL_MINUTES"] * 60

    def _loop():
        while True:
            time.sleep(interval_seconds)
            ok, message = sync_once(app)
            if not ok:
                logger.warning(message)

    thread = threading.Thread(target=_loop, name="sheets-sync", daemon=True)
    thread.start()
    return thread
