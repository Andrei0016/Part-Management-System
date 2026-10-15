import pytest

from app.models import ApiKey, LogEntry
from conftest import get_part


@pytest.fixture
def keys(app, users, api_key):
    k = {role: api_key(user) for role, user in users.items()}
    k[None] = api_key(None)
    return k


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer "}, {"Authorization": "Bearer nope"},
                                     {"Authorization": "Token abc"}])
def test_rejects_missing_or_bad_token(client, headers):
    resp = client.get("/api/parts", headers=headers)
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "Unauthorized"}


def test_last_used_recorded(client, keys):
    client.get("/api/boxes", headers=keys["visitor"])
    assert ApiKey.query.filter(ApiKey.last_used_at.isnot(None)).count() == 1


def test_list_and_search_parts(client, catalog, keys):
    parts = client.get("/api/parts", headers=keys["visitor"]).get_json()["parts"]
    assert {p["name"] for p in parts} == {"Resistor 10k", "Capacitor 100nF"}
    part = next(p for p in parts if p["id"] == "1")
    assert part == {
        "id": "1", "name": "Resistor 10k", "quantity": 50, "minimum_quantity": 10,
        "tags": ["passive", "resistor"], "is_low_stock": False, "box_id": "B1", "shelf": "S1", "row": "R1",
    }
    found = client.get("/api/parts", query_string={"q": "cap"}, headers=keys["visitor"]).get_json()["parts"]
    assert [p["id"] for p in found] == ["2"]
    found = client.get("/api/parts", query_string={"tag": "resistor"}, headers=keys["visitor"]).get_json()["parts"]
    assert [p["id"] for p in found] == ["1"]


def test_low_stock(client, catalog, keys):
    parts = client.get("/api/parts/low-stock", headers=keys[None]).get_json()["parts"]
    assert [p["name"] for p in parts] == ["Capacitor 100nF"]


def test_get_part(client, catalog, keys):
    assert client.get("/api/parts/B1/2", headers=keys["visitor"]).get_json()["part"]["name"] == "Capacitor 100nF"
    resp = client.get("/api/parts/B1/9", headers=keys["visitor"])
    assert resp.status_code == 404 and "not found" in resp.get_json()["error"]


def test_list_boxes(client, catalog, keys):
    boxes = client.get("/api/boxes", headers=keys["visitor"]).get_json()["boxes"]
    assert boxes == [{"box_id": "B1", "shelf": "S1", "row": "R1"}, {"box_id": "B2", "shelf": "S2", "row": "R2"}]


# --- permissions -----------------------------------------------------------

STOCK_OK = {"visitor": False, "member": True, "editor": True, "admin": True, None: False}
CATALOG_OK = {"visitor": False, "member": False, "editor": True, "admin": True, None: False}


@pytest.mark.parametrize("owner", list(STOCK_OK))
def test_stock_permission(client, catalog, keys, owner):
    resp = client.post("/api/parts/B1/1/stock", json={"action": "take", "amount": 1}, headers=keys[owner])
    if STOCK_OK[owner]:
        assert resp.status_code == 200 and resp.get_json()["part"]["quantity"] == 49
    else:
        assert resp.status_code == 403 and "not allowed" in resp.get_json()["error"]
        assert get_part().quantity == 50


@pytest.mark.parametrize("owner", list(CATALOG_OK))
@pytest.mark.parametrize("method,url,body,ok_status", [
    ("post", "/api/parts", {"id": "9", "box_id": "B2", "name": "New"}, 201),
    ("patch", "/api/parts/B1/1", {"name": "Renamed"}, 200),
    ("post", "/api/parts/B1/1/delete", None, 200),
    ("post", "/api/boxes", {"box_id": "B9", "shelf": "S", "row": "R"}, 201),
    ("patch", "/api/boxes/B1", {"shelf": "S7"}, 200),
    ("post", "/api/boxes/B2/delete", None, 200),
])
def test_catalog_permission(client, catalog, keys, owner, method, url, body, ok_status):
    resp = getattr(client, method)(url, json=body, headers=keys[owner])
    if CATALOG_OK[owner]:
        assert resp.status_code == ok_status, resp.get_json()
    else:
        assert resp.status_code == 403
        assert resp.is_json and "not allowed" in resp.get_json()["error"]


def test_deactivated_owner_key_is_read_only(client, catalog, users, keys):
    users["editor"].is_active = False
    from app.extensions import db
    db.session.commit()
    assert client.get("/api/parts", headers=keys["editor"]).status_code == 200
    assert client.patch("/api/parts/B1/1", json={"name": "x"}, headers=keys["editor"]).status_code == 403


# --- writes and validation (editor key) -------------------------------------

def test_stock_add_with_note_is_attributed(client, catalog, keys):
    resp = client.post("/api/parts/B1/1/stock", json={"action": "add", "amount": 5, "note": "restock"},
                       headers=keys["member"])
    assert resp.get_json()["part"]["quantity"] == 55
    entry = LogEntry.query.one()
    assert (entry.user.username, entry.note, entry.quantity_delta) == ("member", "restock", 5)


