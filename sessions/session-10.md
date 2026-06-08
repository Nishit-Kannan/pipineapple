# Session 10 — PineAP foundation (pool store, mode state, Settings tab)

**Date:** 2026-06-08
**Phase:** D — first PineAP session. Foundation for the rogue-AP engine: SSID pool, mode state machine, Settings tab, auto-population hooks. No actual broadcast yet — that arrives in S11 with the hostapd lifecycle. S10 lands everything *around* hostapd so S11 is a focused single-concern change.
**Goal:** Sidebar PineAP entry → Settings tab with mode radios, broadcast/capture toggles, an SSID pool table that auto-fills from recon scans and probe-request observations, and a passive-mode Start path that goes through an ethics-confirm modal.

---

## Checkpoint 1 — Concepts: hostapd, PineAP modes, SSID pool, MFP

Same hostapd daemon we already use for the management AP, different config: on `wlan-ap` instead of `wlan0`, primary BSS plus optional `bss=…` stanzas for multiple SSIDs per radio. Chip cap is ~4-8 simultaneous BSSes per Alfa; the Hak5 approach for larger pools is to cycle the SSID via `hostapd_cli set_ssid` every few hundred ms. We'll do the cycling approach in S11 if the pool exceeds 8.

PineAP's three operation modes, kept faithful to the Hak5 names so the labels carry knowledge:

- `passive` — configured, hostapd silent. Stage settings without making airspace noise.
- `active` — broadcasting the pool as fake beacons. Every device in range sees the pool.
- `advanced` — `active` plus Karma probe responses for *any* SSID a client probes for, not just pool entries. The most dangerous mode against saved open networks.

S10 wires the full state-machine for all three but only `passive` actually starts — `active`/`advanced` persist the setting but Start refuses with a clear "wait for S11" message.

The pool design — one record per SSID with `{ssid, source, first_seen, last_seen, observed_count, pinned, hidden}` — is shaped by the two big realities of running this for any length of time: auto-collection floods the pool fast (one recon scan can add 30+ SSIDs; one phone walking past can add 10-15 directed probes), and operator curation has to be cheap. Pinning protects entries from any future auto-eviction; hiding lets you exclude an entry from broadcast without losing its accumulated timestamps. Source is informational only ("why is this in my pool?"). Validation: 32-byte UTF-8 limit for everyone (the 802.11 cap), but manual adds gate on printable ASCII while auto-population (recon/probe) bypasses that gate because real-world SSIDs include emoji + CJK and dropping them silently is worse than storing them.

MFP / 802.11w is the structural defense and worth understanding because it's the reason this attack class has steadily lost potency. Beacons + management frames are signed using a key derived during the original 4-way handshake; a Karma response claiming the same SSID won't have the right key and the client's supplicant rejects it. WPA3 mandates MFP; WPA2-MFP is increasingly common. Karma still works against open networks (no MFP path), legacy WPA2 without MFP, and phones with explicit Auto-Join on for saved open networks.

---

## Checkpoint 2 — Build

**Files created:**

- `app/services/pineap.py` — `PineAPService` singleton with the pool store (`$DATA_DIR/pineap_pool.json`) + mode state (`$DATA_DIR/pineap_state.json`). `PineAPMode` enum (off/passive/active/advanced) as a str-enum so it round-trips through JSON. Public API: `get_state`, `set_mode`, `set_broadcast`, `set_capture`, `list_pool` (pinned-first + last-seen-desc sort), `add_ssid` (idempotent — existing entries get last_seen + observed_count bumped + source-promoted recon→manual), `remove_ssid`, `set_pinned`, `set_hidden`, `clear_pool` (with `include_pinned` flag), `start`, `stop`. Module-level `auto_add_from_recon` / `auto_add_from_probes` helpers wrap `add_ssid` in try/except so auto-population can't break the calling code path.
- `app/routes/pineap.py` — 13 routes: `/pineap/` (page), `/pineap/state`, `/pineap/mode`, `/pineap/broadcast`, `/pineap/capture`, `/pineap/start`, `/pineap/stop`, `/pineap/pool` (GET/POST), `/pineap/pool/<ssid>` (DELETE), `/pineap/pool/<ssid>/pin`, `/pineap/pool/<ssid>/hide`, `/pineap/pool/clear`. Notifications service called on every operator action.
- `app/templates/pineap.html` — tab bar (Settings live, Open SSID/Evil WPA/Impersonation/Filtering/Clients/Access Points as disabled placeholders pointing to their sessions). Statcards (engine state + pool counts + broadcast/capture state). Mode radios + toggles + Start/Stop. Operator-notes card with ethics + deny-list reminder. Pool table with add form, pin/hide checkboxes, remove buttons, refresh + clear actions. Ethics modal copying the S06 deauth pattern (type `pineap` to confirm).
- `app/static/pineap.js` — bootstrap-or-listen at the bottom (TDZ-safe, lesson from S08). State + pool reloaders, mode/broadcast/capture handlers, ethics modal open/close + confirm gate, pool add/pin/hide/remove/clear handlers.

