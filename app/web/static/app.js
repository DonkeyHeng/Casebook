/* aspen-insight 前端:零构建,原生 JS */

const state = {
  cases: [],
  current: null,
  streams: [],
  blocks: [],
  tab: "streams",
  filter: "",
  bannerCollapsed: false,
  compare: { baseId: null, otherId: null, data: null, scope: "streams" },
  column: null,
};

const COMPARE_FIELDS = [
  { key: "temperature_c", label: "温度 ℃", digits: 2 },
  { key: "pressure_bar", label: "压力 bar", digits: 3 },
  { key: "mass_flow_kgh", label: "流量 kg/h", digits: 1 },
  { key: "vap_frac", label: "汽化率", digits: 3 },
];

/* 设备对比只放 5 个字段:再加顶底温就 18 列了,再多看不过来 */
const BLOCK_COMPARE_FIELDS = [
  { key: "duty_kw", label: "热负荷 kW", digits: 2 },
  { key: "power_kw", label: "功率 kW", digits: 2 },
  { key: "nstage", label: "级数", digits: 0 },
  { key: "top_temp_c", label: "顶温 ℃", digits: 1 },
  { key: "bottom_temp_c", label: "底温 ℃", digits: 1 },
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

/* DELETE 单独一个:api() 只做 GET,别为了它把方法参数塞进去到处传 */
async function apiDelete(path) {
  const res = await fetch(path, { method: "DELETE" });
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
  const list = c.reasons || [];
  const reasons = list.map((r) => `<div class="reason">· ${esc(r)}</div>`).join("");
  const hasReasons = list.length > 0;

  el.className = `banner ${s.cls}` + (state.bannerCollapsed && hasReasons ? " collapsed" : "");
  el.innerHTML = `
    <div class="banner-head">
      <span class="banner-title">
        <strong>${s.text}</strong>　流股 ${c.stream_count} 条 / 设备 ${c.block_count} 个${
          c.run_id ? `　运行 ID ${esc(c.run_id)}` : ""}
      </span>
      ${hasReasons ? `<button class="banner-toggle" id="bannerToggle" type="button"
              title="${state.bannerCollapsed ? "展开问题清单" : "收起问题清单"}">
        ${state.bannerCollapsed ? "展开 ▾" : "收起 ▴"}
      </button>` : ""}
    </div>
    <div class="banner-verdict">${esc(bannerVerdict(c))}</div>
    ${hasReasons ? `<div class="banner-body">${reasons}</div>` : ""}`;

  // 没有理由就不给「展开」—— 点开是空的,比没有按钮更让人困惑
  const toggle = document.getElementById("bannerToggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      state.bannerCollapsed = !state.bannerCollapsed;
      renderBanner();
    });
  }
}

/* 横幅里那句"所以能不能用" —— 只说结论,不复述状态码。
   放在 .banner-body 外面,折叠时它仍然可见:收起的只是明细,结论不该被收起。 */
