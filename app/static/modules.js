// Modules page (Session 15) — list + install/uninstall (restart-on-change).
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  if (!$("modules-tbody")) return; // only on the Modules page

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function status(msg, kind) {
    const el = $("modules-status");
    if (!el) return;
    el.textContent = msg;
    el.hidden = !msg;
    el.className = "settings-status " + (kind === "fail" ? "fail" : kind === "ok" ? "ok" : "muted");
  }

  function render(modules) {
    const tbody = $("modules-tbody");
    if ($("modules-count")) $("modules-count").textContent = String(modules.length);
    if (!modules.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="muted">No modules found under app/modules/.</td></tr>`;
      return;
    }
    tbody.innerHTML = modules.map((m) => {
      let statusCell, action;
      if (m.error) {
        statusCell = `<span class="badge badge-warn">error</span>`;
        action = `<span class="muted" style="font-size:11px;">${esc(m.error)}</span>`;
      } else if (m.installed) {
        statusCell = `<span class="badge badge-good">installed</span>`;
        action = `<button class="bigbtn actbtn-muted mod-act" data-name="${esc(m.name)}" data-act="uninstall" style="margin-top:0; font-size:12px;">Uninstall</button>`;
      } else {
        statusCell = `<span class="badge">available</span>`;
        action = `<button class="bigbtn mod-act" data-name="${esc(m.name)}" data-act="install" style="margin-top:0; font-size:12px;">Install</button>`;
      }
      return `<tr>
        <td><strong>${esc(m.label)}</strong><div class="muted" style="font-size:11px;"><code>${esc(m.url_prefix)}</code></div></td>
        <td class="muted">${esc(m.version) || "—"}</td>
        <td style="font-size:12px;">${esc(m.description)}</td>
        <td>${statusCell}</td>
        <td>${action}</td>
      </tr>`;
    }).join("");

    tbody.querySelectorAll(".mod-act").forEach((btn) => {
      btn.addEventListener("click", () => onAction(btn.dataset.name, btn.dataset.act, btn));
    });
  }

  async function onAction(name, act, btn) {
    if (btn) btn.disabled = true;
    try {
      const r = await fetch(`/modules/${encodeURIComponent(name)}/${act}`, { method: "POST" });
      const data = await r.json();
      status(data.msg || (data.ok ? "done" : "failed"), data.ok ? "ok" : "fail");
      if (data.modules) render(data.modules);
    } catch (e) {
      status("request failed: " + e, "fail");
      if (btn) btn.disabled = false;
    }
  }

  async function load() {
    try {
      const r = await fetch("/modules/list");
      render((await r.json()).modules || []);
    } catch (e) {
      console.error("[modules] load:", e);
    }
  }

  load();
})();
