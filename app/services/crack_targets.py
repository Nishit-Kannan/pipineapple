"""Crack-target management — SSH-reachable hosts where hashcat runs.

The Pi is a dispatcher: it scps a captured .22000 file to a configured
remote host and runs ``hashcat -m 22000`` over SSH. The Pi never does
PSK cracking itself (Pi 5 CPU is 100-1000x slower than even a modest
GPU; the VideoCore 7 GPU isn't hashcat-supported anyway).

This module owns:

* The persisted list of crack targets at
  ``$DATA_DIR/crack_targets.json``: each entry is {id, name, host,
  user, port, wordlist_path, added_at, last_test_ok, last_test_msg}.
  No secrets — auth is key-based.
* The platform's SSH keypair at ``$DATA_DIR/ssh/id_ed25519`` (private,
  mode 0600) and ``id_ed25519.pub`` (public). Generated on first
  request via ``ssh-keygen -t ed25519``. The public key is what the
  operator copies to each remote target's ~/.ssh/authorized_keys.
* The platform's per-target ``known_hosts`` file at
  ``$DATA_DIR/ssh/known_hosts`` so we don't touch the operator's user
  ssh state. ``-o StrictHostKeyChecking=accept-new`` is OpenSSH's
  modern TOFU mode: accept the host key on first connect, reject if
  the key ever changes.
* Thin wrappers around ssh / scp that always pass the right ``-i`` /
  ``-o UserKnownHostsFile`` / ``-o StrictHostKeyChecking`` /
  ``-o BatchMode=yes`` (never prompts; fail fast if auth would block).

The actual crack-job lifecycle lives in ``app/services/crack.py`` —
this module just provides the building blocks.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
import uuid
from pathlib import Path
from threading import Lock
from typing import Any

log = logging.getLogger(__name__)


# ---------- SSH helpers ----------

# Stored alongside everything else under $DATA_DIR. Operator's
# ~/.ssh/ is left alone — keeps platform state self-contained and
# rebootable to known good state by removing one directory.
_SSH_KEY_TYPE = "ed25519"
_SSH_KEY_BITS = None  # ed25519 ignores -b
_SSH_KEY_COMMENT = "pipineapple"
_SSH_CONNECT_TIMEOUT = 10           # seconds — fail fast if remote is down
_SSH_TEST_TIMEOUT = 15              # seconds for the "Test target" action

# Common SSH options we ALWAYS pass. BatchMode=yes refuses password
# prompts — if key auth fails we fail fast instead of hanging on a
# TTY prompt that nobody will answer.
_SSH_COMMON_OPTS = [
    "-o", "BatchMode=yes",
    "-o", f"ConnectTimeout={_SSH_CONNECT_TIMEOUT}",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=3",
]


def _ssh_dir(data_dir: Path) -> Path:
    p = data_dir / "ssh"
    p.mkdir(parents=True, exist_ok=True)
    p.chmod(0o700)
    return p


def _key_paths(data_dir: Path) -> tuple[Path, Path]:
    sshd = _ssh_dir(data_dir)
    return sshd / "id_ed25519", sshd / "id_ed25519.pub"


def _known_hosts_path(data_dir: Path) -> Path:
    return _ssh_dir(data_dir) / "known_hosts"


def ensure_keys(data_dir: Path) -> tuple[Path, Path]:
    """Generate the platform's SSH keypair if missing. Returns
    (private_path, public_path). Idempotent."""
    priv, pub = _key_paths(data_dir)
    if priv.is_file() and pub.is_file():
        return priv, pub
    # ssh-keygen complains if either file exists alone — clear stragglers
    for p in (priv, pub):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    cmd = [
        "ssh-keygen",
        "-t", _SSH_KEY_TYPE,
        "-f", str(priv),
        "-N", "",                       # empty passphrase
        "-C", _SSH_KEY_COMMENT,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10.0)
    if r.returncode != 0:
        raise RuntimeError(
            f"ssh-keygen failed: {r.stderr.strip() or r.stdout.strip()}"
        )
    # Tighten perms for safety. ssh-keygen already does this but be
    # defensive in case of umask weirdness.
    priv.chmod(0o600)
    pub.chmod(0o644)
    log.info("generated platform SSH key at %s", priv)
    return priv, pub


def get_public_key_text(data_dir: Path) -> str:
    """Return the public key body so the operator can copy it to each
    remote's ~/.ssh/authorized_keys. Generates the key if missing."""
    _, pub = ensure_keys(data_dir)
    return pub.read_text().strip()


def get_public_key_fingerprint(data_dir: Path) -> str:
    """SHA256 fingerprint of the public key, like ssh-keygen -l shows."""
    _, pub = ensure_keys(data_dir)
    r = subprocess.run(
        ["ssh-keygen", "-l", "-f", str(pub)],
        capture_output=True, text=True, timeout=3.0,
    )
    if r.returncode != 0:
        return "(fingerprint unavailable)"
    # Output format: "256 SHA256:abcd... comment (ED25519)"
    return r.stdout.strip()


