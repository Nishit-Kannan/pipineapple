"""Modules blueprint — the plugin manager UI (Session 15, Phase F).

Lists the available modules (discovered under ``app/modules/``) with their
installed state, and lets the operator install/uninstall them. Install model
is **restart-on-change**: install/uninstall only updates the registry; the
blueprint is (un)loaded on the next ``pipineapple`` restart — so every action
here reminds the operator to restart.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from app.services.modules import get_loader
from app.services.notifications import notifications

bp = Blueprint("modules", __name__, url_prefix="/modules")


@bp.route("/")
def index():
    return render_template("modules.html", modules=get_loader().list_modules())


@bp.route("/list")
def list_json():
    return jsonify({"modules": get_loader().list_modules()})


@bp.route("/<name>/install", methods=["POST"])
def install(name: str):
    ok, msg = get_loader().install(name)
    (notifications.success if ok else notifications.warning)(
        f"module install: {msg}", source="modules")
    return jsonify({"ok": ok, "msg": msg, "modules": get_loader().list_modules()}), \
        (200 if ok else 400)


@bp.route("/<name>/uninstall", methods=["POST"])
def uninstall(name: str):
    ok, msg = get_loader().uninstall(name)
    (notifications.info if ok else notifications.warning)(
        f"module uninstall: {msg}", source="modules")
    return jsonify({"ok": ok, "msg": msg, "modules": get_loader().list_modules()}), \
        (200 if ok else 400)


@bp.route("/<name>/install-deps", methods=["POST"])
def install_deps(name: str):
    """apt-install the module's declared system dependencies. May take a
    while (apt-get update + install). Only the packages from the module's
    own manifest are installed."""
    ok, msg = get_loader().install_requirements(name)
    (notifications.success if ok else notifications.error)(
        f"module deps: {msg}", source="modules")
    return jsonify({"ok": ok, "msg": msg, "modules": get_loader().list_modules()}), \
        (200 if ok else 400)
