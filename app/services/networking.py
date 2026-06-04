"""Networking service — wlan0 mode orchestration.

Owns the wlan0 state machine: idle / client / management AP. All
mutations go through this service so the state stays coherent and the
mode switch is atomic enough.

State is persisted to ``$DATA_DIR/networking.json`` so the Flask
process restores the same mode after a restart::

    {
        "wlan0_mode":     "ap" | "client" | "idle",
        "mgmt_ap": {
            "ssid":       "PiPineapple-Mgmt",
            "password":   "<plaintext, written into hostapd.conf>",
            "channel":    6,
            "subnet":     "10.42.0.0/24",
            "gateway_ip": "10.42.0.1"
        }
    }

Wifi client profiles live in NetworkManager's own store (not in our
JSON) — saving here would just duplicate state.
"""

from __future__ import annotations

import ipaddress
import json
import logging
from pathlib import Path
from threading import Lock
from typing import Any

from app.services.job_manager import job_manager
from app.tools import dnsmasq, hostapd, iproute, nm

log = logging.getLogger(__name__)

DEFAULT_MGMT_AP = {
    "ssid":       "PiPineapple-Mgmt",
    "password":   "",
    "channel":    6,
    "subnet":     "10.42.0.0/24",
    "gateway_ip": "10.42.0.1",
}

# Bootstrap credentials used on truly first boot, before the operator
# completes the setup wizard. Documented in README so the operator knows
# what to join. The setup wizard forces the operator to change both
# fields, so these are only ever active during the gap between first
# power-on and password setup.
BOOTSTRAP_MGMT_AP = {
    "ssid":       "PiPineapple-Setup",
    "password":   "pineapple-setup",   # WPA2, ≥8 chars, documented
    "channel":    6,
    "subnet":     "10.42.0.0/24",
    "gateway_ip": "10.42.0.1",
}

CONF_DIR = Path("/etc/pipineapple")


