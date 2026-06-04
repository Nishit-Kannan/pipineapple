"""Management Access scoping — deny-list of source CIDRs.

A second line of defense alongside the auth login screen: once the
rogue AP comes alive (Phase D, Session 11+), victim clients on
``10.0.0.0/24`` (or whatever DHCP range we use) shouldn't even see the
management UI. Adding their subnet to the deny list returns 403 at the
WSGI layer before any auth check happens — they don't get to see the
login page, the brand, or anything else.

Localhost is always allowed regardless of the deny list, so the
operator on the Pi shell can always reach the local Flask via curl.

Storage: ``$DATA_DIR/access_control.json``::

    {
        "deny_cidrs": ["10.0.0.0/24"]
    }
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
from pathlib import Path
from threading import Lock

log = logging.getLogger(__name__)


class AccessControlService:
    """Module-level singleton that the before_request middleware queries.

    Constructed lazily on first attach so the DATA_DIR can come from
    Flask app config rather than being hard-coded.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._data_dir: Path | None = None
        self._deny_cidrs: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        self._mtime: float = 0.0

    def attach(self, data_dir: Path) -> None:
        with self._lock:
            self._data_dir = data_dir
            self._load()

    @property
    def _path(self) -> Path:
        if self._data_dir is None:
            raise RuntimeError("access_control not attached")
        return self._data_dir / "access_control.json"

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text())
            cidrs = []
            for raw in data.get("deny_cidrs", []):
                try:
                    cidrs.append(ipaddress.ip_network(raw, strict=False))
                except ValueError as e:
                    log.warning("invalid CIDR in deny list: %s (%s)", raw, e)
            self._deny_cidrs = cidrs
            self._mtime = self._path.stat().st_mtime
            log.info("access_control: loaded %d deny CIDRs", len(cidrs))
        except FileNotFoundError:
            self._deny_cidrs = []
            self._mtime = 0.0
        except (json.JSONDecodeError, OSError) as e:
            log.warning("access_control load failed: %s", e)
            self._deny_cidrs = []

    def _save(self) -> None:
        if self._data_dir is None:
            return
        self._data_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "deny_cidrs": [str(n) for n in self._deny_cidrs],
        }
        tmp = self._path.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(data, f, indent=2)
        tmp.replace(self._path)
        try:
            self._mtime = self._path.stat().st_mtime
        except OSError:
            pass

    def _refresh_if_changed(self) -> None:
        """Reload the deny list if the JSON file changed on disk.

        Lets the UI write the file via add/remove_cidr and have the
        middleware see the new state without a Flask restart.
        """
        if self._data_dir is None:
            return
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            if self._deny_cidrs:
                self._deny_cidrs = []
                self._mtime = 0.0
            return
        if mtime != self._mtime:
            self._load()

    # ---------- Public API ----------
    def is_denied(self, ip: str) -> bool:
        """Return True if the IP is in any deny-list CIDR.

        Localhost (127.0.0.0/8 and ::1) is always allowed.
        """
        if not ip:
            return False
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if addr.is_loopback:
            return False
        with self._lock:
            self._refresh_if_changed()
            for net in self._deny_cidrs:
                # Skip if address family doesn't match
                if isinstance(addr, ipaddress.IPv4Address) and not isinstance(net, ipaddress.IPv4Network):
                    continue
                if isinstance(addr, ipaddress.IPv6Address) and not isinstance(net, ipaddress.IPv6Network):
                    continue
                if addr in net:
                    return True
        return False

    def list_cidrs(self) -> list[str]:
        with self._lock:
            return [str(n) for n in self._deny_cidrs]

    def add_cidr(self, cidr: str) -> tuple[bool, str]:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError as e:
            return False, f"invalid CIDR {cidr!r}: {e}"
        with self._lock:
            if any(str(n) == str(net) for n in self._deny_cidrs):
                return False, f"already in deny list: {net}"
            self._deny_cidrs.append(net)
            self._save()
        return True, f"added {net}"

    def remove_cidr(self, cidr: str) -> tuple[bool, str]:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError as e:
            return False, f"invalid CIDR {cidr!r}: {e}"
        with self._lock:
            before = len(self._deny_cidrs)
            self._deny_cidrs = [n for n in self._deny_cidrs if str(n) != str(net)]
            if len(self._deny_cidrs) == before:
                return False, f"not in deny list: {net}"
            self._save()
        return True, f"removed {net}"


# Module-level singleton; factory attaches on app construction.
access_control = AccessControlService()
