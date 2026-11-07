/* Casebook 前端:零构建,原生 JS */

const state = {
  cases: [],
  current: null,
  streams: [],
  blocks: [],
  tab: "streams",          // 数据页里的子视图:streams / blocks / compare
  page: "home",            // 当前页面:home / streams / blocks / compare
  emptyText: "",           // 空态文案(读不到工况列表等错误也走这里)
  emptyIsError: false,     // 空态是「出错」还是「本来就还没有工况」
  flow: {                  // 流程页:数据 + 布局 + 视图 + 聚焦
    caseId: null, data: null, layout: null, view: null, focus: null,
    center: null,          // 被点中的设备(和 2 层邻居分开配色)
    showLabels: false,
  },
  filter: "",
  bannerCollapsed: false,
  compare: { baseId: null, otherId: null, data: null, scope: "streams" },
  impact: null,            // 报错波及范围(/api/cases/{id}/impact):哪些流股不能用
  column: null,
};

const COMPARE_FIELDS = [
  { key: "temperature_c", label: "温度 ℃", digits: 2 },
  { key: "pressure_bar", label: "压力 bar", digits: 3 },
  { key: "mass_flow_kgh", label: "流量 kg/h", digits: 1 },
  { key: "vap_frac", label: "汽化率", digits: 3 },
];

/* 设备对比只放 6 个字段:热负荷/功率/级数/顶底温 + ΔH(焓平衡自检的原始量) */
const BLOCK_COMPARE_FIELDS = [
  { key: "duty_kw", label: "热负荷 kW", digits: 2 },
  { key: "power_kw", label: "功率 kW", digits: 2 },
  { key: "nstage", label: "级数", digits: 0 },
  { key: "top_temp_c", label: "顶温 ℃", digits: 1 },
  { key: "bottom_temp_c", label: "底温 ℃", digits: 1 },
  { key: "dh_kw", label: "ΔH kW", digits: 2 },
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

async function apiPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || data.message || `请求失败:${res.status}`);
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
  empty: { cls: "empty", text: "没有结果" },
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
  const badTears = (c.tear || []).filter(
    (t) => typeof t.error === "number" && t.tolerance && t.error > t.tolerance);

  if (c.severity === "empty") {
    return "模型里没有任何结果 —— 这份文件没在 Aspen 里运行过(或结果为空),先把仿真跑完再读取。";
  }
  if (c.severity === "ok") {
    return "没有报错 —— 这份数据可以用来做定量分析(算收率、比能耗、做工况对比)。";
  }
  if (c.severity === "warning") {
    const bits = [];
    if (warns) bits.push(`${warns} 个模块告警`);
    if (badTears.length) bits.push(`${badTears.length} 条撕裂流误差超过容差`);
    const what = bits.length ? bits.join(" + ") : "有情况需要核对";
    return `${what}、没有报错 —— 数据可用,引用时注明即可。`;
  }
  // error:说清楚到底错在哪。撕裂流已经不参与 error 判定(见 reader.judge 的注释),
  // 剩下的硬信号只有模块级 BLKSTAT=1 和 CVSTAT≠0。
  if (errors) {
    return `本次运行有 ${errors} 个模块报错 —— 不建议用它算收率 / 能耗 / 做工况对比;其余流股只作定性参考。`;
  }
  return "收敛块自己报了错(CVSTAT 非 0)—— 不建议用这份数据做定量结论。";
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
    // 报错设备碰过的流股在这里标出来:数据不可用 / 仅告警(点徽标跳到那台设备)
    { key: "impact", label: "受影响", impact: true },
    { key: "temperature_c", label: "温度 ℃", num: true, digits: 2 },
    { key: "pressure_bar", label: "压力 bar", num: true, digits: 3 },
    { key: "mass_flow_kgh", label: "质量流量 kg/h", num: true, digits: 1 },
    { key: "mole_flow_kmolh", label: "摩尔流量 kmol/h", num: true, digits: 2 },
    { key: "vap_frac", label: "汽化率", num: true, digits: 3 },
    { key: "vol_flow_m3h", label: "体积流量 m³/h", num: true, digits: 1 },
    // 焓(第 1 步新增):能量流 kW = HMX_FLOW,可直接相加做 ΔH;质量/摩尔焓按单位集换算
    { key: "enthalpy_kjkg", label: "质量焓 kJ/kg", num: true, digits: 1 },
    { key: "enthalpy_kjkmol", label: "摩尔焓 kJ/kmol", num: true, digits: 1 },
    { key: "enthalpy_flow_kw", label: "能量流 kW", num: true, digits: 1 },
  ];
  const rows = rowsForTab();
  const hits = impactMap();
  const thead = document.querySelector("#table thead");
  const tbody = document.querySelector("#table tbody");
  thead.innerHTML = `<tr>${cols.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("")}</tr>`;
  tbody.innerHTML = rows.map((row) => {
    const hit = hits.get(row.name);
    const rowCls = hit ? (hit.severity === "error" ? " row-err" : " row-warn") : "";
    return `<tr class="${rowCls.trim()}">${cols.map((c) => {
      if (c.impact) return `<td>${impactChip(hit)}</td>`;
      if (c.key === "name") {
        // 流股名可点:按需看这条流股的组分(18 组分很贵,不能全量读)
        return `<td class="name"><a href="#" class="stream-link" data-stream="${esc(row.name)}"
                   title="看这条流股的组分">${esc(row.name)}</a></td>`;
      }
      const value = c.num ? fmt(row[c.key], c.digits) : esc(row[c.key] ?? "");
      return `<td class="${c.cls || ""} ${c.num ? "num" : ""}">${value}</td>`;
    }).join("")}</tr>`;
  }).join("");

  tbody.querySelectorAll(".stream-link").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      openComposition(a.dataset.stream);
    });
  });
  let count = `${rows.length} 行`;
  const sm = state.impact && state.impact.summary;
  if (sm && sm.error) count += ` · ${sm.error} 条受报错影响`;
  else if (sm && sm.warning) count += ` · ${sm.warning} 条仅告警`;
  document.getElementById("count").textContent = count;
}

