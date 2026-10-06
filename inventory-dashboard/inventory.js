"use strict";

/* ---------------------------------------------------------------------
 * Inventory & Fleet Management - Cockpit module
 * All server communication goes through inventory_cli.py (run as root via
 * cockpit.spawn), which itself holds the admin token in a root-only config
 * file. The browser never sees any server secret.
 * ------------------------------------------------------------------- */

const CLI_PATH = "/usr/share/cockpit/inventory-dashboard/inventory_cli.py";

const STATE = {
  agents: [],
  agentsLoadedAt: 0,
  currentHostId: null
};

/* ---------------- generic helpers ---------------- */

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function el(html) {
  const div = document.createElement("div");
  div.innerHTML = html.trim();
  return div.firstElementChild;
}

function fmtBytes(n) {
  if (n === null || n === undefined) return "\u2014";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function fmtDate(ts) {
  if (!ts) return "never";
  return new Date(ts * 1000).toLocaleString();
}

function fmtAgo(ts) {
  if (!ts) return "never";
  const secs = Math.max(0, Date.now() / 1000 - ts);
  if (secs < 60) return "just now";
  if (secs < 3600) return Math.floor(secs / 60) + "m ago";
  if (secs < 86400) return Math.floor(secs / 3600) + "h ago";
  return Math.floor(secs / 86400) + "d ago";
}

function showAlert(type, message, opts) {
  opts = opts || {};
  const area = document.getElementById("inv-alert-area");
  const box = el(`<div class="alert ${type}"><span class="alert-close">&times;</span><div class="alert-msg"></div></div>`);
  box.querySelector(".alert-msg").textContent = message;
  box.querySelector(".alert-close").addEventListener("click", () => box.remove());
  area.prepend(box);
  if (type === "success" && !opts.sticky) setTimeout(() => box.remove(), 4500);
  return box;
}

function errText(err) {
  if (!err) return "Unknown error";
  return typeof err === "string" ? err : (err.message || JSON.stringify(err));
}

/* Calls inventory_cli.py as root and returns parsed .data, or throws with
   .error as the message. Always expects the CLI's {"ok":..,"data":..,
   "error":..} envelope, so failures never show up as a raw traceback.
   Pass opts.input as a string for normal (JSON-stdin) calls, or as a
   Uint8Array with opts.binary=true to stream raw bytes to stdin (used only
   for uploading approved-file bytes) - the CLI's stdout is always plain
   JSON text either way, so binary mode is decoded back to text here. */
async function cli(args, opts) {
  opts = opts || {};
  const input = opts.input;
  const binary = !!opts.binary;
  const spawnOpts = Object.assign({ superuser: "require", err: "message" }, opts);
  delete spawnOpts.input;
  if (!binary) delete spawnOpts.binary;
  let out;
  try {
    const process = cockpit.spawn(["python3", CLI_PATH, ...args], spawnOpts);
    if (input !== undefined) process.input(input, false);
    out = await process;
  } catch (e) {
    throw new Error(errText(e));
  }
  const text = binary ? new TextDecoder("utf-8").decode(out) : out;
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch (e) {
    throw new Error("Unexpected output from inventory_cli.py: " + String(text).slice(0, 300));
  }
  if (!parsed.ok) throw new Error(parsed.error || "Unknown error");
  return parsed.data;
}

/* ---------------- modal ---------------- */

function openModal(title, bodyHtml, opts) {
  opts = opts || {};
  const host = document.getElementById("modal-host");
  const backdrop = el(`
    <div class="modal-backdrop">
      <div class="modal ${opts.wide ? "wide" : ""}">
        <div class="modal-header"><h3></h3><button class="modal-close">&times;</button></div>
        <div class="modal-body"></div>
        <div class="modal-footer"></div>
      </div>
    </div>`);
  backdrop.querySelector("h3").textContent = title;
  backdrop.querySelector(".modal-body").innerHTML = bodyHtml;
  const footer = backdrop.querySelector(".modal-footer");
  function close() { backdrop.remove(); }
  backdrop.querySelector(".modal-close").addEventListener("click", close);
  backdrop.addEventListener("click", (e) => { if (e.target === backdrop) close(); });
  (opts.buttons || []).forEach((btn) => {
    const b = document.createElement("button");
    b.textContent = btn.label;
    b.className = btn.className || "";
    b.addEventListener("click", () => btn.onClick(close, backdrop));
    footer.appendChild(b);
  });
  host.appendChild(backdrop);
  return { close, node: backdrop };
}

function confirmModal(message, onConfirm) {
  openModal("Confirm", `<p>${escapeHtml(message)}</p>`, {
    buttons: [
      { label: "Cancel", onClick: (close) => close() },
      { label: "Confirm", className: "danger", onClick: (close) => { close(); onConfirm(); } }
    ]
  });
}

/* ---------------- charts (hand-rolled inline SVG, no dependency) ---------------- */

function donutChartSvg(segments, opts) {
  opts = opts || {};
  const size = opts.size || 160, stroke = opts.stroke || 24;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const total = segments.reduce((s, x) => s + x.value, 0);
  if (total === 0) return `<div class="chart-empty">No data yet</div>`;
  let offset = 0;
  const rings = segments.filter((s) => s.value > 0).map((seg) => {
    const dash = (seg.value / total) * c;
    const ring = `<circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${seg.color}" stroke-width="${stroke}" ` +
      `stroke-dasharray="${dash.toFixed(2)} ${(c - dash).toFixed(2)}" stroke-dashoffset="${(-offset).toFixed(2)}" ` +
      `transform="rotate(-90 ${size / 2} ${size / 2})"><title>${escapeHtml(seg.label)}: ${seg.value}</title></circle>`;
    offset += dash;
    return ring;
  }).join("");
  const svg = `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}">${rings}` +
    `<text class="chart-total" x="50%" y="50%" text-anchor="middle" dominant-baseline="central" font-size="22" font-weight="700">${total}</text></svg>`;
  const legend = segments.filter((s) => s.value > 0).map((seg) => `
    <div class="chart-legend-item"><span class="chart-legend-swatch" style="background:${seg.color}"></span>${escapeHtml(seg.label)}: ${seg.value}</div>
  `).join("");
  return `<div style="display:flex;align-items:center;gap:20px;flex-wrap:wrap"><div>${svg}</div><div class="chart-legend">${legend}</div></div>`;
}

function barChartSvg(segments, opts) {
  opts = opts || {};
  const width = opts.width || 220, barHeight = opts.barHeight || 22, gap = 12;
  const total = segments.reduce((s, x) => s + x.value, 0);
  if (total === 0) return `<div class="chart-empty">No data yet</div>`;
  const height = segments.length * (barHeight + gap) - gap;
  const bars = segments.map((seg, i) => {
    const w = Math.max(2, (seg.value / total) * width);
    const y = i * (barHeight + gap);
    return `<rect x="0" y="${y}" width="${w.toFixed(1)}" height="${barHeight}" rx="4" fill="${seg.color}"><title>${escapeHtml(seg.label)}: ${seg.value}</title></rect>` +
      `<text class="chart-label" x="${width + 10}" y="${y + barHeight / 2}" dominant-baseline="middle" font-size="12.5">${escapeHtml(seg.label)} (${seg.value})</text>`;
  }).join("");
  return `<svg viewBox="0 0 ${width + 150} ${height}" width="100%" height="${height}" style="max-width:380px">${bars}</svg>`;
}

const OS_COLORS = { linux: "#f0803c", windows: "#00a4ef", macos: "#a3a3a3" };
const OS_LABELS = { linux: "Linux", windows: "Windows", macos: "macOS" };

/* ---------------- theme sync ---------------- */

function initThemeSync() {
  function resolve() {
    let manual = null;
    try { manual = localStorage.getItem("inv:theme"); } catch (e) { /* ignore */ }
    if (manual === "light" || manual === "dark") return manual;
    let pref = "auto";
    try { pref = localStorage.getItem("shell:style") || "auto"; } catch (e) { /* ignore */ }
    if (pref === "light" || pref === "dark") return pref;
    return (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light";
  }
  function currentManual() {
    try { return localStorage.getItem("inv:theme") || "auto"; } catch (e) { return "auto"; }
  }
  function apply() {
    document.documentElement.setAttribute("data-theme", resolve());
    const btn = document.getElementById("inv-theme-toggle");
    if (btn) {
      const m = currentManual();
      btn.textContent = "Theme: " + (m === "auto" ? "Auto" : m === "dark" ? "Dark" : "Light");
    }
  }
  apply();
  window.addEventListener("storage", (e) => { if (!e.key || e.key === "shell:style" || e.key === "inv:theme") apply(); });
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    (mq.addEventListener ? mq.addEventListener.bind(mq) : mq.addListener.bind(mq))("change", apply);
  }
  document.getElementById("inv-theme-toggle").addEventListener("click", () => {
    const order = ["auto", "light", "dark"];
    const next = order[(order.indexOf(currentManual()) + 1) % order.length];
    try { next === "auto" ? localStorage.removeItem("inv:theme") : localStorage.setItem("inv:theme", next); } catch (e) { /* ignore */ }
    apply();
  });
}

/* ---------------- tabs ---------------- */

function initTabs() {
  const tabs = document.querySelectorAll(".sadc-tab");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".sadc-panel").forEach((p) => p.classList.remove("active"));
      tab.classList.add("active");
      document.getElementById("panel-" + tab.dataset.panel).classList.add("active");
      loadPanel(tab.dataset.panel);
    });
  });
}