function bannerVerdict(c) {
  const errors = (c.block_errors || []).length;
  const warns = (c.block_warnings || []).length;

  if (c.severity === "ok") {
    return "没有报错 —— 这份数据可以用来做定量分析(算收率、比能耗、做工况对比)。";
  }
  if (c.severity === "warning") {
    return warns
      ? `有 ${warns} 个模块告警、没有报错 —— 数据可用,引用时注明告警项即可。`
      : "有告警、没有报错 —— 数据可用,引用时注明告警项即可。";
  }
  return errors
    ? `本次运行有 ${errors} 个模块报错 —— 不建议用它算收率 / 能耗 / 做工况对比;其余流股只作定性参考。`
    : "运行状态码显示有错误(但没有具体模块报错)—— 不建议用这份数据做定量结论。";
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
    if (c.key === "name") {
      // 流股名可点:按需看这条流股的组分(18 组分很贵,不能全量读)
      return `<td class="name"><a href="#" class="stream-link" data-stream="${esc(row.name)}"
                 title="看这条流股的组分">${esc(row.name)}</a></td>`;
    }
    const value = c.num ? fmt(row[c.key], c.digits) : esc(row[c.key] ?? "");
    return `<td class="${c.cls || ""} ${c.num ? "num" : ""}">${value}</td>`;
  }).join("")}</tr>`).join("");

  tbody.querySelectorAll(".stream-link").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      openComposition(a.dataset.stream);
    });
  });
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

  if (state.compare.scope === "blocks") {
    renderCompareBlocks(thead, tbody, data);
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

/* 设备对比:负荷/功率/级数/顶底温 各出一组 A/B/Δ,状态单独一列(变了就直接显示 A → B) */
function renderCompareBlocks(thead, tbody, data) {
  const head = ["设备", "类型", "状态"];
  BLOCK_COMPARE_FIELDS.forEach((f) => head.push(`${f.label} A`, `${f.label} B`, "Δ"));
  thead.innerHTML = `<tr>${head.map((h, i) =>
    `<th class="${i === 0 ? "cmp-name" : i < 3 ? "" : "num"}">${esc(h)}</th>`).join("")}</tr>`;

  const q = state.filter.trim().toLowerCase();
  const rows = (data.blocks || []).filter((r) => !q || r.name.toLowerCase().includes(q));

  tbody.innerHTML = rows.map((r) => {
    const sa = r.status ? r.status.base : null;
    const sb = r.status ? r.status.other : null;
    const statusCell = (sa === null || sa === undefined) && (sb === null || sb === undefined)
      ? '<td class="muted">—</td>'
      : sa !== sb
        ? `<td>${sa === null || sa === undefined ? "—" : blockBadge(sa)}
             <span class="muted">→</span>
             ${sb === null || sb === undefined ? "—" : blockBadge(sb)}</td>`
        : `<td>${blockBadge(sb)}</td>`;

    const cells = [
      `<td class="name cmp-name">${esc(r.name)}</td>`,
      `<td>${esc(r.type || "")}</td>`,
      statusCell,
    ];
    BLOCK_COMPARE_FIELDS.forEach((f) => {
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
    `${rows.length} 行 · 共同 ${s.block_common} · 有变化 ${s.block_changed}`
    + (s.block_only_base.length ? ` · 仅 A ${s.block_only_base.length}` : "")
    + (s.block_only_other.length ? ` · 仅 B ${s.block_only_other.length}` : "");
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
  document.getElementById("compPanel").classList.add("hidden");  // 两个抽屉不叠着
  document.getElementById("columnTitle").textContent = `塔剖面 · ${block}`;
  panel.classList.remove("hidden");
  panel.scrollTop = 0;   // 看另一座塔时别带着上一座的滚动位置

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

/* ------------------------------------------------------------ 流股组成 */

/* 摩尔分数可能是 1e-9 量级的痕量:固定 6 位小数会全变成 0.000000,
   所以小的改用科学计数法,大的保留 6 位。 */
function fmtFrac(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v !== "number") return esc(v);
  if (v !== 0 && Math.abs(v) < 1e-4) return v.toExponential(2);
  return v.toFixed(6);
}

/* 百分比:99.6% / 0.21% / 0.0007% —— 按量级换精度,别把痕量写成 0.00% */
function pctText(fraction) {
  if (fraction === null || fraction === undefined) return "—";
  const p = fraction * 100;
  if (p === 0) return "0";
  if (p >= 10) return `${p.toFixed(1)}%`;
  if (p >= 0.1) return `${p.toFixed(2)}%`;
  if (p >= 0.001) return `${p.toFixed(3)}%`;
  return `${p.toExponential(1)}%`;
}

const PHASE_COLORS = ["#2563eb", "#60a5fa", "#93c5fd", "#a5b4fc", "#c4b5fd", "#f59e0b"];
const PHASE_REST_COLOR = "#cbd5e1";

/* 一条相态条:横条按比例分色(前 6 个组分 + 其他),右边写 Top3 百分比 */
function phaseRow(label, key, comps, sum) {
  const total = typeof sum === "number" && sum > 0 ? sum : 0;
  const items = comps
    .map((c) => ({ name: String(c.component), v: typeof c[key] === "number" ? c[key] : 0 }))
    .filter((c) => c.v > 0)
    .sort((a, b) => b.v - a.v);

  if (!total || !items.length) {
    return `<div class="phase-row">
      <span class="phase-label">${esc(label)}</span>
      <span class="phase-bar"></span>
      <span class="phase-legend">该相没有可显示的值</span>
    </div>`;
  }

  const head = items.slice(0, 6);
  const tail = items.slice(6);
  const tailSum = tail.reduce((s, c) => s + c.v, 0);

  const segs = head.map((c, i) =>
    `<i class="phase-seg" style="width:${(c.v / total * 100).toFixed(3)}%;background:${PHASE_COLORS[i % PHASE_COLORS.length]}"></i>`
  ).join("") + (tail.length
    ? `<i class="phase-seg" style="width:${(tailSum / total * 100).toFixed(3)}%;background:${PHASE_REST_COLOR}"></i>`
    : "");

  const legend = head.slice(0, 3).map((c) => `${esc(c.name)} ${pctText(c.v / total)}`);
  if (tail.length) legend.push(`其他 ${pctText(tailSum / total)}`);

  return `<div class="phase-row">
    <span class="phase-label">${esc(label)}</span>
    <span class="phase-bar">${segs}</span>
    <span class="phase-legend" title="${esc(legend.join(" · "))}">${legend.join(" · ")}</span>
  </div>`;
}

const compState = { seq: 0 };

async function openComposition(stream) {
  const panel = document.getElementById("compPanel");
  document.getElementById("columnPanel").classList.add("hidden");
  document.getElementById("compTitle").textContent = `流股组成 · ${stream}`;
  document.getElementById("compBody").innerHTML =
    '<p class="comp-note">正在读取…(工况里存过就直接取;没存过会去当前打开的 Aspen 现读一次,约 0.3 秒)</p>';
  panel.classList.remove("hidden");

  // 连点不同流股时,只认最后一次请求的结果,避免慢的响应盖掉新的
  const seq = ++compState.seq;
  let data;
  try {
    data = await api(`/api/cases/${state.current.id}/streams/${encodeURIComponent(stream)}/composition`);
  } catch (err) {
    if (seq !== compState.seq) return;
    document.getElementById("compBody").innerHTML =
      `<div class="comp-error">${esc(err.message)}</div>`;
    return;
  }
  if (seq !== compState.seq) return;
  renderComposition(data);
}

function renderComposition(data) {
  const body = document.getElementById("compBody");
  const comps = data.components || [];
  const note = data.source === "live"
    ? '<p class="comp-note live">刚从当前打开的 Aspen 读出来,已存入本工况(下次打开直接取,不再连 Aspen)。</p>'
    : '<p class="comp-note">来自本工况已存的数据。</p>';

  const rows = comps.map((c) => {
    const zero = (v) => (v === 0 ? " comp-zero" : "");
    return `<tr>
      <td class="name">${esc(c.component)}</td>
      <td class="num${zero(c.x_liq)}">${fmtFrac(c.x_liq)}</td>
      <td class="num${zero(c.y_vap)}">${fmtFrac(c.y_vap)}</td>
    </tr>`;
  }).join("");

  body.innerHTML = `${note}
    <div class="comp-table-wrap">
      <table>
        <thead><tr><th>组分</th><th class="num">液相摩尔分数 x</th><th class="num">气相摩尔分数 y</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="comp-foot">
      ${phaseRow("液相", "x_liq", comps, data.sum_x)}
      ${phaseRow("气相", "y_vap", comps, data.sum_y)}
      <p class="comp-sum">共 ${data.count} 个组分 · 合计 x = ${fmt(data.sum_x, 4)} · 合计 y = ${fmt(data.sum_y, 4)}${
        data.run_id ? ` · 运行 ID ${esc(data.run_id)}` : ""}</p>
    </div>`;
}

/* ------------------------------------------------------------ 对比 */

function fillCompareSelects() {
  const base = document.getElementById("baseSelect");
  const other = document.getElementById("otherSelect");
  // 记住用户已经选的那两个,只在失效时才回退到最新两条
  const wantBase = base.value;
  const wantOther = other.value;
  const opts = state.cases.map((c) =>
    `<option value="${c.id}">#${c.id} · ${esc(c.read_at)} · ${esc(c.model_name || "")}</option>`).join("");
  base.innerHTML = opts;
  other.innerHTML = opts;
  if (state.cases.length >= 2) {
    const ids = state.cases.map((c) => String(c.id));
    base.value = ids.includes(wantBase) ? wantBase : ids[0];
    other.value = ids.includes(wantOther) ? wantOther : ids[1];
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
  updateCompareSummary();
  renderCompare();
}

