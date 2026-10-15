import os

import click
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from app.config import Config
from app.extensions import csrf, db, login_manager

# Bump this whenever a new _migrate_v*() step is added below. Tracked
# per-database via SQLite's PRAGMA user_version, so each step runs at most
# once ever, and a fresh database (created straight from the current models)
# just gets fast-forwarded to this value with nothing to actually migrate.
SCHEMA_VERSION = 3


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

    @app.errorhandler(HTTPException)
    def _api_errors_as_json(error):
        # API clients (the MCP server) expect JSON even for errors Flask
        # raises itself, like an unknown route or a wrong HTTP method.
        if request.path.startswith("/api/"):
            return jsonify(error=error.name), error.code
        return error

    with app.app_context():
        db.create_all()
        _use_rollback_journal()
        _run_migrations()

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


def _use_rollback_journal():
    """Keep SQLite in its default rollback-journal mode, switching back any
    database an older version left in WAL mode. WAL needs shared memory that
    Docker Desktop's bind mounts (macOS/Windows) don't support: a second
    process writing while the web server runs (e.g. `docker compose exec web
    flask create-admin`) goes unseen by the server and corrupts the file.
    The journal mode is stored in the database file itself, so setting it
    once at startup covers every later connection."""
    if not db.engine.url.get_backend_name().startswith("sqlite"):
        return

    with db.engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=DELETE")


def _run_migrations():
    """Step the on-disk schema forward to SCHEMA_VERSION, one version at a
    time, then record the new version. Safe to call on every boot: once the
    stored version catches up, this is a single PRAGMA read and nothing
    else."""
    if not db.engine.url.get_backend_name().startswith("sqlite"):
        return

    with db.engine.connect() as conn:
        current = conn.exec_driver_sql("PRAGMA user_version").fetchone()[0]

    if current >= SCHEMA_VERSION:
        return

    if current < 1:
        _migrate_v1_attribution()
    if current < 2:
        _migrate_v2_part_composite_key()
    if current < 3:
        _migrate_v3_member_role()

    with db.engine.connect() as conn:
        conn.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()


def _migrate_v1_attribution():
    """v1: per-user attribution. log_entry.user_id becomes nullable (an API
    key can now be genuinely ownerless), user gains is_editor, and the old
    shared MCP system account is deactivated since every key needs its own
    real owner now."""
    with db.engine.connect() as conn:
        cols = conn.exec_driver_sql("PRAGMA table_info(log_entry)").fetchall()
        user_id_col = next((c for c in cols if c[1] == "user_id"), None)
        if cols and user_id_col is not None and user_id_col[3] != 0:
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

        cols = conn.exec_driver_sql("PRAGMA table_info(user)").fetchall()
        if cols and not any(c[1] == "is_editor" for c in cols):
            conn.exec_driver_sql("ALTER TABLE user ADD COLUMN is_editor BOOLEAN NOT NULL DEFAULT 0")
            conn.commit()

        # Raw SQL rather than the User model: the model may already have
        # columns that later migrations haven't added to this database yet.
        if cols:
            legacy_username = os.environ.get("MCP_USER_USERNAME", "mcp")
            conn.exec_driver_sql(
                "UPDATE user SET is_active = 0 WHERE username = ? AND is_active = 1",
                (legacy_username,),
            )
            conn.commit()


def _migrate_v2_part_composite_key():
    """v2: part.id used to be the primary key on its own, formatted as
    "<box_id>-<part_id>" (e.g. 001-001). It's now just the index of the part
    within its box, and (id, box_id) together are the primary key. box_id
    was already its own column even in the old schema, so the new id is
    recovered by stripping that already-known box_id back out of the old
    combined id rather than guessing where to split it."""
    with db.engine.connect() as conn:
        cols = conn.exec_driver_sql("PRAGMA table_info(part)").fetchall()
        if not cols:
            return  # table doesn't exist yet; create_all() made it composite-keyed from the start

        box_id_col = next((c for c in cols if c[1] == "box_id"), None)
        if box_id_col is None or box_id_col[5] != 0:
            return  # box_id is already part of the primary key — nothing to do

        rows = conn.exec_driver_sql(
            "SELECT id, box_id, name, quantity, minimum_quantity, tags FROM part"
        ).fetchall()

        conn.exec_driver_sql("ALTER TABLE part RENAME TO part_old")
        conn.exec_driver_sql("""
            CREATE TABLE part (
                id VARCHAR(50) NOT NULL,
                box_id VARCHAR(20) NOT NULL,
                name VARCHAR(200) NOT NULL,
                quantity INTEGER NOT NULL,
                minimum_quantity INTEGER NOT NULL,
                tags VARCHAR(300) NOT NULL DEFAULT '',
                PRIMARY KEY (id, box_id),
                FOREIGN KEY(box_id) REFERENCES box (box_id)
            )
        """)

        seen = set()
        for old_id, box_id, name, quantity, minimum_quantity, tags in rows:
            new_id = old_id
            if old_id.startswith(f"{box_id}-"):
                new_id = old_id[len(box_id) + 1:]
            elif old_id.endswith(f"-{box_id}"):
                new_id = old_id[: -(len(box_id) + 1)]
            if not new_id or (box_id, new_id) in seen:
                new_id = old_id  # fall back to the legacy id rather than collide or go blank
            seen.add((box_id, new_id))

            conn.exec_driver_sql(
                "INSERT INTO part (id, box_id, name, quantity, minimum_quantity, tags) VALUES (?, ?, ?, ?, ?, ?)",
                (new_id, box_id, name, quantity, minimum_quantity, tags),
            )

        conn.exec_driver_sql("DROP TABLE part_old")
        conn.commit()


def _migrate_v3_member_role():
    """v3: new "member" role between visitor and editor. Visitors used to be
    able to take/add stock; now that's member-only and visitors are browse
    only, so every existing visitor is promoted to member to keep the access
    they already had."""
    with db.engine.connect() as conn:
        cols = conn.exec_driver_sql("PRAGMA table_info(user)").fetchall()
        if not cols:
            return  # table doesn't exist yet; create_all() made it with is_member
        if not any(c[1] == "is_member" for c in cols):
            conn.exec_driver_sql("ALTER TABLE user ADD COLUMN is_member BOOLEAN NOT NULL DEFAULT 0")
        conn.exec_driver_sql("UPDATE user SET is_member = 1 WHERE is_admin = 0 AND is_editor = 0")
        conn.commit()


def register_cli(app):
    @app.cli.command("create-admin")
    @click.option("--username", prompt=True)
    @click.option("--full-name", prompt="Full name")
    @click.option(
        "--password", prompt=True, hide_input=True, confirmation_prompt=True,
        envvar="PMS_ADMIN_PASSWORD", help="Prompted for if not given; can also come from $PMS_ADMIN_PASSWORD.",
    )
    def create_admin(username, full_name, password):
        """Create an admin user."""
        from app.models import User

        if User.query.filter_by(username=username).first() is not None:
            raise click.ClickException(f"User '{username}' already exists.")
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
            raise click.ClickException(
                f"No active user '{username}' found. Create one first (e.g. 'flask create-admin')."
            )

        try:
            _key, raw = _create_api_key(label, owner_user=owner)
        except ApiKeyError as e:
            raise click.ClickException(str(e))
        click.echo(f"New API key ({label}, owned by {username}): {raw}")
        click.echo("This value is shown only once. Store it now — it can be revoked but not recovered.")
