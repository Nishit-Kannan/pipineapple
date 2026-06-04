"""Settings blueprint — adapter management, networking, hostname, etc.

Session 04 only fills the Adapter Management tab. The rest of the
Settings page (Networking, WiFi, Advanced, Help) gets built in Phase G
(Session 18).
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from app.services.adapters import get_service
from app.services.notifications import notifications

bp = Blueprint("settings", __name__, url_prefix="/settings")


# ---------- HTML ----------
@bp.route("/")
def index():
    """Settings page with the Adapter Management tab active."""
    svc = get_service()
    return render_template(
        "settings.html",
        adapters=svc.list_adapters(),
        roles_assigned=svc.get_roles(),
    )


# ---------- JSON API used by the page's JS ----------
@bp.route("/adapters")
def adapters_json():
    """Live adapter list as JSON. JS calls this after every action."""
    svc = get_service()
    return jsonify({
        "adapters":       svc.list_adapters(),
        "roles_assigned": svc.get_roles(),
    })


@bp.route("/adapters/role", methods=["POST"])
def assign_role():
    """Assign (or clear) a role for an adapter, keyed by MAC."""
    data = request.get_json(silent=True) or {}
    mac = (data.get("mac") or "").strip().lower()
    role = (data.get("role") or "none").strip()
    if not mac:
        return jsonify({"ok": False, "msg": "missing mac"}), 400
    svc = get_service()
    ok, msg = svc.set_role(mac, role)
    if ok:
        notifications.info(msg, source="adapters")
    else:
        notifications.warning(msg, source="adapters")
    return jsonify({"ok": ok, "msg": msg, "adapters": svc.list_adapters()})


@bp.route("/adapters/<iface>/mode", methods=["POST"])
def set_mode(iface: str):
    """Toggle an adapter into monitor or managed mode."""
    data = request.get_json(silent=True) or {}
    mode = (data.get("mode") or "").strip()
    svc = get_service()
    ok, messages = svc.set_mode(iface, mode)
    summary = "; ".join(messages)
    if ok:
        notifications.success(f"{iface} -> {mode}: {summary}", source="adapters")
    else:
        notifications.error(f"{iface} -> {mode} failed: {summary}", source="adapters")
    return jsonify({"ok": ok, "messages": messages})


@bp.route("/adapters/<iface>/down", methods=["POST"])
def set_down(iface: str):
    svc = get_service()
    ok, msg = svc.set_down(iface)
    notif = notifications.success if ok else notifications.error
    notif(f"{iface} down: {msg}", source="adapters")
    return jsonify({"ok": ok, "msg": msg})


@bp.route("/adapters/apply-udev", methods=["POST"])
def apply_udev():
    svc = get_service()
    ok, msg = svc.apply_udev_rules()
    if ok:
        notifications.success(f"udev rules applied: {msg}", source="adapters")
    else:
        notifications.error(f"udev apply failed: {msg}", source="adapters")
    return jsonify({"ok": ok, "msg": msg})


@bp.route("/adapters/apply-nm", methods=["POST"])
def apply_nm():
    svc = get_service()
    ok, msg = svc.apply_nm_unmanaging()
    if ok:
        notifications.success(f"NM unmanaging config applied: {msg}", source="adapters")
    else:
        notifications.error(f"NM apply failed: {msg}", source="adapters")
    return jsonify({"ok": ok, "msg": msg})


@bp.route("/adapters/stop-managers", methods=["POST"])
def stop_managers():
    svc = get_service()
    ok, msg = svc.stop_managers()
    notif = notifications.success if ok else notifications.error
    notif(f"stop NM+wpa_supplicant: {msg}", source="adapters")
    return jsonify({"ok": ok, "msg": msg})
