/* aspen-insight 前端:零构建,原生 JS */

const state = {
  cases: [],
  current: null,
  streams: [],
  blocks: [],
  tab: "streams",
  filter: "",
  bannerCollapsed: false,
  compare: { baseId: null, otherId: null, data: null },
  column: null,
};

const COMPARE_FIELDS = [
  { key: "temperature_c", label: "温度 ℃", digits: 2 },
  { key: "pressure_bar", label: "压力 bar", digits: 3 },
  { key: "mass_flow_kgh", label: "流量 kg/h", digits: 1 },
  { key: "vap_frac", label: "汽化率", digits: 3 },
];

const COLUMN_TYPE = /RadFrac|Absorber/;

/* ------------------------------------------------------------ 工具 */

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `请求失败:${res.status}`);
  }
  return res.json();
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function fmt(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value !== "number") return esc(value);
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtDelta(value, digits = 2) {
  if (value === null || value === undefined) return "—";
  if (typeof value !== "number") return esc(value);
  if (value === 0) return "0";
  const sign = value > 0 ? "+" : "";
  return sign + fmt(value, digits);
}

const SEVERITY = {
  ok: { cls: "ok", text: "收敛" },
  warning: { cls: "warn", text: "有告警" },
  error: { cls: "err", text: "有错误" },
};

function badge(severity) {
  const s = SEVERITY[severity] || SEVERITY.warning;
  return `<span class="badge ${s.cls}">${s.text}</span>`;
}

function blockBadge(status) {
  const cls = status === 1 ? "err" : status === 2 ? "warn" : "ok";
  const text = status === 1 ? "错误" : status === 2 ? "告警" : "正常";
  return `<span class="badge ${cls}">${text}</span>`;
}

/* ------------------------------------------------------------ 顶部 */

function renderBanner() {
  const el = document.getElementById("banner");
  const c = state.current;
  // 对比页也保留这块位置:切换页签时顶部不跳,布局更稳
  if (!c) { el.className = "banner hidden"; return; }

  const s = SEVERITY[c.severity] || SEVERITY.warning;
  const reasons = (c.reasons || [])
    .map((r) => `<div class="reason">· ${esc(r)}</div>`).join("");

  el.className = `banner ${s.cls}` + (state.bannerCollapsed ? " collapsed" : "");
  el.innerHTML = `
    <div class="banner-head">
      <span class="banner-title">
        <strong>${s.text}</strong>　流股 ${c.stream_count} 条 / 设备 ${c.block_count} 个${
          c.run_id ? `　运行 ID ${esc(c.run_id)}` : ""}
      </span>
      <button class="banner-toggle" id="bannerToggle" type="button"
              title="${state.bannerCollapsed ? "展开问题清单" : "收起问题清单"}">
        ${state.bannerCollapsed ? "展开 ▾" : "收起 ▴"}
      </button>
    </div>
    <div class="banner-body">${reasons}</div>`;

  document.getElementById("bannerToggle").addEventListener("click", () => {
    state.bannerCollapsed = !state.bannerCollapsed;
    renderBanner();
  });
}

/* ------------------------------------------------------------ 表格 */

function rowsForTab() {
  let rows = state.tab === "streams" ? state.streams : state.blocks;
  const q = state.filter.trim().toLowerCase();
  if (q) rows = rows.filter((r) => String(r.name).toLowerCase().includes(q));

  if (state.tab === "blocks") {
    const rank = (s) => (s === 1 ? 0 : s === 2 ? 1 : 2);
    rows = [...rows].sort((a, b) => rank(a.status) - rank(b.status)
      || String(a.name).localeCompare(String(b.name)));
  }
  return rows;
}

function renderStreams() {
  const cols = [
    { key: "name", label: "流股", cls: "name" },
    { key: "temperature_c", label: "温度 ℃", num: true, digits: 2 },
    { key: "pressure_bar", label: "压力 bar", num: true, digits: 3 },
    { key: "mass_flow_kgh", label: "质量流量 kg/h", num: true, digits: 1 },
    { key: "mole_flow_kmolh", label: "摩尔流量 kmol/h", num: true, digits: 2 },
    { key: "vap_frac", label: "汽化率", num: true, digits: 3 },
    { key: "vol_flow_m3h", label: "体积流量 m³/h", num: true, digits: 1 },
  ];
  const rows = rowsForTab();
  const thead = document.querySelector("#table thead");
  const tbody = document.querySelector("#table tbody");
  thead.innerHTML = `<tr>${cols.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("")}</tr>`;
  tbody.innerHTML = rows.map((row) => `<tr>${cols.map((c) => {
    const value = c.num ? fmt(row[c.key], c.digits) : esc(row[c.key] ?? "");
    return `<td class="${c.cls || ""} ${c.num ? "num" : ""}">${value}</td>`;
  }).join("")}</tr>`).join("");
  document.getElementById("count").textContent = `${rows.length} 行`;
}

