"""Handshakes blueprint — capture lifecycle + (future) list view.

For Session 07 this is the capture-control surface only: start, stop,
status. Session 08 will add the top-level Handshakes page that lists
every captured handshake across all sessions; the list endpoint is
already here (returns the persisted index).

Capture is triggered from the Recon AP slide-out, but the routes live
here (not under /recon/) because the resource lifecycle is owned by
the handshakes service.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services.handshakes import get_service
from app.services.notifications import notifications

bp = Blueprint("handshakes", __name__, url_prefix="/handshakes")


@bp.route("/start", methods=["POST"])
def start():
    """Launch a focused capture for a single AP.

    Body::

        {
          "bssid":   "aa:bb:cc:dd:ee:ff",
          "channel": 6,
          "essid":   "HomeWiFi",        (optional metadata)
          "deauth":  false,             (optional, default false)
          "tool":    "hcxdumptool"      (optional, default hcxdumptool;
                                         supported: "hcxdumptool",
                                         "airodump-ng")
        }
    """
    data = request.get_json(silent=True) or {}
    bssid = (data.get("bssid") or "").strip()
    channel = data.get("channel")
    essid = (data.get("essid") or "").strip()
    deauth = bool(data.get("deauth", False))
    tool = (data.get("tool") or "hcxdumptool").strip()

    if not bssid:
        return jsonify({"ok": False, "messages": ["bssid is required"]}), 400
    try:
        channel = int(channel) if channel is not None else 0
    except (TypeError, ValueError):
        return jsonify({"ok": False, "messages": ["channel must be an int"]}), 400
    if not (1 <= channel <= 196):
        return jsonify({"ok": False, "messages": [f"channel {channel} out of range"]}), 400

    ok, messages = get_service().start_capture(
        bssid, channel, essid, deauth=deauth, tool=tool,
    )
    summary = "; ".join(messages)
    notif = notifications.success if ok else notifications.error
    notif(f"capture start {bssid}: {summary}", source="handshakes")

    status = get_service().get_capture_status(bssid)
    return jsonify({
        "ok":       ok,
        "messages": messages,
        "status":   status,
    })


@bp.route("/stop", methods=["POST"])
def stop():
    """Stop a running capture by BSSID."""
    data = request.get_json(silent=True) or {}
    bssid = (data.get("bssid") or "").strip()
    if not bssid:
        return jsonify({"ok": False, "messages": ["bssid is required"]}), 400

    ok, messages = get_service().stop_capture(bssid)
    summary = "; ".join(messages)
    notifications.info(f"capture stop {bssid}: {summary}", source="handshakes")
    return jsonify({"ok": ok, "messages": messages})


@bp.route("/status/<bssid>")
def status(bssid: str):
    """Live status for a running capture. 404 if no capture is running
    for this BSSID."""
    from flask import abort
    s = get_service().get_capture_status(bssid)
    if s is None:
        abort(404, description=f"no capture in flight for {bssid}")
    return jsonify(s)


@bp.route("/list")
def list_captures():
    """Persisted handshake captures. Optional ``?bssid=`` filters to one AP.

    Used both by the top-level Handshakes page (Session 08) and by the
    AP slide-out's Captures tab (Session 07).
    """
    bssid = request.args.get("bssid")
    captures = get_service().list_captures(bssid=bssid)
    return jsonify({"captures": captures})


@bp.route("/delete", methods=["POST"])
def delete_capture():
    """Delete a single capture by id. Refuses if a capture is in flight
    for that BSSID (would clobber the live writer)."""
    data = request.get_json(silent=True) or {}
    cid = (data.get("id") or "").strip()
    if not cid:
        return jsonify({"ok": False, "msg": "id is required"}), 400
    ok, msg = get_service().delete_capture(cid)
    notif = notifications.success if ok else notifications.warning
    notif(f"capture delete: {msg}", source="handshakes")
    return jsonify({"ok": ok, "msg": msg})


@bp.route("/delete-by-bssid", methods=["POST"])
def delete_by_bssid():
    """Bulk delete every persisted capture for one AP."""
    data = request.get_json(silent=True) or {}
    bssid = (data.get("bssid") or "").strip()
    if not bssid:
        return jsonify({"ok": False, "msg": "bssid is required"}), 400
    ok, msg = get_service().delete_all_for_bssid(bssid)
    notif = notifications.success if ok else notifications.warning
    notif(f"captures bulk-delete {bssid}: {msg}", source="handshakes")
    return jsonify({"ok": ok, "msg": msg})
