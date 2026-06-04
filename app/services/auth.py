"""Authentication service.

Single-user, password-only auth. The password is stored as a werkzeug
scrypt hash in ``$DATA_DIR/auth.json``. File presence signals "platform
initialised"; absence triggers the first-run setup wizard.

Public API::

    auth.is_configured()       # True once the password file exists
    auth.set_password(pw)      # write/overwrite the hashed password
    auth.verify(pw)            # return True if pw matches the stored hash
    auth.is_logged_in()        # check the current Flask session
    auth.login(session_obj)    # mark session as authenticated
    auth.logout(session_obj)   # clear the session

The middleware in ``app/__init__.py`` calls ``is_logged_in()`` on every
request via ``before_request`` and redirects to /setup or /login as
appropriate.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from threading import Lock

from werkzeug.security import check_password_hash, generate_password_hash

log = logging.getLogger(__name__)


class AuthService:
    def __init__(self, data_dir: Path) -> None:
        self._auth_path = data_dir / "auth.json"
        self._lock = Lock()

    # ---------- Password store ----------
    def is_configured(self) -> bool:
        return self._auth_path.is_file()

    def set_password(self, password: str) -> tuple[bool, str]:
        if not password or len(password) < 4:
            return False, "password must be at least 4 characters"
        with self._lock:
            self._auth_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "password_hash": generate_password_hash(password),
                "set_at": time.time(),
            }
            tmp = self._auth_path.with_suffix(".tmp")
            with tmp.open("w") as f:
                json.dump(data, f)
            tmp.replace(self._auth_path)
            # File should only be readable by the running user (root)
            self._auth_path.chmod(0o600)
        log.info("auth: password updated")
        return True, "password set"

    def verify(self, password: str) -> bool:
        with self._lock:
            try:
                data = json.loads(self._auth_path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                return False
        stored = data.get("password_hash", "")
        if not stored:
            return False
        return check_password_hash(stored, password or "")

    # ---------- Session helpers ----------
    @staticmethod
    def login(session_obj) -> None:
        session_obj["authenticated"] = True
        session_obj["login_at"] = time.time()
        session_obj.permanent = True

    @staticmethod
    def logout(session_obj) -> None:
        session_obj.pop("authenticated", None)
        session_obj.pop("login_at", None)

    @staticmethod
    def is_logged_in(session_obj) -> bool:
        return bool(session_obj.get("authenticated"))


def get_service() -> AuthService:
    """Resolve the auth service against the current app's DATA_DIR."""
    from flask import current_app
    return AuthService(current_app.config["DATA_DIR"])
