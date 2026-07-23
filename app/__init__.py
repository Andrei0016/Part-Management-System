import os
import secrets

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
        _seed_mcp_user(app)

    if app.config.get("SHEETS_SYNC_ENABLED") and not app.config.get("TESTING"):
        from app.sheets_sync import start_sync_thread

        start_sync_thread(app)

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


def _seed_mcp_user(app):
    from app.models import User

    username = app.config["MCP_USER_USERNAME"]
    if User.query.filter_by(username=username).first() is not None:
        return
    mcp_user = User(
        username=username,
        full_name=app.config["MCP_USER_FULL_NAME"],
        is_admin=True,
    )
    mcp_user.set_password(os.urandom(24).hex())  # MCP never logs in via password
    db.session.add(mcp_user)
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

    @app.cli.command("regenerate-api-token")
    def regenerate_api_token():
        """Print a new random API token to copy into .env's API_TOKEN."""
        click.echo(secrets.token_hex(32))
        click.echo("Copy this into API_TOKEN in .env, then restart both containers (docker compose up -d).")

    @app.cli.command("sync-now")
    def sync_now():
        """Force an immediate push to Google Sheets."""
        from app.sheets_sync import sync_once

        sync_once(app, force=True)
        click.echo("Sync attempted — check logs for result.")