/* 顶部那行摘要跟着 流股 / 设备 切换,不然看设备表时还写着"共同流股 N 条" */
function updateCompareSummary() {
  const el = document.getElementById("compareSummary");
  const data = state.compare.data;
  if (!data) { el.textContent = ""; return; }
  const s = data.summary;
  if (state.compare.scope === "blocks") {
    el.textContent = `共同设备 ${s.block_common} 个,有变化 ${s.block_changed} 个`
      + (s.block_only_base.length ? `,仅 A:${s.block_only_base.slice(0, 5).join(" ")}` : "")
      + (s.block_only_other.length ? `,仅 B:${s.block_only_other.slice(0, 5).join(" ")}` : "");
  } else {
    el.textContent = `共同流股 ${s.common} 条,有变化 ${s.changed} 条`
      + (s.only_base.length ? `,仅 A:${s.only_base.slice(0, 5).join(" ")}` : "")
      + (s.only_other.length ? `,仅 B:${s.only_other.slice(0, 5).join(" ")}` : "");
  }
}

function setCompareScope(scope) {
  state.compare.scope = scope === "blocks" ? "blocks" : "streams";
  document.querySelectorAll("#compareScope button").forEach((b) =>
    b.classList.toggle("active", b.dataset.scope === state.compare.scope));
  updateCompareSummary();
  renderCompare();
  resetTableScroll();
}