const PANEL_LOADERS = {
  dashboard: loadDashboard,
  hosts: loadHosts,
  deploy: loadDeploy,
  enrollment: loadEnrollment,
  settings: loadSettings
};
const LOADED_ONCE = {};

function loadPanel(name, force) {
  if (LOADED_ONCE[name] && !force) return;
  LOADED_ONCE[name] = true;
  PANEL_LOADERS[name] && PANEL_LOADERS[name]();
}

/* ---------------- server status badge ---------------- */

async function checkServerStatus() {
  const badge = document.getElementById("inv-server-badge");
  const line = document.getElementById("inv-server-line");
  try {
    const status = await cli(["config-status"]);
    if (!status.has_token || !status.config_path_exists) {
      badge.textContent = "not configured";
      badge.className = "badge warn";
      line.textContent = "Run the server installer to create /etc/inventory-server/server.env.";
      return;
    }
    await cli(["dashboard"]);
    badge.textContent = "connected";
    badge.className = "badge ok";
    line.textContent = `Server: ${status.server_url}`;
  } catch (e) {
    badge.textContent = "unreachable";
    badge.className = "badge err";
    line.textContent = errText(e);
  }
}

/* ---------------- agents cache ---------------- */

async function getAgents(force) {
  if (STATE.agents.length && !force && (Date.now() - STATE.agentsLoadedAt) < 15000) return STATE.agents;
  const data = await cli(["agents"]);
  STATE.agents = data.agents;
  STATE.agentsLoadedAt = Date.now();
  return STATE.agents;
}

/* ---------------- DASHBOARD ---------------- */

async function loadDashboard() {
  try {
    const summary = await cli(["dashboard"]);
    renderMetrics(summary);
    renderOsChart(summary.os_breakdown);
    renderOnlineChart(summary.online, summary.offline);
    renderRecentHosts(true);
  } catch (e) {
    document.getElementById("dash-metrics").innerHTML = `<div class="alert error">${escapeHtml(errText(e))}</div>`;
  }
}

function renderMetrics(summary) {
  const cards = [
    { label: "Total agents", value: summary.total_agents, cls: "" },
    { label: "Online", value: summary.online, cls: "ok" },
    { label: "Offline", value: summary.offline, cls: summary.offline > 0 ? "warn" : "" },
    { label: "Software packages tracked", value: summary.software_packages_tracked, cls: "" }
  ];
  document.getElementById("dash-metrics").innerHTML = cards.map((c) => `
    <div class="metric-card ${c.cls}"><div class="metric-value">${c.value}</div><div class="metric-label">${escapeHtml(c.label)}</div></div>
  `).join("");
}

function renderOsChart(osBreakdown) {
  const segments = Object.keys(OS_LABELS).map((k) => ({
    label: OS_LABELS[k], value: osBreakdown[k] || 0, color: OS_COLORS[k]
  }));
  document.getElementById("dash-os-chart").innerHTML = donutChartSvg(segments);
}

function renderOnlineChart(online, offline) {
  document.getElementById("dash-online-chart").innerHTML = barChartSvg([
    { label: "Online", value: online, color: "#3e8635" },
    { label: "Offline", value: offline, color: "#8a8d90" }
  ]);
}

async function renderRecentHosts(force) {
  const wrap = document.getElementById("dash-recent-hosts");
  try {
    const agents = (await getAgents(force)).slice().sort((a, b) => (b.last_seen || 0) - (a.last_seen || 0)).slice(0, 8);
    if (agents.length === 0) { wrap.innerHTML = `<div class="empty-state">No agents enrolled yet. Head to the Enrollment tab to create a key.</div>`; return; }
    wrap.innerHTML = `<table class="data-table"><thead><tr><th></th><th>Hostname</th><th>OS</th><th>Last seen</th></tr></thead><tbody>
      ${agents.map((a) => `
        <tr class="host-row" data-id="${escapeHtml(a.id)}" style="cursor:pointer">
          <td><span class="status-dot ${a.online ? "online" : "offline"}"></span></td>
          <td>${escapeHtml(a.hostname)}</td>
          <td>${escapeHtml(OS_LABELS[a.os] || a.os)}</td>
          <td>${fmtAgo(a.last_seen)}</td>
        </tr>`).join("")}
      </tbody></table>`;
    wrap.querySelectorAll(".host-row").forEach((tr) => tr.addEventListener("click", () => showHostDetail(tr.dataset.id)));
  } catch (e) {
    wrap.innerHTML = `<div class="alert error">${escapeHtml(errText(e))}</div>`;
  }
}

/* ---------------- HOSTS ---------------- */

async function loadHosts() {
  const wrap = document.getElementById("hosts-table-wrap");
  wrap.innerHTML = `<span class="spinner"></span> Loading&hellip;`;
  try {
    const agents = await getAgents(true);
    renderHostsTable(agents);
  } catch (e) {
    wrap.innerHTML = `<div class="alert error">${escapeHtml(errText(e))}</div>`;
  }
}

function renderHostsTable(agents) {
  const wrap = document.getElementById("hosts-table-wrap");
  if (agents.length === 0) {
    wrap.innerHTML = `<div class="empty-state">No agents enrolled yet. Head to the Enrollment tab to create a key and install the agent on a host.</div>`;
    return;
  }
  wrap.innerHTML = `<table class="data-table"><thead><tr><th></th><th>Hostname</th><th>OS</th><th>Version</th><th>IP</th><th>Last seen</th><th></th></tr></thead><tbody>
    ${agents.map((a) => `
      <tr data-id="${escapeHtml(a.id)}" data-hostname="${escapeHtml(a.hostname.toLowerCase())}">
        <td><span class="status-dot ${a.online ? "online" : "offline"}"></span></td>
        <td>${escapeHtml(a.hostname)}</td>
        <td>${escapeHtml(OS_LABELS[a.os] || a.os)} <span class="muted">${escapeHtml(a.os_version || "")}</span></td>
        <td>${escapeHtml(a.agent_version || "")}</td>
        <td><code class="inline">${escapeHtml(a.last_ip || "\u2014")}</code></td>
        <td>${fmtAgo(a.last_seen)}</td>
        <td class="row-actions"><button class="small btn-view">View</button></td>
      </tr>`).join("")}
    </tbody></table>`;
  wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
    tr.querySelector(".btn-view").addEventListener("click", () => showHostDetail(tr.dataset.id));
    tr.addEventListener("dblclick", () => showHostDetail(tr.dataset.id));
  });
  document.getElementById("hosts-search").oninput = (e) => {
    const q = e.target.value.toLowerCase();
    wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
      tr.style.display = tr.dataset.hostname.includes(q) ? "" : "none";
    });
  };
}

/* ---------------- HOST DETAIL ---------------- */

function showHostDetail(agentId) {
  STATE.currentHostId = agentId;
  document.querySelectorAll(".sadc-tab").forEach((t) => t.classList.remove("active"));
  document.querySelectorAll(".sadc-panel").forEach((p) => p.classList.remove("active"));
  document.querySelector('.sadc-tab[data-panel="hosts"]').classList.add("active");
  document.getElementById("panel-host-detail").classList.add("active");
  loadHostDetail(agentId);
}

function initHostSubtabs() {
  const subtabs = document.querySelectorAll(".subtab");
  subtabs.forEach((t) => {
    t.addEventListener("click", () => {
      subtabs.forEach((x) => x.classList.remove("active"));
      document.querySelectorAll(".subpanel").forEach((p) => p.classList.remove("active"));
      t.classList.add("active");
      document.getElementById("sub-" + t.dataset.sub).classList.add("active");
    });
  });
}

