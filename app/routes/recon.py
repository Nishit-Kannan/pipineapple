"""Recon blueprint — passive WiFi scan UI + JSON API.

Three endpoints, deliberately small:

* ``GET  /recon``                  HTML page with the live scan tables.
* ``POST /recon/start``            kick off both airodump jobs.
* ``POST /recon/stop``             stop them and clear state.
* ``GET  /recon/snapshot``         current AP + client tables as JSON
                                   (used by the page's initial render
                                   before the first SocketIO event).

Live updates flow over SocketIO as ``recon:update`` events emitted by
the recon service's background poller.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from app.services.notifications import notifications
from app.services.recon import get_service

bp = Blueprint("recon", __name__, url_prefix="/recon")


# ---------- HTML ----------
@bp.route("/")
def index():
    svc = get_service()
    return render_template(
        "recon.html",
        status=svc.get_status(),
        snapshot=svc.get_snapshot(),
    )


# ---------- JSON API ----------
@bp.route("/snapshot")
def snapshot():
    """Current snapshot. Polled once by the page on load; after that
    the live updates come over SocketIO."""
    return jsonify(get_service().get_snapshot())


@bp.route("/start", methods=["POST"])
def start():
    ok, messages = get_service().start_scan()
    summary = "; ".join(messages)
    notif = notifications.success if ok else notifications.error
    notif(f"recon start: {summary}", source="recon")
    return jsonify({
        "ok":       ok,
        "messages": messages,
        "status":   get_service().get_status(),
    })


@bp.route("/stop", methods=["POST"])
def stop():
    ok, messages = get_service().stop_scan()
    summary = "; ".join(messages)
    notifications.info(f"recon stop: {summary}", source="recon")
    return jsonify({
        "ok":       ok,
        "messages": messages,
        "status":   get_service().get_status(),
    })


# ---------- Slide-out detail endpoints (Session 06) ----------
@bp.route("/ap/<bssid>/detail")
def ap_detail(bssid: str):
    """Full AP record for the slide-out: snapshot fields + parsed
    beacon IEs from the pcap + currently associated clients."""
    from flask import abort
    detail = get_service().get_ap_detail(bssid)
    if detail is None:
        abort(404, description=f"AP {bssid} not in current scan")
    return jsonify(detail)


@bp.route("/client/<mac>/detail")
def client_detail(mac: str):
    """Full client record for the slide-out: snapshot fields + full
    probe history with timing pulled from the pcap."""
    from flask import abort
    detail = get_service().get_client_detail(mac)
    if detail is None:
        abort(404, description=f"client {mac} not in current scan")
    return jsonify(detail)


@bp.route("/ap/<bssid>/deauth", methods=["POST"])
def ap_deauth(bssid: str):
    """Send deauth frames at an AP (or a specific client of it) via
    the injection radio. Ethics: lab/operator-owned equipment only;
    the UI gates this behind a confirm modal."""
    data = request.get_json(silent=True) or {}
    client_mac = (data.get("client_mac") or "").strip() or None
    count = int(data.get("count") or 10)

    ok, messages = get_service().deauth_ap(bssid, client_mac=client_mac, count=count)
    summary = "; ".join(messages)
    notif = notifications.success if ok else notifications.error
    target = client_mac or "(all clients)"
    notif(f"deauth {bssid} -> {target}: {summary}", source="recon")
    return jsonify({"ok": ok, "messages": messages})