def _ssh_argv(
    data_dir: Path, target: dict[str, Any], remote_cmd: str | None = None,
    *, extra_opts: list[str] | None = None,
) -> list[str]:
    """Build an ssh argv pointing at ``target`` with our key + known_hosts.

    If ``remote_cmd`` is provided it's appended as the final arg (run
    that command remotely). If None, an interactive-ish session is
    returned (which we never actually use; all our calls pass a cmd).
    """
    priv, _ = _key_paths(data_dir)
    known = _known_hosts_path(data_dir)
    argv = ["ssh"]
    argv += _SSH_COMMON_OPTS
    argv += extra_opts or []
    argv += [
        "-i", str(priv),
        "-o", f"UserKnownHostsFile={known}",
        # accept-new: TOFU on first connection, reject if host key changes
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "IdentitiesOnly=yes",       # don't try operator's other keys
        "-p", str(int(target.get("port", 22))),
        f"{target['user']}@{target['host']}",
    ]
    if remote_cmd is not None:
        argv.append(remote_cmd)
    return argv


def _scp_argv(
    data_dir: Path, target: dict[str, Any], local_path: Path, remote_path: str,
) -> list[str]:
    priv, _ = _key_paths(data_dir)
    known = _known_hosts_path(data_dir)
    return [
        "scp",
        *_SSH_COMMON_OPTS,
        "-i", str(priv),
        "-o", f"UserKnownHostsFile={known}",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "IdentitiesOnly=yes",
        "-P", str(int(target.get("port", 22))),
        str(local_path),
        f"{target['user']}@{target['host']}:{remote_path}",
    ]


def run_ssh(
    data_dir: Path, target: dict[str, Any], remote_cmd: str,
    *, timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    """One-shot ssh + run remote_cmd. Returns CompletedProcess. Doesn't
    raise on non-zero rc — caller inspects ``returncode``."""
    argv = _ssh_argv(data_dir, target, remote_cmd)
    log.debug("ssh exec: %s", " ".join(argv))
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(
            args=argv, returncode=124, stdout="",
            stderr=f"ssh timed out after {timeout}s: {e}",
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            args=argv, returncode=127, stdout="",
            stderr="ssh command not found",
        )


def run_scp(
    data_dir: Path, target: dict[str, Any],
    local_path: Path, remote_path: str,
    *, timeout: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    argv = _scp_argv(data_dir, target, local_path, remote_path)
    log.debug("scp exec: %s", " ".join(argv))
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(
            args=argv, returncode=124, stdout="",
            stderr=f"scp timed out after {timeout}s: {e}",
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            args=argv, returncode=127, stdout="",
            stderr="scp command not found",
        )


# ---------- Crack-target store ----------

# Validation: hostnames + user names. Conservative; rejects shell
# metacharacters that could change command meaning if we ever swap to
# shell=True (we don't, but defense in depth).
_NAME_RE = re.compile(r"^[A-Za-z0-9 _\-]{1,40}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9._\-]{1,253}$")
_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]{0,31}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9_\-/.~]{1,512}$")