function renderBlocks() {
  const cols = [
    { key: "name", label: "设备", cls: "name" },
    { key: "type", label: "类型" },
    { key: "status", label: "状态", status: true },
    { key: "duty_kw", label: "热负荷 kW", num: true, digits: 2 },
    { key: "power_kw", label: "功率 kW", num: true, digits: 2 },
    { key: "area_m2", label: "面积 m²", num: true, digits: 2 },
    { key: "nstage", label: "级数", num: true, digits: 0 },
    { key: "top_temp_c", label: "顶温 ℃", num: true, digits: 1 },
    { key: "bottom_temp_c", label: "底温 ℃", num: true, digits: 1 },
    { key: "message", label: "说明", cls: "msg" },
  ];
  const rows = rowsForTab();
  const thead = document.querySelector("#table thead");
  const tbody = document.querySelector("#table tbody");
  thead.innerHTML = `<tr>${cols.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("")}</tr>`;
  tbody.innerHTML = rows.map((row) => {
    const isColumn = COLUMN_TYPE.test(row.type || "");
    return `<tr>${cols.map((c) => {
      if (c.status) return `<td>${blockBadge(row.status)}</td>`;
      if (c.key === "name" && isColumn) {
        return `<td class="name"><a href="#" class="col-link" data-block="${esc(row.name)}">${esc(row.name)}</a></td>`;
      }
      const value = c.num ? fmt(row[c.key], c.digits) : esc(row[c.key] ?? "");
      return `<td class="${c.cls || ""} ${c.num ? "num" : ""}">${value}</td>`;
    }).join("")}</tr>`;
  }).join("");

  tbody.querySelectorAll(".col-link").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      openColumn(a.dataset.block);
    });
  });
  document.getElementById("count").textContent = `${rows.length} 行`;
}

function renderCompare() {
  const thead = document.querySelector("#table thead");
  const tbody = document.querySelector("#table tbody");
  const data = state.compare.data;
  if (!data) {
    thead.innerHTML = "";
    tbody.innerHTML = "";
    document.getElementById("count").textContent = "";
    return;
  }

  const head = ["流股"];
  COMPARE_FIELDS.forEach((f) => head.push(`${f.label} A`, `${f.label} B`, "Δ"));
  thead.innerHTML = `<tr>${head.map((h, i) =>
    `<th class="${i === 0 ? "" : "num"}">${esc(h)}</th>`).join("")}</tr>`;

  const q = state.filter.trim().toLowerCase();
  const rows = (data.streams || []).filter((r) => !q || r.name.toLowerCase().includes(q));

  tbody.innerHTML = rows.map((r) => {
    const cells = [`<td class="name">${esc(r.name)}</td>`];
    COMPARE_FIELDS.forEach((f) => {
      const a = r.base ? r.base[f.key] : null;
      const b = r.other ? r.other[f.key] : null;
      const d = r.delta[f.key];
      const cls = d === null || d === undefined || d === 0 ? "" : "changed";
      cells.push(`<td class="num">${fmt(a, f.digits)}</td>`);
      cells.push(`<td class="num">${fmt(b, f.digits)}</td>`);
      cells.push(`<td class="num ${cls}">${fmtDelta(d, f.digits)}</td>`);
    });
    return `<tr>${cells.join("")}</tr>`;
  }).join("");

  const s = data.summary;
  document.getElementById("count").textContent =
    `${rows.length} 行 · 共同 ${s.common} · 有变化 ${s.changed}`
    + (s.only_base.length ? ` · 仅 A ${s.only_base.length}` : "")
    + (s.only_other.length ? ` · 仅 B ${s.only_other.length}` : "");
}

function renderTable() {
  document.getElementById("empty").classList.add("hidden");
  if (state.tab === "streams") renderStreams();
  else if (state.tab === "blocks") renderBlocks();
  else renderCompare();
}

/* ------------------------------------------------------------ 塔剖面 */

const COL_CHART = { width: 480, height: 170, padL: 56, padR: 14, padT: 14, padB: 24 };
const columnState = { rows: [], charts: [] };

