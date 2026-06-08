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

from flask import Blueprint, jsonify, render_template, request, send_file

from app.services.handshakes import get_service
from app.services.notifications import notifications

bp = Blueprint("handshakes", __name__, url_prefix="/handshakes")


# ---------- HTML page (Session 08) ----------
@bp.route("/")
def index():
    """Top-level Handshakes page — full table of every persisted
    capture across all APs and sources. Server pre-renders the
    initial list; client JS refreshes on capture:status SocketIO
    events and after any delete."""
    captures = get_service().list_captures()
    return render_template("handshakes.html", captures=captures)


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
    # Default flipped to airodump in S07.7 — see handshakes.py for the
    # mt76 + hcxdumptool 6.3.5 compatibility reason.
    tool = (data.get("tool") or "airodump-ng").strip()

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


# ---------- Downloads (Session 08) ----------
@bp.route("/<capture_id>/download/pcap")
def download_pcap(capture_id: str):
    """Serve the raw .pcap/.pcapng for forensic / Wireshark / external
    tool consumption. Mime type set per format."""
    from flask import abort
    svc = get_service()
    cap = svc.get_capture_record(capture_id)
    if cap is None:
        abort(404, description=f"no capture {capture_id}")
    pcap_path = svc.resolve_pcap_path(cap)
    if pcap_path is None:
        abort(410, description="pcap file gone from disk (index entry stale)")

    # pcapng has a distinct mime type from libpcap; both browsers/tools
    # accept application/octet-stream so use that for simplicity.
    bssid_fs = cap.get("bssid", "unknown").upper().replace(":", "-")
    fmt = cap.get("pcap_format") or pcap_path.suffix.lstrip(".") or "pcap"
    filename = f"{bssid_fs}_{capture_id[:8]}.{fmt}"
    return send_file(
        pcap_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/octet-stream",
    )


@bp.route("/<capture_id>/download/22000")
def download_22000(capture_id: str):
    """Convert the pcap to hashcat .22000 format on demand and serve.

    Conversion is cached next to the pcap so the second download is
    instant. Returns 404 if the pcap contains no crackable PMKID /
    EAPOL pairs (operator sees a clear message instead of a 0-byte
    file)."""
    from flask import abort
    svc = get_service()
    cap = svc.get_capture_record(capture_id)
    if cap is None:
        abort(404, description=f"no capture {capture_id}")
    path, msg = svc.resolve_or_build_22000(cap)
    if path is None:
        # Common cases: empty pcap (junk capture); hcxpcapngtool not
        # installed; pcap file gone. Either way: 404 with the msg.
        abort(404, description=msg)

    bssid_fs = cap.get("bssid", "unknown").upper().replace(":", "-")
    filename = f"{bssid_fs}_{capture_id[:8]}.22000"
    return send_file(
        path,
        as_attachment=True,
        download_name=filename,
        mimetype="text/plain",
    )
