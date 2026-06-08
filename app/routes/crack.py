"""Crack blueprint — target CRUD + crack-job lifecycle.

Two sub-surfaces:

* ``/crack/targets`` — list/add/remove/test SSH targets. Settings page
  exposes this; the platform's public key is shown so the operator
  knows what to put in each remote's authorized_keys.
* ``/crack/start|stop|jobs`` — dispatch and observe crack jobs. The
  Crack button on the Handshakes page calls /crack/start with
  {capture_id, target_id}; the UI then watches crack:status SocketIO
  events.

All routes return JSON. Errors return 4xx with ``{ok: false, msg: ...}``
or ``{ok: false, messages: [...]}`` matching the existing handshakes
blueprint convention.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services.crack import get_service as get_crack_service
from app.services.crack_targets import get_service as get_targets_service
from app.services.notifications import notifications

bp = Blueprint("crack", __name__, url_prefix="/crack")


# ---------- Targets CRUD ----------

@bp.route("/targets", methods=["GET"])
def list_targets():
    return jsonify({"targets": get_targets_service().list_targets()})


@bp.route("/targets", methods=["POST"])
def add_target():
    """Create a new crack target.

    Body::

        {
          "name":          "Mac Studio",
          "host":          "mac.local",
          "user":          "nishit",
          "port":          22,                  (optional, default 22)
          "wordlist_path": "/usr/share/wordlists/rockyou.txt"
        }
    """
    data = request.get_json(silent=True) or {}
    ok, msg, record = get_targets_service().add_target(
        name=data.get("name", ""),
        host=data.get("host", ""),
        user=data.get("user", ""),
        wordlist_path=data.get("wordlist_path", ""),
        port=data.get("port", 22),
    )
    notif = notifications.success if ok else notifications.warning
    notif(f"crack target add: {msg}", source="crack")
    if not ok:
        return jsonify({"ok": False, "msg": msg}), 400
    return jsonify({"ok": True, "msg": msg, "target": record})


@bp.route("/targets/<target_id>", methods=["DELETE"])
def remove_target(target_id: str):
    ok, msg = get_targets_service().remove_target(target_id)
    notif = notifications.success if ok else notifications.warning
    notif(f"crack target delete: {msg}", source="crack")
    if not ok:
        return jsonify({"ok": False, "msg": msg}), 404
    return jsonify({"ok": True, "msg": msg})


@bp.route("/targets/<target_id>/test", methods=["POST"])
def test_target(target_id: str):
    """Run a remote sanity check: SSH reachable, hashcat installed,
    wordlist readable. Updates the target's last_test_* fields."""
    ok, msg = get_targets_service().test_target(target_id)
    notif = notifications.success if ok else notifications.warning
    notif(f"crack target test: {msg}", source="crack")
    return jsonify({"ok": ok, "msg": msg})


@bp.route("/public-key", methods=["GET"])
def public_key():
    """Return the platform's SSH public key + fingerprint for the
    Settings UI. Generates the key on first call."""
    return jsonify(get_targets_service().get_public_key())


# ---------- Crack jobs ----------

@bp.route("/start", methods=["POST"])
def start_crack():
    """Launch a crack job.

    Body::

        {
          "capture_id": "<id from /handshakes/list>",
          "target_id":  "<id from /crack/targets>"
        }
    """
    data = request.get_json(silent=True) or {}
    capture_id = (data.get("capture_id") or "").strip()
    target_id = (data.get("target_id") or "").strip()
    if not capture_id:
        return jsonify({"ok": False, "messages": ["capture_id required"]}), 400
    if not target_id:
        return jsonify({"ok": False, "messages": ["target_id required"]}), 400

    ok, messages, job = get_crack_service().start_crack(capture_id, target_id)
    summary = "; ".join(messages)
    notif = notifications.success if ok else notifications.error
    notif(f"crack start: {summary}", source="crack")
    status_code = 200 if ok else 400
    return jsonify({"ok": ok, "messages": messages, "job": job}), status_code


@bp.route("/<job_id>/stop", methods=["POST"])
def stop_crack(job_id: str):
    ok, msg = get_crack_service().stop_crack(job_id)
    notif = notifications.success if ok else notifications.warning
    notif(f"crack stop {job_id[:8]}: {msg}", source="crack")
    return jsonify({"ok": ok, "msg": msg})


@bp.route("/jobs", methods=["GET"])
def list_jobs():
    """All crack jobs (active + history), newest first. Optional
    ``?capture_id=`` filters to one capture."""
    cid = request.args.get("capture_id")
    jobs = get_crack_service().list_jobs()
    if cid:
        jobs = [j for j in jobs if j.get("capture_id") == cid]
    return jsonify({"jobs": jobs})


@bp.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id: str):
    from flask import abort
    job = get_crack_service().get_job(job_id)
    if job is None:
        abort(404, description=f"no crack job {job_id}")
    return jsonify(job)
