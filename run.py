"""Development entrypoint for PiPineapple.

Session 02 onwards: launches the app via Flask-SocketIO's ``socketio.run()``
so WebSocket clients can connect. Same host/port as before.

Usage on the Pi (inside an activated venv)::

    python run.py

For Mac-side UI iteration with stubbed tools::

    PIPINEAPPLE_CONFIG=mac python run.py
"""

from __future__ import annotations

from app import create_app, socketio

app = create_app()


if __name__ == "__main__":
    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=bool(app.config.get("DEBUG", False)),
        # SocketIO needs allow_unsafe_werkzeug=True when using debug=True
        # in newer Werkzeug versions because the dev server is technically
        # not allowed in production but Flask-SocketIO refuses to run it
        # without an explicit opt-in. Fine for our dev/lab use.
        allow_unsafe_werkzeug=True,
    )
