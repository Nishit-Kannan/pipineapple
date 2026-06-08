# Session 09 — Crack dispatch (scp + remote hashcat over SSH)

**Date:** 2026-06-08
**Phase:** C — third offensive-output session. Captures land (S07), get exported to `.22000` (S08); this session sends them off-Pi to a remote machine that can actually run hashcat at GPU speed, and streams progress back to the UI.
**Goal:** Per-capture **Crack** button on the Handshakes page → target picker modal → SSH dispatch to a configured remote → live progress (Speed / Progress / Recovered / ETA) → cracked PSK shown in the UI.

This session was *deliberately* a dispatcher, not a local cracker. The Pi 5 CPU on `-m 22000` is ~3-5k H/s; a modest discrete GPU is 100-1000x faster, and the Pi's VideoCore 7 GPU isn't hashcat-supported anyway (OpenCL/HIP backends only).

---

## Checkpoint 1 — Concepts: hashcat 22000, SSH dispatch, key mgmt

**Decided** (via clarifying questions before scoping the build):

- **Configurable list of any SSH-reachable host**, not a hardcoded Mac+Jetson pair. Stored at `$DATA_DIR/crack_targets.json` as `{id, name, host, user, port, wordlist_path, added_at, last_test_*}`. No secrets — auth is key-based.
- **SSH key + per-target known_hosts (TOFU)**. Platform generates its own ed25519 keypair under `$DATA_DIR/ssh/` on first request to the Crack Targets tab. Per-platform `known_hosts` lives there too — we don't touch the operator's `~/.ssh/` at all. `StrictHostKeyChecking=accept-new` is OpenSSH's modern TOFU mode: accept the host key on first connect, reject if it ever changes.
- **No local aircrack-ng fallback** — remote dispatch only. Anything that could be cracked locally with aircrack is too slow on the Pi to matter; collapsing the implementation path is worth more than the corner case.
- **Pi never holds plaintext credentials.** Key auth only, `BatchMode=yes` on every ssh invocation so a password prompt fails fast instead of hanging on a TTY nobody will answer.

