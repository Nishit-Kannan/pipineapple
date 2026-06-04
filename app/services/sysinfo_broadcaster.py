"""Background broadcaster that emits sysinfo updates over SocketIO.

Runs in a single daemon thread inside the Flask process. Every
``interval`` seconds it pulls a fresh status dict from ``sysinfo`` and
emits it as a ``sysinfo`` event on the default namespace. Browser
clients subscribed to this event update their dashboard cards in place.

In production (Session 19 — gunicorn + nginx) we'd run this in a
separate worker to avoid every gunicorn worker process running its own
broadcaster. For dev / single-process runs the daemon thread is plenty.
"""

from __future__ import annotations

import logging
import threading
import time

from app.services import sysinfo

log = logging.getLogger(__name__)


_running: bool = False
_thread: threading.Thread | None = None


def start(socketio, interval: float = 2.0) -> None:
    """Start the broadcaster thread. Idempotent — safe to call twice."""
    global _running, _thread
    if _running:
        log.debug("sysinfo broadcaster already running, skipping start")
        return
    _running = True

    def _loop() -> None:
        log.info("sysinfo broadcaster started (interval=%.1fs)", interval)
        # Slight initial delay so the Flask app finishes initialising
        # before the first emit hits the wire.
        time.sleep(0.25)
        while _running:
            try:
                status = sysinfo.get_system_status()
                socketio.emit("sysinfo", status, namespace="/")
            except Exception:
                log.exception("sysinfo broadcaster emit failed")
            time.sleep(interval)
        log.info("sysinfo broadcaster stopped")

    _thread = threading.Thread(
        target=_loop, daemon=True, name="sysinfo-broadcaster"
    )
    _thread.start()


def stop() -> None:
    """Signal the broadcaster thread to exit. Safe to call when not running."""
    global _running
    _running = False
