"""PineAP blueprint — Settings tab + pool CRUD + lifecycle.

S10 lands the full surface for the Settings tab (mode + broadcast +
capture + pool curation) and the start/stop endpoints. Other tabs in
the Pineapple's PineAP section (Open SSID, Evil WPA, Impersonation,
Filtering, Clients) arrive in S11-S13 and will get their own blueprints
or extend this one as appropriate.

All routes return JSON. Convention matches the rest of the codebase:
``{ok, msg}`` for single-item ops, ``{ok, messages, ...}`` for
multi-step ops, notifications service called for every operator action
so the bell drawer shows what happened.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from app.services.notifications import notifications
from app.services.pineap import (
    SOURCE_MANUAL,
    get_service,
)

bp = Blueprint("pineap", __name__, url_prefix="/pineap")


# ---------- HTML page ----------

@bp.route("/")
def index():
    """PineAP top-level page. S10 only lands the Settings tab; the
    other Pineapple tabs (Open SSID, Evil WPA, Impersonation,
    Filtering, Clients, Access Points) are disabled placeholders
    until their respective sessions."""
    svc = get_service()
    return render_template(
        "pineap.html",
        state=svc.get_state(),
        pool=svc.list_pool(),
    )


# ---------- State / mode ----------

@bp.route("/state", methods=["GET"])
def get_state():
    return jsonify(get_service().get_state())


@bp.route("/mode", methods=["POST"])
def set_mode():
    """Body: ``{"mode": "off|passive|active|advanced"}``."""
    data = request.get_json(silent=True) or {}
    mode = (data.get("mode") or "").strip()
    ok, msg = get_service().set_mode(mode)
    notif = notifications.success if ok else notifications.warning
    notif(f"pineap mode: {msg}", source="pineap")
    return jsonify({"ok": ok, "msg": msg, "state": get_service().get_state()}), \
        (200 if ok else 400)


@bp.route("/broadcast", methods=["POST"])
def set_broadcast():
    """Body: ``{"enabled": true|false}``."""
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled"))
    ok, msg = get_service().set_broadcast(enabled)
    notifications.info(f"pineap broadcast: {msg}", source="pineap")
    return jsonify({"ok": ok, "msg": msg, "state": get_service().get_state()})


@bp.route("/capture", methods=["POST"])
def set_capture():
    """Body: ``{"enabled": true|false}``."""
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled"))
    ok, msg = get_service().set_capture(enabled)
    notifications.info(f"pineap capture: {msg}", source="pineap")
    return jsonify({"ok": ok, "msg": msg, "state": get_service().get_state()})


@bp.route("/start", methods=["POST"])
def start():
    """Bring up PineAP per the current mode. In S10 this only succeeds
    for ``passive`` mode — ``active``/``advanced`` return a clear
    "wait for S11" message."""
    ok, messages = get_service().start()
    summary = "; ".join(messages)
    notif = notifications.success if ok else notifications.warning
    notif(f"pineap start: {summary}", source="pineap")
    return jsonify({
        "ok": ok, "messages": messages, "state": get_service().get_state(),
    }), (200 if ok else 400)


@bp.route("/stop", methods=["POST"])
def stop():
    ok, messages = get_service().stop()
    notifications.info(f"pineap stop: {'; '.join(messages)}", source="pineap")
    return jsonify({
        "ok": ok, "messages": messages, "state": get_service().get_state(),
    })


# ---------- SSID pool ----------

@bp.route("/pool", methods=["GET"])
def list_pool():
    return jsonify({"ssids": get_service().list_pool()})


@bp.route("/pool", methods=["POST"])
def add_pool():
    """Operator-manual SSID add. Body: ``{"ssid": "...", "pin": false}``.
    Auto-population from recon/probe is handled in-process via
    ``pineap.auto_add_from_recon`` / ``auto_add_from_probes``; this
    route is for the operator-typed path only."""
    data = request.get_json(silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    pin = bool(data.get("pin", False))
    ok, msg = get_service().add_ssid(ssid, source=SOURCE_MANUAL, pin=pin)
    notif = notifications.success if ok else notifications.warning
    notif(f"pineap pool +manual: {msg}", source="pineap")
    return jsonify({"ok": ok, "msg": msg}), (200 if ok else 400)


@bp.route("/pool/<path:ssid>", methods=["DELETE"])
def remove_pool(ssid: str):
    ok, msg = get_service().remove_ssid(ssid)
    notif = notifications.info if ok else notifications.warning
    notif(f"pineap pool remove: {msg}", source="pineap")
    return jsonify({"ok": ok, "msg": msg}), (200 if ok else 404)


@bp.route("/pool/<path:ssid>/pin", methods=["POST"])
def pin_pool(ssid: str):
    """Body: ``{"pinned": true|false}``."""
    data = request.get_json(silent=True) or {}
    pinned = bool(data.get("pinned", True))
    ok, msg = get_service().set_pinned(ssid, pinned)
    notifications.info(f"pineap pool pin: {msg}", source="pineap")
    return jsonify({"ok": ok, "msg": msg}), (200 if ok else 404)


@bp.route("/pool/<path:ssid>/hide", methods=["POST"])
def hide_pool(ssid: str):
    """Body: ``{"hidden": true|false}``. Hidden entries are excluded
    from broadcast but kept in the pool with timestamps intact."""
    data = request.get_json(silent=True) or {}
    hidden = bool(data.get("hidden", True))
    ok, msg = get_service().set_hidden(ssid, hidden)
    notifications.info(f"pineap pool hide: {msg}", source="pineap")
    return jsonify({"ok": ok, "msg": msg}), (200 if ok else 404)


@bp.route("/pool/clear", methods=["POST"])
def clear_pool():
    """Body: ``{"include_pinned": false}`` to optionally nuke pinned too."""
    data = request.get_json(silent=True) or {}
    include_pinned = bool(data.get("include_pinned", False))
    ok, msg, removed = get_service().clear_pool(include_pinned=include_pinned)
    notifications.warning(f"pineap pool clear: {msg}", source="pineap")
    return jsonify({"ok": ok, "msg": msg, "removed": removed})
