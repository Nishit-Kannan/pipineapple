"""Learning Centre blueprint — curriculum-as-feature.

Renders the topic-organised console commands documented in
``app.services.learning``. Each phase of the project adds content here
either as a new topic section or as new commands on existing sections.
"""

from __future__ import annotations

from flask import Blueprint, render_template

from app.services import learning as learning_service

bp = Blueprint("learning", __name__, url_prefix="/learning")


@bp.route("/")
def index():
    """Single-page Learning Centre with all topic sections."""
    return render_template(
        "learning.html",
        sections=learning_service.get_sections(),
    )
