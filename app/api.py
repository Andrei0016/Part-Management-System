"""Internal JSON API used by the MCP server and any other integration.

Token-authed, not exposed to end users. Every key is expected to have a real
owner (see /admin/api-keys) so the activity log shows who actually made a
change, whether that was through the web UI or through a key-holding client.
"""
from flask import Blueprint, g, jsonify, request

from app.api_keys import verify_api_key
from app.models import Box, Part
from app.services import (
    ServiceError,
    adjust_stock,
    edit_box,
    edit_part,
    low_stock_parts,
    register_box,
    register_part,
    search_parts,
)

api_bp = Blueprint("api", __name__, url_prefix="/api")

# Sanity bound for stock adjustments — SQLite's INTEGER column is 64-bit, but
# Python ints are arbitrary precision, so a huge value raises an uncaught
# OverflowError deep in the DBAPI layer instead of failing validation cleanly.
MAX_STOCK_AMOUNT = 1_000_000_000


@api_bp.before_request
def require_token():
    provided = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not provided.startswith(prefix):
        return jsonify(error="Unauthorized"), 401
    key = verify_api_key(provided[len(prefix):])
    if key is None:
        return jsonify(error="Unauthorized"), 401
    g.api_key = key


def _key_owner():
    """Who a write is attributed to. May be None (e.g. the bootstrap key
    before anyone's replaced it) — log_action() records that as-is rather
    than falling back to any shared account."""
    key = getattr(g, "api_key", None)
    return key.owner if key is not None else None


def _part_json(part: Part):
    return {
        "id": part.id,
        "name": part.name,
        "quantity": part.quantity,
        "minimum_quantity": part.minimum_quantity,
        "tags": part.tag_list,
        "is_low_stock": part.is_low_stock,
        "box_id": part.box_id,
        "shelf": part.box.shelf,
        "row": part.box.row,
    }


def _box_json(box: Box):
    return {"box_id": box.box_id, "shelf": box.shelf, "row": box.row}


@api_bp.route("/parts", methods=["GET"])
def list_parts():
    query = request.args.get("q")
    tag = request.args.get("tag")
    parts = search_parts(query=query, tag=tag)
    return jsonify(parts=[_part_json(p) for p in parts])


@api_bp.route("/parts/low-stock", methods=["GET"])
def low_stock():
    return jsonify(parts=[_part_json(p) for p in low_stock_parts()])


@api_bp.route("/parts/<part_id>", methods=["GET"])
def get_part(part_id):
    part = Part.query.get(part_id)
    if part is None:
        return jsonify(error=f"Part '{part_id}' not found"), 404
    return jsonify(part=_part_json(part))


@api_bp.route("/parts/<part_id>/stock", methods=["POST"])
def adjust_part_stock(part_id):
    part = Part.query.get(part_id)
    if part is None:
        return jsonify(error=f"Part '{part_id}' not found"), 404

    data = request.get_json(silent=True) or {}
    action = data.get("action")
    amount = data.get("amount")
    note = data.get("note")

    if (
        action not in ("take", "add")
        or not isinstance(amount, int)
        or isinstance(amount, bool)
        or not (0 < amount <= MAX_STOCK_AMOUNT)
    ):
        return jsonify(
            error=f"Provide action ('take'/'add') and an integer amount between 1 and {MAX_STOCK_AMOUNT}"
        ), 400

    delta = amount if action == "add" else -amount
    try:
        adjust_stock(part, delta, _key_owner(), note=note)
    except ServiceError as e:
        return jsonify(error=str(e)), 400

    return jsonify(part=_part_json(part))


@api_bp.route("/parts", methods=["POST"])
def create_part():
    data = request.get_json(silent=True) or {}
    required = ("id", "box_id", "name")
    if not all(data.get(f) for f in required):
        return jsonify(error="id, box_id, and name are required"), 400

    try:
        part = register_part(
            part_id=data["id"],
            box_id=data["box_id"],
            name=data["name"],
            quantity=int(data.get("quantity", 0)),
            minimum_quantity=int(data.get("minimum_quantity", 0)),
            tags=data.get("tags", ""),
            user=_key_owner(),
        )
    except (ServiceError, ValueError, TypeError) as e:
        return jsonify(error=str(e)), 400

    return jsonify(part=_part_json(part)), 201


@api_bp.route("/parts/<part_id>", methods=["PATCH"])
def update_part(part_id):
    part = Part.query.get(part_id)
    if part is None:
        return jsonify(error=f"Part '{part_id}' not found"), 404

    data = request.get_json(silent=True) or {}
    try:
        part = edit_part(
            part,
            name=data.get("name", part.name),
            quantity=int(data.get("quantity", part.quantity)),
            minimum_quantity=int(data.get("minimum_quantity", part.minimum_quantity)),
            tags=data.get("tags", part.tags),
            user=_key_owner(),
        )
    except (ServiceError, ValueError, TypeError) as e:
        return jsonify(error=str(e)), 400

    return jsonify(part=_part_json(part))


@api_bp.route("/boxes", methods=["GET"])
def list_boxes():
    boxes = Box.query.order_by(Box.box_id).all()
    return jsonify(boxes=[_box_json(b) for b in boxes])


@api_bp.route("/boxes", methods=["POST"])
def create_box():
    data = request.get_json(silent=True) or {}
    required = ("box_id", "shelf", "row")
    if not all(data.get(f) for f in required):
        return jsonify(error="box_id, shelf, and row are required"), 400

    try:
        box = register_box(data["box_id"], data["shelf"], data["row"], _key_owner())
    except ServiceError as e:
        return jsonify(error=str(e)), 400

    return jsonify(box=_box_json(box)), 201


@api_bp.route("/boxes/<box_id>", methods=["PATCH"])
def update_box(box_id):
    box = Box.query.get(box_id)
    if box is None:
        return jsonify(error=f"Box '{box_id}' not found"), 404

    data = request.get_json(silent=True) or {}
    box = edit_box(
        box,
        shelf=data.get("shelf", box.shelf),
        row=data.get("row", box.row),
        user=_key_owner(),
    )
    return jsonify(box=_box_json(box))
