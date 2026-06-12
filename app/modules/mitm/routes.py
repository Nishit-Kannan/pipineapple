"""MITM module blueprint."""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from app.services.notifications import notifications
from . import tools
from .service import get_service

bp = Blueprint("mod_mitm", __name__, template_folder="templates",
               url_prefix="/modules/mitm")


@bp.route("/")
def index():
    ok, detail = tools.is_available()
    return render_template("mitm.html", bettercap_ok=ok, bettercap_detail=detail,
                           candidates=get_service().candidate_targets())


@bp.route("/targets")
def targets():
    return jsonify(get_service().candidate_targets())


@bp.route("/start", methods=["POST"])
def start():
    data = request.get_json(silent=True) or {}
    ok, msg = get_service().start(
        targets=(data.get("targets") or "").strip(),
        confirm=data.get("confirm"),
        iface=(data.get("iface") or "").strip() or None,
        dns_spoof=bool(data.get("dns_spoof")),
        dns_domains=(data.get("dns_domains") or "*").strip() or "*",
        dns_redirect_ip=(data.get("dns_redirect_ip") or "").strip() or None,
    )
    (notifications.warning if ok else notifications.info)(
        f"mitm: {msg}", source="mitm")
    return jsonify({"ok": ok, "msg": msg, "status": get_service().get_status()}), \
        (200 if ok else 400)


@bp.route("/stop", methods=["POST"])
def stop():
    ok, msg = get_service().stop()
    (notifications.info if ok else notifications.warning)(
        f"mitm: {msg}", source="mitm")
    return jsonify({"ok": ok, "msg": msg, "status": get_service().get_status()}), \
        (200 if ok else 400)


@bp.route("/status")
def status():
    return jsonify(get_service().get_status())


@bp.route("/events")
def events():
    try:
        limit = int(request.args.get("limit", 200))
    except (TypeError, ValueError):
        limit = 200
    return jsonify({"status": get_service().get_status(),
                    "events": get_service().get_events(limit=limit)})