async function loadHostDetail(agentId) {
  document.getElementById("host-detail-title").textContent = "Loading\u2026";
  document.getElementById("host-detail-subtitle").textContent = "";
  try {
    const resp = await cli(["agent", agentId]);
    const agent = resp.agent;
    const inv = resp.inventory;
    document.getElementById("host-detail-title").innerHTML =
      `<span class="status-dot ${agent.online ? "online" : "offline"}"></span>${escapeHtml(agent.hostname)}`;
    document.getElementById("host-detail-subtitle").textContent =
      `${OS_LABELS[agent.os] || agent.os} \u00b7 ${agent.os_version || ""} \u00b7 last seen ${fmtAgo(agent.last_seen)} (${fmtDate(agent.last_seen)})`;

    renderSystemSub(inv.system && inv.system.data);
    renderSoftwareSub(inv.software && inv.software.data);
    renderProcessesSub(inv.processes && inv.processes.data);
    renderNetworkSub(inv.network && inv.network.data);
    renderIdentitySub(inv.identity && inv.identity.data);
    renderServicesSub(inv.services && inv.services.data);

    document.getElementById("host-detail-revoke").onclick = () => {
      confirmModal(`Revoke agent "${agent.hostname}"? It will no longer be able to check in or receive jobs.`, async () => {
        try { await cli(["agent-revoke", agentId]); showAlert("success", "Agent revoked."); showHostsPanel(); }
        catch (e) { showAlert("error", errText(e)); }
      });
    };
    document.getElementById("host-detail-delete").onclick = () => {
      confirmModal(`Delete all data for "${agent.hostname}"? This cannot be undone.`, async () => {
        try { await cli(["agent-delete", agentId]); showAlert("success", "Agent deleted."); showHostsPanel(); }
        catch (e) { showAlert("error", errText(e)); }
      });
    };
  } catch (e) {
    document.getElementById("host-detail-title").textContent = "Error";
    document.getElementById("sub-system").innerHTML = `<div class="alert error">${escapeHtml(errText(e))}</div>`;
  }
}

async function refreshHostStatus(agentId) {
  try {
    const agents = await getAgents(true);
    const agent = agents.find((item) => item.id === agentId);
    if (!agent || STATE.currentHostId !== agentId) return;
    document.getElementById("host-detail-title").innerHTML =
      `<span class="status-dot ${agent.online ? "online" : "offline"}"></span>${escapeHtml(agent.hostname)}`;
    document.getElementById("host-detail-subtitle").textContent =
      `${OS_LABELS[agent.os] || agent.os} \u00b7 ${agent.os_version || ""} \u00b7 last seen ${fmtAgo(agent.last_seen)} (${fmtDate(agent.last_seen)})`;
  } catch {}
}

function showHostsPanel() {
  document.querySelectorAll(".sadc-panel").forEach((p) => p.classList.remove("active"));
  document.getElementById("panel-hosts").classList.add("active");
  loadPanel("hosts", true);
}

function kvGrid(pairs) {
  return `<div class="kv-grid">${pairs.map(([k, v]) => `
    <div><div class="kv-label">${escapeHtml(k)}</div><div class="kv-value">${v}</div></div>
  `).join("")}</div>`;
}

function renderSystemSub(d) {
  const el2 = document.getElementById("sub-system");
  if (!d) { el2.innerHTML = `<div class="empty-state">No system data yet &mdash; waiting for the agent's next check-in.</div>`; return; }
  const disks = (d.disks || []).map((disk) => `
    <tr><td>${escapeHtml(disk.mountpoint)}</td><td><code class="inline">${escapeHtml(disk.device)}</code></td>
      <td>${escapeHtml(disk.fstype)}</td><td>${fmtBytes(disk.used_bytes)} / ${fmtBytes(disk.total_bytes)}</td>
      <td>${disk.percent_used}%</td></tr>`).join("");
  el2.innerHTML = `
    <div class="card"><div class="card-header">Overview</div><div class="card-body">
      ${kvGrid([
    ["Hostname", escapeHtml(d.hostname)], ["FQDN", escapeHtml(d.fqdn || "")],
    ["OS", escapeHtml(OS_LABELS[d.os] || d.os)], ["OS release", escapeHtml(d.os_release || "")],
    ["Distro", escapeHtml(d.distro || d.manufacturer || "")], ["Architecture", escapeHtml(d.arch || "")],
    ["CPU model", escapeHtml(d.cpu_model || "")], ["Logical cores", d.cpu_logical_cores],
    ["Physical cores", d.cpu_physical_cores], ["CPU usage", (d.cpu_percent ?? "\u2014") + "%"],
    ["Memory", `${fmtBytes(d.memory_used_bytes)} / ${fmtBytes(d.memory_total_bytes)} (${d.memory_percent}%)`],
    ["Swap", `${fmtBytes(d.swap_used_bytes)} / ${fmtBytes(d.swap_total_bytes)}`],
    ["Boot time", fmtDate(d.boot_time)], ["Uptime", fmtAgo(Date.now() / 1000 - d.uptime_seconds).replace(" ago", "")]
  ])}
    </div></div>
    <div class="card"><div class="card-header">Disks</div><div class="card-body">
      <table class="data-table"><thead><tr><th>Mount</th><th>Device</th><th>Type</th><th>Used / total</th><th>%</th></tr></thead>
      <tbody>${disks || '<tr><td colspan="5" class="muted">No disk data</td></tr>'}</tbody></table>
    </div></div>`;
}

function renderSoftwareSub(d) {
  const el2 = document.getElementById("sub-software");
  if (!d || !d.packages) { el2.innerHTML = `<div class="empty-state">No software data yet.</div>`; return; }
  el2.innerHTML = `
    <div class="sadc-toolbar"><div class="left"><input type="text" id="sw-filter" placeholder="Filter packages\u2026" style="width:240px"></div>
      <div class="right muted">${d.count} package(s)</div></div>
    <div id="sw-table-wrap"></div>`;
  const rows = d.packages.map((p) => `
    <tr data-name="${escapeHtml((p.name || "").toLowerCase())}">
      <td>${escapeHtml(p.name)}</td><td>${escapeHtml(p.version || "")}</td>
      <td>${escapeHtml(p.vendor || "")}</td><td>${escapeHtml(p.source || "")}</td>
      <td>${p.size_bytes ? fmtBytes(p.size_bytes) : ""}</td>
    </tr>`).join("");
  document.getElementById("sw-table-wrap").innerHTML =
    `<table class="data-table"><thead><tr><th>Name</th><th>Version</th><th>Vendor</th><th>Source</th><th>Size</th></tr></thead><tbody>${rows}</tbody></table>`;
  document.getElementById("sw-filter").oninput = (e) => {
    const q = e.target.value.toLowerCase();
    document.querySelectorAll("#sw-table-wrap tr[data-name]").forEach((tr) => { tr.style.display = tr.dataset.name.includes(q) ? "" : "none"; });
  };
}

function renderProcessesSub(d) {
  const el2 = document.getElementById("sub-processes");
  if (!d || !d.processes) { el2.innerHTML = `<div class="empty-state">No process data yet.</div>`; return; }
  el2.innerHTML = `
    <div class="sadc-toolbar"><div class="left"><input type="text" id="proc-filter" placeholder="Filter processes\u2026" style="width:240px"></div>
      <div class="right muted">${d.count} process(es), sorted by CPU</div></div>
    <div id="proc-table-wrap"></div>`;
  const rows = d.processes.map((p) => `
    <tr data-name="${escapeHtml((p.name || "").toLowerCase())}">
      <td>${p.pid}</td><td>${escapeHtml(p.name || "")}</td><td>${escapeHtml(p.user || "")}</td>
      <td>${(p.cpu_percent ?? 0).toFixed(1)}%</td><td>${fmtBytes(p.memory_rss_bytes)}</td>
      <td>${escapeHtml(p.status || "")}</td><td><code class="inline">${escapeHtml((p.cmdline || p.exe || "").slice(0, 80))}</code></td>
    </tr>`).join("");
  document.getElementById("proc-table-wrap").innerHTML =
    `<table class="data-table"><thead><tr><th>PID</th><th>Name</th><th>User</th><th>CPU</th><th>Memory</th><th>Status</th><th>Command</th></tr></thead><tbody>${rows}</tbody></table>`;
  document.getElementById("proc-filter").oninput = (e) => {
    const q = e.target.value.toLowerCase();
    document.querySelectorAll("#proc-table-wrap tr[data-name]").forEach((tr) => { tr.style.display = tr.dataset.name.includes(q) ? "" : "none"; });
  };
}

function renderNetworkSub(d) {
  const el2 = document.getElementById("sub-network");
  if (!d) { el2.innerHTML = `<div class="empty-state">No network data yet.</div>`; return; }
  const ifaces = (d.interfaces || []).map((iface) => `
    <div class="card"><div class="card-header"><span>${escapeHtml(iface.name)}</span>
      <span class="badge ${iface.is_up ? "ok" : "neutral"}">${iface.is_up ? "up" : "down"}</span></div>
      <div class="card-body">
        ${kvGrid([["Speed", (iface.speed_mbps || 0) + " Mbps"], ["MTU", iface.mtu]])}
        <table class="data-table" style="margin-top:10px"><thead><tr><th>Family</th><th>Address</th><th>Netmask</th></tr></thead><tbody>
          ${(iface.addresses || []).map((a) => `<tr><td>${escapeHtml(a.family)}</td><td><code class="inline">${escapeHtml(a.address)}</code></td><td>${escapeHtml(a.netmask || "")}</td></tr>`).join("")}
        </tbody></table>
      </div></div>`).join("");
  const conns = (d.connections || []).slice(0, 200).map((c) => `
    <tr><td>${escapeHtml(c.type)}</td><td><code class="inline">${escapeHtml(c.local_address)}</code></td>
      <td><code class="inline">${escapeHtml(c.remote_address || "")}</code></td><td>${escapeHtml(c.status)}</td><td>${c.pid || ""}</td></tr>`).join("");
  el2.innerHTML = `
    <div class="card"><div class="card-header">Overview</div><div class="card-body">
      ${kvGrid([["Default gateway", escapeHtml(d.default_gateway || "\u2014")], ["DNS servers", escapeHtml((d.dns_servers || []).join(", ") || "\u2014")],
    ["Bytes sent", fmtBytes(d.bytes_sent)], ["Bytes received", fmtBytes(d.bytes_recv)]])}
    </div></div>
    ${ifaces}
    <div class="card"><div class="card-header">Connections &amp; listening ports</div><div class="card-body">
      <table class="data-table"><thead><tr><th>Type</th><th>Local</th><th>Remote</th><th>Status</th><th>PID</th></tr></thead>
      <tbody>${conns || '<tr><td colspan="5" class="muted">None visible (may need elevated agent permissions)</td></tr>'}</tbody></table>
    </div></div>`;
}

