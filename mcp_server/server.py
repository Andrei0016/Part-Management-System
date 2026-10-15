import os

import anyio
import requests
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse
from pydantic import BaseModel

API_BASE_URL = os.environ.get("PMS_API_BASE_URL", "http://web:5000")
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP("ftc-parts", host=MCP_HOST, port=MCP_PORT, stateless_http=True)

class ConfirmDelete(BaseModel):
    confirm: bool

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
            return None

        headers = dict(scope.get("headers") or [])
        auth_header = headers.get(b"authorization", b"").decode("latin-1")
        if not auth_header.startswith("Bearer ") or auth_header == "Bearer ":
            response = JSONResponse({"error": "Unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return None

        await self.app(scope, receive, send)
        return None


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
    try:
        data = resp.json()
    except ValueError:
        data = None
    if resp.status_code >= 400:
        error = data.get("error") if isinstance(data, dict) else None
        raise ValueError(error or f"Request failed with status {resp.status_code}")
    if data is None:
        raise ValueError(f"Unexpected non-JSON response (status {resp.status_code}) from the PMS API.")
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
def get_part(part_id: str, box_id: str, ctx: Context = None) -> dict:
    """Get full details (location, quantity, tags) for one part. Parts are
    identified by the box they're in plus their id within that box."""
    token = _caller_token(ctx)
    return _api("GET", f"/api/parts/{box_id}/{part_id}", token)


@mcp.tool()
def list_low_stock(ctx: Context = None) -> dict:
    """List all parts whose quantity is below their configured minimum quantity."""
    token = _caller_token(ctx)
    return _api("GET", "/api/parts/low-stock", token)


@mcp.tool()
def take_stock(part_id: str, box_id: str, amount: int, note: str = "", ctx: Context = None) -> dict:
    """Remove `amount` units of a part from stock. Fails if that would go negative.
    Requires the API key's owner to be a member, editor, or admin."""
    token = _caller_token(ctx)
    return _api(
        "POST", f"/api/parts/{box_id}/{part_id}/stock", token,
        json={"action": "take", "amount": amount, "note": note},
    )


@mcp.tool()
def add_stock(part_id: str, box_id: str, amount: int, note: str = "", ctx: Context = None) -> dict:
    """Add `amount` units of a part to stock. Requires the API key's owner to be
    a member, editor, or admin."""
    token = _caller_token(ctx)
    return _api(
        "POST", f"/api/parts/{box_id}/{part_id}/stock", token,
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
    """Register a new part in an existing box. part_id is the part's id within
    that box only (e.g. "1"), not combined with the box id. tags is a comma-separated string. Requires
    the API key's owner to be an editor or admin."""
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
    box_id: str,
    name: str = None,
    quantity: int = None,
    minimum_quantity: int = None,
    tags: str = None,
    ctx: Context = None,
) -> dict:
    """Edit an existing part. Only fields you pass are changed. Requires the API
    key's owner to be an editor or admin."""
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
    return _api("PATCH", f"/api/parts/{box_id}/{part_id}", token, json=body)


@mcp.tool()
def list_boxes(ctx: Context = None) -> dict:
    """List all storage boxes with their shelf/row location."""
    token = _caller_token(ctx)
    return _api("GET", "/api/boxes", token)

@mcp.tool(annotations = ToolAnnotations(destructiveHint=True))
async def delete_part(part_id: str, box_id: str, ctx: Context = None) -> dict:
    """Permanently delete a part (asks the user to confirm first). Requires
    the API key's owner to be an editor or admin."""
    token = _caller_token(ctx)
    result = await ctx.elicit("You are going to delete a part, THIS CAN'T BE UNDONE! Are you certain?", schema=ConfirmDelete)
    if result.action == "accept" and result.data.confirm:
        return _api("POST", f"/api/parts/{box_id}/{part_id}/delete", token)
    return {"cancelled": True}

@mcp.tool(annotations = ToolAnnotations(destructiveHint=True))
async def delete_box(box_id: str, ctx: Context = None) -> dict:
    """Permanently delete an empty box (asks the user to confirm first).
    Requires the API key's owner to be an editor or admin."""
    token = _caller_token(ctx)
    result = await ctx.elicit("You are going to delete a box, THIS CAN'T BE UNDONE! Are you certain?", schema=ConfirmDelete)
    if result.action == "accept" and result.data.confirm:
        return _api("POST", f"/api/boxes/{box_id}/delete", token)
    return {"cancelled": True}


@mcp.tool()
def register_box(box_id: str, shelf: str, row: str, ctx: Context = None) -> dict:
    """Register a new storage box. Requires the API key's owner to be an editor or admin."""
    token = _caller_token(ctx)
    return _api("POST", "/api/boxes", token, json={"box_id": box_id, "shelf": shelf, "row": row})


@mcp.tool()
def edit_box(box_id: str, shelf: str = None, row: str = None, ctx: Context = None) -> dict:
    """Edit an existing box's shelf/row. Only fields you pass are changed. Requires
    the API key's owner to be an editor or admin."""
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