/* ------------------------------------------------------------ 页签 */

/* 三个页签共用同一个滚动容器(#tableWrap),换内容时必须把滚动条拨回顶部,
   否则会带着上一个页签的位置 —— 新表看起来像"从中间开始",第一行都看不到。 */
function resetTableScroll() {
  const wrap = document.getElementById("tableWrap");
  if (wrap) wrap.scrollTop = 0;
}

function setTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === tab));
  toggleCompareBar(tab === "compare");
  document.getElementById("columnPanel").classList.add("hidden");
  document.getElementById("compPanel").classList.add("hidden");
  renderBanner();
  if (tab === "compare") {
    // 筛选框在对比页是隐藏的,不清空就会"看不见地"把对比行筛掉
    state.filter = "";
    document.getElementById("filter").value = "";
    fillCompareSelects();
    runCompare();
  } else {
    renderTable();
  }
  resetTableScroll();
}

/* 对比入口:平时是工具栏右上角一个小图标;进对比页后,同一行换成两个下拉框。
   整行高度不变,所以切页签时下面的内容不会上下跳。
   左边原本放筛选框的位置,在对比页换成「流股 / 设备」开关。 */
function toggleCompareBar(isCompare) {
  document.getElementById("compareHint").classList.toggle("hidden", isCompare);
  document.getElementById("compareControls").classList.toggle("hidden", !isCompare);
  document.getElementById("filter").classList.toggle("hidden", isCompare);
  document.getElementById("compareScope").classList.toggle("hidden", !isCompare);
  document.getElementById("count").classList.toggle("hidden", isCompare);
}

/* ------------------------------------------------------------ 读取当前工况 */

/* 读数要 9–25 s(1000 流股约 76 s),所以不阻塞页面:
   POST /api/read 起一个后台任务 → 每 0.6 s 问一次 /api/read/status → 完成后自动切到新工况。 */
const READ_POLL_MS = 600;
const READ_DONE_HIDE_MS = 9000;
/* 读到这个秒数还没结束就提示"可能卡住了" —— 121 流股约 25 s,1000 流股约 76 s,
   2 分钟以上基本只有两种情况:Aspen 在忙/弹了对话框,或者许可证在排队。 */
const READ_STALL_SEC = 120;
const readState = { timer: null, hideTimer: null, busy: false };

function readStatusEl() {
  return document.getElementById("readStatus");
}

function setReadBusy(busy) {
  readState.busy = busy;
  const btn = document.getElementById("readBtn");
  btn.disabled = busy;
  btn.textContent = busy ? "读取中…" : "读取当前工况";
}