function chartSVG(series) {
  const { width, height, padL, padR, padT, padB } = COL_CHART;
  const w = width - padL - padR;
  const h = height - padT - padB;
  const n = series[0].points.length;
  const vals = series.flatMap((s) => s.points.map((p) => p.y)).filter((v) => typeof v === "number");
  if (!vals.length) return "";
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const sx = (i) => padL + (n <= 1 ? 0 : (i / (n - 1)) * w);
  const sy = (v) => padT + h - ((v - min) / span) * h;

  const paths = series.map((s) => {
    const d = s.points.map((p, i) =>
      `${i ? "L" : "M"}${sx(i).toFixed(1)},${sy(p.y ?? min).toFixed(1)}`).join(" ");
    return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round"/>`;
  }).join("");

  const markers = series.map((s, i) =>
    `<circle class="marker" data-s="${i}" cx="${padL}" cy="${padT}" r="4"
             fill="${s.color}" stroke="#fff" stroke-width="1.5" opacity="0"/>`).join("");

  const legend = series.map((s, i) => `
    <rect x="${padL + i * 54}" y="3" width="10" height="3" fill="${s.color}"/>
    <text x="${padL + i * 54 + 14}" y="8" class="axis">${esc(s.label)}</text>`).join("");

  return `<svg viewBox="0 0 ${width} ${height}" class="chart-svg">
    <line x1="${padL}" y1="${padT + h}" x2="${width - padR}" y2="${padT + h}" class="axis-line"/>
    ${paths}
    <line class="guide" x1="0" y1="${padT}" x2="0" y2="${padT + h}"
          stroke="#94a3b8" stroke-dasharray="4 3" stroke-width="1" opacity="0"/>
    ${markers}
    ${legend}
    <text x="${padL - 6}" y="${padT + 6}" class="axis" text-anchor="end">${max.toFixed(1)}</text>
    <text x="${padL - 6}" y="${padT + h}" class="axis" text-anchor="end">${min.toFixed(1)}</text>
  </svg>`;
}

/* 鼠标位置 → 第几级(按列宽吸附,不需要对准点) */
function chartIndex(svg, evt) {
  const { width, padL, padR } = COL_CHART;
  const n = Number(svg.dataset.n || 0);
  if (n < 1) return 0;
  const rect = svg.getBoundingClientRect();
  const x = ((evt.clientX - rect.left) / rect.width) * width;
  const w = width - padL - padR;
  const i = Math.round(((x - padL) / w) * (n - 1));
  return Math.max(0, Math.min(n - 1, i));
}

function setHover(idx, evt) {
  const { width, height, padL, padR, padT, padB } = COL_CHART;
  const w = width - padL - padR;
  const h = height - padT - padB;

  // 两张图共用一级:准线和标记点同步
  columnState.charts.forEach(({ svg, series }) => {
    const n = series[0].points.length;
    const guide = svg.querySelector(".guide");
    const markers = svg.querySelectorAll(".marker");
    if (idx === null) {
      guide.setAttribute("opacity", 0);
      markers.forEach((m) => m.setAttribute("opacity", 0));
      return;
    }
    const x = padL + (n <= 1 ? 0 : (idx / (n - 1)) * w);
    guide.setAttribute("x1", x);
    guide.setAttribute("x2", x);
    guide.setAttribute("opacity", 1);

    const vals = series.flatMap((s) => s.points.map((p) => p.y))
      .filter((v) => typeof v === "number");
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const span = max - min || 1;
    markers.forEach((m, si) => {
      const p = series[si].points[idx];
      if (p && typeof p.y === "number") {
        m.setAttribute("cx", x);
        m.setAttribute("cy", padT + h - ((p.y - min) / span) * h);
        m.setAttribute("opacity", 1);
      } else {
        m.setAttribute("opacity", 0);
      }
    });
  });

  const tip = document.getElementById("chartTip");
  if (idx === null) {
    tip.classList.add("hidden");
    highlightRow(null);
    return;
  }

  const row = columnState.rows[idx];
  tip.innerHTML = `<div class="tip-head">第 ${esc(row.stage)} 级</div>
    <div class="tip-row"><span>温度</span><b>${fmt(row.temperature_c, 2)} ℃</b></div>
    <div class="tip-row"><span>压力</span><b>${fmt(row.pressure_bar, 3)} bar</b></div>
    <div class="tip-row"><span>液流量</span><b>${fmt(row.liq_flow, 2)}</b></div>
    <div class="tip-row"><span>气流量</span><b>${fmt(row.vap_flow, 2)}</b></div>
    <div class="tip-row"><span>热负荷</span><b>${fmt(row.duty_kw, 3)} kW</b></div>`;
  tip.classList.remove("hidden");
  positionTip(tip, evt);
  highlightRow(idx);
}

