# Session 04 — Adapter management: sticky names, monitor mode, NM unmanaging

**Date:** 2026-06-04
**Phase:** B — Recon (kickoff)
**Goal:** Make the offensive radios usable from the UI. Settings page comes alive with an Adapter Management tab that lets you assign sticky names to your three Alfa adapters (wlan-mon-2g, wlan-mon-5g, wlan-ap), apply udev rules to make those names persist, configure NetworkManager to leave the offensive interfaces alone, and toggle monitor/managed mode per adapter. This is the first session where the platform actually does something offensive: putting an adapter in monitor mode opens the door to recon and capture in subsequent sessions.

Session detoured for two convenience features (launcher script + read-only Command Stream) the user requested mid-session. Both are now permanent platform features.

---

## Checkpoint 1 — Concepts: NetworkManager interference, udev sticky names, monitor mode kernel-level, sudo/root

**Decided:**

- **Scalpel over sledgehammer for NetworkManager.** Drop a `/etc/NetworkManager/conf.d/99-pipineapple-unmanaged.conf` snippet that tells NM to ignore offensive interfaces by name pattern (`interface-name:wlan-mon-*;interface-name:wlan-ap`). NM continues managing `wlan0`/`eth0` so home Wi-Fi and Ethernet upstream keep working. The `airmon-ng check kill` sledgehammer is still available as a button but stays unused for everyday work.
- **udev rules for sticky adapter names.** Without them, `wlan1`/`wlan2`/`wlan3` shuffle on every reboot based on USB detection timing. With them, the same MAC always becomes the same name. Cost: rules apply on interface-add, so the first apply requires a reboot.
- **Monitor mode is a three-step sequence:** `ip link set <iface> down` → `iw dev <iface> set type monitor` → `ip link set <iface> up`. Drivers refuse a type change while up. `airmon-ng start` wraps this; we do it directly so the user sees the actual commands in the Command Stream.
- **Flask runs as root from S04 onwards.** Every offensive operation needs CAP_NET_ADMIN or root file access (writing to `/etc/`). The clean privilege-separation path (a thin root agent fronted by an unprivileged web app) waits for Session 19's polish.

---

## Checkpoint 2 — Re-launched Flask as root

**Done:** `sudo -E ./.venv/bin/python run.py`. Confirmed the effective uid was 0 via `/proc/<pid>/status`. Dashboard still loaded; all three Alfas now appear in the wireless radios table along with `wlan0`.

**Bug surfaced:** WebSocket handshake returns 500 + werkzeug `AssertionError: write() before start_response` on every page load. Reason — werkzeug's debug-mode reloader re-execs `sys.argv` on file change. Under `sudo`, `sys.argv[0]` is the sudo wrapper, so re-execing creates a nested-sudo process tree (visible as four processes deep instead of the usual two). The double-fork through sudo corrupts the WSGI environ that simple-websocket needs for the upgrade handshake.

**Fix attempt 1:** `use_reloader=False` in `socketio.run()`, controlled by env var `PIPINEAPPLE_RELOADER` (default off). Code edits no longer auto-reload (manual restart needed); the trade-off is fine for our pace.

**Result:** initial connection works (live indicator green, the command-stream feature was added at this point and worked correctly). But every page **refresh** still produced one 500 in the log.

**Fix attempt 2:** `allow_upgrades=False` on the SocketIO server + `transports: ["polling"], upgrade: false` on the client. Skips the WebSocket upgrade attempt entirely; client stays on long-polling. The 500 stops because no upgrade is attempted. Polling latency is acceptable for our 2-second cadence and the upgrade can be re-enabled in Session 19 when we move to nginx + gunicorn (where simple-websocket has no werkzeug WSGI quirks to fight).

---

## Checkpoint 3 — Mid-session detour: run-as-root launcher script

**Built:** `run-as-root.sh` at the project root. Resolves to the script's own directory so it works from anywhere, checks the venv exists, then `exec sudo -E ./.venv/bin/python ./run.py`. Forwards any CLI args. Use this instead of typing the full sudo invocation every time.

---

## Checkpoint 4 — Mid-session detour: read-only Command Stream

**Built:** A new top-level UI feature for transparency. The terminal icon in the title bar (previously a placeholder) now toggles a bottom-drawer panel showing every shell command the platform executes, in real time. Terminal-style: monospace, dark, four columns (timestamp / source tag / command / exit code + duration).

**Architecture:**

