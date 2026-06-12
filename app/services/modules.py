"""Modules system — drop-in plugin loader (Session 15, Phase F).

A *module* is a self-contained extension to the UI living under
``app/modules/<name>/``:

    app/modules/<name>/
        module.toml        # manifest (see below)
        __init__.py        # makes it an importable package
        routes.py          # defines `bp` — a Flask Blueprint
        templates/         # blueprint-local Jinja templates
        services/ tools/   # optional, module-internal

``module.toml`` manifest::

    [module]
    name        = "example"          # slug; must match the directory name
    label       = "Example"          # sidebar / Modules-page label
    version     = "0.1.0"
    description = "What it does."
    blueprint   = "routes:bp"        # "<import-submodule>:<attr>" within the module
    url_prefix  = "/modules/example" # optional; default /modules/<name>
    icon        = "package"          # optional sidebar icon key

**Install model — restart on change.** Discovery scans ``app/modules/``
for manifests (the "available" catalog). Which modules are *installed*
(active) is tracked in ``$DATA_DIR/modules.json``. Installing/uninstalling
only flips that registry; blueprints are imported and registered once, at
app startup, for the installed set. Changes therefore take effect on the
next ``pipineapple`` restart — deliberately simpler and safer than
hot-loading/unloading blueprints into a live Flask app.
"""

from __future__ import annotations

import importlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MANIFEST_NAME = "module.toml"


