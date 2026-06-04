"""Notifications service.

A small in-memory pub/sub for surfacing things to the user. Five severity
levels matching the real Pineapple's set: info, warning, error, success,
unknown. Each notification gets a uuid, timestamp, severity, message, an
optional source label (which subsystem emitted it), and a read flag.

The bell-icon dot indicator in the title bar lights up when there are
unread notifications of severity *warning, error, or success* — info is
shown in the drawer but doesn't make a fuss, otherwise routine "scan
started" events would create constant indicator noise.

Singleton via module-level ``notifications``. The factory calls
``attach_socketio()`` after Flask-SocketIO is initialised so live emits
work; before that, ``add()`` just buffers.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from threading import Lock
from typing import Any, Literal

log = logging.getLogger(__name__)

Severity = Literal["info", "warning", "error", "success", "unknown"]

# Severities that light up the bell-dot indicator when unread.
LOUD_SEVERITIES: set[Severity] = {"warning", "error", "success"}


class NotificationsService:
    def __init__(self, max_entries: int = 50) -> None:
        self._buf: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._lock = Lock()
        self._socketio = None

    def attach_socketio(self, socketio) -> None:
        """Wire the service to a SocketIO instance for live emit."""
        self._socketio = socketio

    def add(
        self,
        severity: Severity,
        message: str,
        source: str = "system",
    ) -> dict[str, Any]:
        """Add a notification and emit it over SocketIO if attached."""
        entry: dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "severity": severity,
            "message": message,
            "source": source,
            "ts": time.time(),
            "read": False,
        }
        with self._lock:
            self._buf.appendleft(entry)
        if self._socketio is not None:
            try:
                self._socketio.emit("notification", entry, namespace="/")
            except Exception:
                log.exception("notification emit failed")
        return entry

    # Convenience wrappers
    def info(self, msg: str, source: str = "system") -> dict[str, Any]:
        return self.add("info", msg, source)

    def warning(self, msg: str, source: str = "system") -> dict[str, Any]:
        return self.add("warning", msg, source)

    def error(self, msg: str, source: str = "system") -> dict[str, Any]:
        return self.add("error", msg, source)

    def success(self, msg: str, source: str = "system") -> dict[str, Any]:
        return self.add("success", msg, source)

    def unknown(self, msg: str, source: str = "system") -> dict[str, Any]:
        return self.add("unknown", msg, source)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._buf)

    def unread_count(self, loud_only: bool = True) -> int:
        with self._lock:
            return sum(
                1 for n in self._buf
                if not n["read"] and (
                    not loud_only or n["severity"] in LOUD_SEVERITIES
                )
            )

    def mark_all_read(self) -> None:
        with self._lock:
            for n in self._buf:
                n["read"] = True
        if self._socketio is not None:
            self._socketio.emit("notification:read_all", {}, namespace="/")

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()
        if self._socketio is not None:
            self._socketio.emit("notification:clear", {}, namespace="/")


# Module-level singleton
notifications = NotificationsService()