function positionTip(tip, evt) {
  const gap = 14;
  const rect = tip.getBoundingClientRect();
  let left = evt.clientX + gap;
  let top = evt.clientY + gap;
  if (left + rect.width > window.innerWidth - 8) left = evt.clientX - rect.width - gap;
  if (top + rect.height > window.innerHeight - 8) top = evt.clientY - rect.height - gap;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${Math.max(8, top)}px`;
}

/* 图 ↔ 表联动:高亮同一级,并把它滚到可见区 */
function highlightRow(idx) {
  const rows = document.querySelectorAll("#columnTable tbody tr");
  rows.forEach((tr, i) => tr.classList.toggle("hl", i === idx));
  if (idx === null) return;
  const tr = rows[idx];
  if (tr && tr.scrollIntoView) tr.scrollIntoView({ block: "nearest" });
}

async function openColumn(block) {
  const panel = document.getElementById("columnPanel");
  document.getElementById("columnTitle").textContent = `塔剖面 · ${block}`;
  panel.classList.remove("hidden");

  const rows = await api(`/api/cases/${state.current.id}/columns/${encodeURIComponent(block)}`);
  state.column = { block, rows };
  columnState.rows = rows;
  columnState.charts = [];

  const temps = rows.map((r) => ({ x: r.stage, y: r.temperature_c }));
  const liq = rows.map((r) => ({ x: r.stage, y: r.liq_flow }));
  const vap = rows.map((r) => ({ x: r.stage, y: r.vap_flow }));

  const chartsEl = document.getElementById("columnCharts");
  chartsEl.innerHTML =
    `<figure class="chart"><figcaption>温度</figcaption>${
      chartSVG([{ points: temps, color: "#2563eb", label: "温度" }])}</figure>`
    + `<figure class="chart"><figcaption>液/气流量</figcaption>${
      chartSVG([
        { points: liq, color: "#2563eb", label: "液" },
        { points: vap, color: "#f59e0b", label: "气" },
      ])}</figure>`;

  const svgs = [...chartsEl.querySelectorAll("svg.chart-svg")];
  svgs.forEach((svg) => { svg.dataset.n = rows.length; });
  columnState.charts = [
    { svg: svgs[0], series: [{ points: temps }] },
    { svg: svgs[1], series: [{ points: liq }, { points: vap }] },
  ].filter((c) => c.svg);

  columnState.charts.forEach(({ svg }) => {
    svg.addEventListener("mousemove", (e) => setHover(chartIndex(svg, e), e));
    svg.addEventListener("mouseleave", () => setHover(null));
  });

  const thead = document.querySelector("#columnTable thead");
  const tbody = document.querySelector("#columnTable tbody");
  thead.innerHTML = `<tr><th>级</th><th class="num">温度 ℃</th><th class="num">压力 bar</th>
    <th class="num">液流量</th><th class="num">气流量</th><th class="num">热负荷 kW</th></tr>`;
  tbody.innerHTML = rows.map((r) => `<tr>
    <td>${r.stage}</td>
    <td class="num">${fmt(r.temperature_c, 2)}</td>
    <td class="num">${fmt(r.pressure_bar, 3)}</td>
    <td class="num">${fmt(r.liq_flow, 2)}</td>
    <td class="num">${fmt(r.vap_flow, 2)}</td>
    <td class="num">${fmt(r.duty_kw, 3)}</td>
  </tr>`).join("");
}

/* ------------------------------------------------------------ 对比 */

function fillCompareSelects() {
  const base = document.getElementById("baseSelect");
  const other = document.getElementById("otherSelect");
  const opts = state.cases.map((c) =>
    `<option value="${c.id}">#${c.id} · ${esc(c.read_at)} · ${esc(c.model_name || "")}</option>`).join("");
  base.innerHTML = opts;
  other.innerHTML = opts;
  if (state.cases.length >= 2) {
    base.value = state.cases[0].id;
    other.value = state.cases[1].id;
  }
}

async function runCompare() {
  const baseId = Number(document.getElementById("baseSelect").value);
  const otherId = Number(document.getElementById("otherSelect").value);
  if (!baseId || !otherId) return;
  if (state.cases.length < 2) {
    document.getElementById("compareSummary").textContent = "至少需要两个工况才能对比。";
    state.compare.data = null;
    renderCompare();
    return;
  }
  state.compare.data = await api(`/api/compare?base=${baseId}&other=${otherId}`);
  const s = state.compare.data.summary;
  document.getElementById("compareSummary").textContent =
    `共同流股 ${s.common} 条,有变化 ${s.changed} 条`
    + (s.only_base.length ? `,仅 A:${s.only_base.slice(0, 5).join(" ")}` : "")
    + (s.only_other.length ? `,仅 B:${s.only_other.slice(0, 5).join(" ")}` : "");
  renderCompare();
}

