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

    Configures both the platform admin password AND the operator's
    permanent management AP credentials in a single form. If the Pi
    is currently running the bootstrap AP, the new credentials replace
    the bootstrap values and the AP restarts in the background so the
    operator sees a clear "reconnect to new SSID" message before losing
    their connection.
    """
    svc = get_service()
    if svc.is_configured():
        return redirect(url_for("auth.login"))

    net = get_networking()
    bootstrap_active = net.is_running_bootstrap()
    error: str | None = None
    success_info: dict | None = None

    if request.method == "POST":
        pw1 = (request.form.get("password") or "").strip()
        pw2 = (request.form.get("password_confirm") or "").strip()
        ap_ssid = (request.form.get("ap_ssid") or "").strip()
        ap_pw1  = (request.form.get("ap_password") or "").strip()
        ap_pw2  = (request.form.get("ap_password_confirm") or "").strip()

        if pw1 != pw2:
            error = "platform passwords do not match"
        elif ap_pw1 != ap_pw2:
            error = "AP passwords do not match"
        elif len(ap_ssid) < 1 or len(ap_ssid) > 32:
            error = "AP SSID must be 1–32 characters"
        elif len(ap_pw1) < 8:
            error = "AP WPA2 password must be at least 8 characters"
        elif ap_pw1 == BOOTSTRAP_MGMT_AP["password"]:
            error = "please pick a new AP password — leaving the bootstrap default isn't safe"
        else:
            ok, msg = svc.set_password(pw1)
            if not ok:
                error = msg
            else:
                svc.login(session)
                notifications.success("PiPineapple initialised — password set", source="auth")

                # Save the operator's AP credentials and restart the AP
                # in the background so the success page can render before
                # the operator's connection drops.
                import threading
                import time as _time

                def _reconfigure_and_restart():
                    _time.sleep(3)  # give the response time to land
                    try:
                        net.reconfigure_and_restart_ap(ap_ssid, ap_pw1, channel=6)
                    except Exception:
                        from flask import current_app
                        current_app.logger.exception("AP reconfigure failed")

                threading.Thread(
                    target=_reconfigure_and_restart,
                    daemon=True,
                    name="setup-ap-restart",
                ).start()

                success_info = {
                    "new_ssid":   ap_ssid,
                    "gateway_ip": (DEFAULT_MGMT_AP.get("gateway_ip") or "10.42.0.1"),
                    "bootstrap":  bootstrap_active,
                }

    return render_template(
        "auth/setup.html",
        error=error,
        bootstrap_active=bootstrap_active,
        bootstrap=BOOTSTRAP_MGMT_AP,
        default_ssid=DEFAULT_MGMT_AP["ssid"],
        success_info=success_info,
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
