"""Read-only command stream — the 'show the work' service.

Every subprocess the platform executes via ``app.tools._common.run()``
(plus every JobManager-started long-running process) is published to the
terminal stream. The drawer in the UI subscribes to ``terminal:cmd``
events and renders them in newest-at-bottom order, so the operator can
see exactly which shell commands the platform is running.

Polling commands (the sysinfo broadcaster's periodic reads) are NOT
broadcast — they'd swamp the stream with routine /proc and iw/ip
inspection. Polling is signalled via a ``contextvars.ContextVar`` set
by ``sysinfo.get_system_status()``; ``run()`` checks it and skips the
broadcast when active.
"""

from __future__ import annotations

import logging
import shlex
import time
import uuid
from collections import deque
from threading import Lock
from typing import Any

log = logging.getLogger(__name__)


class TerminalService:
    """In-memory ring buffer + SocketIO emitter for executed commands."""

    def __init__(self, max_entries: int = 200) -> None:
        self._buf: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._lock = Lock()
        self._socketio = None

    def attach_socketio(self, socketio) -> None:
        self._socketio = socketio

    def broadcast(
        self,
        cmd: list[str] | str,
        *,
        source: str = "tool",
        rc: int | None = None,
        duration_ms: float | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Add a command to the stream and emit it over SocketIO.

        ``source`` is a short tag for which subsystem ran the command
        ('tool' for ad-hoc subprocess.run, 'job' for JobManager-started
        long-running processes, or a custom value if a service wants to
        label its own operations).
        """
        if isinstance(cmd, str):
            cmd_list = shlex.split(cmd)
        else:
            cmd_list = list(cmd)

        cmd_str = " ".join(shlex.quote(p) for p in cmd_list)

        entry: dict[str, Any] = {
            "id":          uuid.uuid4().hex[:12],
            "cmd":         cmd_list,
            "cmd_str":     cmd_str,
            "source":      source,
            "ts":          time.time(),
            "rc":          rc,
            "duration_ms": duration_ms,
            "note":        note,
        }
        with self._lock:
            self._buf.appendleft(entry)
        if self._socketio is not None:
            try:
                self._socketio.emit("terminal:cmd", entry, namespace="/")
            except Exception:
                log.exception("terminal broadcast emit failed")
        return entry

    def list(self) -> list[dict[str, Any]]:
        """Most recent first. Used by the JS to seed the drawer on open."""
        with self._lock:
            return list(self._buf)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()
        if self._socketio is not None:
            try:
                self._socketio.emit("terminal:clear", {}, namespace="/")
            except Exception:
                log.exception("terminal clear emit failed")


# Module-level singleton
terminal = TerminalService()
