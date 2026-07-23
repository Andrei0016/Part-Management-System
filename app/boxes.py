from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.auth import admin_required
from app.models import Box
from app.services import ServiceError, delete_box, edit_box, register_box

boxes_bp = Blueprint("boxes", __name__)


@boxes_bp.route("/boxes")
@login_required
def index():
    boxes = Box.query.order_by(Box.box_id).all()
    return render_template("boxes.html", boxes=boxes)


@boxes_bp.route("/boxes/<box_id>")
@login_required
def detail(box_id):
    box = Box.query.get_or_404(box_id)
    return render_template("box_detail.html", box=box)


@boxes_bp.route("/boxes/new", methods=["GET", "POST"])
@admin_required
def new_box():
    if request.method == "POST":
        box_id = request.form.get("box_id", "").strip()
        shelf = request.form.get("shelf", "").strip()
        row = request.form.get("row", "").strip()

        if not box_id or not shelf or not row:
            flash("Box id, shelf, and row are required.", "error")
            return render_template("box_form.html", box=None, form=request.form)

        try:
            register_box(box_id, shelf, row, current_user)
            flash(f"Box '{box_id}' registered.", "success")
            return redirect(url_for("boxes.detail", box_id=box_id))
        except ServiceError as e:
            flash(str(e), "error")
            return render_template("box_form.html", box=None, form=request.form)

    return render_template("box_form.html", box=None, form=None)


@boxes_bp.route("/boxes/<box_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(box_id):
    box = Box.query.get_or_404(box_id)

    if request.method == "POST":
        shelf = request.form.get("shelf", "").strip()
        row = request.form.get("row", "").strip()

        if not shelf or not row:
            flash("Shelf and row are required.", "error")
            return render_template("box_form.html", box=box, form=request.form)

        edit_box(box, shelf, row, current_user)
        flash(f"Box '{box_id}' updated.", "success")
        return redirect(url_for("boxes.detail", box_id=box_id))

    return render_template("box_form.html", box=box, form=None)


@boxes_bp.route("/boxes/<box_id>/delete", methods=["POST"])
@admin_required
def delete(box_id):
    box = Box.query.get_or_404(box_id)
    try:
        delete_box(box, current_user)
        flash(f"Box '{box_id}' deleted.", "success")
        return redirect(url_for("boxes.index"))
    except ServiceError as e:
        flash(str(e), "error")
        return redirect(url_for("boxes.edit", box_id=box_id))
