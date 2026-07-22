from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import db
from app.models import User

auth_bp = Blueprint("auth", __name__)


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


@auth_bp.before_app_request
def _enforce_active_session():
    # UserMixin.is_authenticated defers to is_active, so a deactivated user's
    # session already reads as logged-out — but the stale "_user_id" lingers
    # in the session, so login_required just keeps re-flashing the generic
    # "please log in" message. Detect that case here and clear it with a
    # message that actually explains what happened.
    if current_user.is_authenticated:
        return
    user_id = session.get("_user_id")
    if user_id is None:
        return
    user = db.session.get(User, int(user_id))
    if user is not None and not user.is_active:
        logout_user()
        flash("Your account has been deactivated.", "error")
        return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("parts.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user is None or not user.check_password(password):
            flash("Invalid username or password.", "error")
            return render_template("login.html")
        if not user.is_active:
            flash("This account has been deactivated. Contact an admin.", "error")
            return render_template("login.html")
        login_user(user)
        next_url = request.args.get("next")
        return redirect(next_url or url_for("parts.index"))

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