function renderBlocks() {
  const cols = [
    { key: "name", label: "设备", cls: "name" },
    { key: "type", label: "类型" },
    { key: "status", label: "状态", status: true },
    { key: "duty_kw", label: "热负荷 kW", num: true, digits: 2 },
    { key: "power_kw", label: "功率 kW", num: true, digits: 2 },
    // 焓平衡自检:ΔH = Σ出 − Σ入(流股能量流),参照 = 热负荷 / 轴功率 / 0(绝热)
    { key: "dh_kw", label: "ΔH kW", num: true, digits: 2 },
    { key: "ref_kw", label: "参照 kW", num: true, digits: 2 },
    { key: "balance_state", label: "焓平衡", balance: true },
    { key: "area_m2", label: "面积 m²", num: true, digits: 2 },
    { key: "nstage", label: "级数", num: true, digits: 0 },
    { key: "top_temp_c", label: "顶温 ℃", num: true, digits: 1 },
    { key: "bottom_temp_c", label: "底温 ℃", num: true, digits: 1 },
    { key: "balance_note", label: "平衡说明", cls: "msg" },
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
      if (c.balance) return `<td>${balanceBadge(row.balance_state)}</td>`;
      if (c.key === "name" && isColumn) {
        return `<td class="name"><a href="#" class="col-link" data-block="${esc(row.name)}">${esc(row.name)}</a></td>`;
      }
      const value = c.num ? fmt(row[c.key], c.digits) : esc(row[c.key] ?? "");
      const title = c.key === "balance_note" && row.balance_note ? ` title="${esc(row.balance_note)}"` : "";
      return `<td class="${c.cls || ""} ${c.num ? "num" : ""}"${title}>${value}</td>`;
    }).join("")}</tr>`;
  }).join("");

  tbody.querySelectorAll(".col-link").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      openColumn(a.dataset.block);
    });
  });
  // 台账摘要:用全部设备(不受筛选影响)统计焓平衡四态
  const bs = { ok: 0, bad: 0, no_data: 0, skip: 0 };
  state.blocks.forEach((b) => {
    if (b.balance_state && bs[b.balance_state] !== undefined) bs[b.balance_state] += 1;
  });
  let count = `${rows.length} 行`;
  const parts = [];
  if (bs.ok) parts.push(`通过 ${bs.ok}`);
  if (bs.bad) parts.push(`超差 ${bs.bad}`);
  if (bs.no_data) parts.push(`无法校验 ${bs.no_data}`);
  if (bs.skip) parts.push(`不校验 ${bs.skip}`);
  if (parts.length) count += ` · 焓平衡 ${parts.join(" / ")}`;
  document.getElementById("count").textContent = count;
}

function balanceBadge(bstate) {
  const map = {
    ok: ["ok", "通过"],
    bad: ["err", "超差"],
    no_data: ["warn", "无法校验"],
    skip: ["idle", "不校验"],
  };
  const [cls, text] = map[bstate] || ["idle", "—"];
  return `<span class="badge ${cls}">${text}</span>`;
}

/* ------------------------------------------------------------ 报错波及范围 */

/* 流股名 → {severity, blocks[]};没有报错/告警设备时就是空 Map */
function impactMap() {
  const map = new Map();
  const rows = (state.impact && state.impact.streams) || [];
  rows.forEach((s) => map.set(s.stream, s));
  return map;
}

/* "受影响"那一格的徽标:点了直接跳到流程页并聚焦到出问题的设备 */
function impactChip(item) {
  if (!item) return '<span class="muted">—</span>';
  const first = item.blocks[0];
  const names = item.blocks.map((b) =>
    `${b.block}(${b.direction === "in" ? "进" : "出"})`).join("、");
  const isErr = item.severity === "error";
  return `<a class="badge ${isErr ? "err" : "warn"} impact-chip"
     href="#flow/${encodeURIComponent(first.block)}"
     title="${esc(`${isErr ? "数据不可用" : "仅告警,可用但需注明"}:受 ${names} 影响`)}"
     >${isErr ? "不可用" : "告警"}</a>`;
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

/* 设备对比:负荷/功率/级数/顶底温/ΔH 各出一组 A/B/Δ;状态与焓平衡各一列(变了就显示 A → B) */
function renderCompareBlocks(thead, tbody, data) {
  const head = ["设备", "类型", "状态", "焓平衡"];
  BLOCK_COMPARE_FIELDS.forEach((f) => head.push(`${f.label} A`, `${f.label} B`, "Δ"));
  thead.innerHTML = `<tr>${head.map((h, i) =>
    `<th class="${i === 0 ? "cmp-name" : i < 4 ? "" : "num"}">${esc(h)}</th>`).join("")}</tr>`;

  const q = state.filter.trim().toLowerCase();
  const rows = (data.blocks || []).filter((r) => !q || r.name.toLowerCase().includes(q));

  /* 判定不能相减,只能并排:一致就只显示一个徽标,变了才 A → B */
  const pairCell = (a, b, render) => {
    if ((a === null || a === undefined) && (b === null || b === undefined)) {
      return '<td class="muted">—</td>';
    }
    if (a !== b) {
      return `<td>${a === null || a === undefined ? "—" : render(a)}
        <span class="muted">→</span>
        ${b === null || b === undefined ? "—" : render(b)}</td>`;
    }
    return `<td>${render(b)}</td>`;
  };

  tbody.innerHTML = rows.map((r) => {
    const sa = r.status ? r.status.base : null;
    const sb = r.status ? r.status.other : null;
    const ba = r.balance ? r.balance.base : null;
    const bb = r.balance ? r.balance.other : null;

    const cells = [
      `<td class="name cmp-name">${esc(r.name)}</td>`,
      `<td>${esc(r.type || "")}</td>`,
      pairCell(sa, sb, blockBadge),
      pairCell(ba, bb, balanceBadge),
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

/* ------------------------------------------------------------ 流程页(拓扑图)

   零依赖手写 SVG:先按"进料可达距离"分层(这份模型是闭环,没有入度 0 的设备,
   不能用 Kahn),层太多就折行(蛇形,奇偶行反向),再缩放/平移,点设备聚焦上下游 2 层。
   为什么不用图库:项目约定零构建、依赖越少越好;67 个节点自己算完全够用。 */

const FLOW = {
  nodeW: 126, nodeH: 40, gapX: 46, gapY: 16, rowGap: 76,
  chipW: 88, chipH: 22, chipGap: 22,
  color: { ok: "#15803d", bad: "#b91c1c", no_data: "#b45309", skip: "#9ca3af", "": "#9ca3af" },
};

function flowAdj(data) {
  const adj = new Map();
  data.nodes.forEach((n) => adj.set(n.name, new Set()));
  data.links.forEach((l) => {
    if (adj.has(l.from) && adj.has(l.to)) {
      adj.get(l.from).add(l.to);
      adj.get(l.to).add(l.from);
    }
  });
  return adj;
}

function layoutFlow(data, viewAspect = 1.8) {
  const out = new Map(), into = new Map();
  data.nodes.forEach((n) => { out.set(n.name, []); into.set(n.name, []); });
  data.links.forEach((l) => {
    if (out.has(l.from) && into.has(l.to)) {
      out.get(l.from).push(l.to);
      into.get(l.to).push(l.from);
    }
  });

  // ---- 分层:从进料 BFS(最短距离),环上的回边不参与分层
  const depth = new Map();
  const queue = [];
  data.feeds.forEach((f) => {
    if (out.has(f.to) && !depth.has(f.to)) { depth.set(f.to, 0); queue.push(f.to); }
  });
  if (!queue.length) {
    const seed = data.nodes.slice().sort((a, b) => into.get(a.name).length - into.get(b.name).length)[0];
    if (seed) { depth.set(seed.name, 0); queue.push(seed.name); }
  }
  while (queue.length) {
    const name = queue.shift();
    const d = depth.get(name);
    out.get(name).forEach((nxt) => {
      if (!depth.has(nxt)) { depth.set(nxt, d + 1); queue.push(nxt); }
    });
  }
  let maxDepth = 0;
  depth.forEach((d) => { maxDepth = Math.max(maxDepth, d); });
  data.nodes.forEach((n) => { if (!depth.has(n.name)) depth.set(n.name, maxDepth + 1); });
  depth.forEach((d) => { maxDepth = Math.max(maxDepth, d); });

  const layerLists = [];
  for (let d = 0; d <= maxDepth; d++) layerLists.push([]);
  data.nodes.forEach((n) => layerLists[depth.get(n.name)].push(n.name));
  layerLists.forEach((l) => l.sort());

  // ---- 层内排序:邻居平均位置(重心法),两轮就够,再看不出交叉就那样了
  const pos = new Map();
  layerLists.forEach((list) => list.forEach((name, i) => pos.set(name, i)));
  const bary = (name) => {
    const nb = [...into.get(name), ...out.get(name)];
    if (!nb.length) return 0;
    return nb.reduce((sum, x) => sum + (pos.get(x) ?? 0), 0) / nb.length;
  };
  for (let pass = 0; pass < 2; pass++) {
    for (let li = 1; li < layerLists.length; li++) {
      layerLists[li].sort((a, b) => bary(a) - bary(b) || a.localeCompare(b));
      layerLists[li].forEach((name, i) => pos.set(name, i));
    }
    for (let li = layerLists.length - 2; li >= 0; li--) {
      layerLists[li].sort((a, b) => bary(a) - bary(b) || a.localeCompare(b));
      layerLists[li].forEach((name, i) => pos.set(name, i));
    }
  }

  // ---- 折行:整条链太长就按行折(蛇形),行数取"最接近画布长宽比"的那个
  const layerPitch = FLOW.nodeW + FLOW.gapX;
  const rowHeightOf = (count) => count * (FLOW.nodeH + FLOW.gapY) - FLOW.gapY;
  let rows = 1;
  let perRow = layerLists.length;
  let best = Infinity;
  for (let r = 1; r <= 4; r++) {
    const cols = Math.ceil(layerLists.length / r);
    const heights = new Array(r).fill(0);
    layerLists.forEach((list, L) => {
      const row = Math.min(r - 1, Math.floor(L / cols));
      heights[row] = Math.max(heights[row], list.length);
    });
    const h = heights.reduce((sum, c) => sum + rowHeightOf(c), 0) + FLOW.rowGap * (r - 1);
    const w = cols * layerPitch;
    const score = Math.abs(Math.log((w / Math.max(1, h)) / Math.max(0.6, viewAspect)));
    if (score < best) { best = score; rows = r; perRow = cols; }
  }

  const rowHeights = new Array(rows).fill(0);
  layerLists.forEach((list, L) => {
    const row = Math.min(rows - 1, Math.floor(L / perRow));
    rowHeights[row] = Math.max(rowHeights[row], rowHeightOf(list.length));
  });
  const rowTop = [];
  let acc = 0;
  rowHeights.forEach((h) => { rowTop.push(acc); acc += h + FLOW.rowGap; });

  const positions = new Map();
  layerLists.forEach((list, L) => {
    const row = Math.min(rows - 1, Math.floor(L / perRow));
    const colRaw = L % perRow;
    const col = row % 2 === 0 ? colRaw : perRow - 1 - colRaw;
    const h = list.length * (FLOW.nodeH + FLOW.gapY) - FLOW.gapY;
    const y0 = rowTop[row] + (rowHeights[row] - h) / 2;
    list.forEach((name, i) => {
      positions.set(name, {
        x: col * layerPitch,
        y: y0 + i * (FLOW.nodeH + FLOW.gapY),
        w: FLOW.nodeW, h: FLOW.nodeH, row, col,
      });
    });
  });

  // ---- 边界:进料贴最左,产品贴产出设备右侧(行尾则放到右边)
  const feedNodes = data.feeds.map((f) => {
    const p = positions.get(f.to);
    if (!p) return null;
    return {
      ...f, w: FLOW.chipW, h: FLOW.chipH,
      x: -FLOW.chipW - FLOW.chipGap,
      y: p.y + (FLOW.nodeH - FLOW.chipH) / 2,
    };
  }).filter(Boolean);

  const prodNodes = data.products.map((p) => {
    const s = positions.get(p.from);
    if (!s) return null;
    const atRowEnd = s.col === perRow - 1;
    return {
      ...p, w: FLOW.chipW, h: 18,
      x: atRowEnd ? s.x + FLOW.nodeW + 26 : s.x + FLOW.nodeW - 26,
      y: atRowEnd ? s.y + (FLOW.nodeH - 18) / 2 : s.y - 22,
    };
  }).filter(Boolean);

  const xs = [...positions.values()].map((p) => [p.x, p.x + p.w]);
  feedNodes.forEach((f) => xs.push([f.x, f.x + f.w]));
  prodNodes.forEach((p) => xs.push([p.x, p.x + p.w]));
  const minX = Math.min(...xs.map((v) => v[0]));
  const maxX = Math.max(...xs.map((v) => v[1]));

  return {
    positions, feedNodes, prodNodes, rows, perRow,
    adj: flowAdj(data),
    bbox: {
      x: minX - 24,
      y: -34,
      w: maxX - minX + 48,
      h: acc + 68,
    },
  };
}

function flowSvgEl() { return document.getElementById("flowSvg"); }

function applyFlowView() {
  const svg = flowSvgEl();
  const view = state.flow.view;
  if (!svg || !view) return;
  svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
}

function flowCanvasAspect() {
  const wrap = document.getElementById("flowWrap");
  if (!wrap || !wrap.clientHeight) return 1.8;
  return wrap.clientWidth / wrap.clientHeight;
}

/* 把矩形按画布长宽比扩成铺满的视口 —— 否则 preserveAspectRatio 会留白 */
function fitBoxToAspect(box, aspect) {
  let { x, y, w, h } = box;
  if (w / h > aspect) {
    const nh = w / aspect;
    y -= (nh - h) / 2;
    h = nh;
  } else {
    const nw = h * aspect;
    x -= (nw - w) / 2;
    w = nw;
  }
  return { x, y, w, h };
}

function fitFlow() {
  fitFlowTo(null);
}

/* names 为空 = 全图;否则只贴合这些设备(点击聚焦时用)
   centerName:聚焦时以这台设备为视口中心,并保证至少放大 MIN_FOCUS_ZOOM 倍 */
function fitFlowTo(names, centerName = null) {
  const flow = state.flow;
  const layout = flow.layout;
  if (!layout) return;
  let box = layout.bbox;
  if (names && names.size) {
    const pts = [];
    names.forEach((name) => {
      const p = layout.positions.get(name);
      if (p) pts.push(p);
    });
    layout.feedNodes.forEach((f) => { if (names.has(f.to)) pts.push(f); });
    layout.prodNodes.forEach((p) => { if (names.has(p.from)) pts.push(p); });
    if (pts.length) {
      const padX = 56, padY = 42;
      const minX = Math.min(...pts.map((p) => p.x)) - padX;
      const maxX = Math.max(...pts.map((p) => p.x + p.w)) + padX;
      const minY = Math.min(...pts.map((p) => p.y)) - padY;
      const maxY = Math.max(...pts.map((p) => p.y + p.h)) + padY;
      box = { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
    }
  }
  const aspect = flowCanvasAspect();
  let view = fitBoxToAspect(box, aspect);
  if (centerName) {
    const full = fitBoxToAspect(layout.bbox, aspect);
    const maxW = full.w / 1.9;          // 聚焦至少放大 1.9 倍,免得"点了跟没点一样"
    if (view.w > maxW) {
      const p = layout.positions.get(centerName);
      const cx = p ? p.x + p.w / 2 : box.x + box.w / 2;
      const cy = p ? p.y + p.h / 2 : box.y + box.h / 2;
      view = { x: cx - maxW / 2, y: cy - (maxW / aspect) / 2, w: maxW, h: maxW / aspect };
    }
  }
  flow.view = view;
  applyFlowView();
}

function zoomFlow(factor, cx = 0.5, cy = 0.5) {
  const view = state.flow.view;
  if (!view) return;
  const nw = view.w / factor;
  const nh = view.h / factor;
  if (nw < 80 || nw > 40000) return;
  view.x += (view.w - nw) * cx;
  view.y += (view.h - nh) * cy;
  view.w = nw;
  view.h = nh;
  applyFlowView();
}

function drawFlow() {
  const svg = flowSvgEl();
  const flow = state.flow;
  const layout = flow.layout;
  const data = flow.data;
  if (!svg || !layout || !data) return;

  const focus = flow.focus;
  const at = (name) => layout.positions.get(name);
  const edgeCls = (a, b) => {
    if (!focus) return "flow-edge";
    return focus.has(a) && focus.has(b) ? "flow-edge on" : "flow-edge dim";
  };
  const center = flow.center;
  const nodeCls = (name) => {
    let cls = "flow-node";
    if (focus) {
      cls += name === center ? " center" : focus.has(name) ? " on" : " dim";
    }
    return cls;
  };

  const parts = [`
    <defs>
      <marker id="flowArrow" viewBox="0 0 10 10" refX="9" refY="5"
              markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8"
              stroke="#ffffff" stroke-width="1.8" paint-order="stroke"></path>
      </marker>
    </defs>`, `<g class="flow-edges">`];

  // 设备 → 设备
  data.links.forEach((link) => {
    const a = at(link.from), b = at(link.to);
    if (!a || !b) return;
    const sx = a.x + a.w, sy = a.y + a.h / 2;
    const tx = b.x, ty = b.y + b.h / 2;
    let d;
    if (tx > sx + 16) {
      const dx = Math.max(30, (tx - sx) * 0.5);
      d = `M ${sx} ${sy} C ${sx + dx} ${sy}, ${tx - dx} ${ty}, ${tx} ${ty}`;
    } else {
      // 回边 / 回流(往后走或同行折返):绕下方,别和正向线叠在一起
      const dy = Math.max(46, Math.abs(ty - sy) * 0.5 + 22);
      d = `M ${sx} ${sy} C ${sx + 64} ${sy + dy}, ${tx - 64} ${ty + dy}, ${tx} ${ty}`;
    }
    parts.push(`<path class="${edgeCls(link.from, link.to)}" d="${d}" marker-end="url(#flowArrow)"></path>`);
    if (flow.showLabels) {
      const label = link.streams.length > 1
        ? `${link.streams[0]} +${link.streams.length - 1}` : link.streams[0];
      parts.push(`<text class="flow-label" x="${(sx + tx) / 2}" y="${(sy + ty) / 2 - 4}"
        text-anchor="middle">${esc(label)}</text>`);
    }
  });

  // 进料 / 产品
  layout.feedNodes.forEach((f) => {
    const b = at(f.to);
    if (!b) return;
    const sx = f.x + f.w, sy = f.y + f.h / 2;
    const tx = b.x, ty = b.y + b.h / 2;
    const dx = Math.max(24, (tx - sx) * 0.5);
    parts.push(`<path class="${edgeCls("__feed__", f.to)}"
      d="M ${sx} ${sy} C ${sx + dx} ${sy}, ${tx - dx} ${ty}, ${tx} ${ty}"
      marker-end="url(#flowArrow)"></path>`);
  });
  layout.prodNodes.forEach((p) => {
    const s = at(p.from);
    if (!s) return;
    const sx = s.x + s.w, sy = s.y + s.h / 2;
    const tx = p.x, ty = p.y + p.h / 2;
    const dx = Math.max(20, Math.abs(tx - sx) * 0.5);
    parts.push(`<path class="${edgeCls(p.from, "__prod__")}"
      d="M ${sx} ${sy} C ${sx + dx} ${sy}, ${tx - dx} ${ty}, ${tx} ${ty}"
      marker-end="url(#flowArrow)"></path>`);
  });
  parts.push(`</g>`);

  const boundaryParts = [`<g class="flow-boundaries">`];

  layout.feedNodes.forEach((f) => {
    boundaryParts.push(`<g class="flow-boundary" data-boundary="${esc(f.stream)}"
        transform="translate(${f.x} ${f.y})">
        <rect width="${f.w}" height="${f.h}" rx="7"></rect>
        <text x="${f.w / 2}" y="${f.h / 2 + 3}" text-anchor="middle">进料 ${esc(f.stream)}</text>
      </g>`);
  });
  layout.prodNodes.forEach((p) => {
    boundaryParts.push(`<g class="flow-boundary" data-boundary="${esc(p.stream)}"
        transform="translate(${p.x} ${p.y})">
        <rect width="${p.w}" height="${p.h}" rx="6"></rect>
        <text x="${p.w / 2}" y="${p.h / 2 + 3}" text-anchor="middle">产品 ${esc(p.stream)}</text>
      </g>`);
  });
  boundaryParts.push(`</g>`);
  parts.push(`<g class="flow-nodes">`);

  data.nodes.forEach((n) => {
    const p = at(n.name);
    if (!p) return;
    const tip = `${n.name} · ${n.type || "?"}`
      + (n.dh_kw === null || n.dh_kw === undefined ? "" : ` · ΔH ${fmt(n.dh_kw, 1)} kW`);
    parts.push(`<g class="${nodeCls(n.name)} st-${n.state || "unknown"}"
        data-node="${esc(n.name)}" transform="translate(${p.x} ${p.y})">
        <title>${esc(tip)}</title>
        <rect class="node-body" width="${p.w}" height="${p.h}" rx="8"></rect>
        <rect width="4" height="${p.h}" rx="2" class="flow-stripe"></rect>
        <text x="13" y="${p.h / 2 + 4}">${esc(n.name)}</text>
      </g>`);
  });
  parts.push(`</g>`);
  parts.push(...boundaryParts);
  svg.innerHTML = parts.join("");
}

function renderFlowLegend() {
  const el = document.getElementById("flowLegend");
  if (!el) return;
  const items = [
    ["ok", "通过"], ["bad", "超差"], ["no_data", "无法校验"], ["skip", "不校验"],
    ["boundary", "进料 / 产品"],
  ];
  el.innerHTML = items.map(([key, text]) => {
    const color = key === "boundary" ? "#cbd5e1" : FLOW.color[key];
    return `<span><i style="border-color:${color};background:${key === "boundary" ? "#f8fafc" : "#fff"}"></i>${text}</span>`;
  }).join("");
}

