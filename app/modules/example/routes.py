"""Example module blueprint.

Note the ``template_folder="templates"`` — that makes ``render_template``
resolve names against *this module's* templates/ dir first, so a module
ships its own pages without touching app/templates/. ``url_prefix`` is set
on the blueprint here; the loader respects it.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template

bp = Blueprint(
    "mod_example",
    __name__,
    template_folder="templates",
    url_prefix="/modules/example",
)


@bp.route("/")
def index():
    return render_template("example.html")


@bp.route("/ping")
def ping():
    """Tiny JSON endpoint so the page can prove the blueprint is live."""
    return jsonify({"ok": True, "module": "example", "msg": "pong"})
