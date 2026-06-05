"""PiPineapple Flask application package.

The application is constructed by the ``create_app`` factory below.
Session 02 added a Flask-SocketIO instance for realtime UI updates; the
``socketio`` object is a module-level singleton imported and used by
``run.py`` (via ``socketio.run(app, ...)``) and by every service that
needs to emit events.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Flask
from flask_socketio import SocketIO

from app.config import BaseConfig, resolve_config

# Module-level SocketIO singleton. ``async_mode="threading"`` uses
# Flask-SocketIO's built-in threading backend — no eventlet/gevent
# required for our single-user lab deployment.
#
# ``allow_upgrades=False`` forces clients to stay on long-polling
# instead of trying to upgrade to WebSocket. Werkzeug's WSGI server +
# simple-websocket has an interop bug under sudo that fires a noisy
# (but cosmetic) 500 on every upgrade attempt — the client gracefully
# falls back to polling and everything works, but the log gets spammed.
# Polling is fine for our 2-second cadence; revisit when we move to
# gunicorn+nginx (Session 19) where WebSocket will work cleanly.
socketio = SocketIO(
    cors_allowed_origins="*",
    async_mode="threading",
    allow_upgrades=False,
    logger=False,
    engineio_logger=False,
)


def create_app(config: type[BaseConfig] | str | None = None) -> Flask:
    """Construct and configure a PiPineapple Flask app."""
    app = Flask(__name__, instance_relative_config=False)

    if isinstance(config, type) and issubclass(config, BaseConfig):
        config_class = config
    else:
        config_class = resolve_config(config)
    app.config.from_object(config_class)

    if hasattr(config_class, "validate"):
        config_class.validate()

    data_dir: Path = app.config["DATA_DIR"]
    data_dir.mkdir(parents=True, exist_ok=True)

    os.environ["PIPINEAPPLE_USE_REAL_TOOLS"] = (
        "1" if app.config["USE_REAL_TOOLS"] else "0"
    )

    _configure_logging(app)
    socketio.init_app(app)
    _attach_services(app)
    _register_blueprints(app)
    _install_auth_middleware(app)
    _start_background_tasks(app)

    app.logger.info(
        "PiPineapple started with %s (real tools: %s, data dir: %s, async_mode: %s)",
        config_class.__name__,
        app.config["USE_REAL_TOOLS"],
        data_dir,
        socketio.async_mode,
    )
    return app


def _configure_logging(app: Flask) -> None:
    if app.logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.DEBUG if app.config["DEBUG"] else logging.INFO)


def _attach_services(app: Flask | None = None) -> None:
    """Wire the singleton services to the SocketIO instance.

    Also attaches the access_control service to the data dir so the
    deny-list survives across requests.
    """
    from app.services.access_control import access_control
    from app.services.job_manager import job_manager
    from app.services.notifications import notifications
    from app.services.terminal import terminal
    from app.tools._common import register_command_listener

    notifications.attach_socketio(socketio)
    job_manager.attach_socketio(socketio)
    terminal.attach_socketio(socketio)
    if app is not None:
        access_control.attach(app.config["DATA_DIR"])

    # Hook the terminal service into every non-polling subprocess.run.
    # The factory is the single place that bridges tools → services for
    # the command stream, preserving the dependency direction (tools
    # don't import services directly).
    def _on_command(cmd, source, rc, duration_ms):
        terminal.broadcast(cmd, source=source, rc=rc, duration_ms=duration_ms)
    register_command_listener(_on_command)

    # When a fresh client connects, send it the recent command history
    # so the drawer isn't empty until the next action.
    @socketio.on("terminal:request_history")
    def _send_history():
        from flask_socketio import emit
        emit("terminal:history", terminal.list())


def _register_blueprints(app: Flask) -> None:
    from app.routes.auth import bp as auth_bp
    from app.routes.dashboard import bp as dashboard_bp
    from app.routes.learning import bp as learning_bp
    from app.routes.recon import bp as recon_bp
    from app.routes.settings import bp as settings_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(learning_bp)
    app.register_blueprint(recon_bp)
    app.register_blueprint(settings_bp)

    # Debug routes only in dev/mac configs
    if app.config.get("DEBUG", False):
        from app.routes.debug import bp as debug_bp
        app.register_blueprint(debug_bp)
        app.logger.info("debug routes enabled at /debug/*")


def _install_auth_middleware(app: Flask) -> None:
    """Install before_request hooks for auth + access control.

    Two filters run on every request, in order:

    1. **Access control deny-list** — if the source IP matches any
       configured deny CIDR (e.g. the rogue AP subnet), 403 outright.
       Localhost is always allowed.
    2. **Authentication** — if the platform isn't initialised, redirect
       to /setup. If it is but the session isn't logged in, redirect to
       /login. Whitelisted paths (auth endpoints, static, the bare
       favicon) bypass.
    """
    from flask import redirect, request, session, url_for

    # Routes that don't require an authenticated session
    AUTH_EXEMPT_ENDPOINTS = {
        "auth.setup", "auth.login", "auth.logout",
        "static",
    }

    @app.before_request
    def _filter_request():
        # ---------- Access-control deny-list ----------
        from app.services.access_control import access_control
        remote = request.remote_addr or ""
        if access_control.is_denied(remote):
            return ("Forbidden", 403)

        # ---------- Auth ----------
        # Socket.IO polling/upgrade requests go through Flask too. They
        # carry the session cookie but the HTTP-level redirect-to-/login
        # response we'd emit for unauthenticated users breaks the
        # socket.io client (it doesn't follow 302s). Auth for the
        # SocketIO layer is enforced in the connect handler below,
        # which rejects unauthenticated sessions cleanly.
        if request.path.startswith("/socket.io/"):
            return None

        endpoint = request.endpoint or ""
        if endpoint in AUTH_EXEMPT_ENDPOINTS:
            return None

        from app.services.auth import get_service
        svc = get_service()
        if not svc.is_configured():
            return redirect(url_for("auth.setup"))
        if not svc.is_logged_in(session):
            return redirect(url_for("auth.login"))
        return None

    # SocketIO connect handler — rejects unauthenticated sessions so the
    # live broadcasts don't leak to anonymous clients.
    @socketio.on("connect")
    def _socketio_auth_gate():
        from app.services.auth import get_service
        svc = get_service()
        if not svc.is_configured() or not svc.is_logged_in(session):
            app.logger.debug("socketio connect rejected: unauthenticated")
            return False  # reject the socket connection
        return None


def _start_background_tasks(app: Flask) -> None:
    """Start daemon background workers (sysinfo broadcaster, etc.).

    Guard: skip in werkzeug's reloader PARENT process to avoid double-
    starting. The guard only fires when the reloader is actually enabled
    via ``PIPINEAPPLE_RELOADER=1`` (default off — see run.py). With the
    reloader off, the main process IS the serving process and we always
    run tasks here.
    """
    reloader_enabled = (
        os.environ.get("PIPINEAPPLE_RELOADER", "0").lower()
        in ("1", "true", "yes")
    )
    if reloader_enabled and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        # We're the reloader parent — child will run the tasks.
        return

    from app.services import sysinfo_broadcaster
    sysinfo_broadcaster.start(socketio, interval=2.0)

    # Restore networking state (management AP if previously enabled,
    # or bootstrap AP if first boot) in a background thread so a slow
    # restore doesn't block app startup.
    import threading
    def _restore():
        try:
            from app.services.networking import NetworkingService
            svc = NetworkingService(app.config["DATA_DIR"])
            auth_path = app.config["DATA_DIR"] / "auth.json"
            svc.restore_on_startup(auth_path=auth_path)
        except Exception:
            app.logger.exception("networking restore failed")
    threading.Thread(target=_restore, daemon=True, name="networking-restore").start()
