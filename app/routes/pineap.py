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


# ---------- Open SSID tab (S11) ----------

@bp.route("/ap-config", methods=["POST"])
def set_ap_config():
    """Update the open-AP config (primary SSID, channel, hidden,
    hw_mode). Refuses while PineAP is running."""
    data = request.get_json(silent=True) or {}
    ok, msg = get_service().set_ap_config(
        primary_ssid=data.get("primary_ssid"),
        channel=data.get("channel"),
        primary_hidden=data.get("primary_hidden"),
        hw_mode=data.get("hw_mode"),
        security_mode=data.get("security_mode"),
        evil_wpa_deauth=data.get("evil_wpa_deauth"),
        auto_captive_portal=data.get("auto_captive_portal"),
    )
    notif = notifications.success if ok else notifications.warning
    notif(f"pineap AP config: {msg}", source="pineap")
    return jsonify({"ok": ok, "msg": msg, "state": get_service().get_state()}), \
        (200 if ok else 400)


@bp.route("/clients", methods=["GET"])
def list_clients():
    """Connected + previously-seen clients, enriched with OS fingerprint
    + recent DNS queries from client_recon."""
    from app.services.client_recon import get_service as get_client_recon
    return jsonify({"clients": get_client_recon().list_clients()})


@bp.route("/clients/<mac>", methods=["GET"])
def get_client(mac: str):
    """Full per-client detail including the full DNS query history."""
    from flask import abort
    from app.services.client_recon import get_service as get_client_recon
    c = get_client_recon().get_client(mac)
    if c is None:
        abort(404, description=f"no client {mac}")
    return jsonify(c)


@bp.route("/clients/clear", methods=["POST"])
def clear_clients():
    """Drop all persisted client records. Doesn't kick associated
    clients (that's an S13 feature) — just wipes the recon store."""
    from app.services.client_recon import get_service as get_client_recon
    ok, msg, removed = get_client_recon().clear()
    notifications.warning(f"pineap clients clear: {msg}", source="pineap")
    return jsonify({"ok": ok, "msg": msg, "removed": removed})


@bp.route("/probes", methods=["GET"])
def list_probes():
    """Captive-portal probe log from the sentinel listener. ``?limit=N``
    caps the result (default 200, newest first)."""
    from app.services.captive_sentinel import get_service as get_sentinel
    try:
        limit = int(request.args.get("limit", 200))
    except (TypeError, ValueError):
        limit = 200
    return jsonify({"probes": get_sentinel().list_probes(limit=limit)})


@bp.route("/karma/stats", methods=["GET"])
def karma_stats():
    """Live karma sniffer stats (probes seen, probes replied, unique
    clients/SSIDs). Empty when karma isn't running."""
    from app.services.karma import get_service as get_karma
    return jsonify(get_karma().get_stats())


# ---------- Evil WPA (S12) ----------

@bp.route("/evil-wpa/clone", methods=["POST"])
def clone_evil_wpa():
    """Set up an Evil WPA clone from a Recon-observed AP. Called by
    the "Clone to PineAP" button on the Recon page slide-out. Body::

        {
          "bssid":             "aa:bb:cc:11:22:33",
          "essid":             "HomeNet",
          "channel":           6,
          "source_signal_dbm": -52,            (optional)
          "source_security":   "WPA2-PSK"      (optional, informational)
        }

    Configures primary_ssid + channel + security_mode=wpa2 in one
    shot and records the source AP metadata. Refuses while PineAP
    is running."""
    data = request.get_json(silent=True) or {}
    ok, msg = get_service().clone_evil_wpa_target(
        bssid=(data.get("bssid") or "").strip(),
        essid=(data.get("essid") or "").strip(),
        channel=data.get("channel"),
        source_signal_dbm=data.get("source_signal_dbm"),
        source_security=data.get("source_security"),
        source_mfp_required=data.get("source_mfp_required"),
    )
    notif = notifications.success if ok else notifications.warning
    notif(f"evil-wpa clone: {msg}", source="pineap")
    return jsonify({
        "ok":    ok,
        "msg":   msg,
        "state": get_service().get_state(),
    }), (200 if ok else 400)


@bp.route("/evil-wpa/state", methods=["GET"])
def evil_wpa_state():
    """Live Evil WPA sniffer state + stats (frames seen, EAPOL seen,
    partials extracted, current session id). Empty fields when not
    running."""
    from app.services.evil_wpa import get_service as get_evil_wpa
    return jsonify(get_evil_wpa().get_stats())


@bp.route("/evil-wpa/partials", methods=["GET"])
def evil_wpa_partials():
    """All partial handshakes harvested in the current/last Evil WPA
    session. Each entry has the .22000 hash line plus metadata
    (AP MAC, STA MAC, ESSID, extracted_at). The Crack dispatch
    flow can ingest these via the same .22000 path as recon
    captures (task #113 wires them into the Handshakes page)."""
    from app.services.evil_wpa import get_service as get_evil_wpa
    return jsonify({"partials": get_evil_wpa().list_partials()})


# ---------- Captive portal (S12.5) ----------

@bp.route("/captive-portal/state", methods=["GET"])
def captive_portal_state():
    """Captive-portal status: global opt-in, verify mode, whether the
    portal is live (post bait-switch), armed SSID, attempt/verified
    counts. Merged with the pineap runtime flag."""
    from app.services.captive_portal import get_service as get_cp
    stats = get_cp().get_stats()
    stats["pineap_portal_active"] = bool(
        get_service().get_state().get("captive_portal_active"))
    return jsonify(stats)


@bp.route("/captive-portal/credentials", methods=["GET"])
def captive_portal_credentials():
    """Harvested credential attempts, newest first, each with its
    verify-against-handshake result."""
    from app.services.captive_portal import get_service as get_cp
    try:
        limit = int(request.args.get("limit", 200))
    except (TypeError, ValueError):
        limit = 200
    return jsonify({"credentials": get_cp().list_credentials(limit=limit)})


@bp.route("/captive-portal/clear", methods=["POST"])
def captive_portal_clear():
    from app.services.captive_portal import get_service as get_cp
    ok, msg, removed = get_cp().clear_credentials()
    notifications.warning(f"captive creds clear: {msg}", source="pineap")
    return jsonify({"ok": ok, "msg": msg, "removed": removed})
