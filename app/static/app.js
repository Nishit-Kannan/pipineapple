// PiPineapple — front-end glue.
//
// Connects to the Flask-SocketIO server, subscribes to:
//   - "sysinfo"       — periodic dashboard update payload
//   - "notification"  — single new notification entry
//   - "notification:read_all" / "notification:clear" — drawer management
//
// Updates the live indicator in the title bar based on socket state.

(function () {
  "use strict";

  // ---------- DOM helpers ----------
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }
  function setField(name, html) {
    const el = document.querySelector(`[data-field="${name}"]`);
    if (el) el.innerHTML = html;
  }
  function setText(name, text) {
    const el = document.querySelector(`[data-field="${name}"]`);
    if (el) el.textContent = text;
  }

  // ---------- Format helpers (mirror Python's format_uptime / format_bytes) ----------
  function formatUptime(seconds) {
    if (seconds == null) return "—";
    const s = Math.floor(seconds);
    const days = Math.floor(s / 86400);
    const hours = Math.floor((s % 86400) / 3600);
    const mins = Math.floor((s % 3600) / 60);
    const parts = [];
    if (days) parts.push(`${days}d`);
    if (hours || days) parts.push(`${String(hours).padStart(2, "0")}h`);
    parts.push(`${String(mins).padStart(2, "0")}m`);
    return parts.join(" ");
  }

  function formatBytes(b) {
    if (b == null) return "—";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let size = b;
    for (let i = 0; i < units.length; i++) {
      if (size < 1024 || i === units.length - 1) {
        return units[i] === "B"
          ? `${Math.floor(size)} ${units[i]}`
          : `${size.toFixed(1)} ${units[i]}`;
      }
      size /= 1024;
    }
  }

  function escapeHtml(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function relativeTime(ts) {
    if (!ts) return "";
    const now = Date.now() / 1000;
    const diff = Math.floor(now - ts);
    if (diff < 5)   return "just now";
    if (diff < 60)  return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }

  // ---------- Button state helpers (UI pass) ----------
  // Shared so page scripts drive the one colour-code: blue actionable,
  // grey disabled, red busy. busy = action in progress (NOT greyed).
  function setBtnBusy(el, busy) { if (el) el.classList.toggle("is-busy", !!busy); }
  function setBtnDisabled(el, dis) {
    if (!el) return;
    el.disabled = !!dis;
    if (dis) el.classList.remove("is-busy");
  }

  // ---------- Live indicator ----------
  // Three states: "up" (connected), "connecting" (initial / mid-reconnect),
  // "down" (disconnect_error or socket.io client absent). We use
  // "connecting" instead of "down" during the initial page load so the
  // badge doesn't flash "OFFLINE" for the brief window before the first
  // SocketIO poll lands — which looked broken to operators.
  let _lastLiveState = "connecting";
  let _connectingSince = Date.now();
  function setLive(state) {
    _lastLiveState = state;
    if (state === "connecting") _connectingSince = Date.now();
    const ind = $("#live-indicator");
    if (!ind) return;
    ind.classList.remove("live-up", "live-down", "live-connecting");
    if (state === "up") {
      ind.classList.add("live-up");
      ind.title = "WebSocket connected — live updates active";
    } else if (state === "connecting") {
      ind.classList.add("live-connecting");
      ind.title = "Connecting to server…";
    } else {
      ind.classList.add("live-down");
      ind.title = "WebSocket disconnected";
    }
    const label = ind.querySelector(".live-label");
    if (label) {
      label.textContent =
        state === "up" ? "live" :
        state === "connecting" ? "connecting" :
        "offline";
    }
  }
  // Initial state — show "connecting" until the first connect event lands
  setLive("connecting");

  // ---------- Dashboard updates ----------
  function applySysinfo(status) {
    const sys = status.system;
    if (sys) {
      if (sys.cpu_temp_c != null) {
        setField("cpu_temp",
          `${sys.cpu_temp_c.toFixed(1)}<span class="statcard-unit">°C</span>`);
        const t = sys.cpu_temp_c;
        const cls = t >= 75 ? "badge-danger" : t >= 65 ? "badge-warn" : "badge-good";
        const label = t >= 75 ? "hot" : t >= 65 ? "warm" : "ok";
        setField("cpu_temp_badge", `<span class="badge ${cls}">${label}</span>`);
      } else {
        setField("cpu_temp", "—");
        setField("cpu_temp_badge", "");
      }
      if (sys.memory) {
        setField("memory_pct",
          `${sys.memory.used_pct}<span class="statcard-unit">%</span>`);
        setText("memory_bytes",
          `${formatBytes(sys.memory.used_bytes)} / ${formatBytes(sys.memory.total_bytes)}`);
      }
      setText("uptime", formatUptime(sys.uptime_seconds));
      setText("kernel", sys.kernel || "");
    }
    setText("radios_count", String((status.wireless || []).length));
    const monitorCount = (status.wireless || []).filter(w => w.mode === "monitor").length;
    setText("radios_monitor", `${monitorCount} in monitor mode`);
    setText("reg_domain", status.reg_domain || "—");

    // Rebuild wireless tbody
    const wlBody = document.querySelector('[data-field="wireless_tbody"]');
    if (wlBody && status.wireless) {
      wlBody.innerHTML = status.wireless.map(renderWirelessRow).join("");
    }
    // Rebuild interfaces tbody
    const ifBody = document.querySelector('[data-field="interfaces_tbody"]');
    if (ifBody && status.interfaces) {
      ifBody.innerHTML = status.interfaces.map(renderInterfaceRow).join("");
    }
  }

  function renderWirelessRow(w) {
    let modeBadge;
    if (w.mode === "monitor")       modeBadge = '<span class="badge badge-warn">monitor</span>';
    else if (w.mode === "AP")       modeBadge = '<span class="badge badge-warn">ap</span>';
    else if (w.mode === "managed")  modeBadge = '<span class="badge badge-muted">managed</span>';
    else                            modeBadge = `<span class="badge badge-muted">${escapeHtml(w.mode) || "—"}</span>`;

    const channel = w.channel
      ? `${w.channel} <span class="muted">(${w.frequency_mhz} MHz)</span>`
      : "—";
    const width = w.width_mhz ? `${w.width_mhz} MHz` : "—";
    const ssid = w.ssid
      ? `<code>${escapeHtml(w.ssid)}</code>`
      : '<span class="muted">—</span>';
    const driver = `<code>${escapeHtml(w.driver) || "—"}</code>`;
    const txp = w.txpower_dbm != null ? `${w.txpower_dbm.toFixed(1)} dBm` : "—";
    const sig = w.signal_dbm != null
      ? `${Math.round(w.signal_dbm)} dBm`
      : '<span class="muted">—</span>';

    return `<tr>
      <td><code>${escapeHtml(w.name)}</code></td>
      <td>${modeBadge}</td>
      <td>${channel}</td>
      <td>${width}</td>
      <td>${ssid}</td>
      <td>${driver}</td>
      <td>${txp}</td>
      <td>${sig}</td>
    </tr>`;
  }

  function renderInterfaceRow(iface) {
    let stateBadge;
    if (iface.state === "UP")        stateBadge = '<span class="badge badge-good">up</span>';
    else if (iface.state === "DOWN") stateBadge = '<span class="badge badge-muted">down</span>';
    else                             stateBadge = `<span class="badge badge-muted">${escapeHtml((iface.state || "").toLowerCase())}</span>`;

    const addrs = (iface.addresses && iface.addresses.length)
      ? iface.addresses.map(a => `<code>${escapeHtml(a)}</code>`).join("<br>")
      : '<span class="muted">—</span>';

    return `<tr>
      <td><code>${escapeHtml(iface.name)}</code></td>
      <td>${stateBadge}</td>
      <td><code>${escapeHtml(iface.mac) || "—"}</code></td>
      <td>${addrs}</td>
    </tr>`;
  }

  // ---------- Command stream drawer ----------
  const termState = {
    list: [],
    open: false,
    autoScroll: true,
  };

  function fmtClockTime(ts) {
    const d = new Date(ts * 1000);
    return `${String(d.getHours()).padStart(2, "0")}:`
         + `${String(d.getMinutes()).padStart(2, "0")}:`
         + `${String(d.getSeconds()).padStart(2, "0")}`;
  }

  function rcClass(rc) {
    if (rc == null)  return "term-rc-pending";
    if (rc === 0)    return "term-rc-ok";
    return "term-rc-fail";
  }

  function rcLabel(rc, durationMs) {
    if (rc == null)  return "running";
    const dur = durationMs != null ? ` (${durationMs.toFixed(0)}ms)` : "";
    return `rc=${rc}${dur}`;
  }

  function renderTermLine(entry) {
    const sourceCls = `term-source-${entry.source || "tool"}`;
    const noteHtml = entry.note
      ? `<div class="term-note">↳ ${escapeHtml(entry.note)}</div>`
      : "";
    return `<div class="term-line">
      <span class="term-ts">${fmtClockTime(entry.ts)}</span>
      <span class="term-source ${sourceCls}">${escapeHtml(entry.source || "tool")}</span>
      <span class="term-cmd">${escapeHtml(entry.cmd_str || (entry.cmd || []).join(" "))}</span>
      <span class="term-rc ${rcClass(entry.rc)}">${escapeHtml(rcLabel(entry.rc, entry.duration_ms))}</span>
      ${noteHtml}
    </div>`;
  }

  function renderTermBody() {
    const body = $("#term-body");
    if (!body) return;
    if (termState.list.length === 0) {
      body.innerHTML = `<div class="term-empty muted">
        No commands captured yet. Open the dashboard or trigger an action to see commands flow.
      </div>`;
      return;
    }
    // Render oldest-first so newest lands at the bottom (terminal-style).
    const oldestFirst = termState.list.slice().reverse();
    body.innerHTML = oldestFirst.map(renderTermLine).join("");
    if (termState.autoScroll) {
      body.scrollTop = body.scrollHeight;
    }
  }

  function appendTermLine(entry) {
    termState.list.unshift(entry);
    // Cap at 200 to match server-side
    termState.list = termState.list.slice(0, 200);
    if (!termState.open) return;
    const body = $("#term-body");
    if (!body) return;
    // If the empty placeholder is showing, replace it
    const empty = body.querySelector(".term-empty");
    if (empty) {
      body.innerHTML = "";
    }
    body.insertAdjacentHTML("beforeend", renderTermLine(entry));
    if (termState.autoScroll) {
      body.scrollTop = body.scrollHeight;
    }
  }

  function toggleTermDrawer(force) {
    const drawer = $("#term-drawer");
    if (!drawer) return;
    const willOpen = force != null ? force : drawer.hidden;
    drawer.hidden = !willOpen;
    termState.open = willOpen;
    if (willOpen) {
      renderTermBody();
      // Ask the server for recent history in case we missed events
      // before the drawer was opened.
      if (window.pipineapple && window.pipineapple.socket) {
        window.pipineapple.socket.emit("terminal:request_history");
      }
    }
  }

  function clearTerm() {
    termState.list = [];
    renderTermBody();
  }

  // ---------- Notifications drawer ----------
  const notifState = {
    list: [],
    open: false,
  };

  function badgeClassForSeverity(sev) {
    switch (sev) {
      case "info":    return "badge-info";
      case "warning": return "badge-warn";
      case "error":   return "badge-danger";
      case "success": return "badge-good";
      default:        return "badge-muted";
    }
  }

  function renderNotifList() {
    const ul = $("#notif-list");
    if (!ul) return;
    if (notifState.list.length === 0) {
      ul.innerHTML = '<li class="notif-empty muted">No notifications yet.</li>';
      return;
    }
    ul.innerHTML = notifState.list.map(n => `
      <li class="notif-item ${n.read ? "" : "unread"}">
        <span class="badge ${badgeClassForSeverity(n.severity)}">${escapeHtml(n.severity)}</span>
        <div class="notif-body">
          <div class="notif-message">${escapeHtml(n.message)}</div>
          <div class="notif-meta muted">${escapeHtml(n.source)} · ${relativeTime(n.ts)}</div>
        </div>
      </li>
    `).join("");
  }

  function updateNotifDot() {
    const dot = $("#notif-dot");
    if (!dot) return;
    const loud = notifState.list.filter(n =>
      !n.read && ["warning", "error", "success"].includes(n.severity)
    ).length;
    dot.hidden = loud === 0;
  }

  function toggleDrawer(force) {
    const drawer = $("#notif-drawer");
    if (!drawer) return;
    const willOpen = force != null ? force : drawer.hidden;
    drawer.hidden = !willOpen;
    notifState.open = willOpen;
    if (willOpen) renderNotifList();
  }

  function addNotification(entry) {
    notifState.list.unshift(entry);
    // Cap at 50 to match server-side
    notifState.list = notifState.list.slice(0, 50);
    if (notifState.open) renderNotifList();
    updateNotifDot();
  }

  function markAllRead() {
    notifState.list.forEach(n => n.read = true);
    renderNotifList();
    updateNotifDot();
  }

  function clearAll() {
    notifState.list = [];
    renderNotifList();
    updateNotifDot();
  }

  // ---------- Boot ----------
  function init() {
    // Universal click feedback — a brief red "performing" pulse on any
    // actionable button that isn't managed by page-level lifecycle state
    // (those carry data-stateful and own their own colour). The :active
    // CSS handles the press; this makes instant actions flash busy too.
    document.addEventListener("click", (e) => {
      const b = e.target.closest(".bigbtn, .actbtn");
      if (!b || b.disabled || b.dataset.stateful || b.classList.contains("is-busy")) return;
      b.classList.add("is-busy");
      setTimeout(() => b.classList.remove("is-busy"), 450);
    }, true);

    // Notification drawer button wiring
    const btn = $("#notif-btn");
    if (btn) btn.addEventListener("click", () => toggleDrawer());

    // Terminal drawer button wiring
    const termBtn = $("#term-btn");
    if (termBtn) termBtn.addEventListener("click", () => toggleTermDrawer());
    const termClose = $("#term-close");
    if (termClose) termClose.addEventListener("click", () => toggleTermDrawer(false));
    const termClear = $("#term-clear");
    if (termClear) termClear.addEventListener("click", () => clearTerm());

    // Pause autoscroll if the user scrolls up within the term body
    const termBody = $("#term-body");
    if (termBody) {
      termBody.addEventListener("scroll", () => {
        const atBottom = termBody.scrollHeight - termBody.scrollTop - termBody.clientHeight < 16;
        termState.autoScroll = atBottom;
      });
    }

    const markBtn = $("#notif-mark-read");
    if (markBtn) markBtn.addEventListener("click", () => {
      markAllRead();
      // Optional: tell server, but server doesn't currently care
    });

    const clearBtn = $("#notif-clear");
    if (clearBtn) clearBtn.addEventListener("click", () => {
      clearAll();
      // Optional: tell server to clear its buffer too
      fetch("/debug/notify/clear", { method: "POST" }).catch(() => {});
    });

    // Close drawer when clicking outside
    document.addEventListener("click", (e) => {
      const drawer = $("#notif-drawer");
      const trigger = $("#notif-btn");
      if (!drawer || drawer.hidden) return;
      if (drawer.contains(e.target) || (trigger && trigger.contains(e.target))) return;
      toggleDrawer(false);
    });

    // ---------- Power menu (reboot / shutdown) ----------
    const powerBtn = $("#power-btn");
    const powerMenu = $("#power-menu");
    function togglePower(show) {
      if (!powerMenu) return;
      const open = show === undefined ? powerMenu.hidden : show;
      powerMenu.hidden = !open;
      if (powerBtn) powerBtn.setAttribute("aria-expanded", String(open));
    }
    if (powerBtn) powerBtn.addEventListener("click", (e) => { e.stopPropagation(); togglePower(); });
    document.addEventListener("click", (e) => {
      if (!powerMenu || powerMenu.hidden) return;
      if (powerMenu.contains(e.target) || (powerBtn && powerBtn.contains(e.target))) return;
      togglePower(false);
    });

    // Delegate power actions — works for the title-bar menu AND the
    // Settings → System card (both use [data-power-action]).
    async function doPower(action) {
      const isShutdown = action === "shutdown";
      const word = isShutdown ? "shut down" : "reboot";
      let msg = `Are you sure you want to ${word} the Pi?`;
      if (isShutdown) {
        msg += "\n\nIt will power off completely and need a physical power "
             + "cycle to come back. You'll lose this connection.";
      } else {
        msg += "\n\nYou'll lose this connection until it's back up (~30–60s).";
      }
      if (!window.confirm(msg)) return;
      try {
        const r = await fetch(`/settings/system/${action}`, { method: "POST" });
        const data = await r.json().catch(() => ({}));
        alert(data.msg || (isShutdown ? "Shutting down…" : "Rebooting…"));
      } catch (_) {
        // The box is going down — a dropped connection here is expected.
        alert(isShutdown ? "Shutting down…" : "Rebooting…");
      }
    }
    document.addEventListener("click", (e) => {
      const el = e.target.closest && e.target.closest("[data-power-action]");
      if (!el) return;
      e.preventDefault();
      togglePower(false);
      doPower(el.dataset.powerAction);
    });

    // Connect SocketIO if the library is loaded
    if (typeof io === "undefined") {
      console.warn("socket.io client not loaded; live updates disabled");
      setLive("down");
      return;
    }

    // Polling-only — the server disables upgrades (see app/__init__.py for
    // why). The client tries polling first by default; explicitly omitting
    // websocket from the allowed transports keeps the network panel clean.
    const socket = io({ transports: ["polling"], upgrade: false });

    socket.on("connect", () => { setLive("up"); });
    socket.on("disconnect", () => { setLive("connecting"); });
    socket.on("connect_error", () => {
      // Only flip to "down" if connect_error keeps firing for a few
      // seconds. A single error during initial connect or transport
      // upgrade isn't worth shouting OFFLINE about.
      if (_lastLiveState === "up" || Date.now() - _connectingSince > 5000) {
        setLive("down");
      } else {
        setLive("connecting");
      }
    });

    socket.on("sysinfo", (data) => {
      try { applySysinfo(data); }
      catch (e) { console.error("applySysinfo failed", e); }
    });

    socket.on("notification", (entry) => {
      addNotification(entry);
    });

    socket.on("notification:read_all", () => {
      markAllRead();
    });

    socket.on("notification:clear", () => {
      clearAll();
    });

    // Terminal stream — non-polling commands run by the platform
    socket.on("terminal:cmd", (entry) => {
      appendTermLine(entry);
    });

    socket.on("terminal:history", (history) => {
      // Replace local state with the server's authoritative ring buffer
      termState.list = Array.isArray(history) ? history : [];
      if (termState.open) renderTermBody();
    });

    socket.on("terminal:clear", () => {
      clearTerm();
    });

    // Ask for recent history on every connect so the drawer is seeded
    // even if the user opens it later in the session.
    socket.on("connect", () => {
      socket.emit("terminal:request_history");
    });

    // Expose for debugging + page scripts (button helpers).
    window.pipineapple = { socket, notifState, termState, setBtnBusy, setBtnDisabled };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
