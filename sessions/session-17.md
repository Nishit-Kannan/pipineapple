# Session 17 — MITM module (bettercap) — Phase F finale

**Goal.** Active man-in-the-middle as a drop-in module: ARP-spoof a selected
private target so its traffic routes through the Pi, optionally DNS-spoof, and
surface the live DNS / HTTP / credential stream. Scope decided with Nishit:
**core MITM only** (ARP + DNS spoof + inspection) — the roadmap's
captive-portal-builder is dropped because S12.5 already serves portals.

## Target fence — the hybrid (decided this session)

MITM intercepts *other devices'* traffic, so the fence matters more than
anywhere else. Chosen posture:

- **RFC1918-only** — public targets are always refused.
- **Flexible source** — target can be a custom private IP/CIDR, a PineAP
  client lease, or an nmap-discovered host (so the uplink hosts from S16 are
  targetable, which a clients-only fence would have blocked).
- **Default OFF + typed `mitm` confirm** — same ethics gate as
  pineap / phishing / active.
- Documented limit: the fence stops public-internet interception but **can't
  verify ownership** — lab-only discipline is still on the operator. (Came up
  via Nishit's IoT question — IoT devices are valid private targets and often
  the most fruitful, since they speak plaintext/legacy protocols.)

## What was built (`app/modules/mitm/`)

- **`tools/bettercap.py`** — `build_argv` / `build_caplet` assemble a
  non-interactive bettercap invocation (`-iface … -no-colors -eval "net.probe
  on; set arp.spoof.targets …; arp.spoof on; net.sniff on; [dns.spoof…];
  events.stream on"`). `parse_event_line` strips ANSI and buckets each stdout
  line into **dns / http / cred / info** (creds detected by keyword across any
  sniff tag; unmatched-but-interesting → info). `is_available` checks the
  binary. `STUB_EVENT_LINES` exercises every bucket on the Mac.
- **`service.py` — `MitmService`** — `start()` enforces the typed confirm +
  RFC1918 fence + auto-detects the interface (the Pi iface whose subnet
  contains the target — wlan-ap for rogue clients, eth0 for uplink), launches
  bettercap as a **killable Popen**, and a reader thread streams stdout →
  `parse_event_line` → bounded per-bucket lists + SocketIO emit. `stop()`
  SIGTERMs it (restoring ARP). `candidate_targets()` merges PineAP clients +
  nmap results for the picker. Stub mode replays the canned lines then idles
  until stopped.
- **`routes.py` + `templates/mitm.html`** — target picker (clients + nmap
  hosts grouped, or custom), interface override, DNS-spoof toggle + domains +
  redirect IP, typed-`mitm` arm field (Start disabled until it matches), Stop,
  live status (+ the exact bettercap command), and four event panels:
  **Captured credentials**, DNS, HTTP, and an Event log. Self-contained inline
  JS (poll `/events` every 2s + `mitm:event`/`mitm:status` sockets).
- **`module.toml`** — `requires = ["bettercap"]`, so the Modules page flags it
  missing and offers one-click apt install.

## Console exercise

The bettercap `-eval` caplet (net.probe / arp.spoof / net.sniff /
events.stream), optional dns.spoof layering, and the fence. In the Learning
Centre `mitm-bettercap` section.

## Verification

`verify_s17.py` — tool (argv/caplet shape, dns toggle, event bucketing incl.
the password line → cred), service (private fence allow/refuse, confirm gate,
public refusal, stub start → events in dns/http/cred buckets, double-start
guard, stop → "stopped by operator"), and routes (install→restart→register,
page + confirm/tables render, sidebar nav, start refused without confirm 400,
start with confirm → events surfaced, stop). All green; module suites
(S15/S16/S17) pass; `py_compile` + inline-JS `node --check` clean.

## Design notes / to refine on hardware

- **Event parsing is approximate.** bettercap's stdout text varies by version;
  the parser keys off `[tag]` prefixes + keyword hints and falls interesting
  lines through to the Event-log bucket. Expect to tune the regexes against
  real output (same "refine on hardware" path as evil_wpa/captive).
- **bettercap REST API** was considered for cleaner JSON events but skipped to
  avoid standing up its HTTP listener + creds; stdout streaming is simpler and
  keeps the module self-contained. Revisit if parsing proves too brittle.
- ARP spoof needs the target on the **same L2 subnet** as the Pi; cross-subnet
  / cross-VLAN targets aren't reachable.

## Phase F is now complete

S15 (module loader) · S16 (nmap) · S17 (MITM) all built + stub-verified.
Remaining roadmap: Phase G — S18 (Settings: networking/wifi/advanced/censorship)
and S19 (production polish: auth/HTTPS/systemd/web-terminal). Candidate Phase H
(Metasploit) sketched in `docs/phase-H-metasploit-sketch.md`.

## Parked

Hardware tests outstanding: S15 module install, S17 MITM end-to-end (incl.
bettercap event-parser tuning), plus the earlier S12.5 thorough test and
S13/S14. nmap (S16) hardware-tested ✓.
