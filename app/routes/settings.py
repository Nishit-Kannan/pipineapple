"""Settings blueprint — adapter management, networking, hostname, etc.

Session 04 only fills the Adapter Management tab. The rest of the
Settings page (Networking, WiFi, Advanced, Help) gets built in Phase G
(Session 18).
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from app.services.access_control import access_control
from app.services.adapters import get_service
from app.services.networking import get_service as get_networking
from app.services.notifications import notifications

bp = Blueprint("settings", __name__, url_prefix="/settings")


# ---------- HTML ----------
@bp.route("/")
def index():
    """Settings page with the Adapter Management tab active."""
    svc = get_service()
    net = get_networking()
    return render_template(
        "settings.html",
        adapters=svc.list_adapters(),
        roles_assigned=svc.get_roles(),
        deny_cidrs=access_control.list_cidrs(),
        networking=net.get_state(),
    )


# ---------- Networking tab ----------
@bp.route("/networking")
def networking_json():
    return jsonify(get_networking().get_state())


@bp.route("/networking/wifi/scan", methods=["POST"])
def wifi_scan():
    return jsonify({"networks": get_networking().scan_wifi()})


@bp.route("/networking/wifi/connect", methods=["POST"])
def wifi_connect():
    data = request.get_json(silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    pw = data.get("password") or ""
    if not ssid:
        return jsonify({"ok": False, "msg": "missing ssid"}), 400
    ok, msg = get_networking().connect_wifi(ssid, pw if pw else None)
    notif = notifications.success if ok else notifications.error
    notif(f"wifi connect: {msg}", source="networking")
    return jsonify({"ok": ok, "msg": msg, "state": get_networking().get_state()})


@bp.route("/networking/wifi/save", methods=["POST"])
def wifi_save():
    """Save a Wi-Fi profile without trying to connect right now.

    Used when wlan0 is busy hosting the management AP — we can stash the
    credentials so NetworkManager auto-connects once wlan0 is freed.
    """
    data = request.get_json(silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    pw = data.get("password") or ""
    if not ssid:
        return jsonify({"ok": False, "msg": "missing ssid"}), 400
    ok, msg = get_networking().save_wifi(ssid, pw if pw else None)
    notif = notifications.success if ok else notifications.warning
    notif(f"wifi save: {msg}", source="networking")
    return jsonify({"ok": ok, "msg": msg, "state": get_networking().get_state()})


@bp.route("/networking/wifi/disconnect", methods=["POST"])
def wifi_disconnect():
    ok, msg = get_networking().disconnect_wifi()
    return jsonify({"ok": ok, "msg": msg, "state": get_networking().get_state()})


@bp.route("/networking/wifi/forget", methods=["POST"])
def wifi_forget():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "msg": "missing name"}), 400
    ok, msg = get_networking().forget_wifi(name)
    return jsonify({"ok": ok, "msg": msg, "state": get_networking().get_state()})


@bp.route("/networking/mgmt-ap/configure", methods=["POST"])
def mgmt_ap_configure():
    data = request.get_json(silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    pw = (data.get("password") or "").strip()
    channel = int(data.get("channel") or 6)
    ok, msg = get_networking().configure_mgmt_ap(ssid, pw, channel)
    return jsonify({"ok": ok, "msg": msg, "state": get_networking().get_state()})


@bp.route("/networking/mgmt-ap/enable", methods=["POST"])
def mgmt_ap_enable():
    ok, messages = get_networking().enable_mgmt_ap()
    summary = "; ".join(messages)
    notif = notifications.success if ok else notifications.error
    notif(f"management AP enable: {summary}", source="networking")
    return jsonify({"ok": ok, "messages": messages, "state": get_networking().get_state()})


@bp.route("/networking/mgmt-ap/disable", methods=["POST"])
def mgmt_ap_disable():
    ok, messages = get_networking().disable_mgmt_ap()
    notifications.info("management AP disabled", source="networking")
    return jsonify({"ok": ok, "messages": messages, "state": get_networking().get_state()})


# ---------- Security tab ----------
@bp.route("/access/deny", methods=["POST"])
def add_deny_cidr():
    data = request.get_json(silent=True) or {}
    cidr = (data.get("cidr") or "").strip()
    ok, msg = access_control.add_cidr(cidr)
    notif = notifications.success if ok else notifications.warning
    notif(f"access deny add: {msg}", source="security")
    return jsonify({"ok": ok, "msg": msg, "deny_cidrs": access_control.list_cidrs()})


@bp.route("/access/deny/remove", methods=["POST"])
def remove_deny_cidr():
    data = request.get_json(silent=True) or {}
    cidr = (data.get("cidr") or "").strip()
    ok, msg = access_control.remove_cidr(cidr)
    notif = notifications.success if ok else notifications.warning
    notif(f"access deny remove: {msg}", source="security")
    return jsonify({"ok": ok, "msg": msg, "deny_cidrs": access_control.list_cidrs()})


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
