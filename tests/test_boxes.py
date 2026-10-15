import pytest

from app.extensions import db
from app.models import Box


def _box(box_id):
    db.session.expire_all()
    return db.session.get(Box, box_id)


def test_boxes_list_and_detail(app, users, catalog, login):
    client = login("visitor")
    page = client.get("/boxes").get_data(as_text=True)
    assert "B1" in page and "B2" in page
    page = client.get("/boxes/B1").get_data(as_text=True)
    assert "Resistor 10k" in page and "Shelf S1, row R1" in page
    assert "No parts in this box yet." in client.get("/boxes/B2").get_data(as_text=True)
    assert client.get("/boxes/NOPE").status_code == 404


@pytest.mark.parametrize("role,allowed", [
    ("visitor", False), ("member", False), ("editor", True), ("admin", True),
])
def test_box_permissions(app, users, catalog, login, role, allowed):
    client = login(role)
    expected = 200 if allowed else 403
    assert client.get("/boxes/new").status_code == expected
    assert client.get("/boxes/B1/edit").status_code == expected
    assert ("Register box" in client.get("/boxes").get_data(as_text=True)) == allowed
    assert ("Edit box" in client.get("/boxes/B1").get_data(as_text=True)) == allowed
    assert client.post("/boxes/B2/delete").status_code == (302 if allowed else 403)


def test_register_box(app, users, login):
    resp = login("editor").post("/boxes/new", data={"box_id": "B9", "shelf": "S", "row": "R"})
    assert resp.status_code == 302
    assert _box("B9").shelf == "S"


@pytest.mark.parametrize("data,message", [
    ({"box_id": "B1", "shelf": "S", "row": "R"}, "already exists"),
    ({"box_id": "", "shelf": "S", "row": "R"}, "required"),
])
def test_register_box_validation(app, users, catalog, login, data, message):
    resp = login("editor").post("/boxes/new", data=data)
    assert message in resp.get_data(as_text=True)


def test_edit_box(app, users, catalog, login):
    login("editor").post("/boxes/B1/edit", data={"shelf": "S9", "row": "R9"})
    assert (_box("B1").shelf, _box("B1").row) == ("S9", "R9")


def test_edit_box_requires_fields(app, users, catalog, login):
    resp = login("editor").post("/boxes/B1/edit", data={"shelf": "", "row": "R9"})
    assert b"Shelf and row are required" in resp.data
    assert _box("B1").shelf == "S1"


def test_delete_empty_box(app, users, catalog, login):
    login("editor").post("/boxes/B2/delete")
    assert _box("B2") is None


def test_cannot_delete_box_with_parts(app, users, catalog, login):
    resp = login("editor").post("/boxes/B1/delete", follow_redirects=True)
    assert b"is not empty" in resp.data
    assert _box("B1") is not None
