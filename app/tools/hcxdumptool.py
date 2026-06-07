"""hcxdumptool wrapper — PMKID + EAPOL capture.

Replaces airodump-ng for the focused handshake-capture flow (Session
07.5). The recon-side broad-scan still uses airodump (Session 05) —
airodump's CSV is what feeds the AP/Client tables. hcxdumptool only
shines for handshake/PMKID work.

Why we switched:

* **PMKID without a client.** hcxdumptool actively probes APs and
  extracts the PMKID from the AP's M1 response. No deauth needed,
  no client needs to be present. This is the answer to "in a real
  pen test I can't ask the client to forget the network" — modern
  devices cache PMKs and skip M1/M2 on reconnect, but a fresh PMKID
  extracted via active scan works for hashcat mode 22000.
* **pcapng output**, modern format, scapy reads it.
* **Quieter on the air** — no continuous deauth flood required.

Install on Pi OS::

    sudo apt install -y hcxtools

(hcxtools provides hcxdumptool + hcxpcapngtool. The latter we use in
Session 08/09 for converting pcapng to hashcat's .22000 format.)

CLI surface (hcxdumptool 6.x):

* ``-i INTERFACE``         — capture interface
* ``-w OUTPUT.pcapng``     — output file (modern PCAPNG format)
* ``-c CHANNEL``           — pin to one channel (lock)
* ``--disable_active_scan`` — opt out of active PMKID extraction
                              (passive-only; rarely useful for us)
* ``--errormax=N``         — exit on error count (left default)

We deliberately do NOT BPF-filter to a specific BSSID at the tool
level. hcxdumptool's ``--bpfc`` takes BPF *bytecode* (decimal output
of ``tcpdump -ddd``), which would require shelling out to tcpdump
first — a lot of moving parts for marginal pcap size savings. Instead
we lock the channel and let our handshake_detector parse pcaps with
BSSID filtering at that stage. Extra noise is a few KB of beacons
from other APs on the same channel; negligible compared to the
multi-MB recon pcaps.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.tools._common import stub_mode

log = logging.getLogger(__name__)


def build_cmd(
    iface: str,
    output_path: str | Path,
    *,
    channel: int,
    active: bool = True,
) -> list[str]:
    """Build hcxdumptool command line for focused single-channel capture.

    * ``iface``   — interface in monitor mode (typically ``wlan-ap``).
    * ``output_path`` — pcapng file to write. hcxdumptool overwrites
      if it exists; caller is responsible for path uniqueness.
    * ``channel`` — single channel to lock to. Typically the target
      AP's channel from the recon snapshot.
    * ``active``  — default True. Active mode probes APs aggressively
      and extracts PMKID from M1 responses — the whole reason we
      moved off airodump. Set False for purely passive capture
      (waits for natural EAPOL frames only, no PMKID without a client).

    Caller is responsible for putting ``iface`` in monitor mode and
    pinning to ``channel`` before launching (same as the airodump flow).
    """
    if not (1 <= channel <= 196):
        # Defensive — caller's resolver should have set this from a
        # parsed beacon's channel field. Surface bad values early.
        raise ValueError(f"channel {channel} out of range")

    cmd = [
        "hcxdumptool",
        "-i", iface,
        "-w", str(output_path),
        "-c", str(channel),
    ]
    if not active:
        # Passive mode — no active probes. Only catches EAPOL when a
        # client happens to (re)associate. Strictly worse than active
        # for our use case; included for completeness / future "stealth"
        # mode.
        cmd.append("--disable_active_scan")
    return cmd


def is_stub() -> bool:
    """Same predicate other tool wrappers expose."""
    return stub_mode()
