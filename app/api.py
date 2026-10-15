"""Internal JSON API used by the MCP server and any other integration.

Every key acts with its owner's role: reads are open to any valid key,
taking/adding stock needs a member (or above), and registering, editing, or
deleting parts and boxes needs an editor (or above). A key with no owner, or
whose owner has been deactivated, is read-only.
"""
from functools import wraps

from flask import Blueprint, g, jsonify, request

from app.api_keys import verify_api_key
from app.extensions import db
from app.models import Box, Part
from app.services import (
    ServiceError,
    adjust_stock,
    edit_box,
    edit_part,
    delete_part as delete_part_service,
    delete_box as delete_box_service,
    low_stock_parts,
    register_box,
    register_part,
    search_parts,
)

api_bp = Blueprint("api", __name__, url_prefix="/api")

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
    """Who a write is attributed to. May be None for an ownerless key —
    log_action() records that as-is rather than falling back to any shared
    account."""
    key = getattr(g, "api_key", None)
    return key.owner if key is not None else None


def _requires(permission, description):
    """Gate a write endpoint on a User permission property (e.g.
    "can_adjust_stock") of the calling key's owner. Answers with a JSON 403
    rather than abort(403) so API clients (the MCP server) get a readable
    error instead of an HTML page."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            owner = _key_owner()
            if owner is None or not owner.is_active or not getattr(owner, permission):
                return jsonify(error=f"This API key's owner is not allowed to {description}."), 403
            return view(*args, **kwargs)

        return wrapped

    return decorator


stock_access_required = _requires("can_adjust_stock", "take or add stock")
catalog_access_required = _requires("can_edit_catalog", "register, edit, or delete parts and boxes")


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


@api_bp.route("/parts/<box_id>/<part_id>", methods=["GET"])
def get_part(part_id, box_id):
    part = Part.query.filter_by(id=part_id, box_id=box_id).first()
    if part is None:
        return jsonify(error=f"Part '{part_id}' not found in box '{box_id}'"), 404
    return jsonify(part=_part_json(part))

@api_bp.route("/parts/<box_id>/<part_id>/delete", methods=["POST"])
@catalog_access_required
def delete_part(part_id, box_id):
    part = Part.query.filter_by(id=part_id, box_id=box_id).first()
    if part is None:
        return jsonify(error=f"Part '{part_id}' not found in box '{box_id}'"), 404
    part_json = _part_json(part)
    delete_part_service(part, _key_owner())
    return jsonify(part=part_json)

@api_bp.route("/boxes/<box_id>/delete", methods=["POST"])
@catalog_access_required
def delete_box(box_id):
    box = db.session.get(Box, box_id)
    if box is None:
        return jsonify(error=f"Box '{box_id}' not found"), 404
    box_json = _box_json(box)
    try:
        delete_box_service(box, _key_owner())
    except ServiceError as e:
        return jsonify(error=str(e)), 400
    return jsonify(box=box_json)

@api_bp.route("/parts/<box_id>/<part_id>/stock", methods=["POST"])
@stock_access_required
def adjust_part_stock(part_id, box_id):
    part = Part.query.filter_by(id=part_id, box_id=box_id).first()
    if part is None:
        return jsonify(error=f"Part '{part_id}' not found in box '{box_id}'"), 404

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
@catalog_access_required
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


@api_bp.route("/parts/<box_id>/<part_id>", methods=["PATCH"])
@catalog_access_required
def update_part(part_id, box_id):
    part = Part.query.filter_by(id=part_id, box_id=box_id).first()
    if part is None:
        return jsonify(error=f"Part '{part_id}' not found in box '{box_id}'"), 404

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
@catalog_access_required
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
@catalog_access_required
def update_box(box_id):
    box = db.session.get(Box, box_id)
    if box is None:
        return jsonify(error=f"Box '{box_id}' not found"), 404

    data = request.get_json(silent=True) or {}
    shelf = str(data.get("shelf", box.shelf)).strip()
    row = str(data.get("row", box.row)).strip()
    if not shelf or not row:
        return jsonify(error="shelf and row cannot be blank"), 400

    box = edit_box(box, shelf=shelf, row=row, user=_key_owner())
    return jsonify(box=_box_json(box))
