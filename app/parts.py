from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.auth import editor_required
from app.models import Box, Part
from app.services import (
    ServiceError,
    adjust_stock,
    all_tags,
    delete_part,
    edit_part,
    low_stock_parts,
    register_part,
    search_parts,
)

parts_bp = Blueprint("parts", __name__)


@parts_bp.route("/")
@login_required
def index():
    query = request.args.get("q", "").strip()
    tag = request.args.get("tag", "").strip()
    parts = search_parts(query=query or None, tag=tag or None)
    return render_template(
        "index.html",
        parts=parts,
        query=query,
        tag=tag,
        tags=all_tags(),
        low_stock=low_stock_parts(),
    )


@parts_bp.route("/parts/<part_id>")
@login_required
def detail(part_id):
    part = Part.query.get_or_404(part_id)
    return render_template("part_detail.html", part=part)


@parts_bp.route("/parts/<part_id>/stock", methods=["POST"])
@login_required
def adjust(part_id):
    part = Part.query.get_or_404(part_id)
    action = request.form.get("action")
    try:
        amount = int(request.form.get("amount", "0"))
    except ValueError:
        flash("Quantity must be a whole number.", "error")
        return redirect(url_for("parts.detail", part_id=part_id))

    if amount <= 0:
        flash("Quantity must be greater than zero.", "error")
        return redirect(url_for("parts.detail", part_id=part_id))

    delta = amount if action == "add" else -amount
    try:
        adjust_stock(part, delta, current_user)
        flash(f"{'Added' if delta > 0 else 'Took'} {amount} of '{part.name}'.", "success")
    except ServiceError as e:
        flash(str(e), "error")

    return redirect(url_for("parts.detail", part_id=part_id))


@parts_bp.route("/parts/new", methods=["GET", "POST"])
@editor_required
def new_part():
    boxes = Box.query.order_by(Box.box_id).all()
    if request.method == "POST":
        part_id = request.form.get("id", "").strip()
        box_id = request.form.get("box_id", "").strip()
        name = request.form.get("name", "").strip()
        tags = request.form.get("tags", "").strip()
        try:
            quantity = int(request.form.get("quantity", "0"))
            minimum_quantity = int(request.form.get("minimum_quantity", "0"))
        except ValueError:
            flash("Quantity and minimum quantity must be whole numbers.", "error")
            return render_template("part_form.html", boxes=boxes, part=None, form=request.form)

        if not part_id or not box_id or not name:
            flash("Part id, box, and name are required.", "error")
            return render_template("part_form.html", boxes=boxes, part=None, form=request.form)

        try:
            register_part(part_id, box_id, name, quantity, minimum_quantity, tags, current_user)
            flash(f"Part '{part_id}' registered.", "success")
            return redirect(url_for("parts.detail", part_id=part_id))
        except ServiceError as e:
            flash(str(e), "error")
            return render_template("part_form.html", boxes=boxes, part=None, form=request.form)

    return render_template("part_form.html", boxes=boxes, part=None, form=None)


@parts_bp.route("/parts/<part_id>/edit", methods=["GET", "POST"])
@editor_required
def edit(part_id):
    part = Part.query.get_or_404(part_id)
    boxes = Box.query.order_by(Box.box_id).all()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        tags = request.form.get("tags", "").strip()
        try:
            quantity = int(request.form.get("quantity", "0"))
            minimum_quantity = int(request.form.get("minimum_quantity", "0"))
        except ValueError:
            flash("Quantity and minimum quantity must be whole numbers.", "error")
            return render_template("part_form.html", boxes=boxes, part=part, form=request.form)

        if not name:
            flash("Name is required.", "error")
            return render_template("part_form.html", boxes=boxes, part=part, form=request.form)

        edit_part(part, name, quantity, minimum_quantity, tags, current_user)
        flash(f"Part '{part_id}' updated.", "success")
        return redirect(url_for("parts.detail", part_id=part_id))

    return render_template("part_form.html", boxes=boxes, part=part, form=None)


@parts_bp.route("/parts/<part_id>/delete", methods=["POST"])
@editor_required
def delete(part_id):
    part = Part.query.get_or_404(part_id)
    delete_part(part, current_user)
    flash(f"Part '{part_id}' deleted.", "success")
    return redirect(url_for("parts.index"))