**Status block parsing.** `hashcat -m 22000 --quiet --status --status-timer=10` emits a structured status block every 10s with `Speed.#1`, `Progress`, `Recovered`, `Time.Estimated` lines. We tail the per-job log file every 2s, regex-extract these four fields, and emit `crack:status` SocketIO events when the parsed state changes (cheap dedup so we don't spam the socket every tick with identical values).

**Cracked PSK line.** When hashcat finds the password it prints the matched 22000 line plus `:password`. All 22000 header fields are hex/numeric and never contain `:`, so `str.partition(':', 1)` on the FIRST colon recovers the PSK verbatim. This matters: passwords with `:` in them (like `p@ss:word!`) survive. The earlier `rsplit(':', 1)` approach truncated them.

---

## Checkpoint 2 — Build

**Files created:**

- `app/services/crack_targets.py` — SSH keypair generation (ed25519, mode 0600 on private), per-platform known_hosts, validated target store. Public helpers: `ensure_keys`, `get_public_key_text`, `get_public_key_fingerprint`, `run_ssh`, `run_scp`. `CrackTargetsService` singleton with `list_targets`, `add_target`, `remove_target`, `test_target`, `get_public_key`. Validation regexes for name/host/user/path so shell-metacharacter input is rejected upstream (defense in depth — we don't use `shell=True` anywhere, but the rejection still belongs at the input boundary).
- `app/services/crack.py` — `CrackService` singleton owning a per-job `_CrackJob` record (capture+target snapshots, JobManager job_id, log_path, last-parsed Speed/Progress/Recovered/ETA, cracked_password). `start_crack(capture_id, target_id)` resolves the `.22000` (via `handshakes.resolve_or_build_22000`), scps it to `/tmp/pipineapple-<jobid>.22000` on the remote, launches `hashcat -m 22000 --quiet --status --status-timer=10 --potfile-disable …` over SSH via JobManager with `stdout_path` pointed at a per-job log file, then spawns an app-context-pushing daemon thread that tails the log and emits status. `stop_crack` SIGTERMs the local ssh process; OpenSSH propagates the signal to hashcat which exits cleanly. The trailing `; rm -f <remote_22000>` always cleans up the remote temp file.
- `app/routes/crack.py` — `GET/POST /crack/targets`, `DELETE /crack/targets/<id>`, `POST /crack/targets/<id>/test`, `GET /crack/public-key`, `POST /crack/start`, `POST /crack/<job_id>/stop`, `GET /crack/jobs[/<id>]`. Convention matches the handshakes blueprint: `{ok, msg}` or `{ok, messages}` with notifications service calls for every operator action.

**Files modified:**

- `app/__init__.py` — register the crack blueprint.
- `app/templates/settings.html` — added "Crack Targets" tab to the tab bar + a `tab-panel` with the public-key display (key + SHA256 fingerprint + copy-paste instructions) and the target table (add form + name/host/user/port/wordlist columns + Test/Remove actions).
- `app/static/settings.js` — `loadCrackPubkey`, `loadCrackTargets`, `renderCrackTargets`, add/test/remove handlers wired in `init()`.
- `app/templates/handshakes.html` — new "Crack jobs" card below the captures table (table hidden when empty, with `crack-empty` placeholder) and a `<div id="crack-modal">` matching the existing `.modal` / `.modal-card` pattern from the ethics modal.
- `app/static/handshakes.js` — Crack button per row (disabled unless the capture has a full handshake or a PMKID), modal open/close + target dropdown population, `onCrackStart` posts `/crack/start`, `reloadCracks` + `renderCracks` for the jobs table, `crack:status` SocketIO subscription. Speed formatter handles H/s → kH/s/MH/s/GH/s.

---

## Checkpoint 3 — The cracked-PSK parser bug (caught by tests)

First cut of `_extract_cracked_psk` did `parts = line.rsplit(":", 1)` and returned `parts[1]`. Logic was: the rightmost `:` separates header from password. Wrong for passwords that contain `:`.

Test case:

```
WPA*02*hash*mac1*mac2*essid*nonce*eapol*pair:p@ss:word!
```

Expected `p@ss:word!`. Got `word!`.

Fix: switch to a regex that matches the whole `WPA*…:…` line in one shot (`_CRACKED_LINE_RE`), then `line.partition(":", 1)` on the first colon. Works because 22000 header fields are all hex / numeric / star-separated and provably never contain `:`. Re-tested with five password shapes including the pathological `::weird::` — all five pass.

Lesson stored locally as a comment in `crack.py`: don't be clever splitting on the *wrong end* of a string. The format's structure tells you which end is safe.

---

## Checkpoint 4 — Verification

Pi-deploy + real crack-to-Mac is deferred (operator needs the Mac free for a separate test). What we *did* run, via the Flask test client against the mac dev config:

- `GET /crack/public-key` → 200; ed25519 key really generates on disk; SHA256 fingerprint returned matches `ssh-keygen -l`.
- `POST /crack/targets` rejects empty/invalid input (`'name must be 1-40 chars …'`) and duplicate names with the right status codes.
- `POST /crack/targets` round-trips a valid target through list + delete.
- `POST /crack/start` with a bogus capture_id returns 400 with `'no capture with id nope'` — the resolver runs before the scp does.
- `GET /crack/jobs` empty initially, returns persisted history.
- `/settings` page renders with the new tab (`tab-crack`, `crack-pubkey` both present).
- `/handshakes/` page renders with the modal + jobs table markup (`crack-modal`, `crack-tbody`).
- `node --check` on both JS files: parse OK.

Real Pi-side smoke test (scp + remote hashcat against rockyou with a known weak PSK) is parked under task #91 / next session.

---

## Checkpoint 5 — Notes for future sessions

- **Per-platform vs per-user known_hosts.** Putting `known_hosts` under `$DATA_DIR/ssh/` means: if the platform itself is moved to a new Pi, the operator re-trusts each remote on first connect (correct behavior). If the operator wants to share trust with another tool on the same Pi, they'd have to `cat $DATA_DIR/ssh/known_hosts >> ~/.ssh/known_hosts` manually. Acceptable trade-off — isolation is the bigger win.
- **No SSH multiplexing.** Each crack job opens its own ssh connection. For ~minute-long jobs the ~100ms connect overhead is irrelevant; for streaming many short jobs we'd want `ControlMaster=auto + ControlPersist=60`. Not worth it now.
- **`--potfile-disable` is deliberate.** The remote's hashcat potfile could otherwise auto-recover a previous run and skip the wordlist entirely, which looks like "cracked instantly" in the UI but actually proves nothing about *this* dispatch. Each crack we initiate is fresh.
- **Exit code 1 ≠ failure.** Hashcat exits 1 on wordlist exhaustion (`exhausted` in our enum). The dispatcher distinguishes that from "we SIGTERMed it" (`stopped`) and "ssh barfed" (`failed`).
- **Pi 5 GPU is a dead end.** Don't revisit. VideoCore 7 isn't on the hashcat backend list (CUDA/OpenCL/HIP). If we ever want on-device cracking, the path is a HAT-attached accelerator or just plug a USB GPU dock into one of the Pi's USB3 ports and dispatch to it like any other remote.

---

## What's next

Phase D — PineAP / rogue AP territory. SSID spoofing (S10), Karma-style probe-responses (S11), the full Evil WPA flow (S12), Evil Enterprise (S13). All offensive surfaces gated behind the ethics-confirm modal pattern from S06.
