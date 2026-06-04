"""PiPineapple Flask application package.

The application is constructed by the ``create_app`` factory below. Code
that needs the Flask app — including ``run.py`` and tests — must call
``create_app()`` rather than importing a module-level singleton, so that
multiple independently configured instances can coexist.
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask

from app.config import BaseConfig, resolve_config


def create_app(config: type[BaseConfig] | str | None = None) -> Flask:
    """Construct and configure a PiPineapple Flask app.

    Parameters
    ----------
    config:
        Either a config class (``DevConfig``, ``TestConfig``, …), a config
        alias string (``"dev"``, ``"mac"``, ``"test"``, ``"prod"``), or
        ``None`` to read the alias from the ``PIPINEAPPLE_CONFIG`` env
        var (defaulting to ``"dev"``).
    """
    app = Flask(__name__, instance_relative_config=False)

    # Resolve the config class. Strings and None go through resolve_config;
    # an actual class is used directly.
    if isinstance(config, type) and issubclass(config, BaseConfig):
        config_class = config
    else:
        config_class = resolve_config(config)
    app.config.from_object(config_class)

    # Production-only: blow up loudly if SECRET_KEY wasn't overridden.
    if hasattr(config_class, "validate"):
        config_class.validate()

    # Ensure the data directory exists. The path may have been overridden
    # via PIPINEAPPLE_DATA_DIR, so we resolve it from app.config rather
    # than from the class attribute.
    data_dir: Path = app.config["DATA_DIR"]
    data_dir.mkdir(parents=True, exist_ok=True)

    # Mirror USE_REAL_TOOLS into the environment so the tool wrappers in
    # ``app.tools`` (which deliberately don't import Flask) can read it
    # via a single env var. The factory is the single point of truth for
    # this flag's actual value.
    import os
    os.environ["PIPINEAPPLE_USE_REAL_TOOLS"] = (
        "1" if app.config["USE_REAL_TOOLS"] else "0"
    )

    _configure_logging(app)
    _register_blueprints(app)

    app.logger.info(
        "PiPineapple started with %s (real tools: %s, data dir: %s)",
        config_class.__name__,
        app.config["USE_REAL_TOOLS"],
        data_dir,
    )
    return app


def _configure_logging(app: Flask) -> None:
    """Set up a sensible log format for the dev server.

    Production deployments will replace this when running under gunicorn
    or behind nginx; for now we just want timestamped console output.
    """
    if app.logger.handlers:
        # Flask in debug mode already configured a handler; leave it.
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.DEBUG if app.config["DEBUG"] else logging.INFO)


def _register_blueprints(app: Flask) -> None:
    """Register all Flask blueprints with the app.

    Each phase of the curriculum adds blueprints here. Keep the imports
    local to this function so importing ``app`` doesn't pull in every
    blueprint's transitive dependencies at module load time.
    """
    from app.routes.dashboard import bp as dashboard_bp
    from app.routes.learning import bp as learning_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(learning_bp)