function flowNeighbors(name) {
  const data = state.flow.data;
  const ins = [], outs = [];
  data.links.forEach((l) => {
    if (l.to === name) ins.push(l);
    if (l.from === name) outs.push(l);
  });
  return {
    ins, outs,
    feeds: data.feeds.filter((f) => f.to === name),
    products: data.products.filter((p) => p.from === name),
  };
}

function setFlowPanel(name) {
  const panel = document.getElementById("flowPanel");
  if (!panel) return;
  if (!name || !state.flow.data) { panel.classList.add("hidden"); return; }
  const node = state.flow.data.nodes.find((n) => n.name === name);
  if (!node) { panel.classList.add("hidden"); return; }
  const { ins, outs, feeds, products } = flowNeighbors(name);
  const kv = (k, v) => `<div class="kv"><span>${k}</span><span>${v}</span></div>`;
  const list = (items, dir) => items.length
    ? `<ul>${items.map((l) => `<li>${esc(l.stream)}${
        l.side ? ` <span class="muted">(${l.side === "cold" ? "冷侧" : "热侧"})</span>` : ""
      } ${dir} ${esc(l.from === name ? l.to : l.from)}</li>`).join("")}</ul>`
    : `<p class="muted" style="margin:4px 0 8px">无</p>`;

  document.getElementById("flowPanelTitle").textContent = `${name} · ${node.type || "?"}`;
  document.getElementById("flowPanelBody").innerHTML = `
    <div class="flow-panel-grid">
      <div>
        ${kv("焓平衡", balanceBadge(node.state))}
        ${kv("ΔH", node.dh_kw === null || node.dh_kw === undefined ? "—" : `${fmt(node.dh_kw, 2)} kW`)}
        ${kv("参照", node.ref_kw === null || node.ref_kw === undefined ? "—" : `${fmt(node.ref_kw, 2)} kW`)}
        ${kv("热负荷 / 功率", `${node.duty_kw === null || node.duty_kw === undefined ? "—" : `${fmt(node.duty_kw, 2)} kW`} / ${node.power_kw === null || node.power_kw === undefined ? "—" : `${fmt(node.power_kw, 2)} kW`}`)}
        ${kv("理论级数", node.nstage ?? "—")}
      </div>
      <div>
        <div class="flow-panel-head">进料侧(${ins.length + feeds.length})</div>
        ${list(ins, "←")}
        ${feeds.length ? `<ul>${feeds.map((f) => `<li>边界进料 ${esc(f.stream)}</li>`).join("")}</ul>` : ""}
        <div class="flow-panel-head">出料侧(${outs.length + products.length})</div>
        ${list(outs, "→")}
        ${products.length ? `<ul>${products.map((p) => `<li>边界产品 ${esc(p.stream)}</li>`).join("")}</ul>` : ""}
      </div>
    </div>`;
  panel.classList.remove("hidden");
}

