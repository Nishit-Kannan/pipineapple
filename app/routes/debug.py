"""Debug routes — only registered when DEBUG is true.

Lets you exercise the live UI from the shell without waiting for real
events. Useful for sanity-checking the notification drawer and the
JobManager skeleton during development.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services.job_manager import job_manager
from app.services.notifications import notifications

bp = Blueprint("debug", __name__, url_prefix="/debug")


@bp.route("/notify", methods=["POST", "GET"])
def trigger_notification():
    """POST /debug/notify with JSON {severity, message} — emits a notification.

    GET form takes ``?severity=...&message=...`` for convenience from the
    browser address bar.
    """
    data = request.get_json(silent=True) or {}
    severity = (
        data.get("severity")
        or request.args.get("severity")
        or "info"
    ).lower()
    message = (
        data.get("message")
        or request.args.get("message")
        or f"Test {severity} notification from /debug/notify"
    )
    if severity not in ("info", "warning", "error", "success", "unknown"):
        return jsonify({"error": "invalid severity"}), 400
    entry = notifications.add(severity, message, source="debug")
    return jsonify(entry)


@bp.route("/notify/burst")
def burst_notifications():
    """Fire one notification of each severity — handy for visual review."""
    out = []
    out.append(notifications.info("Info notification — routine event", source="debug"))
    out.append(notifications.warning("Warning notification — something to know about", source="debug"))
    out.append(notifications.error("Error notification — something failed", source="debug"))
    out.append(notifications.success("Success notification — handshake captured", source="debug"))
    return jsonify(out)


@bp.route("/job/start", methods=["GET", "POST"])
def start_job():
    """Start an arbitrary job via the JobManager.

    Use POST with JSON ``{cmd, name?}``, or GET ``?cmd=...``.  For safety,
    only registered in DEBUG mode anyway.
    """
    data = request.get_json(silent=True) or {}
    cmd = data.get("cmd") or request.args.get("cmd")
    name = data.get("name") or request.args.get("name")
    if not cmd:
        return jsonify({"error": "missing cmd"}), 400
    job = job_manager.start_job(cmd, name=name, tags=["debug"])
    return jsonify(job.to_dict())


@bp.route("/job/list")
def list_jobs():
    return jsonify([j.to_dict() for j in job_manager.list_jobs()])


@bp.route("/job/<job_id>/stop", methods=["POST", "GET"])
def stop_job(job_id):
    stopped, reason = job_manager.stop_job(job_id)
    return jsonify({"stopped": stopped, "reason": reason, "job_id": job_id})


@bp.route("/job/<job_id>")
def get_job(job_id):
    job = job_manager.get_job(job_id)
    if job is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({**job.to_dict(), "stdout": job.stdout_lines()})
