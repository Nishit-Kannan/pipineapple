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
from app.services.networking import (
    BOOTSTRAP_MGMT_AP,
    DEFAULT_MGMT_AP,
    get_service as get_networking,
)
from app.services.notifications import notifications

bp = Blueprint("auth", __name__)


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """First-run setup wizard.

    Sets the platform admin password only. Management AP credentials
    are left on bootstrap defaults so the operator's current connection
    doesn't drop mid-setup. The operator changes AP credentials later
    via Settings → Networking → Management AP, ideally after they've
    configured upstream Wi-Fi for wlan0 (so they have a fallback path
    if the AP restart drops them).
    """
    svc = get_service()
    if svc.is_configured():
        return redirect(url_for("auth.login"))

    net = get_networking()
    bootstrap_active = net.is_running_bootstrap()
    error: str | None = None

    if request.method == "POST":
        pw1 = (request.form.get("password") or "").strip()
        pw2 = (request.form.get("password_confirm") or "").strip()

        if pw1 != pw2:
            error = "platform passwords do not match"
        elif len(pw1) < 4:
            error = "platform password must be at least 4 characters"
        else:
            ok, msg = svc.set_password(pw1)
            if not ok:
                error = msg
            else:
                svc.login(session)
                notifications.success("PiPineapple initialised — password set", source="auth")
                # Drop them straight at the dashboard. Management AP keeps
                # broadcasting bootstrap credentials; the operator changes
                # them later from Settings → Networking once they've
                # configured upstream Wi-Fi.
                return redirect(url_for("dashboard.index"))

    return render_template(
        "auth/setup.html",
        error=error,
        bootstrap_active=bootstrap_active,
        bootstrap=BOOTSTRAP_MGMT_AP,
    )


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