function hideReadStatus(delay = 0) {
  clearTimeout(readState.hideTimer);
  const hide = () => readStatusEl().classList.add("hidden");
  if (delay) readState.hideTimer = setTimeout(hide, delay);
  else hide();
}

/* 失败提示照原样显示:connect.py 里那句提示本来就是分好行的人话(该查哪几项) */
function showReadError(message) {
  const el = readStatusEl();
  el.className = "read-status error";
  el.innerHTML = `<span class="read-text">${esc(message)}</span>
    <button class="read-close" type="button" title="关闭">✕</button>`;
  el.querySelector(".read-close").addEventListener("click", () => hideReadStatus());
  el.classList.remove("hidden");
}

function renderReadRunning(snap) {
  const el = readStatusEl();
  const total = Number(snap.total || 0);
  const done = Number(snap.done || 0);
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
  const elapsed = Number(snap.elapsed_sec || 0);
  const parts = [esc(snap.phase || "读取中")];
  if (total) parts.push(`${done}/${total}`);
  if (snap.label) parts.push(esc(snap.label));

  const stalled = elapsed >= READ_STALL_SEC;
  const hint = stalled
    ? `<span class="read-hint">已经读了 ${fmtSeconds(elapsed)},比正常慢很多 ——
         可能是 Aspen 正在忙、弹出了对话框,或者许可证在排队。
         请看一眼 Aspen 窗口;实在不行点右上角「退出」重开后再读。</span>`
    : "";
  // 同时开着几个模型时,attach() 取的是 ROT 里第一个 —— 显示出来才能发现读错了对象
  const model = snap.model_path ? baseName(snap.model_path) : "";

  el.className = `read-status running${stalled ? " stalled" : ""}`;
  el.innerHTML = `
    <span class="read-dot"></span>
    <span class="read-text">${parts.join(" · ")}</span>
    <span class="read-progress"><span class="read-bar" style="width:${pct}%"></span></span>
    ${model ? `<span class="read-model" title="${esc(snap.model_path)}">${esc(model)}</span>` : ""}
    <span class="read-time">${elapsed.toFixed(1)} s</span>
    ${hint}`;
  el.classList.remove("hidden");
}