- New service `app/services/terminal.py` — bounded deque (200 entries) + SocketIO emitter.
- A `contextvars.ContextVar` polling flag in `app/tools/_common.py` — when set (sysinfo broadcaster wraps its tool calls in it), `run()` suppresses the broadcast. Result: routine periodic reads (iw / ip / vcgencmd / ethtool every 2s) don't spam the stream. Action-triggered commands (mode toggle, udev/NM apply, JobManager job starts) DO show up because they run outside the polling context.
- A callback registry in `_common.py` that the factory subscribes the terminal service to. Tools don't import services directly; the bridge happens at app construction.
- JobManager's `start_job` also broadcasts to the stream with `source="job"`.

**The polling-context idea is the load-bearing one** — it lets us have a "show me everything the platform does" view that stays useful instead of becoming noise. Future periodic services should also wrap their work in `polling_context()` to stay out of the stream.

---

## Checkpoint 5 — Settings page scaffold

**Built:**

- `app/routes/settings.py` blueprint mounted at `/settings/`.
- Sidebar Settings entry comes alive (was disabled, now links to `/settings/`).
- `app/templates/settings.html` with a tab bar (Adapter Management active, Networking/WiFi/Advanced/Help disabled with Session 18 tooltips).
- Stat cards on the page: Wireless interfaces / Roles assigned / Monitor mode count.

---

## Checkpoint 6 — Adapter detection, role assignment, udev rules generation

**Built:**

- `app/services/adapters.py` — joins `iw dev` + `ip -j addr show` + `ethtool` + persistent role assignments into one structured list. Roles persist to `$DATA_DIR/adapter_roles.json` keyed by MAC (lowercased), with uniqueness enforcement.
- `app/tools/udev.py` — `render_rules({mac: name})` produces the rules file body, `write_rules()` writes to `/etc/udev/rules.d/99-pipineapple-adapters.rules`, `reload_rules()` runs `udevadm control --reload-rules && udevadm trigger`. Stub mode writes to `/tmp/...preview` so Mac dev can inspect.
- Settings UI: adapter table with role dropdowns. Selecting a role POSTs `/settings/adapters/role`, the service writes the JSON store, and the page refreshes the table via the JSON API. Status pill at the page bottom shows what just happened.
- "Generate & apply udev rules" button → `apply_udev_rules()` service method → file write + `udevadm reload-rules` + `udevadm trigger`. The new names take effect on next interface add (unplug+replug or reboot).

---

## Checkpoint 7 — NetworkManager unmanaging + monitor mode toggle

**Built:**

- `app/tools/nm.py` — renders `/etc/NetworkManager/conf.d/99-pipineapple-unmanaged.conf`, writes it, runs `nmcli general reload` (or falls back to `systemctl reload NetworkManager`). Also exposes `stop_managers()` for the sledgehammer button.
- Monitor / Managed mode toggle per adapter via `AdapterService.set_mode(iface, mode)` — runs the three-step sequence and surfaces each command in the Command Stream tagged `tool`. Returns a list of step messages so the status pill can show what happened.
- Down button for forcing an interface down without changing its type.

**JobManager is the first real consumer of the Command Stream** — its `start_job` broadcasts with `source="job"`. Mode-toggle subprocesses go through `run()` and broadcast as `source="tool"`. The visual distinction (different accent color per source in the drawer) lets the user separate "ad-hoc tool call" from "long-running process I should stop later."

---

## Checkpoint 8 — Deploy + verification

**Done:**

- Pushed to GitHub, pulled on the Pi, launched via `./run-as-root.sh`.
- Browsed to Settings, saw three Alfas + `wlan0` in the adapter table.
- Assigned `wlan-mon-2g`, `wlan-mon-5g`, `wlan-ap` to the three Alfas via the dropdowns. Each assignment triggered a notification.
- Clicked "Generate & apply udev rules" — file written, udev reloaded.
- Verified on the Pi: `cat /etc/udev/rules.d/99-pipineapple-adapters.rules` showed three rules with the right MACs and names.
- Clicked "Generate & apply NM config" — file written, nmcli reloaded.
- Rebooted the Pi for the udev names to take effect. After reboot: `iw dev` showed the three Alfas with their new sticky names.
- Toggled `wlan-mon-2g` to monitor mode via the UI. The Command Stream showed the three commands (`ip link set wlan-mon-2g down`, `iw dev wlan-mon-2g set type monitor`, `ip link set wlan-mon-2g up`), each with `rc=0` and millisecond durations. Cross-check: `iw dev wlan-mon-2g info` reported `type monitor`.
- `nmcli device status` confirmed offensive radios show STATE `unmanaged` while `wlan0` stays `connected`.

---

## Checkpoint 9 — Console exercise

For each of the UI actions, the corresponding manual command was run to verify the wrapper:

| UI action | Manual equivalent | Result |
|-----------|---|---|
| Assign role (writes JSON store) | `cat $DATA_DIR/adapter_roles.json` | Same content as UI |
| Apply udev rules | `cat /etc/udev/rules.d/99-pipineapple-adapters.rules` then `sudo udevadm control --reload-rules && sudo udevadm trigger` | Identical |
| Apply NM config | `cat /etc/NetworkManager/conf.d/99-pipineapple-unmanaged.conf` then `sudo nmcli general reload` | Identical |
| Monitor toggle | `sudo ip link set <iface> down; sudo iw dev <iface> set type monitor; sudo ip link set <iface> up; iw dev <iface> info` | Identical |
| Stop managers | `sudo systemctl stop NetworkManager wpa_supplicant` | Identical |

All commands captured in three new Learning Centre sections (monitor-mode, udev-sticky-names, network-manager).

---

## Checkpoint 10 — Learning Centre updated

Three new topic sections added:

1. **Monitor mode & adapter state** — the three-step sequence, `iw dev <iface> info` verification, `iw dev set channel`.
2. **udev sticky names** — rule format, `udevadm control --reload-rules`, `udevadm trigger`, `udevadm info`, listing `/sys/class/net/`.
3. **NetworkManager control** — viewing the unmanaging config, `nmcli general reload`, `nmcli device status`, the sledgehammer `systemctl stop`.

Learning Centre now has eight topic sections accumulating across Sessions 01, 02, and 04.

---

## Checkpoint 11 — Session 04 wrap

**Shipped:**

- Settings page with a tab layout matching the real Pineapple. Adapter Management is the first active tab.
- Adapter detection / role assignment / udev rules generation pipeline. Sticky names survive reboots.
- NM unmanaging config — offensive radios stay unmanaged across reboots while home Wi-Fi continues to work.
- Monitor / Managed / Down per-adapter toggle via JobManager-adjacent code path (subprocess.run via `_common.py`).
- Mid-session additions: `run-as-root.sh` launcher script, read-only Command Stream panel with polling-context suppression.

**Two non-obvious bugs hit and worked around:**

1. **Werkzeug debug-mode reloader + sudo = nested-sudo process tree** that breaks simple-websocket's WSGI hijack. Fixed by `use_reloader=False` default with env-var override.
2. **simple-websocket + werkzeug WSGI write-finalization incompatibility** even without the reloader — every WebSocket upgrade attempt produces a cosmetic 500 in the log even though polling fallback delivers events correctly. Fixed by disabling transport upgrades server-side (`allow_upgrades=False`) and explicitly using `transports: ["polling"]` on the client. Real WebSocket gets re-enabled in Session 19 when we move behind nginx + gunicorn.

**Parked for Session 05 (Recon scan table):**

- First real JobManager consumer for backgrounded `airodump-ng` — locked to recon-interface channel(s) with three-radio dual-band parallel-scan support.
- AP and client tables with sort/search/pagination, fed by parsing airodump's CSV rotation.
- Live updates of the scan results via SocketIO.

**Parked further:**

- Real WebSocket transport (Session 19, behind gunicorn+nginx).
- Auto-reload of Python file changes (currently off; manual restart required).
- Real Web Terminal (interactive bash). Currently the terminal icon shows a read-only command stream; interactive mode lands in Session 19.

---

## Session-wide findings

- **Sudo + werkzeug debug reloader is incompatible.** The reloader re-execs sys.argv, which under sudo becomes nested sudo. Disable the reloader when running as root.
- **simple-websocket + werkzeug 3.1.x dev server has a WSGI write-finalization bug** that surfaces as `AssertionError: write() before start_response` on every WS upgrade attempt. Cosmetic (polling fallback works) but spams the log. Disable upgrades for now; real fix comes with proper WSGI/ASGI server in production polish.
- **The polling-context pattern is generally useful.** Wrapping a periodic service's work in `polling_context()` cleanly suppresses its actions from the Command Stream while leaving everything else visible. Future periodic services (recon scan in S05, etc.) should follow the same pattern when their reads are routine.
- **Strict service/tool separation paid off again.** Adding the Command Stream meant a single registration in the factory; tools didn't need to know about the service. The callback-registry approach (tools fire listeners they don't import) was the right shape.

---

## What's now possible on the platform

A working Adapter Management view that turns three identical Alfa adapters into reliably-named offensive radios:

- `wlan-mon-2g` — 2.4 GHz monitor radio (default)
- `wlan-mon-5g` — 5 GHz monitor radio (default)  
- `wlan-ap` — managed-mode radio reserved for rogue AP duty

NetworkManager unmanaging config makes these names "off-limits" to NM, so monitor mode survives any background activity. Sticky names mean every later session can reference them with confidence — no more "which wlanN is which Alfa today" guessing.

That's Phase B's foundation. Session 05 builds the recon scan table on top.
