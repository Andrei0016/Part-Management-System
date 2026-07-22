"""Internal JSON API used by the MCP server container.

Token-authed, not exposed to end users. All writes are attributed to the
seeded MCP system user so the activity log always shows who (a human, via
the web UI, or Claude via MCP) made a change.
"""
from flask import Blueprint, current_app, jsonify, request

from app.models import Box, Part, User
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


@api_bp.before_request
def require_token():
    expected = current_app.config.get("API_TOKEN")
    if not expected:
        return jsonify(error="API is disabled: API_TOKEN is not configured"), 503
    provided = request.headers.get("Authorization", "")
    if provided != f"Bearer {expected}":
        return jsonify(error="Unauthorized"), 401


def _mcp_actor():
    username = current_app.config["MCP_USER_USERNAME"]
    return User.query.filter_by(username=username).first()


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

    if action not in ("take", "add") or not isinstance(amount, int) or amount <= 0:
        return jsonify(error="Provide action ('take'/'add') and a positive integer amount"), 400

    delta = amount if action == "add" else -amount
    try:
        adjust_stock(part, delta, _mcp_actor(), note=note)
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
            user=_mcp_actor(),
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
            user=_mcp_actor(),
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
        box = register_box(data["box_id"], data["shelf"], data["row"], _mcp_actor())
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
        user=_mcp_actor(),
    )
    return jsonify(box=_box_json(box))
