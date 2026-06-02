"""Dashboard blueprint — the landing page and primary status panel.

This is the only blueprint registered in Session 01. Later phases add
blueprints in sibling modules (``recon.py``, ``capture.py``, etc.).
"""

from __future__ import annotations

from flask import Blueprint, render_template

from app.services import sysinfo

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def index():
    """Render the status panel.

    Service-layer call returns a dict that the template renders. The
    route stays thin — no data shaping here. Add a few helper filters
    to the template context so the template doesn't reach into Python
    builtins for formatting.
    """
    status = sysinfo.get_system_status()
    return render_template(
        "dashboard.html",
        status=status,
        format_uptime=sysinfo.format_uptime,
        format_bytes=sysinfo.format_bytes,
    )