@pytest.mark.parametrize("body", [
    {}, {"action": "take"}, {"action": "steal", "amount": 1}, {"action": "add", "amount": 0},
    {"action": "add", "amount": -1}, {"action": "add", "amount": "5"}, {"action": "add", "amount": True},
    {"action": "add", "amount": 1.5}, {"action": "add", "amount": 10**10},
])
def test_stock_validation(client, catalog, keys, body):
    resp = client.post("/api/parts/B1/1/stock", json=body, headers=keys["member"])
    assert resp.status_code == 400
    assert get_part().quantity == 50


def test_take_too_much(client, catalog, keys):
    resp = client.post("/api/parts/B1/2/stock", json={"action": "take", "amount": 3}, headers=keys["member"])
    assert resp.status_code == 400 and "only 2 in stock" in resp.get_json()["error"]


def test_stock_on_missing_part(client, catalog, keys):
    resp = client.post("/api/parts/B1/9/stock", json={"action": "add", "amount": 1}, headers=keys["member"])
    assert resp.status_code == 404


def test_create_part(client, catalog, keys):
    resp = client.post("/api/parts", headers=keys["editor"], json={
        "id": "3", "box_id": "B2", "name": "LED", "quantity": 4, "minimum_quantity": 1, "tags": "led",
    })
    assert resp.status_code == 201
    assert resp.get_json()["part"]["shelf"] == "S2"
    assert get_part("3", "B2").tags == "led"


@pytest.mark.parametrize("body", [
    {"id": "3", "box_id": "B2"},
    {"id": "1", "box_id": "B1", "name": "dup"},
    {"id": "3", "box_id": "NOPE", "name": "x"},
    {"id": "3", "box_id": "B2", "name": "x", "quantity": "many"},
    {"id": "3", "box_id": "B2", "name": "x", "quantity": -2},
])
def test_create_part_validation(client, catalog, keys, body):
    assert client.post("/api/parts", json=body, headers=keys["editor"]).status_code == 400


def test_update_part_partial(client, catalog, keys):
    resp = client.patch("/api/parts/B1/1", json={"quantity": 7}, headers=keys["editor"])
    assert resp.status_code == 200
    part = get_part()
    assert (part.name, part.quantity, part.minimum_quantity) == ("Resistor 10k", 7, 10)


@pytest.mark.parametrize("body", [{"quantity": "x"}, {"minimum_quantity": -1}])
def test_update_part_validation(client, catalog, keys, body):
    assert client.patch("/api/parts/B1/1", json=body, headers=keys["editor"]).status_code == 400


def test_update_missing_part(client, catalog, keys):
    assert client.patch("/api/parts/B1/9", json={"name": "x"}, headers=keys["editor"]).status_code == 404


def test_delete_part(client, catalog, keys):
    resp = client.post("/api/parts/B1/1/delete", headers=keys["editor"])
    assert resp.get_json()["part"]["name"] == "Resistor 10k"
    assert get_part() is None
    assert client.post("/api/parts/B1/1/delete", headers=keys["editor"]).status_code == 404


def test_create_box(client, keys):
    resp = client.post("/api/boxes", json={"box_id": "B5", "shelf": "S", "row": "R"}, headers=keys["editor"])
    assert resp.status_code == 201 and resp.get_json()["box"]["box_id"] == "B5"
    assert client.post("/api/boxes", json={"box_id": "B5", "shelf": "S", "row": "R"},
                       headers=keys["editor"]).status_code == 400
    assert client.post("/api/boxes", json={"box_id": "B6"}, headers=keys["editor"]).status_code == 400


def test_update_box(client, catalog, keys):
    resp = client.patch("/api/boxes/B1", json={"row": "R5"}, headers=keys["editor"])
    assert resp.get_json()["box"] == {"box_id": "B1", "shelf": "S1", "row": "R5"}
    assert client.patch("/api/boxes/NOPE", json={}, headers=keys["editor"]).status_code == 404


@pytest.mark.parametrize("body", [{"shelf": ""}, {"row": "  "}])
def test_update_box_rejects_blank_fields(client, catalog, keys, body):
    assert client.patch("/api/boxes/B1", json=body, headers=keys["editor"]).status_code == 400


def test_delete_box(client, catalog, keys):
    resp = client.post("/api/boxes/B2/delete", headers=keys["editor"])
    assert resp.status_code == 200
    assert resp.get_json() == {"box": {"box_id": "B2", "shelf": "S2", "row": "R2"}}
    resp = client.post("/api/boxes/B1/delete", headers=keys["editor"])
    assert resp.status_code == 400 and "not empty" in resp.get_json()["error"]
    assert client.post("/api/boxes/NOPE/delete", headers=keys["editor"]).status_code == 404


def test_unknown_api_route_returns_json(client, keys):
    resp = client.get("/api/nope", headers=keys["editor"])
    assert resp.status_code == 404
    assert resp.is_json