function focusFlow(name) {
  const flow = state.flow;
  const adj = flow.layout.adj;
  const seen = new Set([name]);
  let frontier = [name];
  for (let hop = 0; hop < 2; hop++) {
    const next = [];
    frontier.forEach((cur) => {
      (adj.get(cur) || []).forEach((nb) => {
        if (!seen.has(nb)) { seen.add(nb); next.push(nb); }
      });
    });
    frontier = next;
  }
  flow.focus = seen;
  flow.center = name;
  drawFlow();
  setFlowPanel(name);
  requestAnimationFrame(() => requestAnimationFrame(() => fitFlowTo(seen, name)));
  history.replaceState(null, "", `#flow/${encodeURIComponent(name)}`);
}

function clearFlowFocus() {
  state.flow.focus = null;
  state.flow.center = null;
  drawFlow();
  setFlowPanel(null);
  requestAnimationFrame(() => requestAnimationFrame(fitFlow));
  if (location.hash.startsWith("#flow/")) history.replaceState(null, "", "#flow");
}

async function renderFlow() {
  const svg = flowSvgEl();
  const empty = document.getElementById("flowEmpty");
  const c = state.current;
  const flow = state.flow;

  if (!c) {
    if (svg) svg.innerHTML = "";
    empty.textContent = "还没有工况。先读取一个工况,流程图会按它的设备连接关系自动生成。";
    empty.classList.remove("hidden");
    setFlowPanel(null);
    renderFlowLegend();
    return;
  }
  empty.classList.add("hidden");
  if (flow.caseId !== c.id || !flow.data) {
    try {
      flow.data = await api(`/api/cases/${c.id}/flowsheet`);
      flow.caseId = c.id;
      flow.focus = null;
      flow.center = null;
      flow.view = null;
      const wrap = document.getElementById("flowWrap");
      const aspect = wrap && wrap.clientHeight
        ? wrap.clientWidth / wrap.clientHeight : 1.8;
      flow.layout = layoutFlow(flow.data, aspect);
      setFlowPanel(null);
    } catch (err) {
      if (svg) svg.innerHTML = "";
      empty.textContent = `读不到流程图数据:${err.message}`;
      empty.classList.remove("hidden");
      return;
    }
  }
  drawFlow();
  fitFlow();
  requestAnimationFrame(fitFlow);
  renderFlowLegend();

  // #flow/设备名:直接聚焦到那台设备(刷新/分享都能还原)
  const hashParts = location.hash.replace("#", "").split("/");
  if (hashParts[1]) {
    const name = decodeURIComponent(hashParts[1]);
    if (flow.data.nodes.some((n) => n.name === name)) focusFlow(name);
  }
}

