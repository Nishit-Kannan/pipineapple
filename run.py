"""Development entrypoint for PiPineapple.

Usage on the Pi (inside an activated venv)::

    python run.py

This binds to 0.0.0.0:5000 so you can browse from the Mac. The Flask dev
server is fine for the Pi during development; production deployment uses
a WSGI server behind nginx (Session 17).

Config is read from the ``PIPINEAPPLE_CONFIG`` env var (defaults to dev).
For Mac-side UI iteration with stubbed tools::

    PIPINEAPPLE_CONFIG=mac python run.py
"""

from __future__ import annotations

from app import create_app

app = create_app()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=bool(app.config.get("DEBUG", False)),
    )
