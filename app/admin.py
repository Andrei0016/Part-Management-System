from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.api_keys import ApiKeyError, create_api_key, delete_api_key, reactivate_api_key, revoke_api_key
from app.auth import admin_required
from app.extensions import db
from app.models import ApiKey, LogEntry, SyncState, User

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/log")
@admin_required
def log():
    entries = LogEntry.query.order_by(LogEntry.timestamp.desc()).limit(200).all()
    return render_template("admin_log.html", entries=entries, sync_state=SyncState.get())


@admin_bp.route("/users")
@admin_required
def users():
    all_users = User.query.order_by(User.username).all()
    return render_template("admin_users.html", users=all_users)


@admin_bp.route("/users/new", methods=["GET", "POST"])
@admin_required
def new_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")
        is_admin = request.form.get("is_admin") == "on"
        is_editor = request.form.get("is_editor") == "on"

        if not username or not full_name or not password:
            flash("Username, full name, and password are required.", "error")
            return render_template("admin_user_form.html", form=request.form)

        if User.query.filter_by(username=username).first() is not None:
            flash(f"User '{username}' already exists.", "error")
            return render_template("admin_user_form.html", form=request.form)

        user = User(username=username, full_name=full_name, is_admin=is_admin, is_editor=is_editor)
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

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")
        is_admin = request.form.get("is_admin") == "on"
        is_editor = request.form.get("is_editor") == "on"

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
        user.is_editor = is_editor
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


@admin_bp.route("/sync-now", methods=["POST"])
@admin_required
def sync_now():
    from app.sheets_sync import sync_once

    ok, message = sync_once(current_app._get_current_object(), force=True)
    flash(message, "success" if ok else "error")
    return redirect(request.referrer or url_for("parts.index"))


@admin_bp.route("/api-keys")
@admin_required
def api_keys():
    keys = ApiKey.query.order_by(ApiKey.created_at.desc()).all()
    return render_template("admin_api_keys.html", keys=keys)


@admin_bp.route("/api-keys/new", methods=["GET", "POST"])
@admin_required
def new_api_key():
    active_users = User.query.filter_by(is_active=True).order_by(User.username).all()

    if request.method == "POST":
        label = request.form.get("label", "").strip()
        username = request.form.get("owner_username", "").strip()

        if not label:
            flash("A label is required, e.g. \"Alex's Claude Desktop\".", "error")
            return render_template("admin_api_key_form.html", form=request.form, users=active_users)

        owner = User.query.filter_by(username=username, is_active=True).first()
        if owner is None:
            flash("Pick who this key belongs to — every key needs a real, active owner.", "error")
            return render_template("admin_api_key_form.html", form=request.form, users=active_users)

        try:
            key, raw_key = create_api_key(label, owner_user=owner)
        except ApiKeyError as e:
            flash(str(e), "error")
            return render_template("admin_api_key_form.html", form=request.form, users=active_users)

        return render_template("admin_api_key_created.html", key=key, raw_key=raw_key)

    return render_template("admin_api_key_form.html", form=None, users=active_users)


@admin_bp.route("/api-keys/<int:key_id>/revoke", methods=["POST"])
@admin_required
def revoke_api_key_route(key_id):
    key = db.session.get(ApiKey, key_id)
    if key is None:
        abort(404)
    revoke_api_key(key)
    flash(f"Key '{key.label}' revoked.", "success")
    return redirect(url_for("admin.api_keys"))


@admin_bp.route("/api-keys/<int:key_id>/reactivate", methods=["POST"])
@admin_required
def reactivate_api_key_route(key_id):
    key = db.session.get(ApiKey, key_id)
    if key is None:
        abort(404)
    reactivate_api_key(key)
    flash(f"Key '{key.label}' reactivated.", "success")
    return redirect(url_for("admin.api_keys"))


@admin_bp.route("/api-keys/<int:key_id>/delete", methods=["POST"])
@admin_required
def delete_api_key_route(key_id):
    key = db.session.get(ApiKey, key_id)
    if key is None:
        abort(404)
    label = key.label
    delete_api_key(key)
    flash(f"Key '{label}' permanently deleted.", "success")
    return redirect(url_for("admin.api_keys"))
