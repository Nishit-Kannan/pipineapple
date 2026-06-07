# Session 07 — Handshake capture (and 07.5: the hcxdumptool rework)

**Date:** 2026-06-05 → 2026-06-07
**Phase:** C — first offensive-output session. Capture the cryptographic material an attacker would use to crack a WPA2/WPA3-Personal PSK offline. Output feeds Session 08's top-level Handshakes page and Session 09's crack-dispatch action.
**Goal:** From the Recon AP slide-out, one click should kick off a focused capture against that AP and surface live M1/M2/M3/M4 status. Captured pcap lands in `$DATA_DIR/handshakes/<bssid>/` with an index entry. Modern, real-pen-test-realistic: should work without asking the target's clients to cooperate.

Session 07 originally built this with airodump-ng + active deauth. Session 07.5 reworked it to use hcxdumptool because the airodump-only approach kept producing M3-only captures (PMK caching defeats the deauth-and-wait pattern, and modern clients fast-reconnect via cached PMK). The split is documented here as one journal; both are part of the same arc.

---

## Checkpoint 1 — Concepts: EAPOL 4-way + PMKID + focused vs hopping

**Decided:**

- **The EAPOL 4-way handshake** is what cracks a WPA2-PSK offline. M2 carries the MIC hashcat tries to recreate. M1+M2 is enough; M1+M2+M3+M4 is ideal. Identifying messages: all four are EAPOL-Key frames (type 3) with Pairwise=1; the (Install, Ack, MIC, Secure) tuple distinguishes them — M1 (0,1,0,0), M2 (0,0,1,0), M3 (1,1,1,1), M4 (0,0,1,1).
- **PMKID is the better target for pen testing.** It lives in M1's Key Data as a vendor-specific KDE (OUI 00-0F-AC, type 4). hcxdumptool can extract it by actively probing the AP — the AP responds with M1 including PMKID, no client involvement at all. hashcat mode 22000 cracks PMKID-alone targets just as readily as full 4-way targets.
- **Focused beats hopping.** Recon's two airodumps hop 14 channels each; a 4-way completes in ~50-100 ms and they'd catch it once in maybe 30 attempts. The focused capture (channel + BSSID pinned) sees every frame on the target's wire.
- **Three-radio strategy.** Recon adapters keep hopping (`wlan-mon-2g`, `wlan-mon-5g`). `wlan-ap` (the third Alfa, also our injection radio) does focused handshake capture. Same adapter we use for deauth, which is deliberate — when capture + deauth happen together, they share the same channel/radio rather than racing.
- **Deauth gate stays.** Same ethics-confirm modal style from Session 06. Default OFF for capture (post-07.5 — PMKID via active scan doesn't need it); default ON would be too aggressive for the realistic use case.

---

## Checkpoint 2 — Build (Session 07 baseline, airodump-based)

**Files created:**

- `app/tools/handshake_detector.py` — scapy-based EAPOL frame parser. `detect_handshakes(pcap)` walks a pcap, returns per-(BSSID, station) M-sets with `is_complete` / `is_partial` classification. Direction-aware (FromDS/ToDS) so AP-vs-station addresses are correctly assigned. Cached on (path, mtime) for 2 s — needed because the capture status poller hits once per second.
- `app/tools/aireplay.py` — already existed from Session 06's standalone Deauth button; the capture flow reuses it for the periodic deauth bursts.
- `app/services/handshakes.py` — `HandshakesService` singleton. `start_capture(bssid, channel, essid, *, deauth)` resolves the injection radio, sets monitor + channel, launches a focused airodump-ng job, spawns a status-poller thread + optional deauth burst loop. `stop_capture()` SIGINTs the job, joins threads, writes metadata to `$DATA_DIR/handshakes/index.json`. `list_captures(bssid=None)`, `delete_capture(id)`, `delete_all_for_bssid(b)` for management.
- `app/routes/handshakes.py` — six routes: `/handshakes/start`, `/stop`, `/status/<bssid>`, `/list`, `/delete`, `/delete-by-bssid`.

**Files modified:**

- `app/services/recon.py` — added `INJECT_ROLE = "wlan-ap"` constant (shared with the capture path), `deauth_ap` from Session 06 already had the right orchestration we copied for capture.
- `app/static/recon.js` — Capture button next to Deauth in the AP slide-out actions row; auto-disabled when MFP required; live status line showing M1/M2/M3/M4 dots and deauth burst count. Capture modal with ethics confirm. "Captures" tab in the AP slide-out listing prior captures with per-row + bulk delete.
- `app/static/style.css` — `.msg-dot` (M1-M4 pills) + `.capture-pill` (waiting/partial/complete) + `.capture-row` list styling.

---

## Checkpoint 3 — First test on the Pi (Session 07 baseline)

Hit a wall almost immediately. Two captures attempted:

| Capture | AP | Duration | Outcome |
|---|---|---|---|
| 1 | Nishit_Wifi (ch 1) | 34 s | `deauth_count: 0`, pcap had **1 packet**, aireplay timed out (30 s) |
| 2 | TL (ch 6) | 26 s | `deauth_count: 3`, pcap had 3470 packets, but only **M3 captured** |

Capture 1: aireplay-ng was hanging waiting for a beacon from the target BSSID. The pcap being empty meant `wlan-ap` wasn't actually seeing frames on channel 1 — driver / channel-pin race on the very first use of the radio. Subsequent uses inherited the working state from the first attempt, which is why capture 2 worked at the airodump level.

Capture 2: aireplay fired three times, 3470 packets of mostly beacons + data captured, but only M3. The smoking gun for **PMK caching** — when a client has recently associated, it caches the PMK and on the next reassociation both sides skip M1/M2, going straight to M3+M4. We caught M3 but not the rest.

Operator's pushback at this point: *"In a pen test I can't ask the client to forget the network."* Correct. The airodump + deauth approach is training-wheels.

---

## Checkpoint 4 — Session 07.5: swap to hcxdumptool

**Decision:** replace airodump with hcxdumptool for the focused capture flow. Recon's broad scan stays on airodump (Session 05) — that's the right tool for the AP/Client table.

Why hcxdumptool:
- **PMKID without a client.** Active-scan mode sends association requests to the AP; AP responds with M1 containing PMKID; captured. No client needed, no deauth needed. Defeats PMK caching entirely — we're targeting the AP-side derivation, not the client-side.
- **Captures EAPOL too** when a client happens to associate naturally. Strict superset of airodump for handshake purposes.
- **pcapng output** — modern format, supports per-packet annotations, hashcat's `hcxpcapngtool` converts to `.22000` format directly.
- **Quieter on the air** — no continuous deauth needed.

**Files added/modified in 07.5:**

- `app/tools/hcxdumptool.py` — new wrapper. `build_cmd(iface, output_path, channel, active=True)`. No BPF filter at the tool level — we lock channel and let the parser do BSSID filtering. hcxdumptool's `--bpfc` takes BPF *bytecode* (decimal from `tcpdump -ddd`), not human expressions, which would require shelling out — extra moving parts for marginal pcap size savings.
- `app/tools/handshake_detector.py` — added PMKID detection. `_m1_has_pmkid(eapol_bytes)` scans M1's Key Data for the `0xDD 0x14 00 0F AC 04` KDE marker. Per-pair `has_pmkid` flag; `is_partial` now also marks "PMKID alone" as crackable. `summarize_for_capture` rolls up `has_pmkid` at the AP level.
- `app/services/handshakes.py` — `start_capture(... , deauth=False)` (was True). Uses `hcxdumptool.build_cmd` instead of `airodump.build_cmd`. Output is `<timestamp>.pcapng` instead of `<prefix>-01.cap`. Index entries now include `tool: "hcxdumptool"`, `pcap_format: "pcapng"`, `has_pmkid`.
- `app/static/recon.js` — capture modal copy updated to explain PMKID approach; deauth checkbox unchecked by default. Status renderer shows `[PMKID]` badge when PMKID has been captured. Captures lists (both per-AP tab and top-level Recon card) show the badge.

**Deploy:** one `apt install -y hcxtools` on the Pi, restart pipineapple, done. hcxtools also gives us `hcxpcapngtool` which Session 08 will use for `.pcapng` → `.22000` format conversion.

---

## Checkpoint 5 — UX additions (operator-driven)

Operator feedback drove several useful additions mid-session:

- **"Captures" tab in the AP slide-out** — list prior captures per AP with status pill, M1-M4 dots, PMKID badge, deauth count, pcap size, timestamp, per-row + bulk delete.
- **Top-level "Captures" card on the Recon page** — same data but grouped by AP, viewable in one shot from the main scan view. Auto-refreshes when any capture finishes.
- **Live scan duration** in the Recon control row (carried over from S05, now ticks only while state == "running").
- **`connecting` state in the live/offline badge** — previously the badge flashed `OFFLINE` for ~200 ms on every page load before SocketIO connected; now it shows `connecting` (amber) instead, so a slow-AP-induced delay doesn't look like a broken connection.

---

## Bugs, gotchas, and the perf trail

This session was a long debugging arc. Documenting all of it because each one is a class of bug that will recur.

### A. PMK caching defeats deauth-and-wait

Already covered above. The fundamental motivation for moving off airodump for handshake capture.

### B. First-use radio race on `wlan-ap`

aireplay hung 30 s on the first capture because `wlan-ap` had just been put in monitor mode + pinned to a new channel — the driver needed a moment to actually start hearing frames. Fix in `start_capture`: 1.5 s settle delay after `iw set channel`, then verify with `iw dev <iface> info` that the iface is in monitor mode AND on the right channel before launching airodump. Fail loudly with a clear message instead of spending 30 s on an aireplay timeout.

### C. The 4 GB log file (round two)

Repeat of the Session 06 bug. JobManager's `stdout_path` was writing airodump's status output to disk; hcxdumptool does the same kind of stream. Fix is the same: `stdout_path="/dev/null"` for both tools, since the useful data is in the pcap.

### D. Pi locks up on stop_capture (twice)

First incarnation: the synchronous teardown blocked the HTTP request thread for 10+ seconds (job stop wait + final scapy parse + index write). On Werkzeug's limited thread pool this starved SocketIO polling, browser flipped to "offline". Fix: `stop_capture` returns instantly, spawns a daemon thread with `app.app_context()` push that does the actual work; emits `capture:status` SocketIO event with `ended=true` when done. Also skipped the redundant final pcap parse (the poller has been updating `last_status` every second — just snapshot that).

Second incarnation: app_context wasn't being pushed in the daemon thread (we hit this same bug for the networking restore thread in S04.7 — third strike now). The teardown crashed silently mid-stop because `_resolve_inject_iface` calls `current_app`. Daemon-thread runner now always wraps the work in `with app.app_context(): ...` and `finally`-blocks the state-to-IDLE so the badge never stays stuck.

### E. Orphan capture processes after Flask SIGKILL

If systemd SIGKILLs pipineapple (timeout during shutdown, etc.), the JobManager's atexit cleanup doesn't fire. airodump/hcxdumptool/aireplay children survive, get reparented to init. They keep writing pcap and consuming CPU. On the next pipineapple restart, the operator's UI is sluggish for no apparent reason because background processes are eating the radio. Diagnosis: `pgrep -af airodump aireplay hcxdumptool` after restart; orphans have PPID=1. Recovery: `sudo killall -9 airodump-ng aireplay-ng hcxdumptool`. Long-term fix on the to-do: process groups in JobManager + startup scan-and-kill of orphan capture tools.

### F. Captures with no UI (operator: "where do I see past captures?")

Initial Session 07 only had per-capture status in the live slide-out; once a capture stopped and the operator moved on, there was no way to see what had been captured. Added the per-AP "Captures" tab to the slide-out, then on subsequent feedback added the top-level Recon-page "Captures" card grouped by AP. Both share the same `/handshakes/list` endpoint (with optional `?bssid=` filter).

### G. Page loads taking 6-8 seconds

Mystery for a while — top showed the Pi idle, `time curl /login` was 51 ms locally. But the browser-side reported 9 s for `style.css`, 5 s each for `recon.js` and `socket.io.min.js`, etc. Diagnosed as the Realtek mgmt AP's throughput hitting ~3 KB/s effective per parallel request, combined with Werkzeug's `Cache-Control: no-cache` default forcing an ETag round trip on every request. Two mitigations: set `SEND_FILE_MAX_AGE_DEFAULT = 300` (5-minute cache, browser stops re-asking for unchanged assets); plus the operator should plug the Pi into Ethernet for dev work (the mgmt AP is for field use, not for the inner dev loop). Real long-term fix (nginx in front of Flask) is parked for Session 19.

### H. iw parser bleeding P2P-device mode into adjacent interface

Found while debugging unrelated symptoms. `iw dev` output includes "Unnamed/non-netdev interface" stanzas (brcmfmac's auto-spawned P2P-device for wlan0). Our parser only recognised `Interface NAME` as section breaks, so the P2P-device's `type P2P-device` line was being attributed to whichever wireless interface came right before it (typically `wlan-mon-2g`). Settings → Adapter Management consequently showed `wlan-mon-2g` in `p2p-device` mode. Fix: treat lines starting with `phy#` or `Unnamed` as section breaks that finalise the current interface.

---

## Session-wide findings

- **Tool choice matters more than careful orchestration.** We spent a lot of code making airodump-ng + deauth do something it's structurally bad at (capturing handshakes from cache-friendly clients). hcxdumptool does it natively in one call. Lesson: when the current approach keeps producing edge-case failures, consider whether you've chosen the right tool, not just whether the orchestration is right.
- **Real-pen-test reality forces realistic design.** "In a pen test I can't ask the client to forget the network" was the single most important piece of feedback in the session. Without it, we'd have shipped a feature that demos in the lab and fails in the field.
- **Async by default for any work that takes >100 ms.** Three separate places this session — recon `stop_scan`, handshake `stop_capture`, networking restore — needed the daemon-thread-with-app_context pattern. Worth adding a small helper.
- **PMKID caching, app_context-in-threads, orphans-after-SIGKILL, and stdout-to-disk-balloons are the four failure modes that keep recurring.** Each one bit us in S06 AND S07. The codebase needs them codified, not just documented. Possible Session 7.6: a small `app/util/runtime.py` with `async_with_context(app, fn)` helper, JobManager process-group cleanup, and a startup orphan-scan.

---

## Parked for later

- **Top-level Handshakes page** (Session 08 in the roadmap) — download as `.cap` and `.22000`, automatic-collection toggle that pulls EAPOL frames from any active recon scan. The capture engine and storage are now ready for it.
- **Crack action** (Session 09) — dispatch off-Pi to a Mac/Jetson over SSH. The Pi 5's CPU/GPU is way too slow for serious cracking; the platform's role is capture, not crack.
- **Process groups in JobManager** — kill the whole tree on shutdown, scan for orphans on startup. Bites us at least once a session right now.
- **`async_with_context` helper** in a new `app/util/runtime.py` — codify the "spawn daemon thread + push app_context + try/finally" pattern we keep re-implementing.
- **MFP downgrade attack / transition-mode confusion** — for WPA3-only APs, neither PMKID nor deauth works. Out of scope for Phase C; possibly Phase D or a separate research module.
- **`tcpdump -ddd` pipeline for BPF-filtering at hcxdumptool** — would shrink pcap size when capturing one BSSID on a busy channel. Not needed yet.

---

## What's now possible

- **PMKID capture in seconds** from any vulnerable AP without client cooperation — the realistic baseline for actual engagements.
- **Full 4-way capture as an opt-in** when both PMKID + full handshake are wanted (deauth checkbox in the capture modal).
- **Per-AP + cross-AP capture visibility** on the Recon page — slide-out tab for per-AP detail, top-level card for the all-APs view. Per-row + per-AP delete; metadata persisted in `index.json`.
- **Live capture status with M1/M2/M3/M4 + PMKID badge** so the operator sees progress in real time instead of waiting blindly.
- **Session 08 (top-level Handshakes page) and Session 09 (crack dispatch) are unblocked.** The capture engine, the pcap storage format (pcapng — hashcat-friendly via hcxpcapngtool), and the metadata index all match what those sessions need.
