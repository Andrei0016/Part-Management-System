"""MCP server exposing the parts system to Claude.

Runs as its own container (Streamable-HTTP transport) so it's reachable
over the LAN at any time, independent of where Claude itself runs. It never
touches the database directly — every tool call goes through the web app's
token-authed internal API, which keeps activity logging and the Sheets
dirty-flag centralized in one writer.

This server holds no credential of its own. Each connecting client presents
its own bearer key (from /admin/api-keys on `web`); _BearerAuthGate only
checks that *some* key is present, then every tool call forwards that exact
key to `web`'s /api/*, which is the single source of truth for whether it's
actually valid. This means each person gets their own key and their own
attribution in the activity log, instead of every MCP-driven write sharing
one identity. mcp==1.28.1's own RequestContext plumbs the real inbound
Starlette Request (headers and all) through to each individual tool call —
re-set on every HTTP POST, not just once per session — so this is standard
SDK behavior, not something hand-rolled here.
"""
import os

import anyio
import requests
from mcp.server.fastmcp import Context, FastMCP
from starlette.responses import JSONResponse

API_BASE_URL = os.environ.get("PMS_API_BASE_URL", "http://web:5000")
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP("ftc-parts", host=MCP_HOST, port=MCP_PORT)


class _BearerAuthGate:
    """Plain ASGI middleware: reject any HTTP request with no bearer token
    at all, before it ever reaches the MCP session/tool machinery. Doesn't
    validate the token itself — that happens per-call, against `web`'s
    ApiKey table, once the token is forwarded in _api()."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        auth_header = headers.get(b"authorization", b"").decode("latin-1")
        if not auth_header.startswith("Bearer ") or auth_header == "Bearer ":
            response = JSONResponse({"error": "Unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _caller_token(ctx: Context) -> str:
    """The bearer token the connecting client presented for *this* call.
    Fails loudly rather than falling back to any default credential."""
    request = ctx.request_context.request
    if request is None:
        raise ValueError("No request context available — cannot determine caller's key.")
    auth_header = request.headers.get("authorization", "")
    prefix = "Bearer "
    if not auth_header.startswith(prefix):
        raise ValueError("No bearer token present on this request.")
    return auth_header[len(prefix):]


def _api(method, path, token, **kwargs):
    resp = requests.request(
        method,
        f"{API_BASE_URL}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
        **kwargs,
    )
    data = resp.json()
    if resp.status_code >= 400:
        raise ValueError(data.get("error", f"Request failed with status {resp.status_code}"))
    return data


@mcp.tool()
def search_parts(query: str = "", tag: str = "", ctx: Context = None) -> dict:
    """Search parts by name/id substring and/or category tag. Returns each
    match's box, shelf, row, quantity, and whether it's low on stock."""
    token = _caller_token(ctx)
    params = {}
    if query:
        params["q"] = query
    if tag:
        params["tag"] = tag
    return _api("GET", "/api/parts", token, params=params)


@mcp.tool()
def get_part(part_id: str, ctx: Context = None) -> dict:
    """Get full details (location, quantity, tags) for one part by its id (box_id-part_id)."""
    token = _caller_token(ctx)
    return _api("GET", f"/api/parts/{part_id}", token)


@mcp.tool()
def list_low_stock(ctx: Context = None) -> dict:
    """List all parts whose quantity is below their configured minimum quantity."""
    token = _caller_token(ctx)
    return _api("GET", "/api/parts/low-stock", token)


@mcp.tool()
def take_stock(part_id: str, amount: int, note: str = "", ctx: Context = None) -> dict:
    """Remove `amount` units of a part from stock. Fails if that would go negative."""
    token = _caller_token(ctx)
    return _api(
        "POST", f"/api/parts/{part_id}/stock", token,
        json={"action": "take", "amount": amount, "note": note},
    )


@mcp.tool()
def add_stock(part_id: str, amount: int, note: str = "", ctx: Context = None) -> dict:
    """Add `amount` units of a part to stock."""
    token = _caller_token(ctx)
    return _api(
        "POST", f"/api/parts/{part_id}/stock", token,
        json={"action": "add", "amount": amount, "note": note},
    )


@mcp.tool()
def register_part(
    part_id: str,
    box_id: str,
    name: str,
    quantity: int = 0,
    minimum_quantity: int = 0,
    tags: str = "",
    ctx: Context = None,
) -> dict:
    """Register a new part. part_id must be formatted box_id-part_id (e.g. 001-001)
    and the box must already exist. tags is a comma-separated string."""
    token = _caller_token(ctx)
    return _api(
        "POST",
        "/api/parts",
        token,
        json={
            "id": part_id,
            "box_id": box_id,
            "name": name,
            "quantity": quantity,
            "minimum_quantity": minimum_quantity,
            "tags": tags,
        },
    )


@mcp.tool()
def edit_part(
    part_id: str,
    name: str = None,
    quantity: int = None,
    minimum_quantity: int = None,
    tags: str = None,
    ctx: Context = None,
) -> dict:
    """Edit an existing part. Only fields you pass are changed."""
    token = _caller_token(ctx)
    body = {
        k: v
        for k, v in {
            "name": name,
            "quantity": quantity,
            "minimum_quantity": minimum_quantity,
            "tags": tags,
        }.items()
        if v is not None
    }
    return _api("PATCH", f"/api/parts/{part_id}", token, json=body)


@mcp.tool()
def list_boxes(ctx: Context = None) -> dict:
    """List all storage boxes with their shelf/row location."""
    token = _caller_token(ctx)
    return _api("GET", "/api/boxes", token)


@mcp.tool()
def register_box(box_id: str, shelf: str, row: str, ctx: Context = None) -> dict:
    """Register a new storage box."""
    token = _caller_token(ctx)
    return _api("POST", "/api/boxes", token, json={"box_id": box_id, "shelf": shelf, "row": row})


@mcp.tool()
def edit_box(box_id: str, shelf: str = None, row: str = None, ctx: Context = None) -> dict:
    """Edit an existing box's shelf/row. Only fields you pass are changed."""
    token = _caller_token(ctx)
    body = {k: v for k, v in {"shelf": shelf, "row": row}.items() if v is not None}
    return _api("PATCH", f"/api/boxes/{box_id}", token, json=body)


async def _serve():
    import uvicorn

    gated_app = _BearerAuthGate(mcp.streamable_http_app())
    config = uvicorn.Config(gated_app, host=MCP_HOST, port=MCP_PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    anyio.run(_serve)
