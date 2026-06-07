"""Configuration classes for the PiPineapple Flask app.

Pick a config by setting the ``PIPINEAPPLE_CONFIG`` environment variable to
the dotted path of one of the classes below, e.g.::

    export PIPINEAPPLE_CONFIG=app.config.DevConfig

The factory in ``app/__init__.py`` loads the named class via
``app.config.from_object()``.
"""

from __future__ import annotations

import os
from pathlib import Path


class BaseConfig:
    """Defaults shared by every environment."""

    SECRET_KEY = os.environ.get("PIPINEAPPLE_SECRET_KEY", "dev-secret-change-me")

    # Where capture files, configs, and other runtime artifacts get written.
    # Override per environment.
    DATA_DIR: Path = Path(os.environ.get("PIPINEAPPLE_DATA_DIR", "/tmp/pipineapple"))

    # If True, services call real subprocesses on the Pi.
    # If False, services use stub data — useful when developing on the Mac
    # without a wireless adapter handy.
    USE_REAL_TOOLS: bool = True

    # Browser-cache lifetime for /static/* files. Werkzeug's default
    # sends Cache-Control: no-cache, which makes the browser do an
    # ETag round trip on every request. Fine over Ethernet (304s are
    # tiny + fast). Painful over the slow Realtek mgmt AP where every
    # round trip adds visible latency. 300 s (5 min) is short enough
    # that a deploy of static assets is noticed quickly, long enough
    # that navigating between pages within one session skips the
    # round trip entirely. Hard-refresh (Cmd-Shift-R / Ctrl-Shift-R)
    # always bypasses this — that's by design in every browser.
    SEND_FILE_MAX_AGE_DEFAULT = 300


class DevConfig(BaseConfig):
    """Local development / on-Pi runtime."""

    DEBUG = True
    TESTING = False
    USE_REAL_TOOLS = True


class MacDevConfig(BaseConfig):
    """Develop UI changes on the Mac with stubbed tool output.

    The sysinfo, airodump, etc. wrappers return canned data when
    USE_REAL_TOOLS is False, so the UI can be exercised without monitor
    mode or wireless adapters.
    """

    DEBUG = True
    TESTING = False
    USE_REAL_TOOLS = False


class TestConfig(BaseConfig):
    """pytest fixture config."""

    DEBUG = False
    TESTING = True
    USE_REAL_TOOLS = False
    DATA_DIR = Path("/tmp/pipineapple-test")


class ProdConfig(BaseConfig):
    """Production runtime on the Pi (behind nginx with TLS, in Phase G)."""

    DEBUG = False
    TESTING = False
    USE_REAL_TOOLS = True

    # SECRET_KEY must come from the environment in prod, never default.
    @classmethod
    def validate(cls) -> None:
        if cls.SECRET_KEY == "dev-secret-change-me":
            raise RuntimeError(
                "PIPINEAPPLE_SECRET_KEY must be set in production"
            )


CONFIG_MAP = {
    "dev": DevConfig,
    "mac": MacDevConfig,
    "test": TestConfig,
    "prod": ProdConfig,
}


def resolve_config(name: str | None = None) -> type[BaseConfig]:
    """Resolve a config alias or dotted path to a class.

    Order: explicit ``name`` argument, ``PIPINEAPPLE_CONFIG`` env var,
    default ``DevConfig``.
    """
    chosen = name or os.environ.get("PIPINEAPPLE_CONFIG", "dev")
    if chosen in CONFIG_MAP:
        return CONFIG_MAP[chosen]
    # Allow dotted-path overrides (e.g. for custom subclasses)
    if "." in chosen:
        module_name, class_name = chosen.rsplit(".", 1)
        module = __import__(module_name, fromlist=[class_name])
        return getattr(module, class_name)
    raise ValueError(f"Unknown config alias: {chosen!r}")
