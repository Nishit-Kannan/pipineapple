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

from flask import Blueprint, jsonify, render_template

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