/* ------------------------------------------------------------ 流程图导出 */

/* 导出的 SVG 是独立文件,读不到 style.css —— 所以把流程图用到的规则原样内联一份。
   改 style.css 里 .flow-* 的颜色时记得同步这里(否则"屏幕上"和"导出的图"会不一样)。 */
const FLOW_EXPORT_CSS = `
  .flow-edge { fill: none; stroke: #94a3b8; stroke-width: 1.2; }
  .flow-edge.dim { opacity: .1; }
  .flow-edge.on { stroke: #2563eb; stroke-width: 2; }
  .flow-label { font-size: 9.5px; fill: #6b7280; paint-order: stroke; stroke: #fff; stroke-width: 2.5px; }
  .flow-node rect { fill: #fff; stroke: #cbd5e1; stroke-width: 1.2; }
  .flow-node .node-body { fill: #fff; fill-opacity: .82; }
  .flow-node .flow-stripe { fill: #9ca3af; stroke: none; }
  .flow-node text { font-family: "Microsoft YaHei", "Segoe UI", system-ui, sans-serif;
    font-size: 14.5px; font-weight: 600; fill: #1f2328; }
  .flow-node.st-ok rect { stroke: #86efac; }
  .flow-node.st-ok .flow-stripe { fill: #15803d; }
  .flow-node.st-bad rect { stroke: #fca5a5; }
  .flow-node.st-bad .flow-stripe { fill: #b91c1c; }
  .flow-node.st-no_data rect { stroke: #fcd34d; }
  .flow-node.st-no_data .flow-stripe { fill: #b45309; }
  .flow-node.st-skip rect { stroke: #d1d5db; }
  .flow-node.st-skip .flow-stripe { fill: #cbd5e1; }
  .flow-node.dim { opacity: .18; }
  .flow-node.on .node-body { stroke: #93c5fd; stroke-width: 1.6; fill-opacity: .92; }
  .flow-node.center .node-body { fill: #eff6ff; fill-opacity: 1; stroke: #2563eb; stroke-width: 2.6; }
  .flow-node.center .flow-stripe { fill: #2563eb; }
  .flow-node.center text { fill: #2563eb; font-weight: 700; }
  .flow-boundary rect { fill: #f8fafc; stroke: #cbd5e1; stroke-width: 1.2; stroke-dasharray: 3 3; }
  .flow-boundary text { font-size: 10.5px; fill: #6b7280; }
`;

