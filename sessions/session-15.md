# Session 15 — Modules system architecture (Phase F)

**Goal.** Build the drop-in plugin loader that the rest of Phase F (nmap
module S16, MITM module S17) and the candidate Metasploit phase all depend on.
Decision up front: **restart-on-change**, not hot-loading — install/uninstall
update a registry; blueprints register once at app startup.

## What was built

- **`app/services/modules.py` — `ModuleLoader`.**
  - `discover()` scans `app/modules/*/module.toml`, parsing each manifest
    (tomllib → tomli → a minimal flat-format fallback, because the Mac dev box
    is Python 3.10 with no `tomllib` while the Pi is 3.11). Returns a
    `ModuleInfo` per directory, with `error` set (not raised) on a bad manifest
    or a name/dir mismatch.
  - Installed state lives in `$DATA_DIR/modules.json` (`{"installed": [...]}`).
    `install()` / `uninstall()` only flip that registry and return a
    "restart to (un)load" message. The filesystem catalog under
    `app/modules/` is never mutated.
  - `register_installed(app)` runs once from the app factory: for each
    installed module it imports `app.modules.<name>.<submodule>` and registers
    the blueprint attr from the manifest's `blueprint = "routes:bp"`. A broken
    module is logged and skipped — it can't take the app down.
  - `installed_modules()` feeds the dynamic sidebar.

- **`app/modules/example/` — reference module.** `module.toml` + `__init__.py`
  + `routes.py` (Blueprint with `template_folder="templates"`, `url_prefix`)
  + `templates/example.html`. A page plus a `/modules/example/ping` JSON
  endpoint that proves the blueprint registered and its own template resolved.
  This is the "copy me to scaffold a module" starting point.

- **`app/routes/modules.py` + `templates/modules.html` + `static/modules.js`.**
  The Modules page lists the available catalog with installed state and
  install/uninstall buttons (each reminds you to restart). `/modules/list`
  JSON API drives the table.

- **App factory wiring (`app/__init__.py`).** Registered the `modules`
  blueprint, called `get_loader().register_installed(app)` after the core
  blueprints, and added a context processor injecting `installed_modules` into
  every template.

- **`base.html`.** The previously-disabled "Modules" sidebar item now links to
  the Modules page, and installed modules render their own nav items
  dynamically below it.

- **Learning Centre.** New `modules-loader` section (manifest shape, the
  registry, why registration is restart-bound, scaffolding a module).

## Console exercise

- `cat app/modules/example/module.toml` — the manifest contract.
- `python3 -c "... iter_rules() ... '/modules/'"` — confirm which module
  routes registered at startup (installed-only).
- `cat $PIPINEAPPLE_DATA_DIR/modules.json` — the installed registry.
- `cp -r app/modules/example app/modules/mymod` — scaffold a new module.

## Verification

`verify_s15.py` — 19/19 passed: discovery + manifest parse, install/uninstall
registry, route 404 before restart vs 200 after a fresh app (restart
emulation), blueprint-local template render, the module JSON endpoint, dynamic
sidebar nav, and the Modules page + list API. No regression in S12.5/S13/S14
suites. `py_compile` + `node --check` clean.

## Design notes / why restart-on-change

Hot-loading a Flask blueprint into a running app is doable but fiddly
(`url_map` is built once; unregistering blueprints isn't first-class), and the
failure modes — half-registered routes, stale template caches, a module crash
during live import taking down the running server — aren't worth it for a
single-user lab tool. Registering at startup keeps the loader simple and makes
a module's load path identical to every core blueprint's. The cost is a
`systemctl restart pipineapple` after install/uninstall, surfaced everywhere in
the UI.

## Parked / next

- **S16 — nmap module** is the first real consumer: post-association recon
  against Open-AP / PineAP clients or a cracked-PSK subnet, rendered as sortable
  tables. It validates the loader against a real tool wrapper.
- When the Metasploit phase starts, build `services/target_guard.py` (shared
  lab-CIDR fence) alongside its module — see `docs/phase-H-metasploit-sketch.md`.
- Still outstanding from earlier: S12.5 journal/Learning-Centre writeup pending
  Nishit's thorough captive-portal test; S13 + S14 hardware tests parked.