function renderIdentitySub(d) {
  const el2 = document.getElementById("sub-identity");
  if (!d) { el2.innerHTML = `<div class="empty-state">No identity data yet.</div>`; return; }
  const users = (d.users || []).map((u) => `
    <tr><td>${escapeHtml(u.username)}</td><td>${escapeHtml(String(u.uid))}</td><td>${escapeHtml(u.full_name || "")}</td>
      <td>${u.is_system ? '<span class="badge neutral">system</span>' : '<span class="badge ok">standard</span>'}</td>
      <td>${escapeHtml(u.shell || "")}</td></tr>`).join("");
  const groups = (d.groups || []).map((g) => `
    <tr><td>${escapeHtml(g.name)}</td><td>${escapeHtml(String(g.gid))}</td><td>${escapeHtml((g.members || []).join(", "))}</td></tr>`).join("");
  el2.innerHTML = `
    <div class="card"><div class="card-header">Users (${d.user_count})</div><div class="card-body">
      <table class="data-table"><thead><tr><th>Username</th><th>UID</th><th>Full name</th><th>Type</th><th>Shell</th></tr></thead><tbody>${users}</tbody></table>
    </div></div>
    <div class="card"><div class="card-header">Groups (${d.group_count})</div><div class="card-body">
      <table class="data-table"><thead><tr><th>Name</th><th>GID</th><th>Members</th></tr></thead><tbody>${groups}</tbody></table>
    </div></div>`;
}

function renderServicesSub(d) {
  const el2 = document.getElementById("sub-services");
  if (!d) { el2.innerHTML = `<div class="empty-state">No services data yet.</div>`; return; }
  if (!d.services || d.services.length === 0) {
    el2.innerHTML = `<div class="empty-state">No services reported (the host's service manager may not be reachable from the agent).</div>`;
    return;
  }
  el2.innerHTML = `
    <div class="sadc-toolbar"><div class="left"><input type="text" id="svc-filter" placeholder="Filter services\u2026" style="width:240px"></div>
      <div class="right muted">${d.running_count} running / ${d.count} total</div></div>
    <div id="svc-table-wrap"></div>`;
  const rows = d.services.map((s) => `
    <tr data-name="${escapeHtml((s.name || "").toLowerCase())}">
      <td>${escapeHtml(s.name)}</td><td>${escapeHtml(s.display_name || "")}</td>
      <td><span class="badge ${["running", "active"].includes(s.status) ? "ok" : "neutral"}">${escapeHtml(s.status)}</span></td>
      <td>${escapeHtml(s.sub_status || "")}</td><td>${escapeHtml(s.start_mode || "")}</td>
    </tr>`).join("");
  document.getElementById("svc-table-wrap").innerHTML =
    `<table class="data-table"><thead><tr><th>Name</th><th>Display name</th><th>Status</th><th>Detail</th><th>Start mode</th></tr></thead><tbody>${rows}</tbody></table>`;
  document.getElementById("svc-filter").oninput = (e) => {
    const q = e.target.value.toLowerCase();
    document.querySelectorAll("#svc-table-wrap tr[data-name]").forEach((tr) => { tr.style.display = tr.dataset.name.includes(q) ? "" : "none"; });
  };
}

/* ---------------- DEPLOY ---------------- */

const MAX_SCRIPT_BYTES = 256 * 1024;
const MAX_APPROVED_FILE_BYTES = 1024 * 1024 * 1024;
const MAX_EMBEDDED_FILE_BYTES = 48 * 1024;
const MAX_DEPLOY_BLOCKS = 50;
const DEPLOY_BLOCKS = [];
let DEPLOY_BLOCK_INTERPRETER = null;
const BLOCK_LABELS = {
  print: "Print text",
  wait: "Wait",
  code: "Command or code",
  directory: "Create directory",
  download: "Download file",
  writeFile: "Write uploaded file",
  environment: "Set environment variable",
  service: "Service action"
};

