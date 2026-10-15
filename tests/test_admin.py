import pytest

from app.extensions import db
from app.models import ApiKey, User
from conftest import PASSWORD

ADMIN_URLS = ("/admin/log", "/admin/users", "/admin/users/new", "/admin/api-keys", "/admin/api-keys/new")


@pytest.mark.parametrize("role", ["visitor", "member", "editor"])
def test_admin_pages_forbidden_for_non_admins(app, users, login, role):
    client = login(role)
    for url in ADMIN_URLS:
        assert client.get(url).status_code == 403, url
    assert "API keys" not in client.get("/").get_data(as_text=True)


def test_admin_pages_render_for_admin(app, users, login):
    client = login("admin")
    for url in ADMIN_URLS:
        assert client.get(url).status_code == 200, url
    assert client.get(f"/admin/users/{users['member'].id}/edit").status_code == 200
    assert client.get("/admin/users/999/edit").status_code == 404


def test_users_list_shows_roles(app, users, login):
    page = login("admin").get("/admin/users").get_data(as_text=True)
    for role in ("Visitor", "Member", "Editor", "Admin"):
        assert f"<td>{role}</td>" in page


@pytest.mark.parametrize("role", ["visitor", "member", "editor", "admin"])
def test_create_user_with_each_role(app, users, login, role):
    login("admin").post("/admin/users/new", data={
        "username": f"new-{role}", "full_name": "New", "password": "pw", "role": role,
    })
    user = User.query.filter_by(username=f"new-{role}").one()
    assert user.role == role
    assert user.check_password("pw")


@pytest.mark.parametrize("data,message", [
    ({"username": "member", "full_name": "X", "password": "pw", "role": "member"}, "already exists"),
    ({"username": "x", "full_name": "X", "password": "pw", "role": "superuser"}, "valid role"),
    ({"username": "x", "full_name": "", "password": "pw", "role": "member"}, "required"),
    ({"username": "x", "full_name": "X", "password": "", "role": "member"}, "required"),
])
def test_create_user_validation(app, users, login, data, message):
    resp = login("admin").post("/admin/users/new", data=data)
    assert message in resp.get_data(as_text=True)
    assert User.query.count() == 4


def test_edit_user_changes_role_both_ways(app, users, login):
    client = login("admin")
    uid = users["member"].id
    for role in ("visitor", "editor", "member", "admin", "visitor"):
        client.post(f"/admin/users/{uid}/edit", data={
            "username": "member", "full_name": "Member", "password": "", "role": role,
        })
        db.session.expire_all()
        assert db.session.get(User, uid).role == role


def test_edit_user_password_optional(app, users, login):
    client = login("admin")
    uid = users["member"].id
    client.post(f"/admin/users/{uid}/edit", data={"username": "member", "full_name": "M", "password": "", "role": "member"})
    db.session.expire_all()
    assert db.session.get(User, uid).check_password(PASSWORD)
    client.post(f"/admin/users/{uid}/edit", data={"username": "member", "full_name": "M", "password": "new", "role": "member"})
    db.session.expire_all()
    assert db.session.get(User, uid).check_password("new")


def test_edit_user_rename_collision(app, users, login):
    resp = login("admin").post(f"/admin/users/{users['member'].id}/edit", data={
        "username": "editor", "full_name": "M", "password": "", "role": "member",
    })
    assert b"already exists" in resp.data


def test_admin_cannot_demote_self(app, users, login):
    resp = login("admin").post(f"/admin/users/{users['admin'].id}/edit", data={
        "username": "admin", "full_name": "A", "password": "", "role": "editor",
    })
    assert b"remove your own admin access" in resp.data
    db.session.expire_all()
    assert db.session.get(User, users["admin"].id).is_admin


def test_deactivate_and_reactivate_user(app, users, login):
    client = login("admin")
    uid = users["member"].id
    client.post(f"/admin/users/{uid}/deactivate")
    db.session.expire_all()
    assert not db.session.get(User, uid).is_active
    client.post(f"/admin/users/{uid}/reactivate")
    db.session.expire_all()
    assert db.session.get(User, uid).is_active


def test_admin_cannot_deactivate_self(app, users, login):
    resp = login("admin").post(f"/admin/users/{users['admin'].id}/deactivate", follow_redirects=True)
    assert b"deactivate your own account" in resp.data


def test_activity_log_shows_actions(app, users, catalog, login):
    login("member").post("/parts/B1/1/stock", data={"action": "take", "amount": "3"})
    page = login("admin").get("/admin/log").get_data(as_text=True)
    assert "Member" in page and "take" in page and "B1-1" in page and "-3" in page


# --- API keys -------------------------------------------------------------

def test_create_api_key_shows_raw_once(app, users, login):
    client = login("admin")
    resp = client.post("/admin/api-keys/new", data={"label": "Claude", "owner_username": "member"})
    page = resp.get_data(as_text=True)
    assert "API key created" in page
    key = ApiKey.query.one()
    assert key.owner.username == "member"
    raw = page.split('data-token="')[1].split('"')[0]
    assert len(raw) == 64
    assert app.test_client().get("/api/parts", headers={"Authorization": f"Bearer {raw}"}).status_code == 200
    assert raw not in client.get("/admin/api-keys").get_data(as_text=True)


@pytest.mark.parametrize("data,message", [
    ({"label": "", "owner_username": "member"}, "label is required"),
    ({"label": "x", "owner_username": "ghost"}, "real, active owner"),
])
def test_create_api_key_validation(app, users, login, data, message):
    resp = login("admin").post("/admin/api-keys/new", data=data)
    assert message in resp.get_data(as_text=True)
    assert ApiKey.query.count() == 0


def test_duplicate_active_label_rejected_until_revoked(app, users, login):
    client = login("admin")
    client.post("/admin/api-keys/new", data={"label": "dup", "owner_username": "member"})
    resp = client.post("/admin/api-keys/new", data={"label": "dup", "owner_username": "member"})
    assert b"already exists" in resp.data
    client.post(f"/admin/api-keys/{ApiKey.query.one().id}/revoke")
    resp = client.post("/admin/api-keys/new", data={"label": "dup", "owner_username": "member"})
    assert b"API key created" in resp.data


def test_revoke_reactivate_delete_key(app, users, login, api_key):
    headers = api_key(users["member"])
    key = ApiKey.query.one()
    client = login("admin")
    api = app.test_client()

    client.post(f"/admin/api-keys/{key.id}/revoke")
    assert api.get("/api/parts", headers=headers).status_code == 401
    client.post(f"/admin/api-keys/{key.id}/reactivate")
    assert api.get("/api/parts", headers=headers).status_code == 200
    client.post(f"/admin/api-keys/{key.id}/delete")
    assert ApiKey.query.count() == 0
    assert api.get("/api/parts", headers=headers).status_code == 401
