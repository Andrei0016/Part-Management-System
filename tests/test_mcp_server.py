"""MCP server tools, run against the real Flask API: requests.request is
routed into the Flask test client, so each tool goes through the same
auth, permission, and validation code a real deployment would."""
import asyncio
import importlib.util
import os
from types import SimpleNamespace

import pytest
import requests

from conftest import REPO_ROOT, get_part


@pytest.fixture(scope="module")
def server():
    spec = importlib.util.spec_from_file_location("pms_mcp_server", os.path.join(REPO_ROOT, "mcp_server", "server.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def wired(server, app, monkeypatch):
    client = app.test_client()

    def fake_request(method, url, headers=None, timeout=None, params=None, json=None):
        assert url.startswith(server.API_BASE_URL)
        flask_resp = client.open(
            url[len(server.API_BASE_URL):], method=method, headers=headers, query_string=params, json=json
        )
        resp = requests.Response()
        resp.status_code = flask_resp.status_code
        resp._content = flask_resp.data
        resp.headers.update(dict(flask_resp.headers))
        return resp

    monkeypatch.setattr(server.requests, "request", fake_request)
    return server


def ctx_for(headers, elicit_answer=None):
    async def elicit(message, schema):
        action, confirm = elicit_answer or ("accept", True)
        return SimpleNamespace(action=action, data=SimpleNamespace(confirm=confirm))

    request = SimpleNamespace(headers={"authorization": headers["Authorization"]})
    return SimpleNamespace(request_context=SimpleNamespace(request=request), elicit=elicit)


@pytest.fixture
def ctx(users, api_key):
    return lambda role, **kw: ctx_for(api_key(users[role]), **kw)


def test_all_tools_registered(server):
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert names == {
        "search_parts", "get_part", "list_low_stock", "take_stock", "add_stock", "register_part",
        "edit_part", "list_boxes", "delete_part", "delete_box", "register_box", "edit_box",
    }


def test_read_tools(wired, catalog, ctx):
    c = ctx("visitor")
    assert {p["id"] for p in wired.search_parts(ctx=c)["parts"]} == {"1", "2"}
    assert [p["id"] for p in wired.search_parts(query="cap", ctx=c)["parts"]] == ["2"]
    assert [p["id"] for p in wired.search_parts(tag="resistor", ctx=c)["parts"]] == ["1"]
    assert wired.get_part("2", "B1", ctx=c)["part"]["name"] == "Capacitor 100nF"
    assert [p["id"] for p in wired.list_low_stock(ctx=c)["parts"]] == ["2"]
    assert len(wired.list_boxes(ctx=c)["boxes"]) == 2


def test_stock_tools(wired, catalog, ctx):
    c = ctx("member")
    assert wired.take_stock("1", "B1", 5, note="build", ctx=c)["part"]["quantity"] == 45
    assert wired.add_stock("1", "B1", 2, ctx=c)["part"]["quantity"] == 47
    with pytest.raises(ValueError, match="only 47 in stock"):
        wired.take_stock("1", "B1", 100, ctx=c)


def test_catalog_tools(wired, catalog, ctx):
    c = ctx("editor")
    assert wired.register_box("B3", "S3", "R3", ctx=c)["box"]["box_id"] == "B3"
    assert wired.edit_box("B3", row="R9", ctx=c)["box"]["row"] == "R9"
    part = wired.register_part("1", "B3", "Diode", quantity=4, tags="semi", ctx=c)["part"]
    assert (part["box_id"], part["id"], part["shelf"]) == ("B3", "1", "S3")
    assert wired.edit_part("1", "B3", minimum_quantity=10, ctx=c)["part"]["is_low_stock"] is True
    assert get_part("1", "B3").name == "Diode"  # untouched fields stay as they were


def test_delete_tools_confirm(wired, catalog, ctx):
    assert asyncio.run(wired.delete_part("2", "B1", ctx=ctx("editor")))["part"]["id"] == "2"
    assert get_part("2") is None
    assert asyncio.run(wired.delete_box("B2", ctx=ctx("editor")))["box"]["box_id"] == "B2"


@pytest.mark.parametrize("answer", [("accept", False), ("decline", None), ("cancel", None)])
def test_delete_tools_cancel(wired, catalog, ctx, answer):
    assert asyncio.run(wired.delete_part("1", "B1", ctx=ctx("editor", elicit_answer=answer))) == {"cancelled": True}
    assert get_part() is not None


def test_permission_errors_are_readable(wired, catalog, ctx):
    with pytest.raises(ValueError, match="not allowed to take or add stock"):
        wired.take_stock("1", "B1", 1, ctx=ctx("visitor"))
    with pytest.raises(ValueError, match="not allowed to register, edit, or delete"):
        wired.edit_part("1", "B1", name="x", ctx=ctx("member"))


def test_not_found_errors_are_readable(wired, catalog, ctx):
    with pytest.raises(ValueError, match="not found"):
        wired.get_part("9", "B1", ctx=ctx("visitor"))


def test_non_json_error_is_readable(server, monkeypatch):
    """A proxy error page or a crash must surface as a clear error, not a
    JSON decode traceback."""
    def fake_request(*args, **kwargs):
        resp = requests.Response()
        resp.status_code = 502
        resp._content = b"<html>Bad Gateway</html>"
        return resp

    monkeypatch.setattr(server.requests, "request", fake_request)
    with pytest.raises(ValueError, match="502"):
        server._api("GET", "/api/parts", "token")


def test_missing_token_fails_loudly(server):
    c = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers={})))
    with pytest.raises(ValueError, match="No bearer token"):
        server.search_parts(ctx=c)


def test_tool_docs_describe_current_id_format(server):
    """Part ids used to be "<box>-<part>"; telling a model that now would make
    it create parts with the box id baked in twice."""
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    for name in ("register_part", "get_part"):
        assert "box_id-part_id" not in tools[name].description


def test_bearer_gate(server):
    from starlette.testclient import TestClient

    app = server._BearerAuthGate(server.mcp.streamable_http_app())
    with TestClient(app) as client:
        assert client.post("/mcp", json={}).status_code == 401
        assert client.post("/mcp", json={}, headers={"Authorization": "Bearer "}).status_code == 401
        assert client.post("/mcp", json={}, headers={"Authorization": "Bearer abc"}).status_code != 401
