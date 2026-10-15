import pytest

from app.models import LogEntry, Part
from conftest import get_part


def test_index_lists_parts_and_low_stock(app, users, catalog, login):
    page = login("visitor").get("/").get_data(as_text=True)
    assert "Resistor 10k" in page and "Capacitor 100nF" in page
    assert "Low stock:" in page
    assert "B1-1" in page


@pytest.mark.parametrize("query,expected,unexpected", [
    ("resist", "Resistor 10k", "Capacitor 100nF"),
    ("RESIST", "Resistor 10k", "Capacitor 100nF"),
    ("B1-2", "Capacitor 100nF", "Resistor 10k"),
])
def test_search(app, users, catalog, login, query, expected, unexpected):
    page = login("visitor").get("/", query_string={"q": query}).get_data(as_text=True)
    table = page.split("<tbody>")[1]
    assert expected in table and unexpected not in table


def test_tag_filter_is_whole_token(app, users, catalog, login):
    page = login("visitor").get("/", query_string={"tag": "capacitor"}).get_data(as_text=True)
    table = page.split("<tbody>")[1]
    assert "Capacitor 100nF" in table and "Resistor 10k" not in table


def test_no_results(app, users, catalog, login):
    page = login("visitor").get("/", query_string={"q": "zzz"}).get_data(as_text=True)
    assert "No parts found." in page


def test_part_detail(app, users, catalog, login):
    page = login("visitor").get("/parts/B1/2").get_data(as_text=True)
    assert "Capacitor 100nF" in page
    assert "B1-2" in page  # box-first full id, same as everywhere else
    assert "LOW STOCK" in page


def test_part_detail_404(app, users, catalog, login):
    assert login("visitor").get("/parts/B1/999").status_code == 404


# --- stock (member and up) -------------------------------------------------

@pytest.mark.parametrize("role,allowed", [
    ("visitor", False), ("member", True), ("editor", True), ("admin", True),
])
def test_stock_permissions(app, users, catalog, login, role, allowed):
    client = login(role)
    page = client.get("/parts/B1/1").get_data(as_text=True)
    assert ("Take stock" in page) == allowed
    resp = client.post("/parts/B1/1/stock", data={"action": "take", "amount": "5"})
    assert resp.status_code == (302 if allowed else 403)
    assert get_part().quantity == (45 if allowed else 50)


def test_add_stock_and_log(app, users, catalog, login):
    login("member").post("/parts/B1/1/stock", data={"action": "add", "amount": "7"})
    assert get_part().quantity == 57
    entry = LogEntry.query.one()
    assert (entry.action, entry.quantity_delta, entry.part_id) == ("add", 7, "B1-1")
    assert entry.user.username == "member"


def test_cannot_take_more_than_in_stock(app, users, catalog, login):
    resp = login("member").post(
        "/parts/B1/2/stock", data={"action": "take", "amount": "3"}, follow_redirects=True
    )
    assert b"only 2 in stock" in resp.data
    assert get_part("2").quantity == 2


@pytest.mark.parametrize("amount", ["0", "-3", "abc", ""])
def test_invalid_amount_rejected(app, users, catalog, login, amount):
    login("member").post("/parts/B1/1/stock", data={"action": "add", "amount": amount})
    assert get_part().quantity == 50


@pytest.mark.parametrize("action", ["", "bogus", None])
def test_unknown_stock_action_rejected(app, users, catalog, login, action):
    data = {"amount": "5"}
    if action is not None:
        data["action"] = action
    login("member").post("/parts/B1/1/stock", data=data)
    assert get_part().quantity == 50


# --- register / edit / delete (editor and up) ------------------------------

@pytest.mark.parametrize("role,allowed", [
    ("visitor", False), ("member", False), ("editor", True), ("admin", True),
])
def test_catalog_permissions(app, users, catalog, login, role, allowed):
    client = login(role)
    expected = 200 if allowed else 403
    assert client.get("/parts/new").status_code == expected
    assert client.get("/parts/B1/1/edit").status_code == expected
    assert ("Register part" in client.get("/").get_data(as_text=True)) == allowed
    assert ("Edit part" in client.get("/parts/B1/1").get_data(as_text=True)) == allowed
    resp = client.post("/parts/B1/1/delete")
    assert resp.status_code == (302 if allowed else 403)
    assert (get_part() is None) == allowed


def test_register_part(app, users, catalog, login):
    resp = login("editor").post("/parts/new", data={
        "id": "3", "box_id": "B2", "name": "LED red", "quantity": "20",
        "minimum_quantity": "5", "tags": "led,optical",
    })
    assert resp.status_code == 302
    part = get_part("3", "B2")
    assert (part.name, part.quantity, part.tag_list) == ("LED red", 20, ["led", "optical"])
    assert LogEntry.query.filter_by(action="register").count() == 1


def test_same_part_id_allowed_in_different_boxes(app, users, catalog, login):
    login("editor").post("/parts/new", data={
        "id": "1", "box_id": "B2", "name": "Other", "quantity": "0", "minimum_quantity": "0",
    })
    assert get_part("1", "B2").name == "Other"
    assert get_part("1", "B1").name == "Resistor 10k"


@pytest.mark.parametrize("data,message", [
    ({"id": "1", "box_id": "B1", "name": "Dup"}, "already exists"),
    ({"id": "9", "box_id": "NOPE", "name": "X"}, "does not exist"),
    ({"id": "", "box_id": "B1", "name": "X"}, "required"),
    ({"id": "9", "box_id": "B1", "name": "X", "quantity": "-1"}, "cannot be negative"),
    ({"id": "9", "box_id": "B1", "name": "X", "quantity": "lots"}, "whole numbers"),
])
def test_register_part_validation(app, users, catalog, login, data, message):
    data = {"quantity": "0", "minimum_quantity": "0", **data}
    resp = login("editor").post("/parts/new", data=data)
    assert resp.status_code == 200
    assert message in resp.get_data(as_text=True)
    assert Part.query.count() == 2


def test_edit_part(app, users, catalog, login):
    login("editor").post("/parts/B1/1/edit", data={
        "name": "Resistor 4.7k", "quantity": "8", "minimum_quantity": "10", "tags": "passive",
    })
    part = get_part()
    assert (part.name, part.quantity, part.minimum_quantity, part.tags) == ("Resistor 4.7k", 8, 10, "passive")
    assert part.is_low_stock


def test_edit_part_form_prefilled_with_zero(app, users, catalog, login):
    client = login("editor")
    client.post("/parts/B1/1/edit", data={"name": "R", "quantity": "0", "minimum_quantity": "0", "tags": ""})
    page = client.get("/parts/B1/1/edit").get_data(as_text=True)
    assert 'name="quantity" min="0" required value="0"' in page


def test_edit_part_requires_name(app, users, catalog, login):
    resp = login("editor").post("/parts/B1/1/edit", data={"name": "", "quantity": "1", "minimum_quantity": "0"})
    assert b"Name is required" in resp.data
    assert get_part().name == "Resistor 10k"


def test_delete_part_logged(app, users, catalog, login):
    login("editor").post("/parts/B1/1/delete")
    assert get_part() is None
    assert LogEntry.query.filter_by(action="delete").one().part_id == "B1-1"
