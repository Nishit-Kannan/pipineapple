# Session 11 — Open AP + Karma/Mana + captive sentinel + client recon

**Date:** 2026-06-08
**Phase:** D — second PineAP session. First one that actually makes radio waves: real hostapd lifecycle, real DHCP, real probe-response injection, a captive-portal sentinel that lets victim phones treat the rogue as "real internet", and a dnsmasq-log parser that enriches every connected client with OS fingerprint + DNS query history.
**Goal:** Sidebar PineAP → Open SSID tab → operator configures primary SSID + channel, hits Start (with ethics confirm), beacons go up on `wlan-ap` advertising the primary plus extras from the pool, victim phones associating get DHCP leases, captive-portal probes succeed, dnsmasq's verbose log streams into a per-client view with OS guess and recent DNS queries. Advanced mode adds Karma/Mana probe responses for pool SSIDs.

---

## Checkpoint 1 — Concepts: open AP bring-up, dnsmasq, Karma/Mana, probe-response

Same bring-up sequence as the management AP (S04.6) but on `wlan-ap`: `rfkill unblock` → `nmcli unmanage` → `ip addr add 10.0.0.1/24` → `ip link up` → dnsmasq → hostapd. Reverse on stop.

hostapd open mode is just `auth_algs=1` with no `wpa=` lines. Multi-SSID via `bss=<iface>_<n>` stanzas, each with its own `ssid=` and `bssid=`. Chip cap is around 4-8 BSSes per radio on the mt76x2u Alfas; we cap at `DEFAULT_MAX_BSS=8` defensively. Per-SSID BSSIDs are deterministic — `BLAKE2b(salt || ssid)[:6]` with the locally-administered bit forced on. Same SSID always gets the same MAC across reboots (returning victims see a familiar BSSID); different SSIDs get different MACs (no shared BSSID tell).

