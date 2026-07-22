import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

    _default_db_path = os.path.join(os.getcwd(), "instance", "parts.db")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{_default_db_path}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Internal API used by the MCP server container.
    API_TOKEN = os.environ.get("API_TOKEN", "")

    # System account used to attribute MCP-driven changes in the activity log.
    MCP_USER_USERNAME = os.environ.get("MCP_USER_USERNAME", "mcp")
    MCP_USER_FULL_NAME = os.environ.get("MCP_USER_FULL_NAME", "Claude (MCP)")

    # MCP server connection info, shown on the admin MCP-info page. The web app
    # doesn't use these itself — they're only read here so an admin can see how
    # the mcp container is configured without shelling into it.
    MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
    MCP_PORT = os.environ.get("MCP_PORT", "8000")
    PMS_API_BASE_URL = os.environ.get("PMS_API_BASE_URL", "http://web:5000")

    # Google Sheets sync
    GOOGLE_SHEETS_ID = os.environ.get("GOOGLE_SHEETS_ID", "")
    GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_FILE", "/run/secrets/google_service_account.json"
    )
    SYNC_INTERVAL_MINUTES = int(os.environ.get("SYNC_INTERVAL_MINUTES", "20"))
    SHEETS_SYNC_ENABLED = os.environ.get("SHEETS_SYNC_ENABLED", "true").lower() == "true"
