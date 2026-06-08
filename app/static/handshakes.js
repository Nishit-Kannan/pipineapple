/* Handshakes top-level page (Session 08).
 *
 * Loads /handshakes/list on page load, re-renders the table, supports:
 *   - per-row download (raw pcap, .22000 hashcat format)
 *   - per-row delete
 *   - bulk select + bulk delete
 *   - refresh button (manual)
 *   - automatic refresh on capture:status SocketIO events (ended=true)
 *
 * Other recon.js helpers (_fmtBytes, _fmtTs, _fmtMsgDots) aren't shared
 * across files in this codebase, so we re-implement the small ones we
 * need locally. Keeps the page self-contained.
 */

(function () {
  "use strict";

  // Only run on the handshakes page
  document.addEventListener("DOMContentLoaded", () => {
    if (!document.getElementById("hs-tbody")) return;
    init();
  });

  const $ = (id) => document.getElementById(id);
  const escapeHtml = (s) =>
    String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const selected = new Set();   // capture ids currently checked

  function fmtBytes(n) {
    if (n == null) return "—";
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }
  function fmtTs(u) {
    if (!u) return "—";
    return new Date(u * 1000).toLocaleString();
  }
  function fmtMsgDots(msgs) {
    const set = new Set(msgs || []);
    return [1, 2, 3, 4].map((n) =>
      set.has(n)
        ? `<span class="msg-dot msg-dot-on" title="M${n} captured">M${n}</span>`
        : `<span class="msg-dot" title="M${n} not seen">M${n}</span>`
    ).join("");
  }

  function init() {
    $("hs-refresh").addEventListener("click", reload);
    $("hs-bulk-delete").addEventListener("click", onBulkDelete);
    $("hs-select-all").addEventListener("change", onSelectAll);

    // Subscribe to capture lifecycle for live refresh
    const tryWire = () => {
      const sock = window.pipineapple && window.pipineapple.socket;
      if (!sock) { setTimeout(tryWire, 200); return; }
      sock.on("capture:status", (p) => {
        if (p && p.ended) reload();
      });
    };
    tryWire();

    reload();
  }

  async function reload() {
    let captures = [];
    try {
      const r = await fetch("/handshakes/list");
      if (r.ok) {
        const data = await r.json();
        captures = data.captures || [];
      }
    } catch (e) { /* render empty */ }
    selected.clear();
    updateBulkButton();
    render(captures);
  }

  function render(captures) {
    const tbody = $("hs-tbody");
    const table = $("hs-table");
    const empty = $("hs-empty");
    const count = $("hs-count");

    if (count) count.textContent = String(captures.length);

    if (!captures.length) {
      if (tbody) tbody.innerHTML = "";
      if (table) table.hidden = true;
      if (empty) empty.hidden = false;
      return;
    }

    if (table) table.hidden = false;
    if (empty) empty.hidden = true;

    tbody.innerHTML = captures.map((c) => rowHtml(c)).join("");

    // Per-row delete
    tbody.querySelectorAll("[data-act=del]").forEach((b) => {
      b.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm("Delete this capture? The pcap and .22000 cache will be removed.")) return;
        await fetch("/handshakes/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: b.dataset.id }),
        });
        reload();
      });
    });

    // Per-row checkboxes
    tbody.querySelectorAll("input[type=checkbox][data-cid]").forEach((cb) => {
      cb.addEventListener("change", () => {
        if (cb.checked) selected.add(cb.dataset.cid);
        else selected.delete(cb.dataset.cid);
        updateBulkButton();
      });
    });
  }

  function rowHtml(c) {
    let pill;
    if (c.is_complete)      pill = `<span class="capture-pill capture-complete">complete</span>`;
    else if (c.is_partial)  pill = `<span class="capture-pill capture-partial">partial</span>`;
    else                     pill = `<span class="capture-pill capture-progress">no hs</span>`;
    const pmkid = c.has_pmkid
      ? ` <span class="capture-pill capture-complete" title="PMKID captured — crackable on its own">PMKID</span>`
      : "";
    // Source label: Recon Capture for everything we make today. Phase D
    // will add Evil WPA / Evil Enterprise sources; index entries from
    // those will carry source="..." that we'll display verbatim.
    const source = escapeHtml(c.source || "Recon capture");
    const deauthBit = c.deauth_used ? `deauth ×${c.deauth_count || 0}` : "passive";

    return `<tr data-id="${escapeHtml(c.id)}">
      <td><input type="checkbox" data-cid="${escapeHtml(c.id)}"
                 ${selected.has(c.id) ? "checked" : ""}></td>
      <td><span class="muted">${source}</span></td>
      <td><strong>${escapeHtml(c.essid_at_capture || "<unknown>")}</strong></td>
      <td><code>${escapeHtml(c.bssid)}</code></td>
      <td>${escapeHtml(c.channel_at_capture || "—")}</td>
      <td><span class="muted">${escapeHtml(c.tool || "hcxdumptool")}</span></td>
      <td>${pill}${pmkid} ${fmtMsgDots(c.messages_seen)}
        <div class="muted" style="font-size:10px; margin-top:2px;">${deauthBit}</div></td>
      <td class="muted">${escapeHtml(fmtTs(c.started_at))}</td>
      <td>${escapeHtml(c.duration_secs || 0)}s</td>
      <td>${escapeHtml(fmtBytes(c.pcap_size_bytes))}</td>
      <td>
        <a class="actbtn actbtn-muted" style="font-size:11px;"
           href="/handshakes/${encodeURIComponent(c.id)}/download/pcap"
           title="Download raw pcap / pcapng">pcap</a>
        <a class="actbtn actbtn-muted" style="font-size:11px;"
           href="/handshakes/${encodeURIComponent(c.id)}/download/22000"
           title="Convert (cached) and download hashcat .22000 format">.22000</a>
        <button class="actbtn actbtn-muted" data-act="del" data-id="${escapeHtml(c.id)}"
                style="font-size:11px;" title="Delete">×</button>
      </td>
    </tr>`;
  }

  function onSelectAll(e) {
    const tbody = $("hs-tbody");
    if (!tbody) return;
    const cbs = tbody.querySelectorAll("input[type=checkbox][data-cid]");
    cbs.forEach((cb) => {
      cb.checked = e.target.checked;
      if (e.target.checked) selected.add(cb.dataset.cid);
      else selected.delete(cb.dataset.cid);
    });
    updateBulkButton();
  }

  function updateBulkButton() {
    const btn = $("hs-bulk-delete");
    if (!btn) return;
    btn.disabled = selected.size === 0;
    btn.textContent = selected.size === 0
      ? "Delete selected"
      : `Delete ${selected.size} selected`;
  }

  async function onBulkDelete() {
    if (selected.size === 0) return;
    if (!confirm(`Delete ${selected.size} capture(s)? Their pcap and .22000 cache files will be removed.`)) return;
    // Sequential — backend is fast, no need for parallel
    for (const id of Array.from(selected)) {
      await fetch("/handshakes/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id }),
      });
    }
    selected.clear();
    reload();
  }
})();