dnsmasq runs with `log-dhcp` + `log-queries` + `log-facility` pointed at a specific file, not syslog (so the tailer doesn't need root syslog perms). The DHCP exchange leaks: client MAC + IP + hostname (option 12) + the parameter request list (option 55, a stable per-OS fingerprint) + vendor class (option 60). DNS queries leak just source-IP + name; we correlate to MAC via the dnsmasq lease file. Forward DNS to 1.1.1.1 + 8.8.8.8 so the captive-portal probe succeeds end-to-end.

Captive-portal sentinels: every modern OS hits a known URL the moment it gets a lease. iOS → `/hotspot-detect.html` (expects `Success` HTML), Android → `/generate_204` (expects 204), Windows → `/connecttest.txt` (expects `Microsoft Connect Test`). If the probe succeeds with the right body, the OS treats the network as healthy and lets app traffic flow. We answer truthfully so association looks normal, and log the User-Agent header — a better OS-version fingerprint than DHCP option 55. (S17 will add a "lie" toggle to force the OS into captive-portal mode.)

Karma vs Mana: classical Karma replies to *every* directed probe request. Mana (sensepost refinement, and our choice from S11 scoping) only replies to probes whose SSID is in the curated pool. Bounded collateral, operator-curated targets. Karma can't ride hostapd alone — hostapd only responds to probes for SSIDs it advertises. We run a parallel Scapy sniffer on `wlan-mon-5g` (recon paused while Karma is up), and on every `Dot11ProbeReq` whose SSID is in the pool, inject a `Dot11ProbeResp` claiming the SSID with the platform's primary BSSID. Locked to hostapd's channel — accept that we only catch probes the client happens to send on that channel during its scan; in practice we catch one within seconds because phones probe frequently.

Auto deny-list: on Start, push `10.0.0.0/24` into the management-access deny-list. On Stop, remove. Means a victim phone that auto-joins the rogue can't reach the management UI on wlan0 even if they wanted to.

---

## Checkpoint 2 — Build

**Files created:**

- `app/services/captive_sentinel.py` — `CaptiveSentinelService` singleton. Threading-HTTP server on the gateway IP (Mac dev: `127.0.0.1:8081`). Sentinel response table for Apple / Android / Windows / Firefox endpoints. Per-request log with MAC resolution via the dnsmasq lease file. Persisted to `$DATA_DIR/pineap_probes.json`. Idempotent start/stop.
- `app/services/client_recon.py` — `ClientReconService` singleton. Tails dnsmasq's log file in a daemon thread; parses DHCP discover/request/ack/options/vendor lines and DNS query lines. Maintains a per-client record: MAC, IP, hostname, OS fingerprint (longest-prefix-match against a built-in table covering iOS, macOS, Android, Windows, Linux, IoT), DNS query history. Persisted to `$DATA_DIR/pineap_clients.json`. Live SocketIO events: `client:upsert`, `client:query`.
- `app/services/karma.py` — `KarmaService` singleton. Scapy sniff loop on the karma interface (`wlan-mon-5g` by default). For each Dot11ProbeReq, checks pool membership (Mana variant), rate-limits per `(client_mac, ssid)` over a 30s window, crafts and injects a Dot11ProbeResp with the platform's primary BSSID. Stats counters for the UI. Stub mode for Mac dev (no scapy injection but lifecycle works).

**Files modified:**

- `app/tools/hostapd.py` — `render_config` extended with `extra_bsses`, `primary_bssid`, `hidden`. New `bssid_for_ssid(ssid, salt)` for deterministic per-SSID MACs. `DEFAULT_MAX_BSS = 8` exposed. Backward-compatible with management AP usage.
- `app/services/pineap.py` — new state fields: `primary_ssid`, `channel`, `hw_mode`, `bssid_salt` (auto-generated `secrets.token_hex(16)` on first save), `subnet`, `gateway_ip`, `dhcp_range`, `karma_iface`, plus job-id fields for hostapd/dnsmasq + running flags for karma/sentinel. State migration: `_load` merges defaults so older state files don't crash. New `set_ap_config` method (refuses while running). `start()` rewritten — for `active`/`advanced` modes runs the full bring-up: nm unmanage → ip addr → ip link up → dnsmasq → hostapd → sentinel → client_recon tailer → deny-list +subnet → (karma + recon pause if advanced). `stop()` reverses every successful step, best-effort, with per-step logging.
- `app/routes/pineap.py` — five new routes: `POST /pineap/ap-config`, `GET /pineap/clients`, `GET /pineap/clients/<mac>`, `POST /pineap/clients/clear`, `GET /pineap/probes`, `GET /pineap/karma/stats`. Total 19 PineAP routes now.
- `app/templates/pineap.html` — Open SSID tab enabled (was disabled placeholder). AP config form (primary SSID + channel + band + hidden), Captive-portal probe log card, Connected Clients table with expand-on-click for the DNS query history.
- `app/static/pineap.js` — Open SSID tab handlers: save AP config, reload clients/probes, render clients (with expandable DNS history detail row), live coalescing of SocketIO `client:upsert` + `client:query` events at ~2Hz max.

---

## Checkpoint 3 — Verification

Service-level tests, all in stub mode:

- **hostapd extensions**: backward-compat with mgmt AP usage preserved, open mode renders without `wpa=` lines, hidden SSID gets `ignore_broadcast_ssid=1`, multi-BSS lays out correctly with primary BSSID override + three extras + one hidden, `bssid_for_ssid` is deterministic + salt-dependent + ssid-dependent + locally-administered bit set + multicast bit clear, missing-bssid on an extra raises a clear `ValueError`.
- **captive sentinel**: real iOS / Android / Windows / Firefox probes get the correct responses (200+Success HTML / 204 / 200+Microsoft Connect Test / 200+canonical), unknown paths 404, UA + path + match-label all logged, persistence across restarts, idempotent start.
- **client_recon**: OS fingerprint table matches expected for iOS / macOS / Android / Windows / Linux / IoT shapes; `_extract_opt55` pulls numeric codes out of dnsmasq's verbose format; full DHCP exchange (multi-line discover→request→options→ack) parses cleanly to a populated client record; DNS queries correlate to the right MAC via source-IP; persistence across instances; tailer picks up appended lines including the realistic dnsmasq emission order. Caught one test-only bug where my synthetic log had `dhcp-ack` *before* `requested options` — real dnsmasq emits the other way; parser correctly didn't extract opt55 from a malformed sequence.
- **karma**: probe-response frame is well-formed (73 bytes, type/subtype 0/5, addr1/2/3 correct, beacon_interval 100, cap 0x0021, SSID IE + basic rates + DS Param Set + extended rates IEs all in the right order); pool-membership check fails closed without app context; lifecycle idempotent (start/stop/restart on different channel).
- **pineap lifecycle**: full active mode start runs every step in order — nm unmanage, ip addr, ip link, dnsmasq launch, hostapd launch (with the multi-BSS config rendered as expected: primary `RogueAP-Test` plus three pool extras, pinned-first, hidden excluded), sentinel up, client-recon tailer started, deny-list += `10.0.0.0/24`. Advanced mode additionally pauses recon, starts karma on `wlan-mon-5g` with the right channel + primary_bssid. Stop tears down everything in reverse — karma stop, recon restored, sentinel + tailer stopped, hostapd + dnsmasq SIGTERM'd via JobManager, deny-list -=`10.0.0.0/24`, interface link-down + flush. Idempotent start. `set_ap_config` refuses while running.

Route + template tests via Flask test client:

- 19 PineAP routes registered. AP config POST round-trips. Empty SSID, channel 999, hw_mode 'x' all rejected at the route boundary. 404 on unknown client. Clear-clients works. Page renders with all expected markers (`tab-open`, `open-clients-tbody`, `open-probes-tbody`, `open-primary-ssid`, `open-channel`, `open-hw-mode`, "Open SSID", "Captive-portal probes", "Connected clients").

`node --check` on the extended `pineap.js`: parse OK.

**Pi-side real-radio verification (task #103) deferred** — same pattern as S09. The stub-mode verification covers the orchestration; real beacon broadcast + actual Karma + a phone associating needs hardware + a willing victim device, which we'll do as a follow-up. The blocking question is whether `wlan-ap` is currently free (mgmt AP may have moved there per S04.7's multi-radio work) — needs a quick adapter-management check before testing.

---

## Checkpoint 4 — Notes for S12 / S13

- **Evil WPA (S12)** is mostly an extension of `_start_broadcast`: render hostapd with `wpa=2 + wpa_passphrase=<anything>` instead of open. The "anything" matters less than you'd think — for *handshake harvesting* the AP only needs to complete the 4-way through M1+M2; the client's M3 will fail because we don't know the real PSK, but by then we have what we need. Hook into the handshakes service to surface those partials as `source: "Evil WPA"`. "Clone from Recon AP" is a UI affordance — pre-fill the form from a selected AP's SSID/channel/security.
- **Filtering tab (S13)** wants `accept_mac_file` / `deny_mac_file` integration. hostapd supports both natively. UI: per-MAC allow/deny per BSS, plus an SSID-side filter at the pool layer (which we already have via the `hidden` field — just need to expose a richer UI).
- **Clients tab (S13)** is the proper home for the "kick" action — `hostapd_cli -i wlan-ap disassociate <mac>` is the one-liner. The S11 Open SSID tab's connected-clients view is the minimal version; S13 will move it up to a top-level Clients tab and add the kick + filter actions.
- **mDNS / Bonjour passive observation** I flagged in scope discussion — deferred to S13. Scapy sniffer on the rogue subnet picks up the AirPlay/AirDrop/Chromecast sentinels devices broadcast on association. Cheap to add when we touch the Clients view next.
- **Karma stats UI** — `/pineap/karma/stats` is wired but no UI card consumes it yet. Easy add when S13 gets to the engine-status view.
- **dnsmasq log rotation** — current tailer detects inode change and reopens. Hasn't been verified under load on Pi; flag for S11 hardware test.

---

## What's next

Operator's call. Natural next step is S12 (Evil WPA — the higher-value Karma variant that harvests partial handshakes), but we could also pull the Pi-deploy verification forward as a hardware sanity check before piling on more code.
