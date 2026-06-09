"""Campaigns blueprint (S14) — scripted assessment runs + reports.

Thin routes over CampaignsService: list templates, start/stop a run,
poll status, list past reports, download report JSON/HTML.
"""

from __future__ import annotations

from flask import Blueprint, abort, jsonify, render_template, request, send_file

from app.services.campaigns import get_service
from app.services.notifications import notifications

bp = Blueprint("campaigns", __name__, url_prefix="/campaigns")


@bp.route("/")
def index():
    svc = get_service()
    return render_template(
        "campaigns.html",
        status=svc.get_status(),
        reports=svc.list_reports(),
    )


@bp.route("/status")
def status():
    return jsonify(get_service().get_status())


@bp.route("/start", methods=["POST"])
def start():
    """Body: ``{template, duration_secs, confirm?, target_bssid?}``."""
    data = request.get_json(silent=True) or {}
    ok, msg = get_service().start(
        (data.get("template") or "").strip(),
        duration_secs=data.get("duration_secs", 600),
        confirm=data.get("confirm"),
        target_bssid=data.get("target_bssid"),
    )
    notif = notifications.success if ok else notifications.warning
    notif(f"campaign start: {msg}", source="campaigns")
    return jsonify({"ok": ok, "msg": msg, "status": get_service().get_status()}), \
        (200 if ok else 400)


@bp.route("/stop", methods=["POST"])
def stop():
    ok, msg = get_service().stop()
    notifications.info(f"campaign stop: {msg}", source="campaigns")
    return jsonify({"ok": ok, "msg": msg, "status": get_service().get_status()})


@bp.route("/reports")
def reports():
    return jsonify({"reports": get_service().list_reports()})


@bp.route("/reports/<report_id>/<fmt>")
def download_report(report_id: str, fmt: str):
    if fmt not in ("json", "html"):
        abort(404, description="format must be json or html")
    path = get_service().report_path(report_id, fmt)
    if path is None:
        abort(404, description=f"no {fmt} report for {report_id}")
    mimetype = "application/json" if fmt == "json" else "text/html"
    # HTML viewable inline; JSON downloads.
    return send_file(path, mimetype=mimetype,
                     as_attachment=(fmt == "json"),
                     download_name=f"campaign-{report_id}.{fmt}")
