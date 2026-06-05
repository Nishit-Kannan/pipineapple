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
from app.tools import dnsmasq, hostapd, iproute, iptables, iw, nm, rfkill

log = logging.getLogger(__name__)

DEFAULT_MGMT_AP = {
    "ssid":       "PiPineapple-Mgmt",
    "password":   "",
    "channel":    6,
    "subnet":     "10.42.0.0/24",
    "gateway_ip": "10.42.0.1",
    # Which physical interface hosts the AP. Default wlan0 (Pi onboard)
    # is the only stable name on a fresh first boot. After udev sticky
    # names land, the operator can move this to wlan-mgmt-ap (an Alfa)
    # via the Settings → Networking → Management AP → Interface dropdown.
    "interface":  "wlan0",
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
    "interface":  "wlan0",
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
        """Read networking.json with auto-migration from legacy schemas.

        Legacy schema (S04.6): single field ``wlan0_mode`` conflated
        AP state with wlan0 state. With the multi-radio mgmt AP from
        S04.7, those decouple — AP can be on a non-wlan0 radio while
        wlan0 does its own thing. New schema introduces
        ``mgmt_ap_active`` (bool) as the single source of truth for
        "is the AP running"; ``wlan0_mode`` reverts to meaning
        wlan0's actual mode (idle / client / ap).
        """
        try:
            state = json.loads(self._state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {
                "wlan0_mode":     "idle",
                "mgmt_ap_active": False,
                "mgmt_ap":        dict(DEFAULT_MGMT_AP),
            }

        # Migration: if mgmt_ap_active is absent, derive it from the
        # legacy wlan0_mode field, then correct wlan0_mode if AP is on
        # a non-wlan0 interface.
        if "mgmt_ap_active" not in state:
            legacy_mode = state.get("wlan0_mode", "idle")
            state["mgmt_ap_active"] = (legacy_mode == "ap")
            ap_iface = (state.get("mgmt_ap") or {}).get("interface", "wlan0")
            if legacy_mode == "ap" and ap_iface != "wlan0":
                # AP was on a non-wlan0 interface; wlan0 itself was idle
                state["wlan0_mode"] = "idle"
            log.info("migrated networking.json: mgmt_ap_active=%s wlan0_mode=%s",
                     state["mgmt_ap_active"], state["wlan0_mode"])
        return state

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
            "wlan0_mode":      state.get("wlan0_mode", "idle"),
            "mgmt_ap_active":  bool(state.get("mgmt_ap_active", False)),
            "mgmt_ap":         ap,
            "saved_wifi":      nm.list_saved_wifi(),
            "wireless_ifaces": [w["name"] for w in iw.list_wireless_devices()],
        }

    def scan_wifi(self) -> list[dict]:
        # Ensure NM's wifi radio is on AND the interface is up before
        # asking for a scan — covers the case where wlan0 was just
        # freed from AP mode and NM hasn't re-enabled it yet.
        nm.radio_wifi_on()
        iproute.set_link_state("wlan0", "up")
        return nm.wifi_scan("wlan0", rescan=True)

    # ---------- Client mode ----------
    def connect_wifi(self, ssid: str, password: str | None) -> tuple[bool, str]:
        """Save (if needed) + connect to a Wi-Fi network on wlan0.

        Only disables the management AP if it's currently hosted on
        wlan0 (the interface we're about to use as a client). If the
        AP is on a different radio (e.g. wlan-mgmt-ap), it stays up —
        the radios are physically independent.
        """
        with self._lock:
            state = self._load()
            ap_iface = (state.get("mgmt_ap") or {}).get("interface", "wlan0")
            if state.get("mgmt_ap_active") and ap_iface == "wlan0":
                log.info("AP is on wlan0; disabling before client connect to %s", ssid)
                self._disable_mgmt_ap_unlocked(state)

            ok, msg = nm.wifi_connect(ssid, password, iface="wlan0")
            if not ok:
                self._save(state)
                return False, msg
            state["wlan0_mode"] = "client"
            self._save(state)
        return True, msg

    def save_wifi(self, ssid: str, password: str | None) -> tuple[bool, str]:
        """Save a Wi-Fi profile without trying to connect now.

        Used when wlan0 is busy hosting the management AP and can't act
        as a client. NM will use the saved profile once wlan0 is freed.
        Deliberately passes iface=None so nmcli doesn't refuse to bind
        to wlan0 while it's unmanaged.
        """
        return nm.wifi_save_profile(ssid, password, iface=None)

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
    def set_internet_sharing(self, enabled: bool) -> tuple[bool, list[str]]:
        """Toggle internet sharing on/off for the management AP.

        If the AP is currently running, also reapplies/removes NAT
        rules immediately. Persists in mgmt_ap.internet_sharing so it's
        re-applied on every AP start.
        """
        messages: list[str] = []
        with self._lock:
            state = self._load()
            ap = state.get("mgmt_ap") or dict(DEFAULT_MGMT_AP)
            ap["internet_sharing"] = bool(enabled)
            state["mgmt_ap"] = ap

            subnet = ap.get("subnet", "10.42.0.0/24")
            if state.get("mgmt_ap_active"):
                if enabled:
                    ok, msg = iptables.enable_ip_forward()
                    messages.append(msg)
                    ok, msg = iptables.ensure_nat_masquerade(subnet)
                    messages.append(msg)
                    ok, msg = iptables.ensure_forward_rules(subnet)
                    messages.append(msg)
                    # Also regenerate dnsmasq config + restart so DNS
                    # forwards upstream
                    messages += self._restart_dnsmasq_with_current_config(ap)
                else:
                    ok, msg = iptables.remove_nat_and_forward(subnet)
                    messages.append(msg)
                    messages += self._restart_dnsmasq_with_current_config(ap)
            self._save(state)
        return True, messages

    def _restart_dnsmasq_with_current_config(self, ap: dict) -> list[str]:
        """Re-render dnsmasq config (reflecting current internet_sharing
        flag) and bounce the dnsmasq job so the new config takes effect."""
        messages: list[str] = []
        iface = ap.get("interface", "wlan0")
        gateway = ap.get("gateway_ip", "10.42.0.1")
        subnet  = ap.get("subnet", "10.42.0.0/24")
        share_internet = bool(ap.get("internet_sharing", False))

        net = ipaddress.ip_network(subnet, strict=False)
        all_hosts = list(net.hosts())
        dhcp_start = str(all_hosts[9])
        dhcp_end   = str(all_hosts[99]) if len(all_hosts) >= 100 else str(all_hosts[-2])
        dnsmasq_path = CONF_DIR / "mgmt-ap-dnsmasq.conf"

        ok, msg = dnsmasq.write_config(dnsmasq_path, dnsmasq.render_config(
            iface=            iface,
            gateway_ip=       gateway,
            dhcp_range_start= dhcp_start,
            dhcp_range_end=   dhcp_end,
            forward_dns=      share_internet,
            local_hostnames={
                "pipineapple.local": gateway,
                "pi-lab.local":       gateway,
            },
        ))
        messages.append(msg)

        # Stop the existing dnsmasq job, start a new one with new config
        if self._mgmt_dnsmasq_job_id:
            stopped, reason = job_manager.stop_job(self._mgmt_dnsmasq_job_id)
            messages.append(f"stop dnsmasq: {reason}")
        dn_job = job_manager.start_job(
            ["dnsmasq", "-C", str(dnsmasq_path), "-k", "--log-facility=-"],
            name="mgmt-ap-dnsmasq",
            tags=["networking", "mgmt-ap"],
            stdout_path="/tmp/pipineapple-mgmt-ap-dnsmasq.log",
        )
        self._mgmt_dnsmasq_job_id = dn_job.id
        messages.append(f"restarted dnsmasq (job {dn_job.id})")
        return messages

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
            ok = state.get("mgmt_ap_active", False)
            return ok, messages

    def _enable_mgmt_ap_unlocked(self, state: dict, ap: dict) -> list[str]:
        """Caller holds the lock. Mutates state in place; saves at the end."""
        messages: list[str] = []

        # Which physical interface the AP runs on. Defaults to wlan0 for
        # first-boot bootstrap; operator can move it to wlan-mgmt-ap
        # after the Alfa adapters have their udev sticky names.
        iface = ap.get("interface", "wlan0")
        messages.append(f"target interface: {iface}")

        # 0. Clear any rfkill soft-block. Pi 5 frequently boots with
        #    wifi in a soft-blocked state — `ip link set up` then fails
        #    with "Operation not possible due to RF-kill". Idempotent
        #    no-op if already unblocked.
        ok, msg = rfkill.unblock_wifi()
        messages.append(msg)
        if not ok:
            log.warning("rfkill unblock returned non-zero: %s", msg)

        # 1. Release the interface from NM (only matters for wlan0; the
        #    Alfas are already in NM's unmanaged list by config).
        ok, msg = nm.set_managed(iface, managed=False)
        messages.append(msg)
        if not ok:
            return messages

        # 2. Assign static IP
        gateway = ap.get("gateway_ip", "10.42.0.1")
        subnet  = ap.get("subnet", "10.42.0.0/24")
        prefix = ipaddress.ip_network(subnet, strict=False).prefixlen
        iproute.flush_address(iface)
        ok, msg = iproute.add_address(iface, f"{gateway}/{prefix}")
        messages.append(msg)
        if not ok:
            return messages

        ok, msg = iproute.set_link_state(iface, "up")
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
            iface=    iface,
            ssid=     ap["ssid"],
            password= ap["password"],
            channel=  ap.get("channel", 6),
        ))
        messages.append(msg)
        if not ok:
            return messages

        share_internet = bool(ap.get("internet_sharing", False))
        ok, msg = dnsmasq.write_config(dnsmasq_path, dnsmasq.render_config(
            iface=            iface,
            gateway_ip=       gateway,
            dhcp_range_start= dhcp_start,
            dhcp_range_end=   dhcp_end,
            forward_dns=      share_internet,
            local_hostnames={
                "pipineapple.local": gateway,
                "pi-lab.local":       gateway,
            },
        ))
        messages.append(msg)
        if not ok:
            return messages

        # 4. Start dnsmasq then hostapd via JobManager. Redirect their
        #    stdout to log files because (a) long-running daemons don't
        #    interact well with our pipe+reader model and (b) when they
        #    crash we need post-mortem logs.
        dn_job = job_manager.start_job(
            ["dnsmasq", "-C", str(dnsmasq_path), "-k", "--log-facility=-"],
            name="mgmt-ap-dnsmasq",
            tags=["networking", "mgmt-ap"],
            stdout_path="/tmp/pipineapple-mgmt-ap-dnsmasq.log",
        )
        self._mgmt_dnsmasq_job_id = dn_job.id
        messages.append(f"started dnsmasq (job {dn_job.id})")

        ha_job = job_manager.start_job(
            ["hostapd", str(hostapd_path)],
            name="mgmt-ap-hostapd",
            tags=["networking", "mgmt-ap"],
            stdout_path="/tmp/pipineapple-mgmt-ap-hostapd.log",
        )
        self._mgmt_hostapd_job_id = ha_job.id
        messages.append(f"started hostapd (job {ha_job.id})")

        # 5. Internet sharing (NAT + IP forwarding) — only if the
        #    operator enabled it. Lets AP clients reach the internet
        #    through whichever upstream the Pi has (wlan0 or eth0).
        if share_internet:
            ok, msg = iptables.enable_ip_forward()
            messages.append(msg)
            ok, msg = iptables.ensure_nat_masquerade(subnet)
            messages.append(msg)
            ok, msg = iptables.ensure_forward_rules(subnet)
            messages.append(msg)

        # State bookkeeping:
        # - mgmt_ap_active is the single source of truth for "AP is running"
        # - wlan0_mode reflects wlan0's actual mode; only "ap" when hosting it
        state["mgmt_ap_active"] = True
        if iface == "wlan0":
            state["wlan0_mode"] = "ap"
        self._save(state)
        return messages

    def move_mgmt_ap(self, new_iface: str) -> tuple[bool, list[str]]:
        """Move the management AP from its current interface to ``new_iface``.

        Atomic: stops the AP on the current interface, updates state,
        restarts on the new interface. Operator briefly loses the
        connection during the transition and reconnects to the same SSID
        now hosted by a different physical radio.
        """
        new_iface = new_iface.strip()
        if not new_iface:
            return False, ["target interface is empty"]
        messages: list[str] = []
        with self._lock:
            state = self._load()
            ap = state.get("mgmt_ap") or {}
            current_iface = ap.get("interface", "wlan0")
            if current_iface == new_iface and state.get("wlan0_mode") == "ap":
                return False, [f"already running on {new_iface}"]
            # Tear down on current interface (if active)
            if state.get("mgmt_ap_active"):
                messages += self._disable_mgmt_ap_unlocked(state)
            # Update config
            ap["interface"] = new_iface
            state["mgmt_ap"] = ap
            # Bring up on new interface
            if ap.get("ssid") and ap.get("password"):
                messages += self._enable_mgmt_ap_unlocked(state, ap)
                self._save(state)
                ok = state.get("mgmt_ap_active", False)
                return ok, messages
            # No credentials yet — just save the chosen interface
            self._save(state)
            return True, messages + [f"interface set to {new_iface}; configure SSID+password to enable"]

    def disable_mgmt_ap(self) -> tuple[bool, list[str]]:
        with self._lock:
            state = self._load()
            messages = self._disable_mgmt_ap_unlocked(state)
            self._save(state)
        return True, messages

    def _disable_mgmt_ap_unlocked(self, state: dict) -> list[str]:
        """Caller holds the lock. Mutates state; doesn't save (caller saves)."""
        messages: list[str] = []
        ap = state.get("mgmt_ap") or {}
        iface = ap.get("interface", "wlan0")
        subnet = ap.get("subnet", "10.42.0.0/24")

        # Remove NAT/FORWARD rules if they were set. Best-effort —
        # silently ignores rules that weren't present.
        if ap.get("internet_sharing"):
            ok, msg = iptables.remove_nat_and_forward(subnet)
            messages.append(msg)

        # Stop hostapd + dnsmasq (order matters less, but stop hostapd first
        # so clients see the SSID disappear cleanly).
        for jid_name, jid in (("hostapd", self._mgmt_hostapd_job_id),
                              ("dnsmasq", self._mgmt_dnsmasq_job_id)):
            if jid:
                stopped, reason = job_manager.stop_job(jid)
                messages.append(f"stop {jid_name}: {reason}")
        self._mgmt_hostapd_job_id = None
        self._mgmt_dnsmasq_job_id = None

        # Flush the static IP on the AP interface
        iproute.flush_address(iface)
        messages.append(f"flushed {iface} IP")

        # Return interface to NM (no-op for Alfas; they're always unmanaged)
        ok, msg = nm.set_managed(iface, managed=True)
        messages.append(msg)

        # Explicitly bring the interface back up. NM takes ownership but
        # doesn't auto-bring it up — without this, wlan0 stays DOWN in
        # "unavailable" state and the scan returns nothing. Only matters
        # for wlan0 (Alfas would stay unmanaged by NM anyway).
        if iface == "wlan0":
            ok, msg = iproute.set_link_state("wlan0", "up")
            messages.append(msg)

        state["mgmt_ap_active"] = False
        # Only reset wlan0_mode if wlan0 was actually hosting the AP.
        # If AP was on a different interface, wlan0 is untouched.
        if iface == "wlan0":
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
                # Smart interface selection: if the operator has
                # pre-configured udev rules so wlan-mgmt-ap exists as a
                # named interface (Realtek or similar dedicated AP
                # radio), use it. Otherwise fall back to wlan0 (Pi
                # onboard) — only stable name on truly fresh boots.
                wireless = [w["name"] for w in iw.list_wireless_devices()]
                bootstrap_iface = "wlan-mgmt-ap" if "wlan-mgmt-ap" in wireless else "wlan0"

                log.info("first boot detected — enabling bootstrap AP on %s "
                         "(detected wireless: %s)", bootstrap_iface, wireless)

                bootstrap_ap = dict(BOOTSTRAP_MGMT_AP)
                bootstrap_ap["interface"] = bootstrap_iface

                state = {
                    "wlan0_mode":     "idle",
                    "mgmt_ap_active": False,
                    "mgmt_ap":        bootstrap_ap,
                    "bootstrap":      True,
                }
                messages = self._enable_mgmt_ap_unlocked(state, state["mgmt_ap"])
                for m in messages:
                    log.info("bootstrap-ap: %s", m)
                if not state.get("mgmt_ap_active"):
                    log.error("bootstrap-ap: enable sequence did not reach completion "
                              "(last messages above)")
                self._save(state)
                return

            state = self._load()
            if state.get("mgmt_ap_active"):
                ap = state.get("mgmt_ap") or {}
                if ap.get("ssid") and ap.get("password"):
                    log.info("restoring management AP from saved state (iface=%s)",
                             ap.get("interface", "wlan0"))
                    self._enable_mgmt_ap_unlocked(state, ap)
                    self._save(state)
                else:
                    log.warning("mgmt_ap_active=True but config incomplete; staying down")
                    state["mgmt_ap_active"] = False
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
            if state.get("mgmt_ap_active"):
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
