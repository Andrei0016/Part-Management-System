import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

    SESSION_COOKIE_SAMESITE = "Lax"
    # Off by default since the default deployment is plain HTTP on a LAN — a
    # browser will silently drop the session cookie on every request if this
    # is True and the app isn't served over HTTPS. Set SESSION_COOKIE_SECURE=true
    # in .env once you put this behind TLS (e.g. a reverse proxy).
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"

    # Displayed as the browser tab title and the nav-bar brand in the UI.
    SITE_TITLE = os.environ.get("SITE_TITLE", "PMS")

    _default_db_path = os.path.join(os.getcwd(), "instance", "parts.db")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{_default_db_path}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Bootstrap value for the internal API — auto-imported as a real, ownerless
    # ApiKey row on first run (see app/__init__.py::_seed_bootstrap_api_key).
    # Ongoing key management happens in the ApiKey table via /admin/api-keys,
    # not this env var.
    API_TOKEN = os.environ.get("API_TOKEN", "")

    # Google Sheets sync
    GOOGLE_SHEETS_ID = os.environ.get("GOOGLE_SHEETS_ID", "")
    GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_FILE", "/run/secrets/google_service_account.json"
    )
    SYNC_INTERVAL_MINUTES = int(os.environ.get("SYNC_INTERVAL_MINUTES", "20"))
    SHEETS_SYNC_ENABLED = os.environ.get("SHEETS_SYNC_ENABLED", "true").lower() == "true"