const SVG_NS = "http://www.w3.org/2000/svg";

/* 把当前视图(缩放/聚焦后的样子)做成一个能单独打开的 SVG 文本 */
function flowExportSvg() {
  const svg = flowSvgEl();
  const view = state.flow.view;
  if (!svg || !view || !state.flow.layout) return null;

  const clone = svg.cloneNode(true);
  clone.setAttribute("xmlns", SVG_NS);
  clone.setAttribute("width", Math.round(view.w));
  clone.setAttribute("height", Math.round(view.h));

  const style = document.createElementNS(SVG_NS, "style");
  style.textContent = FLOW_EXPORT_CSS;
  // 白底:SVG 默认透明,贴进 Word/PPT 会变成看不清的"白线浮在白纸"
  const bg = document.createElementNS(SVG_NS, "rect");
  bg.setAttribute("x", view.x);
  bg.setAttribute("y", view.y);
  bg.setAttribute("width", view.w);
  bg.setAttribute("height", view.h);
  bg.setAttribute("fill", "#ffffff");
  clone.insertBefore(bg, clone.firstChild);
  clone.insertBefore(style, clone.firstChild);

  return {
    text: new XMLSerializer().serializeToString(clone),
    width: Math.round(view.w),
    height: Math.round(view.h),
  };
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

function flowFileName(ext) {
  const c = state.current;
  const day = new Date().toISOString().slice(0, 10);
  return `flow-case${c ? c.id : "x"}-${day}.${ext}`;
}

function flowExportMissing() {
  alert("流程页还没有图。先读取一个工况,等流程图出来再导出。");
}

function exportFlowSvg() {
  const out = flowExportSvg();
  if (!out) { flowExportMissing(); return; }
  downloadBlob(new Blob([out.text], { type: "image/svg+xml;charset=utf-8" }),
    flowFileName("svg"));
}

/* PNG 走"SVG → Image → canvas → toBlob",不引第三方库;
   2 倍像素是为了贴进报告/PPT 时不发虚 */
function exportFlowPng() {
  const out = flowExportSvg();
  if (!out) { flowExportMissing(); return; }
  const blob = new Blob([out.text], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const img = new Image();
  img.onload = () => {
    const scale = 2;
    const canvas = document.createElement("canvas");
    canvas.width = out.width * scale;
    canvas.height = out.height * scale;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    URL.revokeObjectURL(url);
    canvas.toBlob((png) => {
      if (png) downloadBlob(png, flowFileName("png"));
      else alert("导出 PNG 失败:浏览器没能把这张图转成 PNG。可以改用「导出 SVG」。");
    }, "image/png");
  };
  img.onerror = () => {
    URL.revokeObjectURL(url);
    alert("导出 PNG 失败:浏览器渲染这张图时出错。可以改用「导出 SVG」。");
  };
  img.src = url;
}

/* ------------------------------------------------------------ 初始化 */

const EMPTY_HINT = [
  "还没有工况。点左上角「读取当前工况」,从已经打开的 Aspen 里读一次。",
  "",
  "没有 Aspen 也能先看界面:点下面的「载入示例工况」。",
].join("\n");

function showEmpty(text) {
  const msg = text || EMPTY_HINT;
  state.emptyText = msg;
  state.emptyIsError = Boolean(text);
  const el = document.getElementById("empty");
  el.textContent = msg;
  el.classList.remove("hidden");
  // 首页是独立页面,数据页隐藏时用户看不到 #empty —— 同步刷一次首页卡片
  if (state.page === "home") renderHome();
}

function hideEmpty() {
  state.emptyText = "";
  state.emptyIsError = false;
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
  const [detail, streams, blocks, impact] = await Promise.all([
    api(`/api/cases/${id}`),
    api(`/api/cases/${id}/streams`),
    api(`/api/cases/${id}/blocks`),
    // 波及范围是锦上添花:接口不通(老版本服务)不该拖垮整页
    api(`/api/cases/${id}/impact`).catch(() => null),
  ]);
  state.current = detail;
  state.streams = streams;
  state.blocks = blocks;
  state.impact = impact;
  const select = document.getElementById("caseSelect");
  if (select.value !== String(id)) select.value = String(id);
  hideEmpty();
  setCaseButtonsEnabled(true);
  setCaseInfo();
  renderBanner();
  if (state.page === "flow") {
    state.flow.caseId = null;        // 逼着下次进流程页按新工况重新拉图
    renderFlow();
  } else if (state.page === "pipes") {
    renderPipes();                   // 管道页按新工况重算(pipe.js)
  } else if (state.page === "home") {
    renderHome();
  } else if (state.tab !== "compare") {
    renderTable();
  }
  resetTableScroll();   // 换工况也从第一行看起
}

/* ------------------------------------------------------------ 路由与首页 */

const PAGES = ["home", "streams", "blocks", "compare", "flow", "pipes"];
const PAGE_TITLE = { streams: "流股", blocks: "设备", compare: "对比", flow: "流程" };

/* 地址栏是唯一真相:#/streams、#/blocks/塔名(直接打开某座塔的剖面) */
function readHashPage() {
  const hash = location.hash.replace("#", "").split("/")[0];
  return PAGES.includes(hash) ? hash : "home";
}

function setPage(page, { push = true } = {}) {
  if (!PAGES.includes(page)) page = "home";
  state.page = page;
  document.querySelectorAll(".side-nav .tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === page));

  const isHome = page === "home";
  const isFlow = page === "flow";
  const isPipes = page === "pipes";       // 管道页自带一套参数面板与渲染(见 pipe.js)
  document.getElementById("pageHome").classList.toggle("hidden", !isHome);
  document.getElementById("pageFlow").classList.toggle("hidden", !isFlow);
  document.getElementById("pagePipes").classList.toggle("hidden", !isPipes);
  document.getElementById("pageData").classList.toggle("hidden", isHome || isFlow || isPipes);
  if (push && location.hash !== `#${page}`) location.hash = page;

  if (isHome) {
    renderBanner();
    renderHome();
    return;
  }
  if (isFlow) {
    renderFlow();
    return;
  }
  if (isPipes) {
    renderPipes();     // pipe.js:进页才拉数据,参数改了才重算
    return;
  }
  document.getElementById("pageTitle").textContent = PAGE_TITLE[page] || "";
  setTab(page);          // 复用原有逻辑:渲染表格 / 对比、清筛选、隐藏侧面板
}

function setCaseInfo() {
  const el = document.getElementById("caseInfo");
  const c = state.current;
  if (!el) return;
  if (!c) { el.textContent = ""; return; }
  const s = SEVERITY[c.severity] || SEVERITY.warning;
  const conn = c.connection_count;
  // 侧栏只有 220px,一行塞不下 —— 状态一行,数字分两行,免得"连接"被拆两半
  el.innerHTML = `<span class="badge ${s.cls}">${s.text}</span>`
    + `<div>${c.stream_count} 流股 · ${c.block_count} 设备</div>`
    + (conn ? `<div>${conn} 连接</div>` : "");
}

function balanceCounts() {
  const counts = { ok: 0, bad: 0, no_data: 0, skip: 0 };
  state.blocks.forEach((b) => {
    if (b.balance_state && counts[b.balance_state] !== undefined) counts[b.balance_state] += 1;
  });
  return counts;
}

/* 首页仪表盘:工况信息 + 规模 + 判定 + 焓平衡 + 异常模块 */
function renderHome() {
  const cards = document.getElementById("homeCards");
  const problemsCard = document.getElementById("homeProblemsCard");
  const problems = document.getElementById("homeProblems");
  const c = state.current;

  if (!c) {
    const hint = state.emptyText || EMPTY_HINT;
    cards.innerHTML = `<section class="card wide">
        <h3>${state.emptyIsError ? "读不到工况" : "还没有工况"}</h3>
        <p class="muted" style="margin:0;line-height:1.8;white-space:pre-wrap">${esc(hint)}</p>
        ${state.emptyIsError ? "" : `
        <div class="empty-actions">
          <button id="loadSampleBtn" class="btn primary" type="button">载入示例工况(无需 Aspen)</button>
          <span class="muted">121 流股 / 77 模块的异常工况样本;会先把你现在的库备份成 <code>cases.db.bak</code></span>
        </div>`}
      </section>`;
    const sampleBtn = document.getElementById("loadSampleBtn");
    if (sampleBtn) sampleBtn.addEventListener("click", installSampleData);
    problemsCard.classList.add("hidden");
    return;
  }

  const bs = balanceCounts();
  const checked = bs.ok + bs.bad;
  const passRate = checked ? Math.round((bs.ok / checked) * 100) : null;
  const towers = state.blocks.filter((b) => b.nstage !== null && b.nstage !== undefined).length;
  const s = SEVERITY[c.severity] || SEVERITY.warning;

  const kv = (k, v) => `<div class="kv"><span>${k}</span><span>${v}</span></div>`;
  const metric = (label, value, cls = "") =>
    `<div class="metric"><b class="${cls}">${value}</b><span>${label}</span></div>`;

  cards.innerHTML = `
    <section class="card">
      <h3>当前工况</h3>
      ${kv("模型", `<span title="${esc(c.model_path || "")}">${esc(c.model_name || "-")}</span>`)}
      ${kv("读取时间", esc(c.read_at || "-"))}
      ${kv("运行 ID", esc(c.run_id || "-"))}
      ${kv("读取耗时", c.elapsed_sec ? `${c.elapsed_sec} s` : "-")}
    </section>

    <section class="card">
      <h3>规模</h3>
      <div class="metric-row">
        ${metric("流股", c.stream_count ?? "—")}
        ${metric("设备", c.block_count ?? "—")}
        ${metric("连接", c.connection_count ?? "—")}
        ${metric("塔", towers || "—")}
      </div>
      <div class="muted" style="margin-top:10px">
        <a class="inline-link" href="#flow">查看流程图(点击设备聚焦上下游 2 层) →</a>
      </div>
    </section>

    <section class="card">
      <h3>收敛判定</h3>
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
        <span class="badge ${s.cls}">${s.text}</span>
        <span class="muted">${c.converged ? "可用于统计对比" : "不建议做定量结论"}</span>
      </div>
      ${kv("判定理由", (c.reasons || []).length ? `${(c.reasons || []).length} 条` : "无")}
      ${(() => {
        const codes = Object.entries(c.status || {})
          .filter(([, v]) => v !== null && v !== undefined)
          .map(([k, v]) => `${k}=${v}`).join(" ") || "—";
        return `<div class="kv"><span>状态码</span><span title="${esc(codes)}">${esc(codes)}</span></div>`;
      })()}
      ${(() => {
        const sm = state.impact && state.impact.summary;
        if (!sm || !sm.total) return "";
        const parts = [];
        if (sm.error) parts.push(`<b class="err-text">${sm.error}</b> 条不可用`);
        if (sm.warning) parts.push(`<b class="warn-text">${sm.warning}</b> 条仅告警`);
        return `<div class="kv"><span>受影响流股</span><span>${parts.join(" · ")}
          <a href="#streams" class="inline-link">看流股表</a></span></div>`;
      })()}
    </section>

    <section class="card">
      <h3>焓平衡自检</h3>
      <div class="metric-row">
        ${metric("通过", bs.ok, "ok-text")}
        ${metric("超差", bs.bad, bs.bad ? "err-text" : "")}
        ${metric("无法校验", bs.no_data, "warn-text")}
        ${metric("不校验", bs.skip)}
      </div>
      <div class="muted" style="margin-top:10px">
        ${passRate === null ? "还没有可校验的设备" : `可校验 ${checked} 台,通过率 ${passRate}%`}
        ${bs.bad ? `;<a href="#blocks" class="inline-link">去看哪几台超差</a>` : ""}
      </div>
    </section>`;

  const bad = state.blocks.filter((b) => b.status === 1 || b.status === 2);
  if (bad.length) {
    problems.innerHTML = `<ul class="problem-list">${bad.map((b) =>
      `<li><b>${esc(b.name)}</b> <span class="muted">${esc(b.type || "")}</span>${
        b.message ? ` — ${esc(b.message)}` : ""}</li>`).join("")}</ul>`;
    problemsCard.classList.remove("hidden");
  } else {
    problemsCard.classList.add("hidden");
  }
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
  if (!confirm("确定退出 Casebook 吗?\n后台服务会一起停止。")) return;
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
  initPipePanel();      // 管道页的参数面板与事件(pipe.js)

  document.getElementById("caseSelect").addEventListener("change", (e) => {
    loadCase(Number(e.target.value));
  });

  document.querySelectorAll(".side-nav .tab").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      setPage(btn.dataset.tab);
    });
  });

  // 浏览器前进/后退、手改地址栏 → 跟着切页
  window.addEventListener("hashchange", () => {
    const page = readHashPage();
    if (page !== state.page) setPage(page, { push: false });
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
  document.getElementById("compareHint").addEventListener("click", () => setPage("compare"));
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

  // ---- 流程页:工具栏 + 缩放平移 + 聚焦 ----
  document.getElementById("flowFit").addEventListener("click", fitFlow);
  document.getElementById("flowZoomIn").addEventListener("click", () => zoomFlow(1.25));
  document.getElementById("flowZoomOut").addEventListener("click", () => zoomFlow(1 / 1.25));
  const labelsBtn = document.getElementById("flowLabels");
  labelsBtn.addEventListener("click", () => {
    state.flow.showLabels = !state.flow.showLabels;
    labelsBtn.classList.toggle("primary", state.flow.showLabels);
    if (state.flow.layout) drawFlow();
  });
  document.getElementById("flowPanelClose").addEventListener("click", clearFlowFocus);
  document.getElementById("flowExportSvg").addEventListener("click", exportFlowSvg);
  document.getElementById("flowExportPng").addEventListener("click", exportFlowPng);
  window.addEventListener("resize", () => {
    if (state.page === "flow") fitFlow();
  });

  const flowSvg = document.getElementById("flowSvg");
  if (flowSvg) {
    /* 交互全部走 pointer 事件,不用 click:
       拖动结束后浏览器仍会补一个 click,若把"空白处 click"当取消聚焦,
       用户拖到远处看邻居、一松手就会被拉回全图。改成 pointerup 里按位移判定。 */
    let drag = null;
    const DRAG_THRESHOLD = 4;      // 像素:超过就算拖动,不算点击

    flowSvg.addEventListener("wheel", (e) => {
      if (!state.flow.view) return;
      e.preventDefault();
      const rect = flowSvg.getBoundingClientRect();
      zoomFlow(e.deltaY < 0 ? 1.12 : 1 / 1.12,
        (e.clientX - rect.left) / rect.width, (e.clientY - rect.top) / rect.height);
    }, { passive: false });

    flowSvg.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      const node = e.target.closest("[data-node]");
      drag = {
        x: e.clientX, y: e.clientY,
        view: state.flow.view ? { ...state.flow.view } : null,
        moved: false,
        node: node ? node.dataset.node : null,
        onBoundary: !!e.target.closest("[data-boundary]"),
        fromBackground: e.target === flowSvg || !!e.target.closest("g.flow-edges"),
      };
      if (drag.view) flowSvg.classList.add("dragging");
    });

    window.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x;
      const dy = e.clientY - drag.y;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) > DRAG_THRESHOLD) drag.moved = true;
      if (!drag.view) return;
      const rect = flowSvg.getBoundingClientRect();
      state.flow.view.x = drag.view.x - dx * drag.view.w / rect.width;
      state.flow.view.y = drag.view.y - dy * drag.view.h / rect.height;
      applyFlowView();
    });

    const endDrag = (e) => {
      const d = drag;
      drag = null;
      flowSvg.classList.remove("dragging");
      if (!d) return;
      if (d.moved) return;                       // 拖动:只平移,不改聚焦
      if (d.node) { focusFlow(d.node); return; }
      // 点空白才取消聚焦;点到边界芯片、连线时不误取消
      if (d.fromBackground && state.flow.focus) clearFlowFocus();
    };
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", (e) => {
      drag = null;
      flowSvg.classList.remove("dragging");
    });
    flowSvg.addEventListener("dblclick", fitFlow);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") clearFlowFocus();
    });
  }
  document.getElementById("deleteBtn").addEventListener("click", deleteCurrentCase);
}

