import time
from functools import wraps
from urllib.parse import urlparse

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import db
from app.models import User

auth_bp = Blueprint("auth", __name__)

# Simple in-process lockout — safe because the web container always runs a
# single gunicorn worker (see CLAUDE.md), so this dict is never shared/raced
# across processes. Keyed on the submitted username (not whether it exists)
# so a lockout never leaks which usernames are real.
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 15 * 60
_failed_logins = {}


def _is_locked_out(username):
    now = time.monotonic()
    attempts = [t for t in _failed_logins.get(username, []) if now - t < _LOGIN_WINDOW_SECONDS]
    _failed_logins[username] = attempts
    return len(attempts) >= _LOGIN_MAX_ATTEMPTS


def _record_failed_login(username):
    _failed_logins.setdefault(username, []).append(time.monotonic())
    # Bound memory growth from an attacker cycling through many fake usernames.
    if len(_failed_logins) > 10_000:
        _failed_logins.clear()


def _clear_failed_logins(username):
    _failed_logins.pop(username, None)


def _is_safe_redirect_target(target):
    if not target:
        return False
    if target.startswith("//") or target.startswith("\\"):
        return False
    parsed = urlparse(target)
    return parsed.netloc == "" and parsed.scheme == ""


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def editor_required(view):
    """Allows editors and admins — for catalog changes (parts/boxes), not
    the admin console (users, API keys, activity log), which stays
    admin_required."""

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.can_edit_catalog:
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

        if _is_locked_out(username):
            flash("Too many failed login attempts. Try again in a few minutes.", "error")
            return render_template("login.html")

        user = User.query.filter_by(username=username).first()
        if user is None or not user.check_password(password):
            _record_failed_login(username)
            flash("Invalid username or password.", "error")
            return render_template("login.html")
        if not user.is_active:
            flash("This account has been deactivated. Contact an admin.", "error")
            return render_template("login.html")

        _clear_failed_logins(username)
        login_user(user)
        next_url = request.args.get("next")
        if _is_safe_redirect_target(next_url):
            return redirect(next_url)
        return redirect(url_for("parts.index"))

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
