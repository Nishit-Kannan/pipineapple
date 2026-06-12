# Phase H (candidate) — Host exploitation via a Metasploit module

> **Status: sketch / not committed.** This phase sits *beyond* the current
> WiFi-Pineapple-shaped roadmap (which ends at S19). It's a fundamentally
> different layer: the rest of the platform attacks the *wireless network and
> association layer* (recon → handshake → rogue AP → MITM). Metasploit /
> Meterpreter attack a *host* — deliver a payload, get a session, run commands.
> Capture this as a future phase only if you want to push past the Pineapple
> analogue into post-access exploitation.

## Where it fits

Logically *after* Phase F's MITM module (S17), because the realistic kill chain
is:

```
PineAP client  ──┐
cracked-PSK     ├─→  nmap module (S16)  ─→  exploit (Phase H)  ─→  Meterpreter session
subnet foothold ─┘        (find hosts/services)   (land a payload)     (interact / loot)
```

It's a **Module** (Phase F architecture), not a top-level section — it drops in
under `app/modules/msf/` with a `module.toml`, routes, templates, tools. The
Modules page already handles install/uninstall and sidebar registration, so no
core changes are needed.

## Architecture (fits `routes/ → services/ → tools/`)

Don't scrape `msfconsole` stdout — drive Metasploit over its **RPC API**
(`msfrpcd`) using **`pymetasploit3`** as the Python client. That gives
structured objects (jobs, sessions, module options) instead of brittle text
parsing, which matches how the rest of the platform wraps tools.

- **`tools/msfrpc.py`** — thin `pymetasploit3` wrapper: connect/auth, list
  modules, set options, run, list jobs/sessions, write to a session, read
  session output. Pure subprocess/RPC, no orchestration.
- **`tools/msfvenom.py`** — wraps `msfvenom` for payload generation (separate
  binary from the RPC daemon).
- **`services/msf.py`** — orchestration + state: `msfrpcd` lifecycle (start via
  JobManager, hold the auth token), a session registry, an audit log. Calls the
  shared target guard (below) for every RHOSTS/LHOST.
- **`services/target_guard.py`** *(shared, promoted out of the msf module)* —
  the single lab-CIDR chokepoint, reusable by **every** future offensive module
  (msf, future C2/exploit modules, etc.), not just this one. One
  `assert_in_lab(ip_or_range)` / `is_lab_target(...)` API that hard-refuses
  anything outside the configured lab CIDR. Sits alongside the existing
  `access_control` service (the deny-CIDR layer); this is its offensive
  counterpart — an *allow*-fence for attack targets. Build it here, wire msf as
  its first consumer.
- **`routes/msf.py`** (module blueprint) — thin endpoints: daemon up/down,
  payload-gen, listeners, sessions, exploit launch.

## Sub-sessions

- **H-1 — module scaffold + `msfrpcd` lifecycle.** `app/modules/msf/` skeleton,
  start/stop the RPC daemon through JobManager, authenticate, surface daemon
  status. Console: `msfrpcd -P <pass> -a 127.0.0.1`, the RPC API vs the
  console, why RPC beats stdout scraping.
- **H-2 — payload generation (`msfvenom`).** UI to pick payload
  (`windows/x64/meterpreter/reverse_tcp`, `linux/.../shell_reverse_tcp`, …),
  set LHOST/LPORT/format/encoder, generate and download. LHOST defaults to the
  Pi's **lab** interface, never `wlan0` upstream. Console: staged vs stageless,
  stagers/encoders, why AV evasion is explicitly out of scope.
- **H-3 — listeners/handlers + sessions (Meterpreter *and* shell).** `multi/handler`
  management, active-listener list, session table covering **both** session
  types: Meterpreter (`sysinfo`, `ls`, `screenshot`, run command, kill) and
  plain command/shell sessions (`shell`/`cmd` payloads — send a command, read
  output, kill). A unified session panel that adapts its actions to the session
  type. Console: Meterpreter vs a raw shell, the handler, transports, what a
  "session" actually is.
- **H-4 — exploit launcher + guards + audit.** Pick a module, set RHOSTS
  **from the nmap module's results / PineAP Clients** (not free-typed), set
  payload, run; everything behind the shared lab-CIDR fence and the audit log.
  Targets are the lab VMs (Windows / Linux). Console: the module/datastore-option
  model, the danger of RHOSTS, why the fence exists.

## Lab-safety gates (non-negotiable — this is the most dangerous capability)

Consistent with the rest of the platform (offensive features default OFF, behind
typed ethics confirms — `pineap`, `phishing`, `active`):

- **Default OFF**, behind a strong typed confirm (e.g. type `exploit`) to even
  arm the module.
- **Lab-CIDR fence.** RHOSTS/LHOST must resolve inside the lab subnet
  (the GL.iNet target range / `10.0.0.0/24`). Hard-refuse public IPs or anything
  outside the configured lab CIDR — one guard function in `services/msf.py` that
  *every* target passes through, no bypass.
- **Shared target guard.** The lab-CIDR fence is `services/target_guard.py`, a
  reusable allow-fence consumed by msf and every future offensive module — not a
  one-off inside msf. Single `assert_in_lab()` chokepoint, no bypass.
- **No free-typed targets.** RHOSTS is selected from nmap results / PineAP
  Clients, with an explicit per-target confirm. No "exploit this arbitrary IP."
- **Owned-device allowlist (optional, stronger).** Restrict to the spare victim
  device by MAC/IP, tied to the PineAP Clients list.
- **Audit log** of every module run, payload generated, and session opened
  (timestamp, operator, target, module) — written like the other audit trails.
- **LHOST never defaults to `wlan0`** (the home/upstream interface).

## Prerequisites & reality check

- **Vulnerable target — available.** Nishit has a VM host and can stand up both
  **Windows and Linux** vulnerable VMs on the lab subnet (Metasploitable3,
  unpatched Win7/Win10, etc.). These are the real exploit targets — *not* the
  iPhone (patched mobiles rarely expose a remotely-exploitable service). So H-4
  is a genuine working-exploit phase, not just a workflow demo. Prereq task:
  attach the lab VMs to the GL.iNet target subnet so they sit inside the lab
  CIDR the guard fences on.
- **Install footprint.** `metasploit-framework` is large; it runs fine on the
  Pi 5 (ARM64) as the attacker. Payloads target the *victim's* arch (usually
  x86/x64), independent of the Pi.
- **Dependencies:** `metasploit-framework` (+ `msfrpcd`, `msfvenom`),
  `pymetasploit3` (Python client).

## Decisions (settled)

- **Vulnerable lab VMs: yes.** Windows + Linux VMs on the lab subnet are the
  exploit targets → build the full phase through H-4 as a working-exploit
  capability, not a demo.
- **Session types: both.** Expose Meterpreter *and* plain command/shell
  sessions in a unified, type-adaptive session panel (H-3).
- **Target guard: promoted to shared.** The lab-CIDR fence is its own
  `services/target_guard.py`, reusable by all future offensive modules; msf is
  its first consumer (built in H-1, enforced from H-4).

## Still to confirm when building

- Exact lab CIDR(s) the guard fences on (the GL.iNet target subnet + wherever
  the VMs attach) — make it config, not hard-coded.
- Whether `target_guard` also cross-checks the owned-device MAC/IP allowlist, or
  just the CIDR (CIDR first; allowlist as a later tightening).
