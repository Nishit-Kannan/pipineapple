# Session 08 — Top-level Handshakes page (and 08.1 cleanup + 07.7 backlog)

**Date:** 2026-06-07 → 2026-06-08
**Phase:** C — second offensive-output session. Take the captures S07 produces and give the operator a single dedicated page to browse, download, and prepare them for off-Pi cracking (Session 09).
**Goal:** Sidebar entry for **Handshakes** that lists every persisted capture across all APs and capture sources, with per-row download buttons (raw pcap + hashcat `.22000` format). The conversion to `.22000` happens on demand via `hcxpcapngtool` and is cached so the second download is instant.

Layered onto this session: the **07.7 backlog** (hcxdumptool turned out to not work on the operator's hardware) and an **08.1 cleanup** (removed the redundant Recon-page Captures card now that the top-level page exists, plus added a per-AP client count to the recon table).

---

## Checkpoint 1 — Concepts: .22000 format + hcxpcapngtool + source labelling

**Decided:**

- **`.22000` is hashcat mode 22000's universal WPA hash format.** One line per crackable target. Format: `WPA*<type>*<mic_or_pmkid>*<MAC_AP>*<MAC_STA>*<ESSID_hex>*<ANONCE_hex>*<EAPOL_hex>*<flags>`. Type 01 = PMKID, type 02 = EAPOL handshake. A single pcap commonly produces both types.
- **`hcxpcapngtool` is the format-agnostic converter.** Reads both legacy libpcap `.cap` (airodump) and modern pcapng (hcxdumptool); outputs `.22000`. Stdout summary reports `PMKIDs written`, `EAPOL pairs M1M2 / M1M3 / M2M3`. Returns 0 even when nothing was extracted, so we judge success by counts + output-file size.
- **Serve both raw pcap and `.22000`.** Different consumers want different formats — Wireshark / re-conversion want the raw bytes; hashcat just wants the `.22000`. Cache the conversion next to the source pcap (`<base>.22000`) so repeated downloads don't re-walk the file.
- **Source labelling: "Recon Capture" for S07, future expansion.** Phase D's PineAP rogue-AP captures (S12) will tag entries with `source: "Evil WPA"` / `"Evil Enterprise"`. For S08, only "Recon Capture" exists; column is informational until then.
- **Crack action stays parked for S09.** The Handshakes page stops at "here's your `.22000` file"; the cracking dispatcher (SSH to Mac/Jetson + remote hashcat + progress streaming) is its own session.

---

## Checkpoint 2 — Build

**Files created:**

- `app/tools/hcxpcapngtool.py` — `convert_to_22000(pcap_in, output_22000) -> (ok, msg, counts)`. Regex-parses hcxpcapngtool's stdout for PMKID + EAPOL counts; treats no-targets as `ok=False` with a clear message so the route can return 404 instead of a 0-byte file. Stub mode writes a synthetic `WPA*01` line for Mac dev.
- `app/templates/handshakes.html` — full-width table layout, server pre-renders the initial set, JS replaces tbody from `/handshakes/list`. Empty + error placeholders always in the DOM (hidden by default) so the JS can toggle visibility without depending on Jinja-time conditionals (fixed a silent-blank-page bug from the first cut).
- `app/static/handshakes.js` — table renderer, per-row delete + downloads (anchor tags to the download endpoints with Content-Disposition: attachment), bulk select + bulk delete, refresh button, `capture:status` SocketIO subscription for live refresh when captures finish.

**Files modified:**

- `app/services/handshakes.py` — `get_capture_record(id)`, `resolve_pcap_path(c)`, `resolve_or_build_22000(c)` (with mtime-based cache invalidation).
- `app/routes/handshakes.py` — `GET /handshakes/` (page), `GET /handshakes/<id>/download/pcap`, `GET /handshakes/<id>/download/22000`. Page route uses the existing `list_captures()` for initial render; downloads use `send_file` with auto-generated filename (BSSID-prefixed).
- `app/templates/base.html` — Handshakes sidebar entry enabled (was greyed out with the "Phase C" tooltip), `handshakes.js` added to the script list.
- `app/config.py` — `SEND_FILE_MAX_AGE_DEFAULT = 300` so static files cache 5 minutes (Werkzeug's default `no-cache` was forcing an ETag round trip on every JS/CSS request, killing perceived page-load speed over the slow mgmt AP).

---

## Checkpoint 3 — The hcxdumptool failure (Session 07.7)

Tested the S07.5 capture flow with hcxdumptool. Job manager logged it starting and exiting 1.6 seconds later with `rc=0` — no pcap file written. Manual run of the same command revealed:

```
Requesting physical interface capabilities. This may take some time.
Please be patient...
failed to arm interface
PACKET_STATISTICS failed
2 ERROR(s) during runtime
Possible reasons:
 driver is broken
 driver is busy (misconfigured system, other services access the INTERFACE)
0 Packet(s) captured by kernel
```

**Root cause: hcxdumptool 6.3.5 + mt76x2u driver + Pi OS Trixie kernel 6.12 are incompatible.** Known upstream issue. Tested variants (`--rds=1`, `-F`, `--rcascan=active`) all failed the same way. Not fixable from our side without a kernel or hcxdumptool upgrade.

**Mitigation (S07.7):** flip the default capture tool from hcxdumptool to airodump-ng in three places — service default constant, route default body parameter, and the modal's radio button. Updated copy in the modal: airodump-ng is now "works today"; hcxdumptool is "currently incompatible" with an explanatory note. Both options remain available so the operator can re-test hcxdumptool when the upstream fix lands or on different hardware.

**Operational consequence:** for the operator's hardware, the realistic capture flow is now airodump + deauth. PMKID extraction (the whole reason we added hcxdumptool in S07.5) is unavailable. Captures still produce full or partial 4-way handshakes when a client (re)associates, which hashcat 22000 can crack.

---

## Checkpoint 4 — The M1+M3 classifier fix

While testing real captures, noticed that operator-recorded captures with `messages_seen: [3]` (M3 alone) were correctly labeled "no handshake" — but a capture with M1+M3 would *also* be wrongly labeled "no handshake" by my detector. hashcat 22000 actually accepts three EAPOL pair types — M1M2, M1M3, M2M3 — not just M1M2.

M1+M3 is the common signature of **PMK caching**: the client uses a cached PMK, both sides skip M1+M2 (no new ANonce derivation), and the AP issues M3 to install the key. We do see M1 in this case (the AP re-sends it as part of the rekey trigger).

**Fix in `handshake_detector.py`** — added the `{1, 3}` set check alongside `{1, 2}` and `{2, 3}` in both `detect_handshakes` and `summarize_for_capture`. Captures that previously showed "no hs" but had M1+M3 now correctly show "partial" and yield a downloadable `.22000` via `hcxpcapngtool`.

---

## Checkpoint 5 — Session 08.1 cleanup

After S08, two things felt redundant:

- **Recon-page Captures card** — the dedicated Handshakes sidebar page does the same job better. The card was the source of the long debug arc this session (operator was looking at the wrong place, then we found a JS rendering bug). Removed it; the per-AP "Captures" tab inside the AP slide-out stays for in-flow visibility while doing recon on one specific AP.
- **Recon APs table lacked a clients column** — no quick way to see which APs have active clients vs beacon-only. Added a `client_count` field per AP record (computed in `_tick` from the merged_clients dict, after the SSID-enrichment pass), rendered as bold when >0 (interesting target) and muted "0" otherwise. Column is sortable.

Net result: -90 lines of JS (the `loadCapturesCard` block), +1 useful column, cleaner Recon page.

---

## Bugs found, fixed, and worth remembering

This session's debugging trail was as long as S07's. All real, all instructive.

### A. Static file caching killed perceived speed

`Werkzeug` defaults to `Cache-Control: no-cache` for static files (sensible in dev — see changes immediately). Over the Realtek mgmt AP at ~3 KB/s per parallel request, every page load did 6 ETag round trips and stacked up to 6-8 seconds. Fix: `SEND_FILE_MAX_AGE_DEFAULT = 300`. After the first slow load, the browser caches everything for 5 minutes. Hard-refresh remains the way to force a re-fetch during dev.

### B. hcxdumptool exits in 1.6s with rc=0

Already covered in Checkpoint 3. The key diagnostic was the journal:
```
started job ... cmd=['hcxdumptool', ...]
... 1.6 seconds later ...
job ... exited rc=0 status=completed
```
rc=0 looked like success, hiding the real failure (which only manual run surfaced).

### C. UI rendering bug — silent blank page

First cut of `handshakes.html` had the empty-state message and the table conditionally rendered by Jinja based on server-side capture count. If JS later fetched and got an empty list (or a fetch error returned no captures), the JS tried to `empty.hidden = false` on a null element (the message wasn't in the DOM because server saw N>0 at render time). Silent no-op, page showed nothing. Fix: always render the empty message + error message elements (hidden by default), let JS toggle them. Added an explicit error message element + `renderError(msg)` so transient fetch failures show "Could not load captures: HTTP 5xx" instead of looking like a JS hang.

### D. Temporal dead zone in handshakes.js

The most "JS-specific" bug of the session. I placed a `bootstrap()` fallback at the top of the IIFE that called `init()`, which used `$` — a `const` declared further down. With `defer` scripts, `document.readyState` is often past "loading" by the time the script body executes, so the else-branch fired immediately and `bootstrap → init → $(...)` threw `ReferenceError: Cannot access '$' before initialization`. The `addEventListener` branch worked by accident (event fires after the whole IIFE finishes initialising). Fix: move the bootstrap-or-listen logic to the very **bottom** of the IIFE, after all const + function declarations.

**Lesson worth keeping**: when you split a script into "set up listeners" + "define helpers" + "run things", the runner has to go last. Function declarations are hoisted; `const` / `let` are not. Mixing them with `defer` (which can run when readyState is "interactive" or "complete") creates this exact trap.

### E. Recon-page captures card "not loading"

Misdiagnosis story. Operator reported "captures not loading in the UI" for hours. I assumed they meant the new Handshakes page. They actually meant the Recon-page Captures card. The Recon-page card was rendering fine for me on stub data but their hardware had its own state issues (mostly the orphan-airodump symptoms from S07 plus the hcxdumptool failure surfacing as empty pcaps). Resolution was the 08.1 cleanup: remove the redundant card, point them at the (now actually-working) Handshakes page.

### F. wlan-ap MAC randomization on `iw dev del` + add

Earlier in the session I had the operator run `iw dev wlan-ap del` followed by `iw phy phyN interface add wlan-ap type managed` to test hcxdumptool with a clean iface. The recreated netdev got a fresh randomized MAC (mt76 quirk on some kernel versions) instead of the EEPROM MAC. udev rule no longer matched. The operator's name persisted (we explicitly named it `wlan-ap` in `iw phy interface add`) but the MAC was different until next physical unplug/replug. Cosmetic, but added noise to debugging — when the iface MAC didn't match the udev rule I briefly thought the operator had swapped adapters.

---

## Session-wide findings

- **Tool incompatibility forced a tactical retreat.** hcxdumptool was supposed to be the long-term answer to the "client cooperation" problem. The mt76/kernel/hcxdumptool combination didn't work on the operator's hardware. The pragmatic answer (flip default to airodump-ng, keep hcxdumptool selectable for future re-test) is the right call — better to ship the working flow as the default and surface the incompatibility honestly than to push operators down a path that fails silently.
- **Working flow needs to be the default.** I had hcxdumptool as default because it was the better tool *in theory*. After 07.7, airodump-ng is default because it works *in practice* on the operator's hardware. Defaults should reflect what works, not what's theoretically best.
- **Silent blank pages are the worst UX failure mode.** Better to show a loud error than to render nothing. The error-surfacing fix in `handshakes.js` (renderError with the actual HTTP/network message) is small but high-leverage.
- **TDZ + `defer` is a foot-gun worth a project convention.** All future IIFE scripts should put the bootstrap call last, after declarations.
- **The hashcat 22000 format is more permissive than I'd assumed.** M1+M3 cracking works; my detector was being too strict. Worth a re-test of every "no hs" entry in the index — some might actually be partial crackable.

---

## Parked for later

- **Crack dispatch (Session 09)** — `.22000` file in hand, next step is sending it to a configured Mac/Jetson over SSH and streaming hashcat progress back. Per-job UI showing speed / ETA / cracked PSK on success.
- **Automatic-collection toggle** that scans the recon pcaps for EAPOL frames and auto-adds anything crackable to the handshakes index as `source: "Recon Scan"`. Was in the S08 roadmap but deferred — the explicit-capture flow plus the per-AP slide-out covers the same ground without the dedup logic.
- **PMKID story for mt76 hardware** — when hcxdumptool gets a fix, or when a different capture tool emerges that works on this driver, revisit. Until then this hardware is EAPOL-only.
- **Top-level "delete all captures" / quota** — the index grows unboundedly. Eventually want a cap (oldest evicted), an admin "delete all" button, and disk-usage display. Not urgent.
- **JobManager process groups + orphan cleanup at startup** — still parked from S07. Will bite again when systemd SIGKILLs pipineapple.
- **`async_with_context` helper** in `app/util/runtime.py` — still parked from S07. Codify the daemon-thread-with-app_context pattern.

---

## What's now possible

- **A single dedicated page for every captured handshake** across all APs. Operator opens sidebar → Handshakes → sees what they have.
- **Downloads work**. Raw pcap for forensics, `.22000` for hashcat. The `.22000` conversion is cached so the second download is instant; the operator can pull, send to their cracking machine, and crack — all the operator-side workflow that Session 09 will automate is already manually possible today.
- **Recon page is cleaner and more informative** — Captures card removed (was duplicating the Handshakes page anyway), per-AP client count added (highlights interesting targets).
- **M1+M3 captures are correctly labeled** — the PMK-caching common case is now crackable from the UI instead of silently mis-classified.
- **Capture defaults to the tool that works** — airodump-ng + deauth produces real handshakes on this hardware. hcxdumptool stays selectable for re-test when the upstream fix lands.
- **Sessions 09 (cracking dispatch) and the eventual Phase D PineAP captures are unblocked.** The storage layout, source labels, and download endpoints already accommodate the future capture sources without changes.