function fmtSeconds(sec) {
  const s = Math.round(sec);
  return s < 60 ? `${s} 秒` : `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
}

/* 从完整路径取文件名(Windows 反斜杠 / 正斜杠都认) */
function baseName(path) {
  return String(path || "").split(/[\\/]/).pop() || "";
}

function renderReadDone(snap) {
  const el = readStatusEl();
  const sev = SEVERITY[snap.severity] || SEVERITY.warning;
  const gaps = Number(snap.unit_gaps || 0);
  const units = (snap.unit_units || []).filter(Boolean);
  const model = snap.model_path ? baseName(snap.model_path) : "";

  // 老模型的单位集可能不是 METCBAR,换算不了就只能留空 —— 必须说清楚,别让人以为模型没结果
  const gapHint = gaps > 0
    ? `<span class="read-hint warn">⚠️ 有 ${gaps} 个字段的原始单位不认识、已留空${
        units.length ? `(${esc(units.join("、"))})` : "(单位为空)"}。
        表里的「—」可能是这个原因,不代表模型没结果。</span>`
    : "";

  el.className = `read-status done ${sev.cls}`;
  el.innerHTML = `
    <span class="read-text">已读入工况 #${esc(snap.case_id)}${model ? `(${esc(model)})` : ""}:
      ${esc(snap.stream_count ?? "-")} 流股 / ${esc(snap.block_count ?? "-")} 设备 ·
      ${sev.text} · ${Number(snap.elapsed_sec || 0).toFixed(1)} s</span>
    ${gapHint}
    <button class="read-close" type="button" title="关闭">✕</button>`;
  el.querySelector(".read-close").addEventListener("click", () => hideReadStatus());
  el.classList.remove("hidden");
  if (!gaps) hideReadStatus(READ_DONE_HIDE_MS);   // 有单位警告就不自动消失,让人看清
}

async function startRead() {
  if (readState.busy) return;
  setReadBusy(true);
  hideReadStatus();

  let payload;
  try {
    const res = await fetch("/api/read", { method: "POST" });
    payload = await res.json().catch(() => ({}));
    if (res.status === 409) {
      // 已经有一次读取在跑(比如页面刷新过):接管它的进度,而不是弹个错就完事
      renderReadRunning(payload.job || {});
      pollRead();
      return;
    }
    if (!res.ok) {
      showReadError(payload.error || `请求失败:${res.status}`);
      setReadBusy(false);
      return;
    }
  } catch (err) {
    showReadError(`连不上后台服务:${err.message}\n\n服务可能已经被关掉了,重新双击桌面快捷方式即可。`);
    setReadBusy(false);
    return;
  }

  renderReadRunning(payload.job || {});
  pollRead();
}

async function pollRead() {
  clearTimeout(readState.timer);
  let snap;
  try {
    snap = await api("/api/read/status");
  } catch (err) {
    setReadBusy(false);
    showReadError(`查询读取进度失败:${err.message}`);
    return;
  }

  if (snap.state === "running") {
    renderReadRunning(snap);
    readState.timer = setTimeout(pollRead, READ_POLL_MS);
    return;
  }

  setReadBusy(false);
  if (snap.state === "done") {
    renderReadDone(snap);
    try {
      await afterRead(snap.case_id);
    } catch (err) {
      showReadError(`工况已经读进来了,但刷新页面数据时出错:${err.message}`);
    }
    return;
  }
  if (snap.state === "error") {
    showReadError(snap.error || "读取失败,原因未知。");
    return;
  }
  hideReadStatus();
}

/* 读完:刷新工况下拉 → 切到新工况 → (若在对比页)重跑对比 */
async function afterRead(caseId) {
  await refreshCases(caseId);
  if (!caseId) return;
  await loadCase(caseId);
  if (state.tab === "compare") await runCompare();
}

/* 页面刷新/重开时,后台可能正在读 —— 接着显示进度,不用让用户再点一次 */
async function resumeReadIfRunning() {
  try {
    const snap = await api("/api/read/status");
    if (snap && snap.state === "running") {
      setReadBusy(true);
      renderReadRunning(snap);
      pollRead();
    }
  } catch (err) {
    // 老版本服务没有这个接口,忽略即可
  }
}

/* ------------------------------------------------------------ 初始化 */

function showEmpty(text) {
  const el = document.getElementById("empty");
  el.textContent = text || "还没有工况。点右上角「读取当前工况」,从已经打开的 Aspen 里读一次。";
  el.classList.remove("hidden");
}

function hideEmpty() {
  document.getElementById("empty").classList.add("hidden");
}

/* 没有工况时三个按钮点了也没反应,不如直接禁用,免得像"坏了" */
function setCaseButtonsEnabled(on) {
  document.getElementById("exportBtn").disabled = !on;
  document.getElementById("aiPackBtn").disabled = !on;
  document.getElementById("deleteBtn").disabled = !on;
}

async function refreshCases(preferId = null) {
  state.cases = await api("/api/cases");

  const select = document.getElementById("caseSelect");
  select.innerHTML = state.cases.map((c) =>
    `<option value="${c.id}">#${c.id} · ${esc(c.model_name || "")} · ${esc(c.read_at || "")}</option>`
  ).join("");

  const target = preferId ?? (state.current ? state.current.id : null);
  if (target && state.cases.some((c) => c.id === target)) select.value = String(target);

  fillCompareSelects();
  if (!state.cases.length) showEmpty();
  setCaseButtonsEnabled(!!state.current && state.cases.length > 0);
}

async function loadCase(id) {
  const [detail, streams, blocks] = await Promise.all([
    api(`/api/cases/${id}`),
    api(`/api/cases/${id}/streams`),
    api(`/api/cases/${id}/blocks`),
  ]);
  state.current = detail;
  state.streams = streams;
  state.blocks = blocks;
  const select = document.getElementById("caseSelect");
  if (select.value !== String(id)) select.value = String(id);
  hideEmpty();
  setCaseButtonsEnabled(true);
  if (state.tab !== "compare") renderTable();
  renderBanner();
  resetTableScroll();   // 换工况也从第一行看起
}

