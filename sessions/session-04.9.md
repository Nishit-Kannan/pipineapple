# Session 04.9 — Internet sharing on the management AP

**Date:** 2026-06-05
**Phase:** A/B interlude — final portability insertion before recon (S05). Field-portable architecture is fully landed after this.
**Goal:** Let devices connected to the management AP reach the internet through whatever upstream the Pi has — home Wi-Fi via wlan0 or Ethernet via eth0. Without this, phones aggressively switch away from the mgmt AP the moment they detect "no internet here." With this, phones stay attached, the operator can browse normally while using the platform, and the AP is fit for actual field deployment.

---

## Checkpoint 1 — Concepts: NAT, IP forwarding, DNS

**Decided:**

- **MASQUERADE on the source subnet, not the egress interface.** First instinct was `iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE` — wrong, because if the Pi's default route is via eth0 (lower metric), the packets leave eth0 un-NAT'd and the ISP drops them. `iptables -t nat -A POSTROUTING -s 10.42.0.0/24 -j MASQUERADE` matches by source subnet and works regardless of which interface egress picks.
- **dnsmasq forwards DNS to upstream resolvers via `server=` lines.** Without `server=` (and with the default `no-resolv`), AP clients get DHCP fine but their DNS queries to `10.42.0.1` go nowhere. Add `server=1.1.1.1` and `server=8.8.8.8`, remove `no-resolv`, restart dnsmasq.
- **`net.ipv4.ip_forward=1`** must be set before any forwarding can happen. Lost on reboot; re-applied on every AP enable when sharing is on.
- **State + rules persistence.** The toggle's "enabled" bit goes in `mgmt_ap.internet_sharing` in `networking.json`. iptables rules don't persist across reboot in our setup — that's by design; the platform re-applies them on every `_enable_mgmt_ap_unlocked` when `share_internet=True`.
- **Operational glue.** Two ancillary things became blockers worth fixing: (1) `DATA_DIR` default is `/tmp/pipineapple` which Pi OS wipes every boot, taking auth + networking state with it. (2) `nm.set_managed(iface, False)` failed on interfaces in NM's permanent unmanaged list, aborting the AP enable sequence with no clear error. Both fixed in this session.

---

## Checkpoint 2 — iptables wrapper

**Built `app/tools/iptables.py`:** thin wrappers over `iptables`, each idempotent — check before adding, ignore "rule doesn't exist" on remove:

- `enable_ip_forward()` — `sysctl -w net.ipv4.ip_forward=1`.
- `ensure_nat_masquerade(subnet)` — `iptables -t nat -A POSTROUTING -s <subnet> -j MASQUERADE`, only if the rule isn't present.
- `ensure_forward_rules(subnet)` — both directions: `-A FORWARD -s <subnet> -j ACCEPT` (outbound) and `-A FORWARD -d <subnet> -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT` (return).
- `remove_nat_and_forward(subnet)` — `-D` on all three rules, best-effort.

Subnet-based throughout — no interface name baked into the rules.

---

## Checkpoint 3 — Service + UI

**Edited `app/services/networking.py`:**