/* ------------------------------------------------------------ 页签 */

function setTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === tab));
  toggleCompareBar(tab === "compare");
  document.getElementById("columnPanel").classList.add("hidden");
  renderBanner();
  if (tab === "compare") {
    fillCompareSelects();
    runCompare();
  } else {
    renderTable();
  }
}

/* 对比入口:平时是工具栏右上角一个小图标;进对比页后,同一行换成两个下拉框。
   整行高度不变,所以切页签时下面的内容不会上下跳。 */
function toggleCompareBar(isCompare) {
  document.getElementById("compareHint").classList.toggle("hidden", isCompare);
  document.getElementById("compareControls").classList.toggle("hidden", !isCompare);
  document.getElementById("filter").classList.toggle("hidden", isCompare);
  document.getElementById("count").classList.toggle("hidden", isCompare);
}

/* ------------------------------------------------------------ 初始化 */

async function loadCase(id) {
  const [detail, streams, blocks] = await Promise.all([
    api(`/api/cases/${id}`),
    api(`/api/cases/${id}/streams`),
    api(`/api/cases/${id}/blocks`),
  ]);
  state.current = detail;
  state.streams = streams;
  state.blocks = blocks;
  if (state.tab !== "compare") renderTable();
  renderBanner();
}

async function init() {
  try {
    state.cases = await api("/api/cases");
  } catch (err) {
    const empty = document.getElementById("empty");
    empty.textContent = err.message;
    empty.classList.remove("hidden");
    return;
  }

  const select = document.getElementById("caseSelect");
  if (!state.cases.length) {
    document.getElementById("empty").classList.remove("hidden");
    return;
  }

  select.innerHTML = state.cases.map((c) =>
    `<option value="${c.id}">#${c.id} · ${esc(c.model_name || "")} · ${esc(c.read_at || "")}</option>`
  ).join("");

  select.addEventListener("change", () => loadCase(Number(select.value)));

  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      setTab(btn.dataset.tab);
      location.hash = btn.dataset.tab === "compare" ? "compare" : btn.dataset.tab;
    });
  });

  document.getElementById("filter").addEventListener("input", (e) => {
    state.filter = e.target.value;
    if (state.tab === "compare") renderCompare();
    else renderTable();
  });

  document.getElementById("baseSelect").addEventListener("change", runCompare);
  document.getElementById("otherSelect").addEventListener("change", runCompare);
  document.getElementById("compareHint").addEventListener("click", () => {
    setTab("compare");
    location.hash = "compare";
  });
  document.getElementById("closeColumn").addEventListener("click", () =>
    document.getElementById("columnPanel").classList.add("hidden"));

  // 导出 Excel:把当前工况下载成工作簿
  document.getElementById("exportBtn").addEventListener("click", () => {
    if (!state.current) return;
    window.location.href = `/api/cases/${state.current.id}/export.xlsx`;
  });

  // 退出:停服务并关掉窗口(应用窗口模式下 window.close() 生效)
  document.getElementById("quitBtn").addEventListener("click", async () => {
    if (!confirm("确定退出 aspen-insight 吗?\n后台服务会一起停止。")) return;
    const btn = document.getElementById("quitBtn");
    btn.disabled = true;
    btn.textContent = "正在退出…";
    try {
      await fetch("/api/shutdown", { method: "POST" });
    } catch (err) {
      // 服务已经关掉,连接被丢弃属于正常
    }
    window.close();
    // 如果窗口没被关掉(普通标签页不允许脚本关闭),退化成一张空白的"已退出"页
    setTimeout(() => {
      document.body.innerHTML = '<div class="exit-screen">已退出</div>';
    }, 500);
  });

  fillCompareSelects();

  const hashParts = location.hash.replace("#", "").split("/");
  const hash = hashParts[0];
  if (["streams", "blocks", "compare"].includes(hash)) {
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === hash));
    state.tab = hash;
    toggleCompareBar(hash === "compare");
  }

  await loadCase(state.cases[0].id);
  if (state.tab === "compare") {
    renderTable();
    runCompare();
  }
  if (hash === "blocks" && hashParts[1]) {
    openColumn(hashParts[1]);
  }
}

init();
