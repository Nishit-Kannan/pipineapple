/* Recon page — live AP + Client tables driven by SocketIO 'recon:update'.
 *
 * Initial render comes from the server-side template; after that, every
 * recon:update event re-renders both <tbody>s in place. Sortable columns
 * are click-driven and remember their state in-memory only.
 */

(function () {
  "use strict";

  // ---- State ----
  let aps = [];       // last-known AP list (from server)
  let clients = [];   // last-known Client list
  let sortAps = { key: "signal_dbm", dir: "desc" };
  let sortClients = { key: "last_seen", dir: "desc" };

  // ---- Helpers ----
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (s) =>
    String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  function signalClass(dbm) {
    if (dbm == null || dbm === "") return "sig-unknown";
    const n = Number(dbm);
    if (n >= -55) return "sig-strong";
    if (n >= -70) return "sig-medium";
    if (n >= -85) return "sig-weak";
    return "sig-faint";
  }

  function compareBy(key, dir) {
    const sign = dir === "desc" ? -1 : 1;
    return (a, b) => {
      const av = a[key];
      const bv = b[key];
      // null/undefined sort last regardless of direction
      const aMissing = av == null || av === "";
      const bMissing = bv == null || bv === "";
      if (aMissing && bMissing) return 0;
      if (aMissing) return 1;
      if (bMissing) return -1;
      // Numeric vs string detection
      if (typeof av === "number" && typeof bv === "number") {
        return sign * (av - bv);
      }
      return sign * String(av).localeCompare(String(bv));
    };
  }

  // ---- Rendering ----
  function renderAps() {
    const tbody = $("recon-ap-tbody");
    if (!tbody) return;
    const sorted = [...aps].sort(compareBy(sortAps.key, sortAps.dir));
    if (sorted.length === 0) {
      tbody.innerHTML = "";
      const empty = $("recon-ap-empty");
      if (empty) empty.hidden = false;
    } else {
      const empty = $("recon-ap-empty");
      if (empty) empty.hidden = true;
      tbody.innerHTML = sorted.map((ap) => {
        const sec = ap.encryption + (ap.auth ? "/" + ap.auth : "");
        const sigVal = ap.signal_dbm == null ? "—" : ap.signal_dbm;
        return `<tr data-bssid="${escapeHtml(ap.bssid)}">
          <td><span class="sig-pill ${signalClass(ap.signal_dbm)}">${escapeHtml(sigVal)}</span></td>
          <td>${escapeHtml(ap.band || "—")}</td>
          <td>${escapeHtml(ap.channel || "—")}</td>
          <td>${ap.essid ? escapeHtml(ap.essid) : "<em class=\"muted\">&lt;hidden&gt;</em>"}</td>
          <td><code>${escapeHtml(ap.bssid)}</code></td>
          <td>${escapeHtml(sec)}</td>
          <td>${escapeHtml(ap.beacons)}</td>
          <td>${escapeHtml(ap.data_packets)}</td>
          <td class="muted">${escapeHtml(ap.last_seen || "—")}</td>
        </tr>`;
      }).join("");
    }
    const count = $("recon-ap-count");
    if (count) count.textContent = String(aps.length);
  }

  function renderClients() {
    const tbody = $("recon-client-tbody");
    if (!tbody) return;
    const sorted = [...clients].sort(compareBy(sortClients.key, sortClients.dir));
    if (sorted.length === 0) {
      tbody.innerHTML = "";
      const empty = $("recon-client-empty");
      if (empty) empty.hidden = false;
    } else {
      const empty = $("recon-client-empty");
      if (empty) empty.hidden = true;
      tbody.innerHTML = sorted.map((c) => {
        const sigVal = c.signal_dbm == null ? "—" : c.signal_dbm;
        const probed = (c.probed_essids && c.probed_essids.length)
          ? c.probed_essids.map(escapeHtml).join(", ")
          : "—";
        return `<tr data-mac="${escapeHtml(c.station_mac)}">
          <td><span class="sig-pill ${signalClass(c.signal_dbm)}">${escapeHtml(sigVal)}</span></td>
          <td><code>${escapeHtml(c.station_mac)}</code></td>
          <td><code>${escapeHtml(c.bssid)}</code></td>
          <td>${escapeHtml(c.packets)}</td>
          <td>${probed}</td>
          <td class="muted">${escapeHtml(c.last_seen || "—")}</td>
        </tr>`;
      }).join("");
    }
    const count = $("recon-client-count");
    if (count) count.textContent = String(clients.length);
  }

  function setStatus(status) {
    const badge = $("recon-state-badge");
    if (badge) {
      badge.textContent = status.state;
      badge.dataset.state = status.state;
      badge.className = "badge " + (
        status.state === "running" ? "badge-good" :
        status.state === "starting" || status.state === "stopping" ? "badge-warn" :
        ""
      );
    }
    const counts = $("recon-counts");
    if (counts) {
      counts.textContent = `${status.ap_count} APs · ${status.client_count} clients`;
    }
    const startBtn = $("recon-start");
    const stopBtn = $("recon-stop");
    if (startBtn) startBtn.disabled = status.state !== "idle";
    if (stopBtn)  stopBtn.disabled  = status.state === "idle";
  }

  // ---- Actions ----
  async function postJson(url, body) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : "{}",
      });
      return await res.json();
    } catch (e) {
      return { ok: false, messages: ["network error: " + e] };
    }
  }

  async function onStart() {
    const startBtn = $("recon-start");
    if (startBtn) startBtn.disabled = true;
    const res = await postJson("/recon/start");
    if (res.status) setStatus(res.status);
    if (!res.ok && startBtn) startBtn.disabled = false;
  }

  async function onStop() {
    const stopBtn = $("recon-stop");
    if (stopBtn) stopBtn.disabled = true;
    const res = await postJson("/recon/stop");
    if (res.status) setStatus(res.status);
    // Clear tables immediately for snappy UX
    aps = [];
    clients = [];
    renderAps();
    renderClients();
  }

  // ---- Sortable column headers ----
  function attachSortHandlers() {
    document.querySelectorAll("#recon-ap-table th[data-sort]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        sortAps.dir = (sortAps.key === key && sortAps.dir === "desc") ? "asc" : "desc";
        sortAps.key = key;
        renderAps();
      });
    });
    document.querySelectorAll("#recon-client-table th[data-sort]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        sortClients.dir = (sortClients.key === key && sortClients.dir === "desc") ? "asc" : "desc";
        sortClients.key = key;
        renderClients();
      });
    });
  }

  // ---- Wire-up ----
  document.addEventListener("DOMContentLoaded", () => {
    const startBtn = $("recon-start");
    const stopBtn = $("recon-stop");
    if (startBtn) startBtn.addEventListener("click", onStart);
    if (stopBtn)  stopBtn.addEventListener("click", onStop);
    attachSortHandlers();

    // Fetch one snapshot immediately so we don't wait up to POLL_INTERVAL
    // for the first SocketIO event after page load.
    fetch("/recon/snapshot")
      .then((r) => r.json())
      .then((snap) => {
        aps = snap.aps || [];
        clients = snap.clients || [];
        if (snap.status) setStatus(snap.status);
        renderAps();
        renderClients();
      })
      .catch(() => {});

    // Subscribe to live updates.
    const tryWire = () => {
      const sock = window.pipineapple && window.pipineapple.socket;
      if (!sock) {
        setTimeout(tryWire, 200);
        return;
      }
      sock.on("recon:update", (snap) => {
        aps = snap.aps || [];
        clients = snap.clients || [];
        if (snap.status) setStatus(snap.status);
        renderAps();
        renderClients();
      });
    };
    tryWire();
  });
})();
