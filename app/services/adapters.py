"""Adapter management service.

Composes the Settings → Adapter Management view by joining iw + ethtool
+ persistent role assignments, and exposes the orchestrated operations
the UI buttons trigger: change adapter mode (down → set type → up),
apply udev rules from current assignments, write NetworkManager
unmanaging config, kill the managers if needed.

Role assignments live in JSON at $DATA_DIR/adapter_roles.json, keyed
by MAC (lowercased):

    {
        "00:c0:ca:11:22:33": "wlan-mon-2g",
        ...
    }

Three canonical role names: ``wlan-mon-2g``, ``wlan-mon-5g``, ``wlan-ap``.
Plus the sentinel ``"none"`` for "no role assigned".
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Lock
from typing import Any

from app.tools import ethtool, iproute, iw, nm, udev

log = logging.getLogger(__name__)

CANONICAL_ROLES = ("wlan-mgmt-ap", "wlan-mon-2g", "wlan-mon-5g", "wlan-ap")


class AdapterService:
    def __init__(self, data_dir: Path) -> None:
        self._roles_path = data_dir / "adapter_roles.json"
        self._lock = Lock()

    # ---------- Role assignments (persisted to JSON) ----------
    def _load_roles(self) -> dict[str, str]:
        try:
            with self._roles_path.open() as f:
                return {k.lower(): v for k, v in json.load(f).items()}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_roles(self, roles: dict[str, str]) -> None:
        self._roles_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._roles_path.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(roles, f, indent=2, sort_keys=True)
        tmp.replace(self._roles_path)

    def get_roles(self) -> dict[str, str]:
        with self._lock:
            return self._load_roles()

    def set_role(self, mac: str, role: str) -> tuple[bool, str]:
        if role != "none" and role not in CANONICAL_ROLES:
            return False, f"unknown role {role!r}"
        mac_lc = mac.strip().lower()
        with self._lock:
            roles = self._load_roles()
            if role == "none":
                roles.pop(mac_lc, None)
            else:
                # Enforce uniqueness — a role can only be assigned to one MAC
                for existing_mac, existing_role in list(roles.items()):
                    if existing_role == role and existing_mac != mac_lc:
                        roles.pop(existing_mac)
                roles[mac_lc] = role
            self._save_roles(roles)
        return True, f"assigned {role} to {mac_lc}"

    # ---------- Adapter listing ----------
    def list_adapters(self) -> list[dict[str, Any]]:
        """Return one dict per wireless interface, joined with role + driver."""
        devices = iw.list_wireless_devices()
        roles = self.get_roles()
        # Pull MAC addresses from `ip` since iw doesn't include them
        all_interfaces = {i["name"]: i for i in iproute.list_interfaces()}

        out: list[dict[str, Any]] = []
        for dev in devices:
            name = dev["name"]
            iface = all_interfaces.get(name, {})
            mac = (iface.get("mac") or "").lower()
            out.append({
                "name":          name,
                "mac":           mac,
                "driver":        ethtool.get_driver(name),
                "mode":          dev["mode"],
                "channel":       dev["channel"],
                "frequency_mhz": dev["frequency_mhz"],
                "ssid":          dev["ssid"],
                "txpower_dbm":   dev["txpower_dbm"],
                "state":         iface.get("state", "UNKNOWN"),
                "role":          roles.get(mac, "none"),
                "is_offensive":  roles.get(mac, "none") in CANONICAL_ROLES,
            })
        return out

    # ---------- Mode toggle: down → set type → up ----------
    def set_mode(self, iface: str, mode: str) -> tuple[bool, list[str]]:
        """Run the three-command sequence to change an adapter's mode.

        Returns (overall_ok, list of step messages). Each subprocess
        call surfaces in the Command Stream via the standard run()
        listener.
        """
        if mode not in ("monitor", "managed"):
            return False, [f"unknown mode {mode!r}"]
        messages: list[str] = []

        ok, msg = iproute.set_link_state(iface, "down")
        messages.append(msg)
        if not ok:
            return False, messages

        ok, msg = iw.set_type(iface, mode)
        messages.append(msg)
        if not ok:
            # Try to bring the interface back up even if type-change failed
            iproute.set_link_state(iface, "up")
            return False, messages

        ok, msg = iproute.set_link_state(iface, "up")
        messages.append(msg)
        return ok, messages

    def set_down(self, iface: str) -> tuple[bool, str]:
        return iproute.set_link_state(iface, "down")

    # ---------- Apply udev rules from current role assignments ----------
    def apply_udev_rules(self) -> tuple[bool, str]:
        roles = self.get_roles()
        if not roles:
            return False, "no role assignments to write — assign at least one first"
        ok, write_msg = udev.write_rules(roles)
        if not ok:
            return False, write_msg
        ok, reload_msg = udev.reload_rules()
        return ok, f"{write_msg}; {reload_msg}"

    # ---------- Apply NetworkManager unmanaging config ----------
    def apply_nm_unmanaging(self) -> tuple[bool, str]:
        ok, write_msg = nm.write_conf()
        if not ok:
            return False, write_msg
        ok, reload_msg = nm.reload()
        return ok, f"{write_msg}; {reload_msg}"

    def stop_managers(self) -> tuple[bool, str]:
        return nm.stop_managers()


def get_service() -> AdapterService:
    """Lazy accessor — the data dir is only known after the factory runs."""
    from flask import current_app
    data_dir = current_app.config["DATA_DIR"]
    return AdapterService(data_dir)
