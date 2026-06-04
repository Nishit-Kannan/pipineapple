// Settings page — adapter management interactions.
//
// All state mutations go through the JSON API at /settings/adapters/*.
// Each action surfaces in the Command Stream (via the backend's run()
// wrapper) and triggers a notification on completion.

(function () {
  "use strict";

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  function showStatus(msg, kind = "info") {
    const el = $("#settings-status");
    if (!el) return;
    el.hidden = false;
    el.textContent = msg;
    el.classList.remove("ok", "fail");
    if (kind === "ok") el.classList.add("ok");
    if (kind === "fail") el.classList.add("fail");
  }

  async function postJSON(url, body = {}) {
    const res = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    return res.json();
  }

  async function refreshAdapters() {
    try {
      const res = await fetch("/settings/adapters");
      const data = await res.json();
      renderAdapters(data.adapters || []);
    } catch (e) {
      console.warn("refreshAdapters failed", e);
    }
  }

  function renderAdapters(adapters) {
    const tbody = document.querySelector("#adapter-table tbody");
    if (!tbody) return;

    tbody.innerHTML = adapters.map(a => {
      let modeBadge;
      if (a.mode === "monitor")      modeBadge = '<span class="badge badge-warn">monitor</span>';
      else if (a.mode === "AP")      modeBadge = '<span class="badge badge-warn">ap</span>';
      else if (a.mode === "managed") modeBadge = '<span class="badge badge-muted">managed</span>';
      else                           modeBadge = `<span class="badge badge-muted">${escapeHtml(a.mode) || "—"}</span>`;

      const channel = a.channel
        ? `${a.channel} <span class="muted">(${a.frequency_mhz} MHz)</span>`
        : "—";

      const roleOpts = [
        ["none",         "— none —"],
        ["wlan-mon-2g",  "wlan-mon-2g"],
        ["wlan-mon-5g",  "wlan-mon-5g"],
        ["wlan-ap",      "wlan-ap"],
      ].map(([v, label]) => {
        const sel = v === a.role ? "selected" : "";
        return `<option value="${v}" ${sel}>${escapeHtml(label)}</option>`;
      }).join("");

      return `<tr data-mac="${escapeHtml(a.mac)}" data-iface="${escapeHtml(a.name)}">
        <td><code>${escapeHtml(a.name)}</code></td>
        <td><code>${escapeHtml(a.mac) || "—"}</code></td>
        <td><code>${escapeHtml(a.driver) || "—"}</code></td>
        <td>${modeBadge}</td>
        <td>${channel}</td>
        <td>
          <select class="role-select" data-mac="${escapeHtml(a.mac)}">${roleOpts}</select>
        </td>
        <td class="action-cell">
          <button class="actbtn" data-action="monitor" data-iface="${escapeHtml(a.name)}">Monitor</button>
          <button class="actbtn" data-action="managed" data-iface="${escapeHtml(a.name)}">Managed</button>
          <button class="actbtn actbtn-muted" data-action="down" data-iface="${escapeHtml(a.name)}">Down</button>
        </td>
      </tr>`;
    }).join("");

    // Update header stat cards
    const assignedCount = adapters.filter(a => a.is_offensive).length;
    const monitorCount = adapters.filter(a => a.mode === "monitor").length;
    const rolesField = $('[data-field="roles_count"]');
    if (rolesField) rolesField.textContent = `${assignedCount} / 3`;
    const monField = $('[data-field="monitor_count"]');
    if (monField) monField.textContent = String(monitorCount);
  }

  function escapeHtml(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // ---------- Event handlers ----------
  function init() {
    if (!document.querySelector("#adapter-table")) return; // not on settings page

    // Role select changes
    document.body.addEventListener("change", async (e) => {
      const sel = e.target.closest(".role-select");
      if (!sel) return;
      const mac = sel.dataset.mac;
      const role = sel.value;
      showStatus(`assigning ${role} to ${mac}…`);
      const res = await postJSON("/settings/adapters/role", { mac, role });
      if (res.ok) {
        showStatus(`role set: ${res.msg}`, "ok");
        renderAdapters(res.adapters || []);
      } else {
        showStatus(`role failed: ${res.msg}`, "fail");
      }
    });

    // Action buttons (monitor / managed / down)
    document.body.addEventListener("click", async (e) => {
      const btn = e.target.closest(".actbtn");
      if (!btn) return;
      const iface = btn.dataset.iface;
      const action = btn.dataset.action;
      btn.disabled = true;
      try {
        if (action === "monitor" || action === "managed") {
          showStatus(`${iface} → ${action}…`);
          const res = await postJSON(`/settings/adapters/${iface}/mode`, { mode: action });
          showStatus(
            (res.messages || []).join(" / "),
            res.ok ? "ok" : "fail"
          );
        } else if (action === "down") {
          showStatus(`bringing ${iface} down…`);
          const res = await postJSON(`/settings/adapters/${iface}/down`);
          showStatus(res.msg, res.ok ? "ok" : "fail");
        }
        await refreshAdapters();
      } finally {
        btn.disabled = false;
      }
    });

    // Apply buttons
    const applyUdev = $("#apply-udev");
    if (applyUdev) applyUdev.addEventListener("click", async () => {
      applyUdev.disabled = true;
      showStatus("writing udev rules…");
      try {
        const res = await postJSON("/settings/adapters/apply-udev");
        showStatus(res.msg, res.ok ? "ok" : "fail");
      } finally {
        applyUdev.disabled = false;
      }
    });

    const applyNm = $("#apply-nm");
    if (applyNm) applyNm.addEventListener("click", async () => {
      applyNm.disabled = true;
      showStatus("writing NM config…");
      try {
        const res = await postJSON("/settings/adapters/apply-nm");
        showStatus(res.msg, res.ok ? "ok" : "fail");
      } finally {
        applyNm.disabled = false;
      }
    });

    const stopMgrs = $("#stop-managers");
    if (stopMgrs) stopMgrs.addEventListener("click", async () => {
      if (!confirm("Stop NetworkManager + wpa_supplicant?\nwlan0 will lose home Wi-Fi until reboot or systemctl start.")) return;
      stopMgrs.disabled = true;
      showStatus("stopping managers…");
      try {
        const res = await postJSON("/settings/adapters/stop-managers");
        showStatus(res.msg, res.ok ? "ok" : "fail");
        await refreshAdapters();
      } finally {
        stopMgrs.disabled = false;
      }
    });

    // Auto-refresh on every sysinfo update so the table reflects mode
    // changes triggered from elsewhere or by external actions.
    if (window.pipineapple && window.pipineapple.socket) {
      const sock = window.pipineapple.socket;
      sock.on("sysinfo", () => refreshAdapters());
    } else {
      // socket might not be ready yet — defer
      window.addEventListener("load", () => {
        if (window.pipineapple && window.pipineapple.socket) {
          window.pipineapple.socket.on("sysinfo", () => refreshAdapters());
        }
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
