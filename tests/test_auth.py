import pytest

from app.extensions import db
from conftest import PASSWORD, make_config


def test_pages_require_login(app):
    client = app.test_client()
    for url in ("/", "/boxes", "/admin/users", "/parts/new"):
        resp = client.get(url)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


def test_login_page_renders(app):
    resp = app.test_client().get("/login")
    assert resp.status_code == 200
    assert b"Log in" in resp.data


def test_login_and_logout(app, users, login):
    client = login("member")
    assert client.get("/").status_code == 200
    resp = client.get("/logout")
    assert resp.status_code == 302
    assert client.get("/").status_code == 302


def test_logged_in_user_visiting_login_is_redirected(app, users, login):
    client = login("member")
    resp = client.get("/login")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


def test_wrong_password_rejected(app, users):
    resp = app.test_client().post("/login", data={"username": "member", "password": "nope"})
    assert resp.status_code == 200
    assert b"Invalid username or password" in resp.data


def test_unknown_user_rejected_with_same_message(app):
    resp = app.test_client().post("/login", data={"username": "ghost", "password": "nope"})
    assert b"Invalid username or password" in resp.data


def test_lockout_after_five_failures(app, users):
    client = app.test_client()
    for _ in range(5):
        client.post("/login", data={"username": "member", "password": "wrong"})
    resp = client.post("/login", data={"username": "member", "password": PASSWORD})
    assert b"Too many failed login attempts" in resp.data
    assert client.get("/").status_code == 302


def test_successful_login_clears_failures(app, users, login):
    client = app.test_client()
    for _ in range(4):
        client.post("/login", data={"username": "member", "password": "wrong"})
    login("member")
    for _ in range(4):
        client.post("/login", data={"username": "member", "password": "wrong"})
    login("member")  # would be locked out if earlier failures still counted


def test_deactivated_user_cannot_log_in(app, make_user):
    make_user("gone", "member", active=False)
    resp = app.test_client().post("/login", data={"username": "gone", "password": PASSWORD})
    assert b"deactivated" in resp.data


def test_deactivation_ends_existing_session(app, users, login):
    client = login("member")
    users["member"].is_active = False
    db.session.commit()
    resp = client.get("/", follow_redirects=True)
    assert b"Your account has been deactivated" in resp.data


@pytest.mark.parametrize("target,expected", [
    ("/boxes", "/boxes"),
    ("//evil.example.com/", "/"),
    ("https://evil.example.com/", "/"),
    ("\\\\evil.example.com", "/"),
])
def test_next_redirect_only_allows_local_paths(app, users, target, expected):
    resp = app.test_client().post(
        "/login", query_string={"next": target}, data={"username": "member", "password": PASSWORD}
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == expected


def test_security_headers(app):
    resp = app.test_client().get("/login")
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]


def test_csrf_enforced_on_forms_but_not_api(tmp_path, monkeypatch):
    from app import create_app

    app = create_app(make_config(tmp_path / "csrf.db", WTF_CSRF_ENABLED=True))
    client = app.test_client()
    assert client.post("/login", data={"username": "x", "password": "y"}).status_code == 400
    # The API is token-authed, so a missing CSRF token must not matter there.
    assert client.post("/api/parts", json={}).status_code == 401


def test_templates_have_no_inline_styles_or_scripts():
    """The CSP allows only 'self' for styles and scripts, so a browser
    silently ignores inline style="" attributes and <script> bodies."""
    import glob
    import os
    import re

    from conftest import REPO_ROOT

    for path in glob.glob(os.path.join(REPO_ROOT, "app", "templates", "*.html")):
        html = open(path).read()
        assert " style=" not in html, path
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), path
