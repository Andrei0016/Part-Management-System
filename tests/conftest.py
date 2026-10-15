"""Shared fixtures. Every test gets its own throwaway SQLite database under
pytest's tmp_path, so nothing here ever touches instance/parts.db."""
import os
import sys

import itertools

import pytest
from flask import g
from flask.testing import FlaskClient

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from app import create_app  # noqa: E402
from app.api_keys import create_api_key  # noqa: E402
from app.config import Config  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Box, Part, User  # noqa: E402

PASSWORD = "pw-for-tests"
ROLES = ("visitor", "member", "editor", "admin")


def make_config(db_path, **overrides):
    attrs = {
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-secret",
    }
    attrs.update(overrides)
    return type("TestConfig", (Config,), attrs)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    # create_app() makes ./instance relative to the cwd; keep it in tmp_path.
    monkeypatch.chdir(tmp_path)
    # The login lockout lives in module state; don't let it leak across tests.
    from app import auth

    auth._failed_logins.clear()
    yield
    auth._failed_logins.clear()


class _FreshUserClient(FlaskClient):
    """Tests keep an app context pushed so they can query the db directly,
    and a request reuses that context — including flask_login's cached
    g._login_user. Drop it before each request so every client is judged by
    its own session cookie, not whoever made the previous request."""

    def open(self, *args, **kwargs):
        g.pop("_login_user", None)
        return super().open(*args, **kwargs)


@pytest.fixture
def app(tmp_path):
    app = create_app(make_config(tmp_path / "test.db"))
    app.test_client_class = _FreshUserClient
    with app.app_context():
        yield app
        db.session.remove()


def _make_user(username, role="visitor", active=True, password=PASSWORD):
    from app.admin import _role_to_flags

    user = User(username=username, full_name=username.title(), is_active=active, **_role_to_flags(role))
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def make_user(app):
    return _make_user


@pytest.fixture
def users(app):
    """One active user per role, keyed by role name."""
    return {role: _make_user(role, role) for role in ROLES}


@pytest.fixture
def catalog(app):
    """Two boxes; box B1 holds a resistor (plenty) and a capacitor (low)."""
    db.session.add_all([
        Box(box_id="B1", shelf="S1", row="R1"),
        Box(box_id="B2", shelf="S2", row="R2"),
        Part(id="1", box_id="B1", name="Resistor 10k", quantity=50, minimum_quantity=10, tags="passive,resistor"),
        Part(id="2", box_id="B1", name="Capacitor 100nF", quantity=2, minimum_quantity=5, tags="passive,capacitor"),
    ])
    db.session.commit()


def get_part(part_id="1", box_id="B1"):
    db.session.expire_all()
    return db.session.get(Part, (part_id, box_id))


@pytest.fixture
def login(app):
    """login(username) -> a fresh test client logged in as that user."""

    def _login(username, password=PASSWORD):
        client = app.test_client()
        resp = client.post("/login", data={"username": username, "password": password})
        assert resp.status_code == 302, f"login as {username} failed"
        return client

    return _login


@pytest.fixture
def api_key(app):
    """api_key(owner_user_or_None) -> Authorization headers for a new key."""

    counter = itertools.count()

    def _key(owner, label="test key"):
        _, raw = create_api_key(f"{label} {next(counter)}", owner_user=owner)
        return {"Authorization": f"Bearer {raw}"}

    return _key