/* 页签只从地址栏恢复一次,之后以点选为准 */
function applyHashTab() {
  const hash = location.hash.replace("#", "").split("/")[0];
  if (!["streams", "blocks", "compare"].includes(hash)) return hash;
  state.tab = hash;
  document.querySelectorAll(".tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === hash));
  toggleCompareBar(hash === "compare");
  return hash;
}

/* 删除当前工况:不可恢复,所以确认框里把"删的是哪一个"写全(模型名 + 时间 + 规模)*/
async function deleteCurrentCase() {
  const c = state.current;
  if (!c) return;

  const msg = `删除工况 #${c.id}?\n\n`
    + `模型:${c.model_name || "-"}\n`
    + `读取时间:${c.read_at || "-"}\n`
    + `规模:${c.stream_count} 流股 / ${c.block_count} 设备\n\n`
    + "明细(流股/设备/塔剖面/组成)会一起删除,不可恢复 —— 要用得重新读一次 Aspen。";
  if (!confirm(msg)) return;

  const btn = document.getElementById("deleteBtn");
  btn.disabled = true;
  btn.textContent = "删除中…";
  try {
    await apiDelete(`/api/cases/${c.id}`);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "删除工况";
    alert(`删除失败:${err.message}`);
    return;
  }
  btn.disabled = false;
  btn.textContent = "删除工况";

  // 删完切到剩下的最新工况;一条都不剩就回到空态
  state.current = null;
  state.streams = [];
  state.blocks = [];
  await refreshCases(null);
  if (state.cases.length) {
    await loadCase(state.cases[0].id);
  } else {
    renderBanner();
    renderTable();
    showEmpty();
    setCaseButtonsEnabled(false);
  }
}

/* 退出:停服务并关掉窗口(应用窗口模式下 window.close() 生效) */
async function quitApp() {
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
}

/* 所有按钮都只绑定一次 —— 即使库里一条工况都没有,也得能点「读取当前工况」 */
function bindEvents() {
  document.getElementById("caseSelect").addEventListener("change", (e) => {
    loadCase(Number(e.target.value));
  });

  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      setTab(btn.dataset.tab);
      location.hash = btn.dataset.tab;
    });
  });

  document.getElementById("filter").addEventListener("input", (e) => {
    state.filter = e.target.value;
    if (state.tab === "compare") renderCompare();
    else renderTable();
  });

  document.getElementById("baseSelect").addEventListener("change", runCompare);
  document.getElementById("otherSelect").addEventListener("change", runCompare);
  document.querySelectorAll("#compareScope button").forEach((b) =>
    b.addEventListener("click", () => setCompareScope(b.dataset.scope)));
  document.getElementById("compareHint").addEventListener("click", () => {
    setTab("compare");
    location.hash = "compare";
  });
  document.getElementById("closeColumn").addEventListener("click", () =>
    document.getElementById("columnPanel").classList.add("hidden"));
  document.getElementById("closeComp").addEventListener("click", () =>
    document.getElementById("compPanel").classList.add("hidden"));

  document.getElementById("readBtn").addEventListener("click", startRead);

  // 导出 Excel:给人看
  document.getElementById("exportBtn").addEventListener("click", () => {
    if (!state.current) return;
    window.location.href = `/api/cases/${state.current.id}/export.xlsx`;
  });

  // 导出 AI 分析包:给 AI 读(Markdown 说明 + CSV)
  document.getElementById("aiPackBtn").addEventListener("click", () => {
    if (!state.current) return;
    window.location.href = `/api/cases/${state.current.id}/export.ai.zip`;
  });

  document.getElementById("quitBtn").addEventListener("click", quitApp);
  document.getElementById("deleteBtn").addEventListener("click", deleteCurrentCase);
}

async function init() {
  bindEvents();

  try {
    await refreshCases(null);
  } catch (err) {
    showEmpty(`读不到工况列表:${err.message}\n\n后台服务可能没起来,重新双击桌面快捷方式。`);
    return;
  }

  const hash = applyHashTab();

  if (!state.cases.length) {
    await resumeReadIfRunning();
    return;
  }

  await loadCase(state.cases[0].id);
  if (state.tab === "compare") {
    renderTable();
    await runCompare();
  }

  const hashParts = location.hash.replace("#", "").split("/");
  if (hash === "blocks" && hashParts[1]) openColumn(hashParts[1]);

  await resumeReadIfRunning();
}

init();
