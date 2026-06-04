"""Development entrypoint for PiPineapple.

Session 02 onwards: launches the app via Flask-SocketIO's ``socketio.run()``
so WebSocket clients can connect. Same host/port as before.

Usage on the Pi (inside an activated venv)::

    python run.py

For Mac-side UI iteration with stubbed tools::

    PIPINEAPPLE_CONFIG=mac python run.py
"""

from __future__ import annotations

import os

# Werkzeug's debug-mode reloader re-execs sys.argv on file change. Under
# sudo (run-as-root.sh wraps us in `sudo -E python run.py`), that re-exec
# creates a nested-sudo process tree which corrupts the WSGI environ
# simple-websocket needs for the WebSocket upgrade handshake. Result:
# 500s on /socket.io/?...transport=websocket and the live indicator
# stays grey. Disabling the reloader fixes it.
#
# Trade-off: code edits no longer auto-reload. Restart manually. Set
# PIPINEAPPLE_RELOADER=1 to opt back in (useful when running as a
# non-root user during pure UI work).
use_reloader = (
    os.environ.get("PIPINEAPPLE_RELOADER", "0").lower() in ("1", "true", "yes")
)

from app import create_app, socketio  # noqa: E402

app = create_app()


if __name__ == "__main__":
    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=bool(app.config.get("DEBUG", False)),
        use_reloader=use_reloader,
        allow_unsafe_werkzeug=True,
    )
