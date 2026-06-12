# Session 16 — Nmap module (Phase F)

**Goal.** First real consumer of the S15 module loader: post-association recon.
Scan the Open-AP / PineAP clients, or the lab subnet after a PSK crack, for live
hosts / open ports / service versions, rendered as sortable tables. Lives
entirely under `app/modules/nmap_scan/` — no core changes beyond what the loader
already provides.

## What was built

- **`tools/nmap.py`** — the wrapper. Four scan profiles (`discovery` `-sn`,
  `quick` `-F -T4`, `services` `-sV -T4`, `scripts` `-sC -sV -T4`), always with
  `--host-timeout 120s -oX -`. Parses the XML with `xml.etree` into
  `{ip, mac, vendor, hostname, state, ports[]}` (open ports only). Stub mode
  returns three canned hosts so the Mac can exercise the full flow without nmap
  or a live subnet. `tools/__init__.py` re-exports the public API.
- **`service.py` — `NmapService`.** Runs one scan at a time in a background
  thread (an `-sV /24` takes minutes), tracks live status, keeps the last
  result set, emits `nmap:status` on completion. Target resolution for the two
  roadmap sources — **PineAP clients** (via `client_recon` leases) and the
  **lab subnet** — plus a custom target. **RFC1918-only guard**
  (`_is_private_target`) refuses public IPs and hostnames at both the service
  and route layers.
- **`routes.py` + `templates/nmap.html`.** Scan form (profile + target source +
  custom field), live status, and a **client-side sortable** hosts table
  (IP / hostname / MAC+vendor / open-port count / services). Inline JS keeps the
  module self-contained — it doesn't touch `app/static/`. Lab CIDR is read from
  pineap state when available, else `10.0.0.0/24`.
- **`module.toml`** — `name=nmap_scan`, `label=Nmap`, `url_prefix=/modules/nmap`.

## Console exercise

`nmap -sn`, `-F -T4`, `-sV -oX -` (why XML, why `--host-timeout`, why a
background thread), `-sC -sV` (default NSE category), and the gateway-IP vs
one-hop-away distinction. Captured in the Learning Centre `nmap-recon` section.

## Verification

`verify_s16.py` — 21/21: XML parse (open-ports-only, service detail), the
private-IP guard (allows RFC1918 / refuses public + hostnames + mixed),
install→restart→register, page render + scan form + sidebar nav, the scan
lifecycle in stub (route refuses public target 400, subnet scan runs to
completion, 3 hosts parsed), and PineAP-client target resolution from
`client_recon`. `py_compile` + inline-JS `node --check` clean. No regression in
S12.5/S13/S14/S15 suites.

## Design notes

- **Why a private-IP fence on a recon tool.** nmap is recon, not exploitation,
  so it doesn't use the (future) shared `target_guard`. But an unfenced scanner
  could trivially be pointed at the public internet, so the module enforces
  RFC1918/loopback/link-local only. When Phase H's `services/target_guard.py`
  lands, this can defer to it for consistency.
- **XML over stdout scraping.** `-oX -` + `xml.etree` is stable across nmap
  versions; the human output is not.
- **Restart-on-change still applies** — install Nmap on the Modules page, then
  `systemctl restart pipineapple` before it appears.

## Parked / next

- **S17 — MITM module (bettercap)** is the last Phase F session: ARP/DNS spoof
  against selected clients, live traffic/cred inspection, captive-portal serving.
- Nmap is the recon half of the kill chain that the candidate Metasploit phase
  (`docs/phase-H-metasploit-sketch.md`) exploits — its results feed RHOSTS.
- Still parked: S12.5 journal writeup (pending thorough captive test), S13 + S14
  hardware tests.