class CrackTargetsService:
    """Manage the persisted list of SSH crack targets + the platform's
    SSH key + thin wrappers around ssh / scp."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._path = data_dir / "crack_targets.json"
        self._lock = Lock()

    # ---------- Persistence ----------
    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self._path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {"targets": []}

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._path)

    # ---------- Public API ----------
    def list_targets(self) -> list[dict[str, Any]]:
        """Return all configured targets, newest first."""
        with self._lock:
            data = self._load()
        targets = list(data.get("targets") or [])
        targets.sort(key=lambda t: t.get("added_at") or 0, reverse=True)
        return targets

    def get_target(self, target_id: str) -> dict[str, Any] | None:
        for t in self.list_targets():
            if t.get("id") == target_id:
                return t
        return None

    def add_target(
        self, name: str, host: str, user: str, wordlist_path: str,
        *, port: int = 22,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """Validate + persist a new crack target. Returns (ok, msg, record)."""
        name = (name or "").strip()
        host = (host or "").strip()
        user = (user or "").strip()
        wordlist_path = (wordlist_path or "").strip()

        if not _NAME_RE.match(name):
            return False, "name must be 1-40 chars (letters/digits/_/-/space)", None
        if not _HOST_RE.match(host):
            return False, "host must be a valid hostname or IP", None
        if not _USER_RE.match(user):
            return False, "user must be a valid unix username", None
        if not _PATH_RE.match(wordlist_path):
            return False, "wordlist path must be a plausible filesystem path", None
        try:
            port = int(port)
        except (TypeError, ValueError):
            return False, "port must be an int", None
        if not (1 <= port <= 65535):
            return False, f"port {port} out of range", None

        record = {
            "id":             uuid.uuid4().hex,
            "name":           name,
            "host":           host,
            "user":           user,
            "port":           port,
            "wordlist_path":  wordlist_path,
            "added_at":       time.time(),
            "last_test_ok":   None,        # populated by test_target()
            "last_test_msg":  None,
            "last_test_at":   None,
        }
        with self._lock:
            data = self._load()
            targets = data.get("targets") or []
            # Duplicate-name guard
            if any(t.get("name") == name for t in targets):
                return False, f"target named {name!r} already exists", None
            targets.append(record)
            data["targets"] = targets
            self._save(data)
        return True, f"added {name}", record

    def remove_target(self, target_id: str) -> tuple[bool, str]:
        with self._lock:
            data = self._load()
            targets = data.get("targets") or []
            new_targets = [t for t in targets if t.get("id") != target_id]
            if len(new_targets) == len(targets):
                return False, f"no target with id {target_id}"
            data["targets"] = new_targets
            self._save(data)
        return True, "removed"

    def test_target(self, target_id: str) -> tuple[bool, str]:
        """Run a quick remote-side sanity check: SSH reachable, hashcat
        installed, wordlist present and readable. Updates the target's
        last_test_* fields. Returns (ok, message)."""
        target = self.get_target(target_id)
        if target is None:
            return False, f"no target with id {target_id}"

        # Compound check, one round trip. Quote the wordlist path so
        # filenames with spaces work; user-supplied so we validated it
        # against _PATH_RE upstream.
        wordlist = target.get("wordlist_path", "")
        # Shell-quote with single quotes; inner single quotes need ''\''
        # but our _PATH_RE already rejects those characters.
        remote_cmd = (
            "set -e; "
            "command -v hashcat >/dev/null || { echo 'hashcat-not-installed'; exit 11; }; "
            f"[ -r '{wordlist}' ] || {{ echo 'wordlist-not-readable'; exit 12; }}; "
            "hashcat --version | head -1; "
            "uname -srm; "
            f"wc -l < '{wordlist}' 2>/dev/null || echo '?'"
        )
        r = run_ssh(
            self._data_dir, target, remote_cmd, timeout=_SSH_TEST_TIMEOUT,
        )

        ok = (r.returncode == 0)
        if ok:
            lines = (r.stdout or "").strip().splitlines()
            hashcat_ver = lines[0] if lines else "?"
            uname = lines[1] if len(lines) > 1 else "?"
            wc = lines[2] if len(lines) > 2 else "?"
            msg = f"hashcat {hashcat_ver} on {uname} · wordlist {wc.strip()} lines"
        elif r.returncode == 11:
            msg = "hashcat not installed on remote (apt install hashcat / brew install hashcat)"
        elif r.returncode == 12:
            msg = f"wordlist not readable at {wordlist}"
        elif r.returncode == 124:
            msg = "ssh timed out — host unreachable or wrong port"
        elif r.returncode == 255:
            # OpenSSH's "couldn't connect" / auth-failed exit code
            stderr_lc = (r.stderr or "").lower()
            if "permission denied" in stderr_lc:
                msg = ("permission denied — copy the platform's public key "
                       "to ~/.ssh/authorized_keys on the remote")
            elif "host key" in stderr_lc:
                msg = ("host key changed since first connect — remove the "
                       "stale entry from $DATA_DIR/ssh/known_hosts")
            else:
                msg = f"ssh failed: {(r.stderr or 'unknown').strip().splitlines()[-1]}"
        else:
            msg = f"remote check failed (rc={r.returncode}): {(r.stderr or '').strip()}"

        # Persist test outcome
        with self._lock:
            data = self._load()
            for t in (data.get("targets") or []):
                if t.get("id") == target_id:
                    t["last_test_ok"] = ok
                    t["last_test_msg"] = msg
                    t["last_test_at"] = time.time()
                    break
            self._save(data)

        return ok, msg

    # ---------- SSH key access ----------
    def get_public_key(self) -> dict[str, str]:
        """Returns {key, fingerprint, instructions} for the Settings UI."""
        ensure_keys(self._data_dir)
        return {
            "key":         get_public_key_text(self._data_dir),
            "fingerprint": get_public_key_fingerprint(self._data_dir),
            "instructions": (
                "Append the key above to ~/.ssh/authorized_keys on each "
                "host you want the platform to crack on. Example one-liner "
                "from your laptop:\n"
                "  echo '<paste key>' | ssh user@host 'cat >> ~/.ssh/authorized_keys'"
            ),
        }


# ---------- Module singleton ----------

_service: "CrackTargetsService | None" = None


def get_service() -> CrackTargetsService:
    global _service
    if _service is None:
        from flask import current_app
        _service = CrackTargetsService(current_app.config["DATA_DIR"])
    return _service
