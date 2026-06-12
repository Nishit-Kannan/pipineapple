"""Nmap module blueprint."""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from app.services.notifications import notifications
from . import tools
from .service import get_service

bp = Blueprint(
    "mod_nmap", __name__,
    template_folder="templates",
    url_prefix="/modules/nmap",
)

DEFAULT_LAB_CIDR = "10.0.0.0/24"


def _lab_cidr() -> str:
    """The rogue-AP subnet to offer as the 'lab subnet' target. Derived
    from pineap state when available, else a sensible default."""
    try:
        from app.services.pineap import get_service as get_pineap
        st = get_pineap().get_state()
        subnet = st.get("subnet")
        if subnet:
            return subnet
    except Exception:
        pass
    return DEFAULT_LAB_CIDR


@bp.route("/")
def index():
    avail_ok, avail_detail = tools.is_available()
    return render_template(
        "nmap.html",
        profiles=[{"id": k, "label": v["label"]} for k, v in tools.PROFILES.items()],
        lab_cidr=_lab_cidr(),
        uplink_cidr=get_service().uplink_cidr(),
        nmap_ok=avail_ok,
        nmap_detail=avail_detail,
    )


@bp.route("/scan", methods=["POST"])
def scan():
    data = request.get_json(silent=True) or {}
    profile = (data.get("profile") or tools.DEFAULT_PROFILE).strip()
    source = (data.get("source") or "subnet").strip()
    custom = data.get("custom")
    svc = get_service()
    target, tmsg = svc.resolve_target(source, custom, _lab_cidr())
    if not target:
        return jsonify({"ok": False, "msg": tmsg}), 400
    ok, msg = svc.start_scan(profile, target)
    (notifications.info if ok else notifications.warning)(
        f"nmap: {msg} ({tmsg})", source="nmap")
    return jsonify({"ok": ok, "msg": msg, "target_desc": tmsg,
                    "status": svc.get_status()}), (200 if ok else 400)


@bp.route("/status")
def status():
    return jsonify(get_service().get_status())


@bp.route("/results")
def results():
    return jsonify({"status": get_service().get_status(),
                    "hosts": get_service().get_results()})