- `set_internet_sharing(enabled)` — saves `mgmt_ap.internet_sharing`, immediately applies/removes the iptables rules if the AP is active, and bounces dnsmasq with new config (`forward_dns=True` adds `server=` lines, `False` adds `no-resolv`).
- `_enable_mgmt_ap_unlocked` reads `share_internet` and applies iptables after starting daemons — same code path the toggle uses, so reboots restore the rules automatically.
- `_disable_mgmt_ap_unlocked` removes iptables rules if sharing was on; also explicitly brings `wlan0` back up if the AP was on wlan0 (NM takes ownership but doesn't auto-bring-up, leaving wlan0 in "unavailable").
- `_restart_dnsmasq_with_current_config(ap)` — re-renders config, stops the existing dnsmasq job, **waits for port 53 to be bindable**, starts a new dnsmasq job. The port-wait is the fix for the race described below.

**Edited `app/tools/dnsmasq.py`:** `render_config` gained a `forward_dns: bool = False` parameter (default off for the recon rogue-AP use case). When True, omits `no-resolv` and adds `server=...` lines for each upstream resolver.

**Edited `app/routes/settings.py`:** `POST /settings/networking/mgmt-ap/internet-sharing` accepts `{enabled: bool}`, calls `set_internet_sharing`. Toggle in `settings.html` POSTs on `change` — no separate "Apply" button.

---

## Checkpoint 4 — Persistent DATA_DIR + systemd unit + bytecode flag

**Changed `run-as-root.sh`** to export `PIPINEAPPLE_DATA_DIR=/var/lib/pipineapple` (the FHS-correct path for runtime state). `/tmp/pipineapple` (the config default) is wiped every reboot, which was silently nuking `auth.json`, `networking.json`, `adapter_roles.json`, and the deny-list. The setup wizard appeared every boot — that's how we found it.

**Added `deploy/pipineapple.service`** and **`deploy/install-service.sh`** — a systemd unit so the platform comes up on boot without operator intervention. `After=NetworkManager.service dbus.service` (NM has to be up so we can unmanage interfaces; we don't wait for `network-online.target` because *we* are the network for the AP clients). `Type=simple`, `User=root`, env vars baked in.

**Added `Environment="PYTHONDONTWRITEBYTECODE=1"`** to the systemd unit. Hit this twice during the session: rsync-with-mtime-preservation gives the new `nm.py` an mtime older than the existing `.pyc`, so Python trusts the cached bytecode and silently runs old code. The fix (`set_managed` was now idempotent in source but the running code was the pre-fix version) appeared deployed but did nothing. Disabling bytecode caching for the service eliminates this entire class of bug.

---

## Checkpoint 5 — Verification on Pi

A few rounds of trial-and-error here; documenting the eventual working state:

- Toggle internet sharing on from the UI. Watch `journalctl -u pipineapple -f`:
  - `sysctl -w net.ipv4.ip_forward=1` → ok
  - `iptables -t nat -C POSTROUTING -s 10.42.0.0/24 -j MASQUERADE` → rc=1 (not present)
  - `iptables -t nat -A POSTROUTING -s 10.42.0.0/24 -j MASQUERADE` → rc=0
  - Same pattern for the two FORWARD rules
  - `dnsmasq.write_config -> /etc/pipineapple/mgmt-ap-dnsmasq.conf` — config rewritten with `server=` lines
  - Stop old dnsmasq job, wait for `port 10.42.0.1:53 free`, start new dnsmasq job
- On phone: forget `PiPineapple-Setup`, rejoin. (Modern phones cache DHCP leases; without a fresh lease iOS in particular reports "incorrect password" when really it's "no DHCP / no DNS yet.")
- `https://google.com` loads.

Reboot test:
- `sudo reboot`. Wait ~30s. Phone sees AP, joins, browses. No operator intervention.
- `sudo cat /var/lib/pipineapple/networking.json` confirms `mgmt_ap_active: true`, `internet_sharing: true`.
- `sudo iptables -t nat -S POSTROUTING` confirms the rule was re-applied by `_enable_mgmt_ap_unlocked`.

---

## Bugs found, fixed, and worth remembering

- **`nm.set_managed` aborted the AP enable on `wlan-mgmt-ap`.** Interface was in NM's permanent unmanaged list via `99-pipineapple-unmanaged.conf`. `nmcli device set wlan-mgmt-ap managed no` returned rc=1 with "Device 'X' is unmanaged." — the desired state was already true but the wrapper treated it as failure. Fix: query current state with `nmcli -t -f DEVICE,STATE device status` first; if it already matches what we want, return success without calling. Belt-and-suspenders: also treat "is unmanaged" stderr as success when that's what we asked for.
- **dnsmasq restart raced port 53.** `stop_job` returns when the process exits, but the kernel can hold the socket briefly. The next dnsmasq fails with "Address already in use", leaving AP clients with no DHCP. Fix: `_wait_for_port_free(addr, port, timeout=3.0)` polls TCP+UDP bind with `SO_REUSEADDR` until both succeed, then start the new dnsmasq.
- **MASQUERADE bound to `-o wlan0` instead of `-s subnet`.** First manual attempt. When `ip route` showed default via eth0 metric 100 (lower than wlan0 metric 600), traffic left eth0 un-NAT'd and never came back. Subnet-based rule fixes it; documented in the wrapper.
- **`/tmp` DATA_DIR.** Setup wizard appeared every reboot, AP state reset, deny-list cleared. Moved to `/var/lib/pipineapple` via systemd env var.
- **Stale `.pyc` after deploy.** rsync `-a` preserves source mtime; `.py` mtime < `.pyc` mtime → Python loads cache → my fix appeared deployed but never ran. Two manual cache nukes + `PYTHONDONTWRITEBYTECODE=1` in the unit, permanently.
- **JS `{% block scripts %}` in `recon.html` was silently dropped.** `base.html` has no `scripts` block; page-specific JS is loaded in `<head>` alongside `settings.js`. Vendored `socket.io.min.js` locally too (no external CDN dep for the management UI).

---

## Session-wide findings

- **The "field-portable" architecture is now real.** Pi powers on alone → systemd starts the platform → mgmt AP comes up (on whichever radio is configured) → operator joins from phone → has internet through the AP → opens the UI → does work. No SSH, no cable, no DNS pinning, no laptop tethering. This was the goal three sessions ago.
- **Most of the time in this session went to operational glue, not the core feature.** The iptables wrapper + DNS forwarding were maybe 30% of the work. The persistent DATA_DIR, systemd unit, bytecode invalidation fix, set_managed idempotency, port-53 wait, MASQUERADE rule shape, and the JS block-loading bug were all blockers that surfaced during testing. None of them were "interesting" individually; collectively they're what made the difference between "internet sharing works on my workbench" and "the AP boots, comes up, and shares internet hands-off with no operator intervention."
- **`iptables -C` + `-A` is the right idempotency pattern.** Every iptables wrapper does check-then-add. Reads cleanly, never duplicates rules, never errors on re-application. The recon session in S05 follows the same pattern for monitor mode (check current mode, only flip if different).
- **Bytecode caching is the kind of bug that erodes trust in deploys.** Two "the fix is in but it doesn't work" cycles before I figured it out. `PYTHONDONTWRITEBYTECODE=1` in the unit is non-negotiable for this project.

---

## Parked for later

- **Per-client bandwidth limiting.** AP clients can saturate wlan0 upstream. `tc qdisc` rules are straightforward but I haven't needed them yet.
- **Capture-portal style UI.** Modern phones expect `connectivitycheck.gstatic.com` / `captive.apple.com` to return specific bytes to decide "this network has internet." Today we just rely on real upstream — works when sharing is on, blocks the phone from auto-switching off the AP. A real captive portal would handle "no upstream but AP is intentional" gracefully; defer until I need it.
- **systemd-resolved interaction.** Pi OS Lite Trixie doesn't run it by default, but on a more "full" OS it would conflict with dnsmasq for port 53. Document the disable steps when I hit it.
- **iptables-persistent.** Today iptables rules are re-applied on every AP enable, which works. `netfilter-persistent` would save/restore on boot independently. Adds a moving part; deferring.

---

## What's now possible

- **The platform is genuinely field-portable.** Box + battery + adapters, anywhere, no support infra. Connect from a phone, do work, internet flows through.
- **The dnsmasq DHCP+DNS combo is reused by the recon-side rogue AP** (Phase D) — same wrapper, same patterns, with `forward_dns=False` for the rogue case (don't actually let rogue-AP victims reach the real internet; that's an ethics line we don't cross).
- **The systemd + persistent-state + bytecode-flag combo means deploys don't break weirdly.** Pull code, `sudo systemctl restart pipineapple`, done. The next time we deploy a fix it actually runs.
- **Session 05 (recon) can start.** All the infrastructure prereqs — adapter management, mgmt AP on its own radio, internet on demand, hands-off boot, durable state — are in place.