/* 首页空态的「载入示例工况」:把内置示例库装进当前库(等价于 --sample,先备份原库) */
async function installSampleData() {
  const ok = confirm(
    "载入内置示例工况?\n\n"
    + "会先用示例数据覆盖当前工况库,原库自动备份成 cases.db.bak。\n"
    + "如果你已经读过自己的模型,点「取消」即可。"
  );
  if (!ok) return;

  const btn = document.getElementById("loadSampleBtn");
  const label = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "正在载入…"; }
  try {
    const res = await apiPost("/api/sample/install");
    hideEmpty();
    await refreshCases(res.case_id ?? null);
    if (state.cases.length) {
      await loadCase(res.case_id ?? state.cases[0].id);
    } else {
      showEmpty("示例库装好了,但没读到工况。刷新一下页面试试。");
    }
  } catch (err) {
    alert(`载入示例失败:${err.message}`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label || "载入示例工况(无需 Aspen)"; }
  }
}

async function init() {
  bindEvents();

  try {
    await refreshCases(null);
  } catch (err) {
    showEmpty(`读不到工况列表:${err.message}\n\n后台服务可能没起来,重新双击桌面快捷方式。`);
    return;
  }

  const page = readHashPage();

  if (!state.cases.length) {
    setPage(page, { push: false });
    await resumeReadIfRunning();
    return;
  }

  // ?case=19 可以指定打开哪个工况(方便分享链接/回看某个旧工况)
  const wantCase = Number(new URLSearchParams(location.search).get("case"));
  const initialCase = state.cases.some((c) => c.id === wantCase) ? wantCase : state.cases[0].id;
  await loadCase(initialCase);
  setPage(page, { push: false });
  if (page === "compare") await runCompare();

  const hashParts = location.hash.replace("#", "").split("/");
  if (page === "blocks" && hashParts[1]) openColumn(hashParts[1]);

  await resumeReadIfRunning();
}

init();
