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
# required for our single-user lab deployment. See Session 02 notes for
# the trade-off vs eventlet.
socketio = SocketIO(
    cors_allowed_origins="*",
    async_mode="threading",
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
    _attach_services()
    _register_blueprints(app)
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


def _attach_services() -> None:
    """Wire the singleton services to the SocketIO instance."""
    from app.services.job_manager import job_manager
    from app.services.notifications import notifications
    from app.services.terminal import terminal
    from app.tools._common import register_command_listener

    notifications.attach_socketio(socketio)
    job_manager.attach_socketio(socketio)
    terminal.attach_socketio(socketio)

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
    from app.routes.dashboard import bp as dashboard_bp
    from app.routes.learning import bp as learning_bp
    from app.routes.settings import bp as settings_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(learning_bp)
    app.register_blueprint(settings_bp)

    # Debug routes only in dev/mac configs
    if app.config.get("DEBUG", False):
        from app.routes.debug import bp as debug_bp
        app.register_blueprint(debug_bp)
        app.logger.info("debug routes enabled at /debug/*")


def _start_background_tasks(app: Flask) -> None:
    """Start daemon background workers (sysinfo broadcaster, etc.)."""
    # Skip background tasks during Flask's reloader parent process.
    # WERKZEUG_RUN_MAIN is set to "true" only in the child process that
    # actually serves requests, so this prevents us starting two
    # broadcaster threads when debug mode forks.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    from app.services import sysinfo_broadcaster

    sysinfo_broadcaster.start(socketio, interval=2.0)