# ---------- Manifest parsing (tomllib → tomli → minimal fallback) ----------
def _load_toml(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    try:
        import tomllib  # py3.11+
        return tomllib.loads(data.decode("utf-8"))
    except ModuleNotFoundError:
        pass
    try:
        import tomli  # py3.10 backport, if installed
        return tomli.loads(data.decode("utf-8"))
    except ModuleNotFoundError:
        pass
    # Minimal fallback for our constrained flat manifest format. Handles
    # ``[section]`` headers, ``key = "string"`` / ``key = true|false|int``,
    # and ``# comments``. Not a general TOML parser — enough for module.toml.
    out: dict[str, Any] = {}
    section = out
    for raw in data.decode("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = out.setdefault(line[1:-1].strip(), {})
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        # strip inline comment outside of quotes
        if val and val[0] not in "\"'":
            val = val.split("#", 1)[0].strip()
        if val.startswith("[") and val.endswith("]"):
            # simple array of quoted strings: ["a", "b"]
            inner = val[1:-1].strip()
            items = []
            for piece in inner.split(","):
                piece = piece.strip().strip('"').strip("'").strip()
                if piece:
                    items.append(piece)
            val = items
        elif (val.startswith('"') and val.endswith('"')) or \
             (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        elif val.lower() in ("true", "false"):
            val = val.lower() == "true"
        else:
            try:
                val = int(val)
            except ValueError:
                pass
        section[key] = val
    return out


@dataclass
class ModuleInfo:
    name: str
    label: str
    version: str = ""
    description: str = ""
    blueprint: str = "routes:bp"
    url_prefix: str = ""
    icon: str = "package"
    requires: list[str] = field(default_factory=list)
    path: Path | None = None
    installed: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "label": self.label, "version": self.version,
            "description": self.description, "url_prefix": self.url_prefix,
            "icon": self.icon, "requires": list(self.requires),
            "installed": self.installed, "error": self.error,
        }


class ModuleLoader:
    def __init__(self, modules_dir: Path, data_dir: Path) -> None:
        self._modules_dir = modules_dir
        self._registry_path = data_dir / "modules.json"

    # ---------- Installed registry ----------
    def _load_installed(self) -> set[str]:
        try:
            data = json.loads(self._registry_path.read_text())
            return set(data.get("installed") or [])
        except (FileNotFoundError, json.JSONDecodeError):
            return set()

    def _save_installed(self, installed: set[str]) -> None:
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._registry_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"installed": sorted(installed)}, indent=2))
        tmp.replace(self._registry_path)

    def is_installed(self, name: str) -> bool:
        return name in self._load_installed()

    # ---------- Discovery ----------
    def discover(self) -> list[ModuleInfo]:
        """Scan the modules dir for manifests. Returns one ModuleInfo per
        directory that has a ``module.toml`` (with ``error`` set if the
        manifest is malformed), sorted by label."""
        installed = self._load_installed()
        found: list[ModuleInfo] = []
        if not self._modules_dir.is_dir():
            return found
        for child in sorted(self._modules_dir.iterdir()):
            manifest = child / MANIFEST_NAME
            if not (child.is_dir() and manifest.is_file()):
                continue
            try:
                doc = _load_toml(manifest)
                m = doc.get("module") or {}
                name = (m.get("name") or child.name).strip()
                info = ModuleInfo(
                    name=name,
                    label=m.get("label") or name,
                    version=str(m.get("version") or ""),
                    description=m.get("description") or "",
                    blueprint=m.get("blueprint") or "routes:bp",
                    url_prefix=m.get("url_prefix") or f"/modules/{name}",
                    icon=m.get("icon") or "package",
                    requires=[str(x) for x in (m.get("requires") or [])],
                    path=child,
                    installed=name in installed,
                )
                if name != child.name:
                    info.error = (f"manifest name {name!r} != directory "
                                  f"{child.name!r}")
            except Exception as e:
                log.exception("module manifest parse failed: %s", manifest)
                info = ModuleInfo(name=child.name, label=child.name,
                                  path=child, installed=child.name in installed,
                                  error=f"manifest error: {e}")
            found.append(info)
        found.sort(key=lambda i: i.label.lower())
        return found

    def get(self, name: str) -> ModuleInfo | None:
        return next((m for m in self.discover() if m.name == name), None)

    def list_modules(self) -> list[dict[str, Any]]:
        out = []
        for m in self.discover():
            d = m.to_dict()
            d["requirements"] = self.requirements_status(m)
            d["missing_requires"] = [r["name"] for r in d["requirements"]
                                     if not r["present"]]
            out.append(d)
        return out

    # ---------- System dependencies ----------
    @staticmethod
    def requirements_status(info: ModuleInfo) -> list[dict[str, Any]]:
        """For each declared requirement, whether its binary is on PATH.
        An entry may be ``"pkg"`` (binary == package) or ``"pkg=binary"``
        when they differ."""
        import shutil
        out = []
        for entry in info.requires:
            pkg, _, binname = entry.partition("=")
            binname = binname or pkg
            out.append({"name": pkg, "bin": binname,
                        "present": shutil.which(binname) is not None})
        return out

    def install_requirements(self, name: str) -> tuple[bool, str]:
        """apt-install the system packages a module declares. Only the
        packages from THIS module's manifest are ever passed to apt — never
        anything from the client — so the route can't be used to install
        arbitrary packages."""
        from app.tools._common import run, stub_mode
        info = self.get(name)
        if info is None:
            return False, f"no module named {name!r}"
        pkgs = [e.partition("=")[0] for e in info.requires]
        if not pkgs:
            return True, "no system dependencies declared"
        if stub_mode():
            log.info("modules: (stub) would apt-install %s", pkgs)
            return True, f"(stub) would install: {', '.join(pkgs)}"
        run(["apt-get", "update"], timeout=180, source="modules")
        res = run(["apt-get", "install", "-y", *pkgs], timeout=600, source="modules")
        if res.returncode == 0:
            log.info("modules: installed deps for %s: %s", name, pkgs)
            return True, f"installed: {', '.join(pkgs)}"
        detail = (res.stderr or res.stdout or "").strip()[:200]
        return False, f"apt-get failed (rc={res.returncode}): {detail}"

    def installed_modules(self) -> list[dict[str, Any]]:
        """Installed + cleanly-loadable modules — used to render dynamic
        sidebar nav. Excludes modules with manifest errors."""
        return [m.to_dict() for m in self.discover()
                if m.installed and not m.error]

    # ---------- Install / uninstall (registry only; restart to apply) ----------
    def install(self, name: str) -> tuple[bool, str]:
        info = self.get(name)
        if info is None:
            return False, f"no module named {name!r}"
        if info.error:
            return False, f"module {name!r} has a manifest error: {info.error}"
        installed = self._load_installed()
        if name in installed:
            return True, f"{name!r} already installed"
        installed.add(name)
        self._save_installed(installed)
        log.info("module installed: %s (restart to load)", name)
        return True, f"{name!r} installed — restart pipineapple to load it"

    def uninstall(self, name: str) -> tuple[bool, str]:
        installed = self._load_installed()
        if name not in installed:
            return True, f"{name!r} not installed"
        installed.discard(name)
        self._save_installed(installed)
        log.info("module uninstalled: %s (restart to unload)", name)
        return True, f"{name!r} uninstalled — restart pipineapple to unload it"

    # ---------- Startup registration ----------
    def register_installed(self, app, package: str = "app.modules") -> list[str]:
        """Import + register the blueprint of every installed module.
        Called once from the app factory. Returns the names registered.
        A broken module is logged and skipped — it can't take the app down."""
        registered: list[str] = []
        for info in self.discover():
            if not info.installed or info.error:
                continue
            try:
                mod_part, _, attr = info.blueprint.partition(":")
                attr = attr or "bp"
                imported = importlib.import_module(f"{package}.{info.name}.{mod_part}")
                bp = getattr(imported, attr)
                # url_prefix on the blueprint wins; only override if the bp
                # didn't set one.
                if getattr(bp, "url_prefix", None):
                    app.register_blueprint(bp)
                else:
                    app.register_blueprint(bp, url_prefix=info.url_prefix)
                registered.append(info.name)
                app.logger.info("module registered: %s -> %s",
                                info.name, info.url_prefix)
            except Exception:
                app.logger.exception("module registration failed: %s", info.name)
        return registered


# ---------- Module singleton ----------
_loader: "ModuleLoader | None" = None


def get_loader() -> ModuleLoader:
    global _loader
    if _loader is None:
        from flask import current_app
        modules_dir = Path(current_app.root_path) / "modules"
        _loader = ModuleLoader(modules_dir, current_app.config["DATA_DIR"])
    return _loader
