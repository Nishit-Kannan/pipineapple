"""Auth routes — first-run setup wizard, login, logout.

Mounted at the root path (no url_prefix) because /login and /setup are
the conventional URLs. The before_request middleware in __init__.py
whitelists these so unauthenticated requests can reach them.
"""

from __future__ import annotations

from flask import (
    Blueprint, flash, redirect, render_template, request, session, url_for,
)

from app.services.auth import get_service
from app.services.notifications import notifications

bp = Blueprint("auth", __name__)


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """First-run setup wizard. Sets the initial password."""
    svc = get_service()
    if svc.is_configured():
        # Already initialised — disallow reset via this route. Password
        # changes go through Settings → Security.
        return redirect(url_for("auth.login"))

    error: str | None = None
    if request.method == "POST":
        pw1 = (request.form.get("password") or "").strip()
        pw2 = (request.form.get("password_confirm") or "").strip()
        if pw1 != pw2:
            error = "passwords do not match"
        else:
            ok, msg = svc.set_password(pw1)
            if not ok:
                error = msg
            else:
                # Auto-log in the user who just set the password
                svc.login(session)
                notifications.success("PiPineapple initialised — password set", source="auth")
                return redirect(url_for("dashboard.index"))

    return render_template("auth/setup.html", error=error)


@bp.route("/login", methods=["GET", "POST"])
def login():
    """Login form."""
    svc = get_service()
    if not svc.is_configured():
        return redirect(url_for("auth.setup"))
    if svc.is_logged_in(session):
        return redirect(url_for("dashboard.index"))

    error: str | None = None
    if request.method == "POST":
        pw = (request.form.get("password") or "").strip()
        if svc.verify(pw):
            svc.login(session)
            return redirect(url_for("dashboard.index"))
        error = "wrong password"

    return render_template("auth/login.html", error=error)


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    svc = get_service()
    svc.logout(session)
    return redirect(url_for("auth.login"))


# ---------- Password change (Settings → Security uses this) ----------
@bp.route("/auth/change-password", methods=["POST"])
def change_password():
    """JSON API. Verifies old password, sets new one."""
    from flask import jsonify
    svc = get_service()
    if not svc.is_logged_in(session):
        return jsonify({"ok": False, "msg": "not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    old = (data.get("old") or "").strip()
    new1 = (data.get("new") or "").strip()
    new2 = (data.get("new_confirm") or "").strip()
    if not svc.verify(old):
        return jsonify({"ok": False, "msg": "old password is wrong"}), 400
    if new1 != new2:
        return jsonify({"ok": False, "msg": "new passwords do not match"}), 400
    ok, msg = svc.set_password(new1)
    if ok:
        notifications.success("password changed", source="auth")
    return jsonify({"ok": ok, "msg": msg})
