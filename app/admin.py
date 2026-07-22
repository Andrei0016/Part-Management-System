from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.auth import admin_required
from app.extensions import db
from app.models import LogEntry, SyncState, User

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/log")
@admin_required
def log():
    entries = LogEntry.query.order_by(LogEntry.timestamp.desc()).limit(200).all()
    return render_template("admin_log.html", entries=entries)


@admin_bp.route("/users")
@admin_required
def users():
    all_users = User.query.order_by(User.username).all()
    return render_template(
        "admin_users.html", users=all_users, mcp_username=current_app.config["MCP_USER_USERNAME"]
    )


@admin_bp.route("/users/new", methods=["GET", "POST"])
@admin_required
def new_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")
        is_admin = request.form.get("is_admin") == "on"

        if not username or not full_name or not password:
            flash("Username, full name, and password are required.", "error")
            return render_template("admin_user_form.html", form=request.form)

        if User.query.filter_by(username=username).first() is not None:
            flash(f"User '{username}' already exists.", "error")
            return render_template("admin_user_form.html", form=request.form)

        user = User(username=username, full_name=full_name, is_admin=is_admin)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash(f"User '{username}' created.", "success")
        return redirect(url_for("admin.users"))

    return render_template("admin_user_form.html", form=None)


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    if user.username == current_app.config["MCP_USER_USERNAME"]:
        flash(f"'{user.username}' is the MCP system account and can't be edited here.", "error")
        return redirect(url_for("admin.users"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")
        is_admin = request.form.get("is_admin") == "on"

        if not username or not full_name:
            flash("Username and full name are required.", "error")
            return render_template("admin_user_form.html", form=request.form, edit_user=user)

        existing = User.query.filter_by(username=username).first()
        if existing is not None and existing.id != user.id:
            flash(f"User '{username}' already exists.", "error")
            return render_template("admin_user_form.html", form=request.form, edit_user=user)

        if user.id == current_user.id and not is_admin:
            flash("You can't remove your own admin access.", "error")
            return render_template("admin_user_form.html", form=request.form, edit_user=user)

        user.username = username
        user.full_name = full_name
        user.is_admin = is_admin
        if password:
            user.set_password(password)
        db.session.commit()
        flash(f"User '{username}' updated.", "success")
        return redirect(url_for("admin.users"))

    return render_template("admin_user_form.html", form=None, edit_user=user)


@admin_bp.route("/users/<int:user_id>/deactivate", methods=["POST"])
@admin_required
def deactivate_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    if user.id == current_user.id:
        flash("You can't deactivate your own account.", "error")
        return redirect(url_for("admin.users"))

    if user.username == current_app.config["MCP_USER_USERNAME"]:
        flash(f"'{user.username}' is the MCP system account and can't be deactivated.", "error")
        return redirect(url_for("admin.users"))

    user.is_active = False
    db.session.commit()
    flash(f"User '{user.username}' deactivated.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/reactivate", methods=["POST"])
@admin_required
def reactivate_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    user.is_active = True
    db.session.commit()
    flash(f"User '{user.username}' reactivated.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/mcp")
@admin_required
def mcp_info():
    mcp_user = User.query.filter_by(username=current_app.config["MCP_USER_USERNAME"]).first()
    sync_state = SyncState.get()
    return render_template(
        "admin_mcp.html",
        api_token=current_app.config["API_TOKEN"],
        mcp_user=mcp_user,
        api_base_url=current_app.config["PMS_API_BASE_URL"],
        mcp_host=current_app.config["MCP_HOST"],
        mcp_port=current_app.config["MCP_PORT"],
        sync_state=sync_state,
    )


@admin_bp.route("/sync-now", methods=["POST"])
@admin_required
def sync_now():
    from app.sheets_sync import sync_once

    ok, message = sync_once(current_app._get_current_object(), force=True)
    flash(message, "success" if ok else "error")
    return redirect(request.referrer or url_for("parts.index"))