function shellQuote(value) {
  return "'" + value.replace(/'/g, "'\\''") + "'";
}

function powershellQuote(value) {
  return "'" + value.replace(/'/g, "''") + "'";
}

function pythonString(value) {
  return JSON.stringify(value);
}

function checkedDownloadUrl(value) {
  let url;
  try { url = new URL(value); } catch (e) { throw new Error("Enter a valid http or https download URL."); }
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error("Downloads support http and https URLs only.");
  return value;
}

function blockScriptLine(block, interpreter, index, imports) {
  const incomplete = `# Complete node ${index + 1}: ${BLOCK_LABELS[block.type]}`;
  if (block.type === "print") {
    if (interpreter === "powershell") return `Write-Output ${powershellQuote(block.value || "")}`;
    if (interpreter === "python") return `print(${pythonString(block.value || "")})`;
    return `printf '%s\\n' ${shellQuote(block.value || "")}`;
  }
  if (block.type === "wait") {
    const seconds = parseInt(block.seconds, 10) || 1;
    if (interpreter === "powershell") return `Start-Sleep -Seconds ${seconds}`;
    if (interpreter === "python") { imports.add("import time"); return `time.sleep(${seconds})`; }
    return `sleep ${seconds}`;
  }
  if (block.type === "code") return block.code || (interpreter === "python" ? "pass" : "# Add command or code");
  if (block.type === "directory") {
    if (!block.path.trim()) return incomplete;
    if (interpreter === "powershell") return `New-Item -ItemType Directory -Path ${powershellQuote(block.path)} -Force | Out-Null`;
    if (interpreter === "python") { imports.add("from pathlib import Path"); return `Path(${pythonString(block.path)}).mkdir(parents=True, exist_ok=True)`; }
    return `mkdir -p -- ${shellQuote(block.path)}`;
  }
  if (block.type === "download") {
    if (!block.url.trim() || !block.path.trim()) return incomplete;
    const url = block.url.trim();
    if (interpreter === "powershell") {
      return `$downloadParent = Split-Path -Parent ${powershellQuote(block.path)}; if ($downloadParent) { New-Item -ItemType Directory -Path $downloadParent -Force | Out-Null }; Invoke-WebRequest -Uri ${powershellQuote(url)} -OutFile ${powershellQuote(block.path)}`;
    }
    if (interpreter === "python") {
      imports.add("import urllib.request");
      imports.add("from pathlib import Path");
      return `Path(${pythonString(block.path)}).parent.mkdir(parents=True, exist_ok=True)\nurllib.request.urlretrieve(${pythonString(url)}, ${pythonString(block.path)})`;
    }
    return `mkdir -p -- "$(dirname -- ${shellQuote(block.path)})"\nif command -v curl >/dev/null 2>&1; then\n  curl --fail --location --show-error --output ${shellQuote(block.path)} -- ${shellQuote(url)}\nelif command -v wget >/dev/null 2>&1; then\n  wget --output-document=${shellQuote(block.path)} -- ${shellQuote(url)}\nelse\n  echo "This download node requires curl or wget." >&2\n  exit 127\nfi`;
  }
  if (block.type === "writeFile") {
    if (!block.path.trim() || !block.filename) return incomplete;
    if (interpreter === "powershell") {
      return `$filePath = ${powershellQuote(block.path)}; $parent = Split-Path -Parent $filePath; if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }; [System.IO.File]::WriteAllBytes($filePath, [Convert]::FromBase64String(${powershellQuote(block.base64)}))`;
    }
    if (interpreter === "python") {
      imports.add("import base64");
      imports.add("from pathlib import Path");
      return `Path(${pythonString(block.path)}).parent.mkdir(parents=True, exist_ok=True)\nPath(${pythonString(block.path)}).write_bytes(base64.b64decode(${pythonString(block.base64)}))`;
    }
    return `mkdir -p -- "$(dirname -- ${shellQuote(block.path)})"\nif base64 --help >/dev/null 2>&1; then\n  printf '%s' ${shellQuote(block.base64)} | base64 --decode > ${shellQuote(block.path)}\nelse\n  printf '%s' ${shellQuote(block.base64)} | base64 -D > ${shellQuote(block.path)}\nfi`;
  }
  if (block.type === "environment") {
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(block.name)) return incomplete;
    if (interpreter === "powershell") return `$env:${block.name} = ${powershellQuote(block.value || "")}`;
    if (interpreter === "python") { imports.add("import os"); return `os.environ[${pythonString(block.name)}] = ${pythonString(block.value || "")}`; }
    return `export ${block.name}=${shellQuote(block.value || "")}`;
  }
  if (block.type === "service") {
    if (!/^[A-Za-z0-9_.@:-]+$/.test(block.name)) return incomplete;
    const action = ["start", "stop", "restart"].includes(block.action) ? block.action : "restart";
    if (interpreter === "powershell") {
      const command = { start: "Start-Service", stop: "Stop-Service", restart: "Restart-Service" }[action];
      return `${command} -Name ${powershellQuote(block.name)}${action === "restart" ? " -Force" : ""}`;
    }
    if (interpreter === "python") {
      imports.add("import os");
      imports.add("import subprocess");
      const powershellCommand = { start: "Start-Service", stop: "Stop-Service", restart: "Restart-Service" }[action];
      return `if os.name == "nt":\n    subprocess.run(["powershell", "-NoProfile", "-Command", ${pythonString(`${powershellCommand} -Name ${powershellQuote(block.name)}${action === "restart" ? " -Force" : ""}`)}], check=True)\nelse:\n    subprocess.run(["systemctl", ${pythonString(action)}, ${pythonString(block.name)}], check=True)`;
    }
    return `systemctl ${action} ${shellQuote(block.name)}`;
  }
  return incomplete;
}

function generateBlockScript(interpreter) {
  if (interpreter === "auto") throw new Error("Choose Bash, PowerShell, or Python before generating a script.");
  const imports = new Set();
  const lines = DEPLOY_BLOCKS.map((block, index) => blockScriptLine(block, interpreter, index, imports));
  return [...imports, ...lines].join("\n\n");
}

function createDeployBlock(type) {
  const defaults = {
    print: { value: "" },
    wait: { seconds: "1" },
    code: { code: "" },
    directory: { path: "" },
    download: { url: "", path: "" },
    writeFile: { path: "", filename: "", fileBytes: 0, base64: "" },
    environment: { name: "", value: "" },
    service: { action: "restart", name: "" }
  };
  return { type, ...defaults[type] };
}

function renderDeployBlocks() {
  const list = document.getElementById("deploy-blocks");
  if (DEPLOY_BLOCKS.length === 0) {
    list.innerHTML = `<div class="empty-state">No nodes added.</div>`;
    return;
  }
  list.replaceChildren();
  DEPLOY_BLOCKS.forEach((block, index) => {
    if (index > 0) {
      const connector = document.createElement("div");
      connector.className = "block-connector";
      connector.textContent = "then";
      list.appendChild(connector);
    }
    const node = document.createElement("div");
    node.className = "block-node";
    const heading = document.createElement("div");
    heading.className = "block-node-heading";
    const title = document.createElement("strong");
    title.textContent = `${index + 1}. ${BLOCK_LABELS[block.type]}`;
    heading.appendChild(title);
    const controls = document.createElement("div");
    controls.className = "block-node-controls";
    [["Up", "Move up", index - 1], ["Down", "Move down", index + 1], ["Remove", "Remove", -1]].forEach(([label, titleText, destination]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "small";
      button.textContent = label;
      button.title = titleText;
      button.setAttribute("aria-label", titleText);
      button.disabled = titleText === "Move up" ? index === 0 : titleText === "Move down" ? index === DEPLOY_BLOCKS.length - 1 : false;
      button.addEventListener("click", () => {
        if (titleText === "Remove") DEPLOY_BLOCKS.splice(index, 1);
        else [DEPLOY_BLOCKS[index], DEPLOY_BLOCKS[destination]] = [DEPLOY_BLOCKS[destination], DEPLOY_BLOCKS[index]];
        renderDeployBlocks();
        syncBlockScript();
      });
      controls.appendChild(button);
    });
    heading.appendChild(controls);
    node.appendChild(heading);

    const fields = document.createElement("div");
    fields.className = "block-fields";
    const addField = (labelText, control, key, fullWidth = false) => {
      const wrapper = document.createElement("div");
      wrapper.className = fullWidth ? "block-field full" : "block-field";
      const label = document.createElement("label");
      label.textContent = labelText;
      label.htmlFor = `block-${index}-${key}`;
      control.id = label.htmlFor;
      wrapper.append(label, control);
      fields.appendChild(wrapper);
      return control;
    };
    const addText = (label, key, type, placeholder, fullWidth = false) => {
      const control = document.createElement("input");
      control.type = type;
      control.placeholder = placeholder || "";
      control.value = block[key] || "";
      control.addEventListener("input", () => { block[key] = control.value; syncBlockScript(); });
      return addField(label, control, key, fullWidth);
    };
    const addSelect = (label, key, options) => {
      const control = document.createElement("select");
      options.forEach(([value, text]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        control.appendChild(option);
      });
      control.value = block[key];
      control.addEventListener("change", () => { block[key] = control.value; syncBlockScript(); });
      return addField(label, control, key);
    };

    if (block.type === "print") addText("Text", "value", "text", "Text to print", true);
    if (block.type === "wait") {
      const control = addText("Seconds", "seconds", "number", "1");
      control.min = "1";
      control.max = "3600";
    }
    if (block.type === "code") {
      const control = document.createElement("textarea");
      control.rows = 4;
      control.placeholder = "Command for Bash/PowerShell or code for Python";
      control.value = block.code || "";
      control.addEventListener("input", () => { block.code = control.value; syncBlockScript(); });
      addField("Command or code", control, "code", true);
    }
    if (block.type === "directory") addText("Directory path", "path", "text", "/var/tmp/my-folder", true);
    if (block.type === "download") {
      addText("File URL", "url", "url", "https://example.com/file.zip");
      addText("Save to path", "path", "text", "/var/tmp/file.zip");
    }
    if (block.type === "writeFile") {
      addText("Destination path", "path", "text", "/var/tmp/config.bin");
      const control = document.createElement("input");
      control.type = "file";
      control.addEventListener("change", async (event) => {
        const file = event.target.files[0];
        if (!file) return;
        if (file.size > MAX_EMBEDDED_FILE_BYTES) {
          block.base64 = "";
          block.filename = "";
          block.fileBytes = 0;
          showAlert("error", "Embedded files are limited to 48 KiB each. The script itself is limited to 256 KiB.");
          status.textContent = "No file selected.";
          event.target.value = "";
          syncBlockScript();
          return;
        }
        try {
          block.base64 = bytesToBase64(new Uint8Array(await file.arrayBuffer()));
          block.filename = file.name;
          block.fileBytes = file.size;
          status.textContent = `${file.name} (${file.size.toLocaleString()} bytes)`;
          syncBlockScript();
        } catch (e) {
          block.base64 = "";
          block.filename = "";
          block.fileBytes = 0;
          status.textContent = "Could not read file.";
          showAlert("error", `Could not read file: ${errText(e)}`);
        }
      });
      addField("File to embed (48 KiB max)", control, "upload", true);
      const status = document.createElement("div");
      status.className = "form-hint block-file-status";
      status.textContent = block.filename ? `${block.filename} (${block.fileBytes.toLocaleString()} bytes)` : "File contents are embedded in the generated script.";
      fields.appendChild(status);
    }
    if (block.type === "environment") {
      addText("Variable name", "name", "text", "APP_MODE");
      addText("Value", "value", "text", "production");
    }
    if (block.type === "service") {
      addSelect("Action", "action", [["start", "Start"], ["stop", "Stop"], ["restart", "Restart"]]);
      addText("Service name", "name", "text", "example.service");
      const hint = document.createElement("div");
      hint.className = "form-hint block-field-hint";
      hint.textContent = "Uses systemd on Linux or Windows services. Requires permission to manage the service; macOS launchd is not supported by this node.";
      fields.appendChild(hint);
    }
    if (block.type === "environment") {
      const hint = document.createElement("div");
      hint.className = "form-hint block-field-hint";
      hint.textContent = "Values are embedded in the job script and stored by the server. Do not use this node for secrets.";
      fields.appendChild(hint);
    }
    node.appendChild(fields);
    list.appendChild(node);
  });
}

function bytesToBase64(bytes) {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

function validateBuilderBlocks() {
  DEPLOY_BLOCKS.forEach((block, index) => {
    const node = index + 1;
    if (["directory", "writeFile"].includes(block.type) && !block.path.trim()) throw new Error(`Node ${node}: enter a destination path.`);
    if (block.type === "download") {
      if (!block.path.trim()) throw new Error(`Node ${node}: enter a destination path.`);
      if (!block.url.trim()) throw new Error(`Node ${node}: enter an http or https URL.`);
      checkedDownloadUrl(block.url.trim());
    }
    if (block.type === "writeFile" && !block.filename) throw new Error(`Node ${node}: choose a file to embed.`);
    if (block.type === "wait" && (!Number.isInteger(Number(block.seconds)) || Number(block.seconds) < 1 || Number(block.seconds) > 3600)) throw new Error(`Node ${node}: wait duration must be between 1 and 3,600 seconds.`);
    if (block.type === "code" && !block.code.trim()) throw new Error(`Node ${node}: enter command or code.`);
    if (block.type === "environment" && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(block.name)) throw new Error(`Node ${node}: use a valid environment variable name.`);
    if (block.type === "service" && !/^[A-Za-z0-9_.@:-]+$/.test(block.name)) throw new Error(`Node ${node}: enter a valid service name.`);
  });
}

function syncBlockScript() {
  const interpreter = document.getElementById("deploy-interpreter").value;
  if (interpreter === "auto") return;
  DEPLOY_BLOCK_INTERPRETER = interpreter;
  try {
    document.getElementById("deploy-script").value = generateBlockScript(interpreter);
  } catch (e) {
    showAlert("error", errText(e));
  }
}

async function loadDeploy() {
  document.getElementById("deploy-target-all").onchange = async (e) => {
    const listEl = document.getElementById("deploy-target-list");
    if (e.target.checked) { listEl.style.display = "none"; return; }
    listEl.style.display = "block";
    if (listEl.dataset.loaded) return;
    listEl.innerHTML = `<span class="spinner"></span>`;
    try {
      const agents = await getAgents();
      listEl.innerHTML = agents.map((a) => `
        <div class="checkbox-row"><input type="checkbox" class="deploy-target-check" value="${escapeHtml(a.id)}" id="dt-${escapeHtml(a.id)}">
          <label for="dt-${escapeHtml(a.id)}">${escapeHtml(a.hostname)} <span class="muted">(${escapeHtml(OS_LABELS[a.os] || a.os)})</span></label></div>
      `).join("") || `<p class="muted">No agents enrolled yet.</p>`;
      listEl.dataset.loaded = "1";
    } catch (e2) { listEl.innerHTML = `<div class="alert error">${escapeHtml(errText(e2))}</div>`; }
  };

  document.getElementById("deploy-run").onclick = async () => {
    if (DEPLOY_BLOCKS.length) {
      try { validateBuilderBlocks(); }
      catch (e) { showAlert("error", errText(e)); return; }
    }
    const script = document.getElementById("deploy-script").value;
    if (!script.trim()) { showAlert("error", "Enter a script to run."); return; }
    if (new Blob([script]).size > MAX_SCRIPT_BYTES) { showAlert("error", "Script exceeds the 256 KiB limit."); return; }
    const name = document.getElementById("deploy-name").value.trim();
    const interpreter = document.getElementById("deploy-interpreter").value;
    const timeout = parseInt(document.getElementById("deploy-timeout").value, 10) || 300;
    if (timeout < 5 || timeout > 3600) { showAlert("error", "Timeout must be between 5 and 3,600 seconds."); return; }
    const all = document.getElementById("deploy-target-all").checked;
    const targetIds = all ? [] : Array.from(document.querySelectorAll(".deploy-target-check:checked")).map((c) => c.value);
    if (!all && targetIds.length === 0) { showAlert("error", "Select at least one target, or check \"All enrolled agents\"."); return; }
    if (targetIds.length > 1000) { showAlert("error", "A job can target up to 1,000 agents."); return; }
    try {
      const body = { name, script, interpreter, target_agent_ids: targetIds, timeout_seconds: timeout };
      const result = await cli(["create-job"], { input: JSON.stringify(body) });
      showAlert("success", `Job "${name || result.job_id}" queued for ${all ? "all agents" : targetIds.length + " agent(s)"}. It runs on each agent's next check-in.`);
      loadJobs();
    } catch (e) { showAlert("error", errText(e), { sticky: true }); }
  };

  document.getElementById("deploy-jobs-refresh").onclick = loadJobs;
  document.getElementById("deploy-add-block").onclick = () => {
    if (DEPLOY_BLOCKS.length >= MAX_DEPLOY_BLOCKS) { showAlert("error", `The block builder supports up to ${MAX_DEPLOY_BLOCKS} nodes.`); return; }
    if (document.getElementById("deploy-interpreter").value === "auto") {
      showAlert("error", "Choose Bash, PowerShell, or Python before adding a block.");
      return;
    }
    DEPLOY_BLOCKS.push(createDeployBlock(document.getElementById("deploy-block-type").value));
    renderDeployBlocks();
    syncBlockScript();
  };
  document.getElementById("deploy-interpreter").onchange = (event) => {
    if (!DEPLOY_BLOCKS.length) return;
    if (event.target.value === "auto") {
      showAlert("error", "Block-generated scripts require a specific interpreter.");
      event.target.value = DEPLOY_BLOCK_INTERPRETER || "python";
      return;
    }
    syncBlockScript();
  };
  document.getElementById("deploy-file").onchange = async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    if (file.size > MAX_SCRIPT_BYTES) {
      showAlert("error", "That file exceeds the 256 KiB limit.");
      event.target.value = "";
      return;
    }
    const extension = file.name.toLowerCase().split(".").pop();
    const interpreters = { sh: "bash", bash: "bash", ps1: "powershell", py: "python" };
    if (!["sh", "bash", "ps1", "py", "txt"].includes(extension)) {
      showAlert("error", "Choose a .sh, .bash, .ps1, .py, or .txt script.");
      event.target.value = "";
      return;
    }
    try {
      document.getElementById("deploy-script").value = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
      if (interpreters[extension]) document.getElementById("deploy-interpreter").value = interpreters[extension];
      if (!document.getElementById("deploy-name").value.trim()) document.getElementById("deploy-name").value = file.name;
      DEPLOY_BLOCKS.splice(0);
      DEPLOY_BLOCK_INTERPRETER = null;
      renderDeployBlocks();
    } catch (e) { showAlert("error", `Could not read script: ${errText(e)}`); }
  };
  loadJobs();
  initFileInstall();
}

async function loadJobs() {
  const wrap = document.getElementById("deploy-jobs-wrap");
  wrap.innerHTML = `<span class="spinner"></span> Loading&hellip;`;
  try {
    const data = await cli(["jobs"]);
    renderJobsTable(data.jobs);
  } catch (e) {
    wrap.innerHTML = `<div class="alert error">${escapeHtml(errText(e))}</div>`;
  }
}

function renderJobsTable(jobs) {
  const wrap = document.getElementById("deploy-jobs-wrap");
  if (jobs.length === 0) { wrap.innerHTML = `<div class="empty-state">No jobs run yet.</div>`; return; }
  const rows = jobs.map((j) => {
    const total = j.results.length;
    const done = j.results.filter((r) => r.status !== "pending" && r.status !== "running").length;
    const success = j.results.filter((r) => r.status === "success").length;
    const failed = j.results.filter((r) => ["failed", "timeout"].includes(r.status)).length;
    let badge = "neutral", label = `${done}/${total} done`;
    if (done === total && total > 0) { badge = failed > 0 ? "warn" : "ok"; label = `${success} ok, ${failed} failed`; }
    return `
      <tr data-id="${escapeHtml(j.id)}">
        <td>${escapeHtml(j.name || "(unnamed)")}</td><td>${j.job_type === "file_install" ? "silent install" : escapeHtml(j.interpreter)}</td>
        <td>${fmtDate(j.created_at)}</td><td>${total}</td>
        <td><span class="badge ${badge}">${label}</span></td>
        <td class="row-actions"><button class="small btn-view-job">View</button></td>
      </tr>`;
  }).join("");
  wrap.innerHTML = `<table class="data-table"><thead><tr><th>Name</th><th>Interpreter</th><th>Created</th><th>Targets</th><th>Status</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
  wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
    tr.querySelector(".btn-view-job").addEventListener("click", () => openJobDetail(tr.dataset.id, jobs.find((j) => j.id === tr.dataset.id)));
  });
}

function openJobDetail(jobId, job) {
  const rows = job.results.map((r) => `
    <tr>
      <td>${escapeHtml((STATE.agents.find((a) => a.id === r.agent_id) || {}).hostname || r.agent_id)}</td>
      <td><span class="badge ${r.status === "success" ? "ok" : ["failed", "timeout"].includes(r.status) ? "err" : "neutral"}">${escapeHtml(r.status)}</span></td>
      <td>${r.exit_code === null || r.exit_code === undefined ? "" : r.exit_code}</td>
      <td><pre class="ldif" style="max-height:120px">${escapeHtml(r.stdout || "")}</pre></td>
      <td><pre class="ldif" style="max-height:120px">${escapeHtml(r.stderr || "")}</pre></td>
    </tr>`).join("");
  const summary = job.job_type === "file_install"
    ? `<p class="muted">Silent install of <code class="inline">${escapeHtml((job.file || {}).original_filename || job.file_id || "(file removed)")}</code>${job.install_args ? ` with args <code class="inline">${escapeHtml(job.install_args)}</code>` : ""}</p>`
    : `<p class="muted"><code class="inline">${escapeHtml((job.script || "").slice(0, 300))}${(job.script || "").length > 300 ? "\u2026" : ""}</code></p>`;
  openModal(`Job: ${job.name || jobId}`, `
    ${summary}
    <table class="data-table"><thead><tr><th>Host</th><th>Status</th><th>Exit</th><th>stdout</th><th>stderr</th></tr></thead><tbody>${rows}</tbody></table>
  `, { wide: true, buttons: [{ label: "Close", className: "primary", onClick: (close) => close() }] });
}

/* ---------------- SILENT FILE INSTALL (approved files) ---------------- */

function initFileInstall() {
  document.getElementById("files-refresh").onclick = loadApprovedFiles;
  document.getElementById("file-upload-btn").onclick = async (event) => {
    const button = event.currentTarget;
    const fileInput = document.getElementById("file-upload");
    const file = fileInput.files[0];
    const resultBox = document.getElementById("file-upload-result");
    if (!file) { showAlert("error", "Choose a file to upload."); return; }
    if (file.size > MAX_APPROVED_FILE_BYTES) { showAlert("error", "That file exceeds the 1 GiB limit."); return; }
    const extension = "." + file.name.toLowerCase().split(".").pop();
    const allowed = [".exe", ".msi", ".msp", ".pkg", ".dmg", ".deb", ".rpm", ".run", ".sh"];
    if (!allowed.includes(extension)) {
      showAlert("error", `Unsupported file type ${extension}. Allowed: ${allowed.join(", ")}`);
      return;
    }
    button.disabled = true;
    resultBox.innerHTML = `<span class="spinner"></span> Uploading&hellip;`;
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const label = document.getElementById("file-label").value.trim();
      const platform = document.getElementById("file-platform").value;
      const silentArgs = document.getElementById("file-silent-args").value.trim();
      await cli(["upload-file", file.name, platform, label, silentArgs], { input: bytes, binary: true });
      resultBox.innerHTML = `<div class="alert success">Uploaded "${escapeHtml(file.name)}". It's now available to push as a silent-install job.</div>`;
      fileInput.value = "";
      document.getElementById("file-label").value = "";
      document.getElementById("file-silent-args").value = "";
      loadApprovedFiles();
    } catch (e) {
      resultBox.innerHTML = `<div class="alert error">${escapeHtml(errText(e))}</div>`;
    } finally { button.disabled = false; }
  };
  loadApprovedFiles();
}

