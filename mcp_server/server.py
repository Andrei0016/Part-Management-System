"""MCP server exposing the parts system to Claude.

Runs as its own container (Streamable-HTTP transport) so it's reachable
over the LAN at any time, independent of where Claude itself runs. It never
touches the database directly — every tool call goes through the web app's
token-authed internal API, which keeps activity logging and the Sheets
dirty-flag centralized in one writer.
"""
import os

import requests
from mcp.server.fastmcp import FastMCP

API_BASE_URL = os.environ.get("PMS_API_BASE_URL", "http://web:5000")
API_TOKEN = os.environ.get("API_TOKEN", "")
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP("ftc-parts", host=MCP_HOST, port=MCP_PORT)


def _api(method, path, **kwargs):
    resp = requests.request(
        method,
        f"{API_BASE_URL}{path}",
        headers={"Authorization": f"Bearer {API_TOKEN}"},
        timeout=10,
        **kwargs,
    )
    data = resp.json()
    if resp.status_code >= 400:
        raise ValueError(data.get("error", f"Request failed with status {resp.status_code}"))
    return data


@mcp.tool()
def search_parts(query: str = "", tag: str = "") -> dict:
    """Search parts by name/id substring and/or category tag. Returns each
    match's box, shelf, row, quantity, and whether it's low on stock."""
    params = {}
    if query:
        params["q"] = query
    if tag:
        params["tag"] = tag
    return _api("GET", "/api/parts", params=params)


@mcp.tool()
def get_part(part_id: str) -> dict:
    """Get full details (location, quantity, tags) for one part by its id (box_id-part_id)."""
    return _api("GET", f"/api/parts/{part_id}")


@mcp.tool()
def list_low_stock() -> dict:
    """List all parts whose quantity is below their configured minimum quantity."""
    return _api("GET", "/api/parts/low-stock")


@mcp.tool()
def take_stock(part_id: str, amount: int, note: str = "") -> dict:
    """Remove `amount` units of a part from stock. Fails if that would go negative."""
    return _api("POST", f"/api/parts/{part_id}/stock", json={"action": "take", "amount": amount, "note": note})


@mcp.tool()
def add_stock(part_id: str, amount: int, note: str = "") -> dict:
    """Add `amount` units of a part to stock."""
    return _api("POST", f"/api/parts/{part_id}/stock", json={"action": "add", "amount": amount, "note": note})


@mcp.tool()
def register_part(
    part_id: str,
    box_id: str,
    name: str,
    quantity: int = 0,
    minimum_quantity: int = 0,
    tags: str = "",
) -> dict:
    """Register a new part. part_id must be formatted box_id-part_id (e.g. 001-001)
    and the box must already exist. tags is a comma-separated string."""
    return _api(
        "POST",
        "/api/parts",
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
) -> dict:
    """Edit an existing part. Only fields you pass are changed."""
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
    return _api("PATCH", f"/api/parts/{part_id}", json=body)


@mcp.tool()
def list_boxes() -> dict:
    """List all storage boxes with their shelf/row location."""
    return _api("GET", "/api/boxes")


@mcp.tool()
def register_box(box_id: str, shelf: str, row: str) -> dict:
    """Register a new storage box."""
    return _api("POST", "/api/boxes", json={"box_id": box_id, "shelf": shelf, "row": row})


@mcp.tool()
def edit_box(box_id: str, shelf: str = None, row: str = None) -> dict:
    """Edit an existing box's shelf/row. Only fields you pass are changed."""
    body = {k: v for k, v in {"shelf": shelf, "row": row}.items() if v is not None}
    return _api("PATCH", f"/api/boxes/{box_id}", json=body)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