**Files modified:**

- `app/__init__.py` — register the pineap blueprint.
- `app/templates/base.html` — enable PineAP sidebar entry (was disabled placeholder), add `pineap.js` to script list.
- `app/services/recon.py` — `_tick` end-of-pass: pull every merged-AP SSID via `pineap.auto_add_from_recon`, pull every directed probe (probed_essids minus the empty broadcast probes) via `pineap.auto_add_from_probes`. Wrapped in try/except so a pool-write fault can't break recon.

---

## Checkpoint 3 — Verification

Ten-check service-level test passed: defaults, validation (length + manual-ASCII gate vs auto-source pass-through), idempotent re-add, source promotion (recon→manual), pinning + sort order, remove + clear-with/without-pinned, mode transitions, start gating (passive succeeds, active/advanced/off refuse with clear messages), stop, disk persistence across instances.

Route-level test against the Flask test client: all 13 routes wired, every code path exercised end-to-end, page renders with all expected markers (`tab-settings`, `pa-pool-tbody`, `pa-ethics-modal`, `pineap.js`, `PineAP`), validation rejects oversized + non-ASCII manual SSIDs at the route boundary, mode gating refuses active/advanced as planned, 404s on missing-SSID pin/hide/delete.

Auto-population test: drove one recon `_tick` against stub-mode data, pool went from 0 → 6 entries (4 from recon, 2 from probe requests). Second tick kept count at 6 but bumped observed_count — idempotency confirmed.

`node --check` on `pineap.js`: parse OK.

Pi-side deploy/verify deferred to S11 (we want hostapd actually broadcasting before doing a real test on hardware — otherwise the only thing to verify is the UI, which the Flask test client already covers).

---

## Checkpoint 4 — Notes for S11

The clean path from here:

- `pineap.start()` currently no-ops in passive and refuses in active/advanced. S11 replaces this with: pick channel, render hostapd config (primary BSS + extra `bss=` stanzas for up to 7 more from the pool), `hostapd.write_config`, launch via `JobManager.start_job`, persist the job_id, set up the dnsmasq DHCP server on the rogue subnet, push the rogue subnet into `access_control` deny-list automatically (one less thing for the operator to forget).
- BSSID strategy decision: single fixed BSSID vs per-SSID pseudo-random (hash of `(salt, ssid) → 6 bytes` with the locally-administered bit set). Lean toward the pseudo-random approach — more convincing, and the stability across restarts is free.
- Pool overflow: when `list_pool()` returns >8 (or whatever the chip's BSS cap is on `wlan-ap`), switch from per-BSS broadcasting to `hostapd_cli set_ssid` rotation every ~500ms. Pinned entries always get a permanent BSS; rotation cycles through unpinned.
- Advanced mode (Karma) needs Scapy probe-response injection on top of hostapd — hostapd alone won't reply to probes for SSIDs it doesn't advertise. Plan: a Scapy thread on `wlan-ap` (or a 4th radio if we get there) sniffing probe requests and crafting matching probe responses with the primary BSSID.

Operator-facing knobs that almost certainly want UI in S11/S12 but were out of S10 scope:

- Channel + band selection for the rogue AP (currently hardcoded `wlan-ap` interface, channel comes from hostapd default).
- Rogue DHCP subnet config (we'll default `10.0.0.0/24` but expose).
- Per-BSS encryption — Open vs WPA2-PSK with a chosen passphrase (for "clone a target AP" in S12).

---

## What's next

S11 — Open SSID tab. First actually-functional rogue AP: static IP on `wlan-ap`, `dnsmasq` for DHCP+DNS, `hostapd` in `auth_algs=1` with `wpa=0` (open). Plus the Karma Scapy injector for Advanced mode. The pool + mode state machine built here will drive what hostapd actually advertises.