async function loadApprovedFiles() {
  const wrap = document.getElementById("files-wrap");
  wrap.innerHTML = `<span class="spinner"></span> Loading&hellip;`;
  try {
    const data = await cli(["files"]);
    renderApprovedFilesTable(data.files);
  } catch (e) {
    wrap.innerHTML = `<div class="alert error">${escapeHtml(errText(e))}</div>`;
  }
}

function renderApprovedFilesTable(files) {
  const wrap = document.getElementById("files-wrap");
  if (files.length === 0) { wrap.innerHTML = `<div class="empty-state">No approved files yet. Upload one above.</div>`; return; }
  const rows = files.map((f) => `
    <tr data-id="${escapeHtml(f.id)}">
      <td>${escapeHtml(f.label || f.original_filename)}</td>
      <td>${escapeHtml(f.original_filename)}</td>
      <td>${escapeHtml(OS_LABELS[f.platform] || f.platform)}</td>
      <td>${fmtBytes(f.size_bytes)}</td>
      <td>${fmtDate(f.uploaded_at)}</td>
      <td class="row-actions">
        <button class="small btn-file-install">Install on agents&hellip;</button>
        <button class="small btn-file-delete">Delete</button>
      </td>
    </tr>`).join("");
  wrap.innerHTML = `<table class="data-table"><thead><tr><th>Label</th><th>Filename</th><th>Platform</th><th>Size</th><th>Uploaded</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
  wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
    const f = files.find((x) => x.id === tr.dataset.id);
    tr.querySelector(".btn-file-install").addEventListener("click", () => openFileInstallModal(f));
    tr.querySelector(".btn-file-delete").addEventListener("click", () => deleteApprovedFile(f));
  });
}

async function deleteApprovedFile(f) {
  if (!confirm(`Delete "${f.label || f.original_filename}"? Jobs that already ran will keep their history, but this file can no longer be pushed to agents.`)) return;
  try {
    await cli(["delete-file", f.id]);
    showAlert("success", "File deleted.");
    loadApprovedFiles();
  } catch (e) { showAlert("error", errText(e), { sticky: true }); }
}

async function openFileInstallModal(f) {
  let agents = [];
  try { agents = await getAgents(); } catch (e) { /* target list falls back to "all agents" below */ }
  const targetsHtml = agents.map((a) => `
    <div class="checkbox-row"><input type="checkbox" class="fi-target-check" value="${escapeHtml(a.id)}" id="fi-${escapeHtml(a.id)}">
      <label for="fi-${escapeHtml(a.id)}">${escapeHtml(a.hostname)} <span class="muted">(${escapeHtml(OS_LABELS[a.os] || a.os)})</span></label></div>
  `).join("") || `<p class="muted">No agents enrolled yet.</p>`;

  openModal(`Install silently &mdash; ${escapeHtml(f.label || f.original_filename)}`, `
    <div class="form-row"><label>Job name</label><input type="text" id="fi-name" value="${escapeHtml("Install " + (f.label || f.original_filename))}"></div>
    <div class="form-row"><label>Extra silent-install args (optional, overrides the file's default)</label>
      <input type="text" id="fi-args" placeholder="${escapeHtml(f.default_silent_args || "leave blank to use the file's default")}"></div>
    <div class="form-row" style="max-width:220px"><label>Timeout (seconds)</label><input type="number" id="fi-timeout" value="600" min="5" max="3600"></div>
    <div class="form-row">
      <label>Targets</label>
      <div class="checkbox-row"><input type="checkbox" id="fi-target-all" checked><label for="fi-target-all">All enrolled agents</label></div>
      <div id="fi-target-list" style="display:none">${targetsHtml}</div>
    </div>
    <div class="alert info">This runs with no UI on each target, using that platform's standard unattended-install switches. It installs on the agent's next check-in.</div>
  `, {
    wide: true,
    buttons: [
      { label: "Cancel", onClick: (close) => close() },
      {
        label: "Queue install", className: "primary", onClick: async (close) => {
          const all = document.getElementById("fi-target-all").checked;
          const targetIds = all ? [] : Array.from(document.querySelectorAll(".fi-target-check:checked")).map((c) => c.value);
          if (!all && targetIds.length === 0) { showAlert("error", "Select at least one target, or check \"All enrolled agents\"."); return; }
          const timeout = parseInt(document.getElementById("fi-timeout").value, 10) || 600;
          const body = {
            name: document.getElementById("fi-name").value.trim(),
            job_type: "file_install",
            file_id: f.id,
            install_args: document.getElementById("fi-args").value.trim(),
            target_agent_ids: targetIds,
            timeout_seconds: timeout,
          };
          try {
            const result = await cli(["create-job"], { input: JSON.stringify(body) });
            showAlert("success", `Install job queued for ${all ? "all agents" : targetIds.length + " agent(s)"}.`);
            close();
            loadJobs();
          } catch (e) { showAlert("error", errText(e), { sticky: true }); }
        },
      },
    ],
  });
  document.getElementById("fi-target-all").onchange = (e) => {
    document.getElementById("fi-target-list").style.display = e.target.checked ? "none" : "block";
  };
}

/* ---------------- ENROLLMENT ---------------- */

function getAgentServerUrl(configuredUrl) {
  const url = new URL(configuredUrl);
  if (["localhost", "127.0.0.1", "[::1]", "::1"].includes(url.hostname)) {
    url.hostname = window.location.hostname;
  }
  return url.origin;
}

async function loadEnrollment() {
  document.getElementById("enroll-create").onclick = async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    const label = document.getElementById("enroll-label").value.trim();
    const expiresRaw = document.getElementById("enroll-expires").value.trim();
    const maxUsesRaw = document.getElementById("enroll-maxuses").value.trim();
    const body = {
      label,
      expires_in_days: expiresRaw ? parseInt(expiresRaw, 10) : null,
      max_uses: maxUsesRaw ? parseInt(maxUsesRaw, 10) : null
    };
    try {
      const result = await cli(["create-enrollment-key"], { input: JSON.stringify(body) });
      renderEnrollResult(result.enrollment_key);
      loadEnrollmentKeys();
    } catch (e) { showAlert("error", errText(e), { sticky: true }); }
    finally { button.disabled = false; }
  };
  document.getElementById("enroll-refresh").onclick = loadEnrollmentKeys;
  loadEnrollmentKeys();
}

function renderEnrollResult(key) {
  cli(["config-status"]).then((status) => {
    const server = getAgentServerUrl(status.server_url);
    const box = document.getElementById("enroll-result");
    box.innerHTML = `
      <div class="alert success" style="white-space:normal">
        Enrollment key created. This is shown <strong>once</strong> &mdash; copy it now.
        <div style="margin-top:8px"><code class="inline">${escapeHtml(key)}</code>
          <button class="small link" id="enroll-copy">Copy</button></div>
      </div>
      <div class="card"><div class="card-header">Install on Linux</div><div class="card-body">
        <pre class="ldif">printf 'Enrollment key: '; read -r -s ENROLLMENT_KEY &amp;&amp; printf '\\n' &amp;&amp; curl -fsSL ${escapeHtml(server)}/agent/install.sh | sudo bash -s -- --server ${escapeHtml(server)} --enrollment-key "$ENROLLMENT_KEY" &amp;&amp; unset ENROLLMENT_KEY</pre>
      </div></div>
      <div class="card"><div class="card-header">Install on macOS</div><div class="card-body">
        <pre class="ldif">printf 'Enrollment key: '; read -r -s ENROLLMENT_KEY &amp;&amp; printf '\\n' &amp;&amp; curl -fsSL ${escapeHtml(server)}/agent/install-macos.sh | sudo bash -s -- --server ${escapeHtml(server)} --enrollment-key "$ENROLLMENT_KEY" &amp;&amp; unset ENROLLMENT_KEY</pre>
      </div></div>
      <div class="card"><div class="card-header">Install on Windows (PowerShell, as Administrator)</div><div class="card-body">
        <pre class="ldif">iwr -useb ${escapeHtml(server)}/agent/install.ps1 | iex; Install-InventoryAgent -Server '${escapeHtml(server)}' -EnrollmentKey (Read-Host 'Enrollment key')</pre>
      </div></div>
      <p class="form-hint">Every install script requires Python 3 already present on the target machine. See the README for offline/pre-staged install options.</p>
    `;
    document.getElementById("enroll-copy").addEventListener("click", () => {
      navigator.clipboard && navigator.clipboard.writeText(key);
      showAlert("success", "Copied to clipboard.");
    });
  });
}

async function loadEnrollmentKeys() {
  const wrap = document.getElementById("enroll-keys-wrap");
  wrap.innerHTML = `<span class="spinner"></span> Loading&hellip;`;
  try {
    const data = await cli(["enrollment-keys"]);
    if (data.keys.length === 0) { wrap.innerHTML = `<div class="empty-state">No enrollment keys yet.</div>`; return; }
    wrap.innerHTML = `<table class="data-table"><thead><tr><th>Label</th><th>Created</th><th>Expires</th><th>Uses</th><th>Status</th><th></th></tr></thead><tbody>
      ${data.keys.map((k) => `
        <tr data-hash="${escapeHtml(k.key_hash)}"><td>${escapeHtml(k.label || "")}</td><td>${fmtDate(k.created_at)}</td>
          <td>${k.expires_at ? fmtDate(k.expires_at) : "never"}</td>
          <td>${k.uses}${k.max_uses ? " / " + k.max_uses : ""}</td>
          <td>${k.revoked ? '<span class="badge err">revoked</span>' : '<span class="badge ok">active</span>'}</td>
          <td class="row-actions">
            <button class="small btn-key-connect">Connect</button>
            <button class="small btn-key-delete">Delete</button>
          </td></tr>
      `).join("")}
    </tbody></table>`;
    wrap.querySelectorAll("tr[data-hash]").forEach((tr) => {
      const keyHash = tr.dataset.hash;
      const keyRow = data.keys.find((k) => k.key_hash === keyHash);
      tr.querySelector(".btn-key-connect").addEventListener("click", () => showConnectInstructions(keyRow));
      tr.querySelector(".btn-key-delete").addEventListener("click", () => deleteEnrollmentKey(keyHash, keyRow));
    });
  } catch (e) {
    wrap.innerHTML = `<div class="alert error">${escapeHtml(errText(e))}</div>`;
  }
}

/* Shown next to any key, any time - not just right after creation. The raw
   key value itself is never stored server-side (only a hash of it), so this
   can't redisplay the original secret; it reproduces the exact install
   commands with a placeholder for the key the admin saved when it was
   created. If that's been lost, the only option is to create a new key and
   delete this one. */
function showConnectInstructions(keyRow) {
  const placeholder = `<YOUR-SAVED-KEY-FOR-${(keyRow.label || "this key").toUpperCase().replace(/[^A-Z0-9]+/g, "-")}>`;
  cli(["config-status"]).then((status) => {
    const server = getAgentServerUrl(status.server_url);
    openModal(`Connect an agent &mdash; ${escapeHtml(keyRow.label || keyRow.key_hash.slice(0, 8))}`, `
      <div class="alert info" style="white-space:normal">
        For security, the raw key value is never stored after creation, so it can't be shown again here.
        Linux and macOS hide the key while you type; PowerShell displays it in the terminal.
        If you no longer have it, delete this key and create a new one instead.
      </div>
      <div class="card"><div class="card-header">Install on Linux</div><div class="card-body">
        <pre class="ldif">printf 'Enrollment key: '; read -r -s ENROLLMENT_KEY &amp;&amp; printf '\\n' &amp;&amp; curl -fsSL ${escapeHtml(server)}/agent/install.sh | sudo bash -s -- --server ${escapeHtml(server)} --enrollment-key "$ENROLLMENT_KEY" &amp;&amp; unset ENROLLMENT_KEY</pre>
      </div></div>
      <div class="card"><div class="card-header">Install on macOS</div><div class="card-body">
        <pre class="ldif">printf 'Enrollment key: '; read -r -s ENROLLMENT_KEY &amp;&amp; printf '\\n' &amp;&amp; curl -fsSL ${escapeHtml(server)}/agent/install-macos.sh | sudo bash -s -- --server ${escapeHtml(server)} --enrollment-key "$ENROLLMENT_KEY" &amp;&amp; unset ENROLLMENT_KEY</pre>
      </div></div>
      <div class="card"><div class="card-header">Install on Windows (PowerShell, as Administrator)</div><div class="card-body">
        <pre class="ldif">iwr -useb ${escapeHtml(server)}/agent/install.ps1 | iex; Install-InventoryAgent -Server '${escapeHtml(server)}' -EnrollmentKey (Read-Host 'Enrollment key')</pre>
      </div></div>
    `, { wide: true, buttons: [{ label: "Close", className: "primary", onClick: (close) => close() }] });
  });
}

async function deleteEnrollmentKey(keyHash, keyRow) {
  const label = keyRow && keyRow.label ? `"${keyRow.label}"` : "this key";
  if (!confirm(`Delete enrollment key ${label}? Agents already enrolled with it keep working - this only stops it being used for new enrollments.`)) return;
  try {
    await cli(["delete-enrollment-key", keyHash]);
    showAlert("success", "Enrollment key deleted.");
    loadEnrollmentKeys();
  } catch (e) { showAlert("error", errText(e), { sticky: true }); }
}

/* ---------------- SETTINGS ---------------- */

async function loadSettings() {
  const box = document.getElementById("settings-status");
  box.innerHTML = `<span class="spinner"></span> Checking&hellip;`;
  try {
    const status = await cli(["config-status"]);
    box.innerHTML = kvGrid([
      ["Server URL", escapeHtml(status.server_url)],
      ["Admin token configured", status.has_token ? '<span class="badge ok">yes</span>' : '<span class="badge err">no</span>'],
      ["Config file present", status.config_path_exists ? '<span class="badge ok">yes</span>' : '<span class="badge err">no</span>']
    ]);
  } catch (e) {
    box.innerHTML = `<div class="alert error">${escapeHtml(errText(e))}</div>`;
  }
}

/* ---------------- boot ---------------- */

function initButtons() {
  document.getElementById("inv-refresh-all").addEventListener("click", () => {
    const active = document.querySelector(".sadc-tab.active").dataset.panel;
    checkServerStatus();
    loadPanel(active, true);
  });
  document.getElementById("host-detail-back").addEventListener("click", showHostsPanel);
  document.getElementById("host-detail-refresh").addEventListener("click", () => loadHostDetail(STATE.currentHostId));
  document.getElementById("hosts-refresh").addEventListener("click", () => loadPanel("hosts", true));
  initHostSubtabs();
}

document.addEventListener("DOMContentLoaded", () => {
  initThemeSync();
  initTabs();
  initButtons();
  checkServerStatus();
  loadPanel("dashboard");
  setInterval(() => {
    if (document.visibilityState !== "visible") return;
    const activePanel = document.querySelector(".sadc-tab.active")?.dataset.panel;
    if (activePanel === "dashboard") loadPanel(activePanel, true);
    else if (activePanel === "hosts") {
      if (document.getElementById("panel-host-detail").classList.contains("active")) {
        refreshHostStatus(STATE.currentHostId);
      } else loadPanel(activePanel, true);
    }
  }, 15000);
});