class NetworkingService:
    def __init__(self, data_dir: Path) -> None:
        self._state_path = data_dir / "networking.json"
        self._lock = Lock()
        self._mgmt_hostapd_job_id: str | None = None
        self._mgmt_dnsmasq_job_id: str | None = None

    # ---------- State persistence ----------
    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self._state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {
                "wlan0_mode": "idle",
                "mgmt_ap": dict(DEFAULT_MGMT_AP),
            }

    def _save(self, state: dict[str, Any]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(state, f, indent=2)
        tmp.replace(self._state_path)

    # ---------- Public read API ----------
    def get_state(self) -> dict[str, Any]:
        with self._lock:
            state = self._load()
        # Don't leak the password into the API response
        ap = dict(state.get("mgmt_ap") or DEFAULT_MGMT_AP)
        ap["password_set"] = bool(ap.get("password"))
        ap.pop("password", None)
        return {
            "wlan0_mode":    state.get("wlan0_mode", "idle"),
            "mgmt_ap":       ap,
            "saved_wifi":    nm.list_saved_wifi(),
        }

    def scan_wifi(self) -> list[dict]:
        return nm.wifi_scan("wlan0", rescan=True)

    # ---------- Client mode ----------
    def connect_wifi(self, ssid: str, password: str | None) -> tuple[bool, str]:
        """Save (if needed) + connect to a Wi-Fi network on wlan0.

        Disables the management AP first if it's currently active.
        """
        with self._lock:
            state = self._load()
            if state.get("wlan0_mode") == "ap":
                log.info("disabling management AP before connecting to %s", ssid)
                self._disable_mgmt_ap_unlocked(state)

            ok, msg = nm.wifi_connect(ssid, password, iface="wlan0")
            if not ok:
                return False, msg
            state["wlan0_mode"] = "client"
            self._save(state)
        return True, msg

    def disconnect_wifi(self) -> tuple[bool, str]:
        with self._lock:
            state = self._load()
            ok, msg = nm.wifi_disconnect("wlan0")
            if ok:
                state["wlan0_mode"] = "idle"
                self._save(state)
        return ok, msg

    def forget_wifi(self, name: str) -> tuple[bool, str]:
        return nm.forget_wifi(name)

    # ---------- Management AP ----------
    def configure_mgmt_ap(self, ssid: str, password: str, channel: int = 6) -> tuple[bool, str]:
        if not ssid or len(ssid) < 1 or len(ssid) > 32:
            return False, "SSID must be 1-32 characters"
        if password and len(password) < 8:
            return False, "WPA2 password must be at least 8 characters"
        with self._lock:
            state = self._load()
            state["mgmt_ap"] = {
                **(state.get("mgmt_ap") or DEFAULT_MGMT_AP),
                "ssid":     ssid,
                "password": password,
                "channel":  channel,
            }
            self._save(state)
        return True, f"management AP config saved (ssid={ssid})"

    def enable_mgmt_ap(self) -> tuple[bool, list[str]]:
        with self._lock:
            state = self._load()
            ap = state.get("mgmt_ap") or {}
            if not ap.get("ssid"):
                return False, ["SSID not configured"]
            if not ap.get("password"):
                return False, ["password not configured (WPA2 required for management AP)"]
            messages = self._enable_mgmt_ap_unlocked(state, ap)
            ok = state.get("wlan0_mode") == "ap"
            return ok, messages

    def _enable_mgmt_ap_unlocked(self, state: dict, ap: dict) -> list[str]:
        """Caller holds the lock. Mutates state in place; saves at the end."""
        messages: list[str] = []
        # 1. Release wlan0 from NM
        ok, msg = nm.set_managed("wlan0", managed=False)
        messages.append(msg)
        if not ok:
            return messages

        # 2. Assign static IP
        gateway = ap.get("gateway_ip", "10.42.0.1")
        subnet  = ap.get("subnet", "10.42.0.0/24")
        prefix = ipaddress.ip_network(subnet, strict=False).prefixlen
        # Flush first to avoid duplicate-address noise on re-enable
        iproute.flush_address("wlan0")
        ok, msg = iproute.add_address("wlan0", f"{gateway}/{prefix}")
        messages.append(msg)
        if not ok:
            return messages

        ok, msg = iproute.set_link_state("wlan0", "up")
        messages.append(msg)
        if not ok:
            return messages

        # 3. Write hostapd + dnsmasq configs
        hostapd_path = CONF_DIR / "mgmt-ap-hostapd.conf"
        dnsmasq_path = CONF_DIR / "mgmt-ap-dnsmasq.conf"

        net = ipaddress.ip_network(subnet, strict=False)
        all_hosts = list(net.hosts())
        # Use a comfortable DHCP range, leaving room before/after for static IPs
        dhcp_start = str(all_hosts[9])   # .10
        dhcp_end   = str(all_hosts[99]) if len(all_hosts) >= 100 else str(all_hosts[-2])

        ok, msg = hostapd.write_config(hostapd_path, hostapd.render_config(
            iface=    "wlan0",
            ssid=     ap["ssid"],
            password= ap["password"],
            channel=  ap.get("channel", 6),
        ))
        messages.append(msg)
        if not ok:
            return messages

        ok, msg = dnsmasq.write_config(dnsmasq_path, dnsmasq.render_config(
            iface=            "wlan0",
            gateway_ip=       gateway,
            dhcp_range_start= dhcp_start,
            dhcp_range_end=   dhcp_end,
            local_hostnames={
                "pipineapple.local": gateway,
                "pi-lab.local":       gateway,
            },
        ))
        messages.append(msg)
        if not ok:
            return messages

        # 4. Start dnsmasq then hostapd via JobManager
        dn_job = job_manager.start_job(
            ["dnsmasq", "-C", str(dnsmasq_path), "-k", "--log-facility=-"],
            name="mgmt-ap-dnsmasq",
            tags=["networking", "mgmt-ap"],
        )
        self._mgmt_dnsmasq_job_id = dn_job.id
        messages.append(f"started dnsmasq (job {dn_job.id})")

        ha_job = job_manager.start_job(
            ["hostapd", str(hostapd_path)],
            name="mgmt-ap-hostapd",
            tags=["networking", "mgmt-ap"],
        )
        self._mgmt_hostapd_job_id = ha_job.id
        messages.append(f"started hostapd (job {ha_job.id})")

        state["wlan0_mode"] = "ap"
        self._save(state)
        return messages

    def disable_mgmt_ap(self) -> tuple[bool, list[str]]:
        with self._lock:
            state = self._load()
            messages = self._disable_mgmt_ap_unlocked(state)
            self._save(state)
        return True, messages

    def _disable_mgmt_ap_unlocked(self, state: dict) -> list[str]:
        """Caller holds the lock. Mutates state; doesn't save (caller saves)."""
        messages: list[str] = []
        # Stop hostapd + dnsmasq (order matters less, but stop hostapd first
        # so clients see the SSID disappear cleanly).
        for jid_name, jid in (("hostapd", self._mgmt_hostapd_job_id),
                              ("dnsmasq", self._mgmt_dnsmasq_job_id)):
            if jid:
                stopped, reason = job_manager.stop_job(jid)
                messages.append(f"stop {jid_name}: {reason}")
        self._mgmt_hostapd_job_id = None
        self._mgmt_dnsmasq_job_id = None

        # Flush the static IP
        from app.tools._common import run as _run
        _run(["ip", "addr", "flush", "dev", "wlan0"])
        messages.append("flushed wlan0 IP")

        # Return wlan0 to NM
        ok, msg = nm.set_managed("wlan0", managed=True)
        messages.append(msg)

        state["wlan0_mode"] = "idle"
        return messages

    # ---------- Startup restore ----------
    def is_first_boot(self, auth_path: Path) -> bool:
        """True if both auth.json and networking.json are absent.

        Triggers the bootstrap management AP so the operator can reach
        the setup wizard via Wi-Fi instead of needing Ethernet.
        """
        return not auth_path.is_file() and not self._state_path.is_file()

    def restore_on_startup(self, auth_path: Path | None = None) -> None:
        """Re-apply the saved mode after Flask starts.

        On true first boot (no auth.json + no networking.json), auto-
        enable the bootstrap management AP so the operator can reach
        the setup wizard over Wi-Fi. Otherwise restore the previously
        saved mode (or stay idle if 'client' — NM takes care of
        reconnecting automatically).
        """
        with self._lock:
            # First-boot bootstrap path
            if auth_path is not None and self.is_first_boot(auth_path):
                log.info("first boot detected (no auth.json + no networking.json) — "
                         "enabling bootstrap management AP")
                state = {
                    "wlan0_mode": "ap",
                    "mgmt_ap":    dict(BOOTSTRAP_MGMT_AP),
                    "bootstrap":  True,
                }
                messages = self._enable_mgmt_ap_unlocked(state, state["mgmt_ap"])
                for m in messages:
                    log.info("bootstrap-ap: %s", m)
                if state.get("wlan0_mode") != "ap":
                    log.error("bootstrap-ap: enable sequence did not reach completion "
                              "(wlan0_mode=%s, last messages above)", state.get("wlan0_mode"))
                self._save(state)
                return

            state = self._load()
            mode = state.get("wlan0_mode", "idle")
            if mode == "ap":
                ap = state.get("mgmt_ap") or {}
                if ap.get("ssid") and ap.get("password"):
                    log.info("restoring management AP from saved state")
                    self._enable_mgmt_ap_unlocked(state, ap)
                    self._save(state)
                else:
                    log.warning("saved mode is 'ap' but mgmt_ap config incomplete; staying idle")
                    state["wlan0_mode"] = "idle"
                    self._save(state)

    # ---------- Bootstrap-to-permanent transition ----------
    def is_running_bootstrap(self) -> bool:
        """True if the currently active AP is the bootstrap one."""
        with self._lock:
            state = self._load()
            return bool(state.get("bootstrap"))

    def reconfigure_and_restart_ap(self, ssid: str, password: str, channel: int = 6) -> tuple[bool, list[str]]:
        """Used by the setup wizard. Disable the current (bootstrap) AP,
        save the operator's new credentials, re-enable with them.

        Done as a single atomic operation so we never end up in a state
        where the AP is down + no config saved.
        """
        messages: list[str] = []
        with self._lock:
            state = self._load()
            # 1. Stop the bootstrap AP if it's running
            if state.get("wlan0_mode") == "ap":
                messages += self._disable_mgmt_ap_unlocked(state)

            # 2. Save the operator's new config
            state["mgmt_ap"] = {
                **(state.get("mgmt_ap") or DEFAULT_MGMT_AP),
                "ssid":     ssid,
                "password": password,
                "channel":  channel,
            }
            state.pop("bootstrap", None)  # no longer bootstrap once user has set their own credentials

            # 3. Re-enable with the new config
            ap = state["mgmt_ap"]
            messages += self._enable_mgmt_ap_unlocked(state, ap)
            self._save(state)
        ok = state.get("wlan0_mode") == "ap"
        return ok, messages


def get_service() -> NetworkingService:
    """Lazy resolver against the current Flask app's DATA_DIR."""
    from flask import current_app
    return NetworkingService(current_app.config["DATA_DIR"])
