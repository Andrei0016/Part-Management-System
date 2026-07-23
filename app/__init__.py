import os

import click
from flask import Flask
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.config import Config
from app.extensions import csrf, db, login_manager


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)

    os.makedirs(os.path.join(os.getcwd(), "instance"), exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from app.auth import auth_bp
    from app.parts import parts_bp
    from app.boxes import boxes_bp
    from app.admin import admin_bp
    from app.api import api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(parts_bp)
    app.register_blueprint(boxes_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)
    csrf.exempt(api_bp)  # token-authed, not session/cookie-based — CSRF doesn't apply

    register_cli(app)

    with app.app_context():
        db.create_all()
        _enable_sqlite_wal()
        _migrate_log_entry_nullable_user_id()
        _migrate_add_user_is_editor_column()
        _deactivate_legacy_mcp_user()
        _seed_bootstrap_api_key(app)

    if app.config.get("SHEETS_SYNC_ENABLED") and not app.config.get("TESTING"):
        from app.sheets_sync import start_sync_thread

        start_sync_thread(app)

    @app.after_request
    def _set_security_headers(response):
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'"
        )
        return response

    return app


def _enable_sqlite_wal():
    if not db.engine.url.get_backend_name().startswith("sqlite"):
        return

    @event.listens_for(Engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    with db.engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")


def _migrate_log_entry_nullable_user_id():
    """One-time schema fixup: log_entry.user_id was originally NOT NULL, back
    when every write was attributed to a real user or the shared MCP system
    account. Now that API keys can be genuinely ownerless, it needs to accept
    NULL. SQLite has no ALTER COLUMN, so this rebuilds the table — safe to
    run on every boot; it's a no-op once the column is already nullable."""
    if not db.engine.url.get_backend_name().startswith("sqlite"):
        return

    with db.engine.connect() as conn:
        cols = conn.exec_driver_sql("PRAGMA table_info(log_entry)").fetchall()
        if not cols:
            return  # table doesn't exist yet; create_all() will make it nullable from the start
        user_id_col = next((c for c in cols if c[1] == "user_id"), None)
        if user_id_col is None or user_id_col[3] == 0:
            return  # already nullable

        conn.exec_driver_sql("""
            CREATE TABLE log_entry_new (
                id INTEGER NOT NULL PRIMARY KEY,
                timestamp DATETIME NOT NULL,
                user_id INTEGER,
                action VARCHAR(20) NOT NULL,
                part_id VARCHAR(50),
                quantity_delta INTEGER,
                note VARCHAR(300),
                FOREIGN KEY(user_id) REFERENCES user (id)
            )
        """)
        conn.exec_driver_sql(
            "INSERT INTO log_entry_new (id, timestamp, user_id, action, part_id, quantity_delta, note) "
            "SELECT id, timestamp, user_id, action, part_id, quantity_delta, note FROM log_entry"
        )
        conn.exec_driver_sql("DROP TABLE log_entry")
        conn.exec_driver_sql("ALTER TABLE log_entry_new RENAME TO log_entry")
        conn.commit()


def _migrate_add_user_is_editor_column():
    """One-time schema fixup: adds user.is_editor for the editor role
    (create/edit/delete parts and boxes, but not the admin console). Unlike
    the log_entry fixup above, SQLite supports ADD COLUMN directly — no
    table rebuild needed. Safe to run on every boot; no-ops once present."""
    if not db.engine.url.get_backend_name().startswith("sqlite"):
        return

    with db.engine.connect() as conn:
        cols = conn.exec_driver_sql("PRAGMA table_info(user)").fetchall()
        if not cols:
            return  # table doesn't exist yet; create_all() will include the column from the start
        if any(c[1] == "is_editor" for c in cols):
            return  # already present

        conn.exec_driver_sql("ALTER TABLE user ADD COLUMN is_editor BOOLEAN NOT NULL DEFAULT 0")
        conn.commit()


def _deactivate_legacy_mcp_user():
    """One-time cleanup: earlier versions auto-created a shared system user
    for MCP-driven writes. Every API key now needs its own real owner, so
    that account is no longer created or relied on — deactivate (never
    hard-delete) whichever user still has that legacy username, since
    historical LogEntry/ApiKey rows may still reference it by id."""
    from app.models import User

    legacy_username = os.environ.get("MCP_USER_USERNAME", "mcp")
    user = User.query.filter_by(username=legacy_username).first()
    if user is not None and user.is_active:
        user.is_active = False
        db.session.commit()


def _seed_bootstrap_api_key(app):
    """First-run migration: turn the .env API_TOKEN into a real ApiKey row so
    upgrading doesn't break existing MCP client configs still sending that
    exact value. No-op once any key exists. Created with no owner — there's
    no guaranteed user to attribute it to this early in a fresh install."""
    from app.api_keys import hash_key
    from app.models import ApiKey

    if ApiKey.query.first() is not None:
        return
    token = app.config.get("API_TOKEN")
    if not token:
        return
    key = ApiKey(
        label="Bootstrap (migrated from .env API_TOKEN)",
        key_hash=hash_key(token),
        owner_user_id=None,
    )
    db.session.add(key)
    db.session.commit()


def register_cli(app):
    @app.cli.command("create-admin")
    @click.option("--username", prompt=True)
    @click.option("--full-name", prompt="Full name")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def create_admin(username, full_name, password):
        """Create an admin user."""
        from app.models import User

        if User.query.filter_by(username=username).first() is not None:
            click.echo(f"User '{username}' already exists.")
            return
        user = User(username=username, full_name=full_name, is_admin=True)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Admin user '{username}' created.")

    @app.cli.command("create-api-key")
    @click.option("--label", prompt=True, help="What this key is for, e.g. 'Alex's Claude Desktop'.")
    @click.option("--username", prompt=True, help="Existing, active user this key's actions are attributed to.")
    def create_api_key(label, username):
        """Create a new /api/* key and print its raw value once."""
        from app.api_keys import ApiKeyError, create_api_key as _create_api_key
        from app.models import User

        owner = User.query.filter_by(username=username, is_active=True).first()
        if owner is None:
            click.echo(f"No active user '{username}' found. Create keys via 'flask create-admin' first if needed.")
            return

        try:
            _key, raw = _create_api_key(label, owner_user=owner)
        except ApiKeyError as e:
            click.echo(str(e))
            return
        click.echo(f"New API key ({label}, owned by {username}): {raw}")
        click.echo("This value is shown only once. Store it now — it can be revoked but not recovered.")

    @app.cli.command("regenerate-api-token")
    @click.option("--label", default="Rotated key", help="What this key is for.")
    def regenerate_api_token(label):
        """Deprecated alias for create-api-key, kept so existing rotation docs still work."""
        from app.api_keys import ApiKeyError, create_api_key as _create_api_key

        try:
            _key, raw = _create_api_key(label)
        except ApiKeyError as e:
            click.echo(str(e))
            return
        click.echo(f"New API key: {raw}")
        click.echo(
            "Copy this into API_TOKEN in .env for whichever client should use it, then restart that container. "
            "Old keys are unaffected — revoke them from /admin/api-keys if you're rotating away from one."
        )

    @app.cli.command("sync-now")
    def sync_now():
        """Force an immediate push to Google Sheets."""
        from app.sheets_sync import sync_once

        sync_once(app, force=True)
        click.echo("Sync attempted — check logs for result.")
