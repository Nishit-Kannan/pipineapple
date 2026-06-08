"""hcxpcapngtool wrapper — pcapng / pcap → hashcat .22000 conversion.

hcxpcapngtool is the format conversion arm of hcxtools (the capture
arm is hcxdumptool, wrapped in app/tools/hcxdumptool.py). Together
they're the modern aircrack-ng replacement for WPA workflows.

The .22000 format (sometimes called .hc22000) is hashcat's universal
WPA hash format. One line per crackable target:

  WPA*<type>*<mic_or_pmkid>*<MAC_AP>*<MAC_STA>*<ESSID_hex>*<ANONCE_hex>*<EAPOL_hex>*<flags>

Type 01 = PMKID (most of the rest of the line is empty).
Type 02 = EAPOL 4-way (full nonces + MIC + EAPOL bytes).

hcxpcapngtool is fast (~milliseconds per MB on the Pi 5). Even so we
cache the converted .22000 file next to the source pcap so repeated
downloads don't redo the work; the route layer in app/routes/handshakes.py
checks the cache before invoking us.

stdout contains a human-readable summary block we parse for counts —
useful for the UI to show "captured 1 PMKID + 1 EAPOL pair" etc.
hcxpcapngtool returns 0 even when nothing was written; we infer
"no targets" from the counts, not the return code.

Install: hcxtools (already installed for S07.5's hcxdumptool).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.tools._common import run, stub_mode

log = logging.getLogger(__name__)


# Patterns hcxpcapngtool prints in its summary block. The exact lines
# vary slightly across versions; we extract conservatively and treat
# missing counters as zero.
_PMKID_RE = re.compile(
    r"^PMKID(?:s)?\s+(?:written)?[\s\.:]*([0-9]+)", re.MULTILINE | re.IGNORECASE,
)
_EAPOL_RE = re.compile(
    r"^EAPOL\s+(?:hashes|pairs)\s+written(?:\s+\(best\))?[\s\.:]*([0-9]+)",
    re.MULTILINE | re.IGNORECASE,
)


def convert_to_22000(
    pcap_in: str | Path,
    output_22000: str | Path,
) -> tuple[bool, str, dict[str, int]]:
    """Convert a pcap/pcapng to hashcat .22000 format.

    Returns ``(ok, message, counts)`` where:

    * ``ok`` is True if at least one PMKID or EAPOL pair was written.
      Format-only failures (file unreadable, hcxpcapngtool missing)
      surface as ``ok=False`` with a descriptive message.
    * ``counts`` is ``{"pmkid": N, "eapol": M}`` from the tool's
      summary. Both default to 0 when the regex doesn't match.
    """
    p_in = Path(pcap_in)
    p_out = Path(output_22000)

    if stub_mode():
        # Pretend we converted; write a synthetic .22000 with one
        # WPA*01 line so the download endpoint test on Mac dev has
        # something real to serve.
        synth = (
            "WPA*01*"
            "1122334455667788aabbccddeeff0011"     # 16-byte PMKID hex
            "*aabbccddee01*112233445501"
            "*53747562537461"                       # "StubSta" but really "StubNet"-ish
            "**\n"
        )
        try:
            p_out.parent.mkdir(parents=True, exist_ok=True)
            p_out.write_text(synth)
        except OSError as e:
            return False, f"(stub) write failed: {e}", {"pmkid": 0, "eapol": 0}
        return True, "(stub) wrote 1 PMKID line", {"pmkid": 1, "eapol": 0}

    if not p_in.is_file():
        return False, f"input not found: {p_in}", {"pmkid": 0, "eapol": 0}

    # hcxpcapngtool will create / overwrite the output file. Make
    # sure the dir exists.
    try:
        p_out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"output dir create failed: {e}", {"pmkid": 0, "eapol": 0}

    r = run(
        ["hcxpcapngtool", "-o", str(p_out), str(p_in)],
        timeout=30.0,
        source="hcxpcapngtool",
    )
    # hcxpcapngtool returns 0 even when 0 targets were written; we
    # judge success by the counts + by the output file existing
    # and being non-empty.
    if r.returncode == 127:
        return False, "hcxpcapngtool not installed (apt install -y hcxtools)", {"pmkid": 0, "eapol": 0}

    pmkid_count, eapol_count = _parse_counts(r.stdout)
    counts = {"pmkid": pmkid_count, "eapol": eapol_count}
    wrote_anything = (
        (pmkid_count + eapol_count) > 0
        or (p_out.is_file() and p_out.stat().st_size > 0)
    )

    if not wrote_anything:
        return False, "no crackable targets in pcap", counts

    msg = f"wrote {pmkid_count} PMKID + {eapol_count} EAPOL pairs to {p_out.name}"
    return True, msg, counts


def _parse_counts(stdout: str) -> tuple[int, int]:
    """Pull PMKID + EAPOL pair counts out of hcxpcapngtool's stdout."""
    if not stdout:
        return 0, 0
    pmkid = 0
    eapol = 0
    m = _PMKID_RE.search(stdout)
    if m:
        try:
            pmkid = int(m.group(1))
        except ValueError:
            pass
    m = _EAPOL_RE.search(stdout)
    if m:
        try:
            eapol = int(m.group(1))
        except ValueError:
            pass
    return pmkid, eapol


def is_stub() -> bool:
    """Same predicate other tool wrappers expose."""
    return stub_mode()
