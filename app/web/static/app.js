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
  family: null,            // 设备页当前家族(column/heat/…),null = 还没挑
  families: [],            // 家族表(/api/block-families)
  pumpSpec: null,          // 泵口径常量(/api/pump-spec):g / 电机效率 / 档位表 / 公式说明
  pump: { margin: 1 },  // 泵页顶部参数条的余量:默认「原样」,和下拉框的初始值保持一致
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

/* 设备对比字段。⚠️ 必须与 server.py 的 block_fields 一致:
   接口加了而这里不加,界面就看不到;接口不加而这里加,值永远是空。 */
const BLOCK_COMPARE_FIELDS = [
  { key: "duty_kw", label: "热负荷 kW", digits: 2 },
  { key: "power_kw", label: "功率 kW", digits: 2 },
  { key: "nstage", label: "级数", digits: 0 },
  { key: "top_temp_c", label: "顶温 ℃", digits: 1 },
  { key: "bottom_temp_c", label: "底温 ℃", digits: 1 },
  { key: "dh_kw", label: "ΔH kW", digits: 2 },
  { key: "head_m", label: "扬程 m", digits: 2 },
  { key: "flow_m3h", label: "流量 m³/h", digits: 2 },
];

const COLUMN_TYPE = /RadFrac|Absorber/;

/* 设备页按家族出列:同一张表,列随家族变。
   目的很直接:泵页不该出现"级数""热负荷",塔页也不该出现"功率"。
   键必须与 app/design/block_family.py 的 FAMILIES 一致。 */
const BLOCK_COLS = {
  name:    { key: "name", label: "设备", cls: "name" },
  type:    { key: "type", label: "类型" },
  status:  { key: "status", label: "状态", status: true },
  duty:    { key: "duty_kw", label: "热负荷 kW", num: true, digits: 2 },
  power:   { key: "power_kw", label: "功率 kW", num: true, digits: 2 },
  // 焓平衡自检:ΔH = Σ出 − Σ入(流股能量流),参照 = 热负荷 / 轴功率 / 0(绝热)
  dh:      { key: "dh_kw", label: "ΔH kW", num: true, digits: 2 },
  ref:     { key: "ref_kw", label: "参照 kW", num: true, digits: 2 },
  balance: { key: "balance_state", label: "焓平衡", balance: true },
  area:    { key: "area_m2", label: "面积 m²", num: true, digits: 2 },
  nstage:  { key: "nstage", label: "级数", num: true, digits: 0 },
  top:     { key: "top_temp_c", label: "顶温 ℃", num: true, digits: 1 },
  bottom:  { key: "bottom_temp_c", label: "底温 ℃", num: true, digits: 1 },
  note:    { key: "balance_note", label: "平衡说明", cls: "msg" },
  message: { key: "message", label: "说明", cls: "msg" },
  // 设备进出温度:取自连接流股的实测温度(不读块上的 B_TEMP —— 换热器没有、反应器是标志位)
  inT:     { key: "inlet_temp_c", label: "进口 ℃", num: true, digits: 1 },
  outT:    { key: "outlet_temp_c", label: "出口 ℃", num: true, digits: 1 },
  coldIn:  { key: "cold_in_temp_c", label: "冷侧进 ℃", num: true, digits: 1 },
  coldOut: { key: "cold_out_temp_c", label: "冷侧出 ℃", num: true, digits: 1 },
  vapOut:  { key: "outlet_vap_frac", label: "出口汽化率", num: true, digits: 3 },
  // 塔的两块负荷:冷凝为负值(放出热量),原样显示
  cond:    { key: "cond_duty_kw", label: "冷凝器 kW", num: true, digits: 2 },
  reb:     { key: "reb_duty_kw", label: "再沸器 kW", num: true, digits: 2 },
  // 泵专用:tips 的内容来自 /api/pump-spec,不在这里写死
  flow:    { key: "flow_m3h", label: "流量 m³/h", num: true, digits: 2, scaled: true, unit: "m³/h" },
  head:    { key: "head_m", label: "扬程 m", num: true, digits: 2, scaled: true, unit: "m" },
  eff:     { key: "pump_eff", label: "效率 %", num: true, digits: 1, percent: true },
  motor:   { key: "motor_kw", label: "建议电机 kW", num: true, digits: 1, tip: "motor" },
  npsh:    { key: "npsh", label: "NPSHₐ m", num: true, digits: 2, npsh: true, tip: "npsh" },
  // p_v:读取工况时自动取 Aspen 进口流股的泡点压力;空着才需手填(见 docs/aspen-field-map.md §13.2)
  pv:      { key: "pv_bar", label: "p_v bar", num: true, pvInput: true },
};

/* 塔页保持原样(用户对塔页满意):只去掉"类型"和"功率"两个恒定/无关列 */
const FAMILY_COLS = {
  // 塔:只有级数与顶/底温是逐塔独有的;热负荷(塔是冷凝+再沸两块)、面积、ΔH 在塔上是恒定空列
  column: ["name", "status", "nstage", "top", "bottom", "cond", "reb", "message"],
  // 换热:面积=HX_AREAC/AREAC2/AREAP;冷/热侧进出口温度来自连接流股
  heat:   ["name", "type", "status", "duty", "area", "inT", "outT", "coldIn", "coldOut",
           "dh", "ref", "balance", "note", "message"],
  // 分离:出口温度 + 出口汽化率(Flash/Decanter 最该看的量)
  flash:  ["name", "type", "status", "duty", "outT", "vapOut", "dh", "ref", "balance", "message"],
  react:  ["name", "type", "status", "duty", "outT", "message"],
  // 泵页:流量/扬程/效率/轴功率/建议电机/p_v/NPSHₐ;级数和热负荷在这里没有意义
  pump:   ["name", "status", "flow", "head", "eff", "power", "motor", "pv", "npsh", "message"],
  move:   ["name", "type", "status", "power", "inT", "outT", "dh", "ref", "balance", "message"],
  mix:    ["name", "type", "status", "outT", "message"],
  other:  ["name", "type", "status", "message"],
};

/* /api/block-families 不通时的兜底:只有显示名。
   分页签靠后端下发的 row.family,所以兜底不影响归类,只影响页签文字与顺序。 */
const FAMILY_FALLBACK = [
  { key: "column", label: "塔" }, { key: "heat", label: "换热" },
  { key: "flash", label: "分离" }, { key: "react", label: "反应" },
  { key: "pump", label: "泵" }, { key: "move", label: "流体输送" },
  { key: "mix", label: "混合分流" }, { key: "other", label: "其他" },
];

/* ------------------------------------------------------------ 工具 */

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `请求失败:${res.status}`);
  }
  return res.json();
}

/* ------------------------------------------------------------ 外观 */

/* 三态:auto(跟随系统)/ light / dark。
   CSS 只认 html[data-theme] 的明确值(auto 由这里解析),所以样式表里
   只需要一套 dark,不用把 prefers-color-scheme 抄两遍。 */
const THEME_KEY = "casebook-theme";
const THEME_MODES = ["auto", "light", "dark"];
const THEME_TEXT = { auto: "跟随系统", light: "浅色", dark: "深色" };
const THEME_ICON = { auto: "#i-auto", light: "#i-sun", dark: "#i-moon" };
const themeMedia = window.matchMedia("(prefers-color-scheme: dark)");
let themeMode = "auto";

function applyTheme() {
  const resolved = themeMode === "auto"
    ? (themeMedia.matches ? "dark" : "light")
    : themeMode;
  document.documentElement.dataset.theme = resolved;
  const label = document.getElementById("themeLabel");
  if (label) label.textContent = THEME_TEXT[themeMode];
  const use = document.querySelector("#themeIcon use");
  if (use) use.setAttribute("href", THEME_ICON[themeMode]);
  const btn = document.getElementById("themeBtn");
  if (btn) btn.title = `外观:${THEME_TEXT[themeMode]}(点一下切换)`;
}

function cycleTheme() {
  const i = THEME_MODES.indexOf(themeMode);
  themeMode = THEME_MODES[(i + 1) % THEME_MODES.length];
  try { localStorage.setItem(THEME_KEY, themeMode); } catch (e) { /* 隐私模式:不记也照样能切 */ }
  applyTheme();
}

function initTheme() {
  const saved = (() => { try { return localStorage.getItem(THEME_KEY); } catch (e) { return null; } })();
  themeMode = THEME_MODES.includes(saved) ? saved : "auto";
  applyTheme();
  const btn = document.getElementById("themeBtn");
  if (btn) btn.addEventListener("click", cycleTheme);
  // 跟随系统时,系统切换了要跟着变
  themeMedia.addEventListener("change", () => { if (themeMode === "auto") applyTheme(); });
}

/* 读设计 token 的实际值:图表颜色只在 style.css 里定义一次。
   以前 JS 里另抄一份调色板,改 CSS 忘了改这里两边就不一致。 */
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/* 数据页顶部的请求提示条(#dataNotice):
   加载中说明"正在读哪一份",失败说明"屏幕上这批数字还是旧的"。
   换工况时下拉已经指到新工况,而表格还是上一份 —— 没有这一条就容易被误读。 */
function showNotice(text, { err = false } = {}) {
  const el = document.getElementById("dataNotice");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("err", Boolean(err));
  el.classList.remove("hidden");
}

function clearNotice() {
  const el = document.getElementById("dataNotice");
  if (!el) return;
  el.textContent = "";
  el.classList.add("hidden");
  el.classList.remove("err");
}

/* 忙态:给要等的那块挂 aria-busy(样式里是淡淡降透明),读屏也能念出来。
   必须 try/finally —— 少这一次复位,一次失败就会永远停在"正在加载"。 */
async function withBusy(ids, fn) {
  const els = (Array.isArray(ids) ? ids : [ids])
    .map((id) => document.getElementById(id)).filter(Boolean);
  els.forEach((el) => el.setAttribute("aria-busy", "true"));
  try {
    return await fn();
  } finally {
    els.forEach((el) => el.removeAttribute("aria-busy"));
  }
}

/* 写请求统一带这个头:跨站页面加自定义头会触发预检,而服务端不回应预检 → 浏览器直接不发。
   服务端 do_POST / do_DELETE 会校验 X-Casebook-Client(见 server.py::_guard)。 */
function writeInit(extra = {}) {
  return { ...extra, headers: { "X-Casebook-Client": "1", ...(extra.headers || {}) } };
}

/* DELETE 单独一个:api() 只做 GET,别为了它把方法参数塞进去到处传 */
async function apiDelete(path) {
  const res = await fetch(path, writeInit({ method: "DELETE" }));
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `请求失败:${res.status}`);
  }
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(path, writeInit({
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  }));
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

/* ------------------------------------------------------------ 设备家族 */

function blockFamilyOf(row) {
  return row.family || "other";
}

function familyCounts() {
  const seen = new Map();
  state.blocks.forEach((b) => {
    const key = blockFamilyOf(b);
    seen.set(key, (seen.get(key) || 0) + 1);
  });
  return seen;
}

/* 默认页签:有塔落塔,否则第一个非空。
   换工况后 state.family 被清空,靠这里重新挑 —— 曾经因为只在 setTab 里重置,
   换工况会停在旧家族显示 0 行。 */
function ensureFamily() {
  const seen = familyCounts();
  if (state.family && seen.get(state.family)) return;
  const first = state.families.find((f) => seen.get(f.key));
  state.family = seen.get("column") ? "column" : (first ? first.key : "column");
}

/* 换设备类型 = 换了一批设备,旧的详情(塔剖面/泵参数/组成)不再对应任何一行。
   不收起来的话,切到换热页还挂着塔的剖面,看着像"这些数属于换热器"。 */
function setFamily(key) {
  state.family = key;
  closeDataDrawers();
  state.column = null;
  renderTable();
  resetTableScroll();
}

function renderFamilyBar() {
  const box = document.getElementById("blockFamilies");
  if (!box) return;
  const seen = familyCounts();
  const items = state.families.filter((f) => seen.get(f.key));
  box.innerHTML = items.map((f) =>
    `<button type="button" role="tab" data-family="${esc(f.key)}"
       aria-selected="${f.key === state.family}"
       class="${f.key === state.family ? "active" : ""}">${esc(f.label)}<b>${seen.get(f.key)}</b></button>`
  ).join("");
  box.querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => setFamily(b.dataset.family)));
}

function blockColumns() {
  const keys = FAMILY_COLS[state.family] || FAMILY_COLS.other;
  return keys.map((k) => BLOCK_COLS[k]).filter(Boolean);
}

async function loadFamilies() {
  try {
    const list = await api("/api/block-families");
    if (Array.isArray(list) && list.length) state.families = list;
  } catch (err) {
    // 老版本服务没有这个接口:页签照样能用(归类是后端随行下发的 family)
    state.families = FAMILY_FALLBACK;
  }
  if (!state.families.length) state.families = FAMILY_FALLBACK;
}

/* ------------------------------------------------------------ 泵口径 */

/* 重力加速度、电机效率、档位表、公式说明都从后端拿,免得 JS 里再抄一遍抄错。
   取不到时 NPSHₐ 一律显示「—」, 不用猜的 g。 */
async function loadPumpSpec() {
  try {
    state.pumpSpec = await api("/api/pump-spec");
  } catch (err) {
    state.pumpSpec = null;
  }
}

/* NPSHₐ = (p_进口 − p_v) × 10⁵ ÷ (ρ·g)
   p_v 优先用 Aspen 进口流股的泡点压力(读取工况时已填进 pv_bar),手填值覆盖它;
   缺 p_v、进口两相、缺密度 → 返回 null(显示「—」),绝不拿 0 顶替。
   泡点进料时 p_v ≈ p_in,结果会有 -0.0 这种浮点噪声,归零免得显示成「-0.00」。 */
function pumpNpsh(row) {
  const spec = state.pumpSpec;
  const pv = row.pv_bar;
  if (!spec || pv === null || pv === undefined || Number.isNaN(Number(pv))) return null;
  const vap = row.pump_vap_frac;
  if (vap !== null && vap !== undefined && vap > 0.001) return null;
  if (row.p_in_bar === null || row.p_in_bar === undefined) return null;
  if (!row.rho_kgm3) return null;
  const value = ((row.p_in_bar - Number(pv)) * 1e5) / (row.rho_kgm3 * spec.g);
  return Math.abs(value) < 0.005 ? 0 : value;   // 显示 2 位小数,小于半位就归零
}

/* p_v 来源:读取工况时取 Aspen 泡点压力(aspen);用户改过就是 manual */
function pvSourceText(row) {
  if (row.pv_bar === null || row.pv_bar === undefined) return "—";
  if (row.pv_source === "aspen") return "Aspen 泡点压力";
  if (row.pv_source === "manual") return "手填";
  return "老工况未标注";
}

/* 为什么这格是「—」:直接写在悬浮提示里,别让人猜 */
function npshWhy(row) {
  if (!state.pumpSpec) return "泵口径常量没取到(后端可能是旧版本)";
  const vap = row.pump_vap_frac;
  if (vap !== null && vap !== undefined && vap > 0.001) return "进口是两相,不计算汽蚀余量";
  if (row.pv_bar === null || row.pv_bar === undefined) {
    return "缺 p_v:模型没给出泡点压力 —— 物性组新建 pv → 加 PL、PBUB(bar)→ 报告选项→流股→物性组 选中 pv → 重置并运行,再重读工况;也可直接在这格手填";
  }
  return "缺进口压力或密度";
}

/* p_v 逐台保存:输入停下来 0.5 s 再发,避免边打字边打接口 */
const pvTimers = new Map();

function schedulePvSave(block, text) {
  clearTimeout(pvTimers.get(block));
  pvTimers.set(block, setTimeout(() => savePv(block, text), 500));
}

async function savePv(block, text) {
  const row = state.blocks.find((b) => b.name === block);
  if (!row || !state.current) return;
  const trimmed = String(text).trim();
  const pv = trimmed === "" ? null : Number(trimmed);
  if (pv !== null && Number.isNaN(pv)) return;
  try {
    const res = await fetch(
      `/api/cases/${state.current.id}/pumps/${encodeURIComponent(block)}/pv`,
      writeInit({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pv_bar: pv }),
      }),
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    row.pv_bar = pv;
    row.pv_source = pv === null ? null : "manual";
    updateNpshCell(block);
  } catch (err) {
    // 存不上就把格子标红,别让人以为已经存进去了
    const tr = [...document.querySelectorAll("#table tbody tr")]
      .find((el) => el.dataset.block === block);
    const input = tr && tr.querySelector("input[data-pv]");
    if (input) {
      input.classList.add("pv-bad");
      input.title = `保存失败:${err.message}`;
    }
  }
}

/* 只刷新这一行的 NPSHₐ 格子,不整表重绘(否则输入框会失焦) */
function updateNpshCell(block) {
  const row = state.blocks.find((b) => b.name === block);
  if (!row) return;
  for (const tr of document.querySelectorAll("#table tbody tr")) {
    if (tr.dataset.block !== block) continue;
    const td = tr.querySelector("td[data-npsh]");
    if (!td) return;
    const v = pumpNpsh(row);
    td.textContent = fmt(v, 2);
    td.title = v === null ? npshWhy(row) : (state.pumpSpec ? state.pumpSpec.npsh_rule : "");
    return;
  }
}

/* 沿用上一工况的 p_v:同一个模型位号相同、液体相同,重读工况后不用重填。
   只认"同模型 + 更早工况",只在点了之后才执行,不自动搬。 */
async function inheritPv() {
  if (!state.current) return;
  const msg = document.getElementById("pumpInheritMsg");
  if (!confirm("把同一模型更早工况里填过的 p_v 按位号搬过来?\n\n只填空着的格子,已填的不会被覆盖。")) return;
  try {
    const res = await fetch(`/api/cases/${state.current.id}/pumps/inherit-pv`, writeInit({ method: "POST" }));
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    state.blocks = await api(`/api/cases/${state.current.id}/blocks`);
    renderTable();
    if (msg) {
      msg.textContent = `已从 #${data.source_case_id}(${data.source_read_at})沿用 ${data.copied} 台,跳过 ${data.skipped} 台已填/不存在`;
    }
  } catch (err) {
    if (msg) msg.textContent = `沿用失败:${err.message}`;
  }
}

function renderPumpBar() {
  const bar = document.getElementById("pumpBar");
  if (!bar) return;
  const show = state.tab === "blocks" && state.family === "pump";
  bar.classList.toggle("hidden", !show);
  if (!show) closePvHelp();
}

/* 「?」指南收起来(切页 / 切家族时也要收,不然回来它还开着) */
function closePvHelp() {
  const help = document.getElementById("pvHelp");
  const body = document.getElementById("pvHelpBody");
  const btn = document.getElementById("pvHelpBtn");
  if (body) body.classList.remove("show");
  if (btn) btn.setAttribute("aria-expanded", "false");
  if (help) delete help.dataset.pinned;
}

/* 参数条只改"显示",不写库:余量乘到流量/扬程上 */
function readPumpBar() {
  const sel = document.getElementById("pumpMargin");
  if (!sel) return;
  const wrap = document.getElementById("pumpMarginCustomWrap");
  if (sel.value === "custom") {
    wrap.classList.remove("hidden");
    const v = Number(document.getElementById("pumpMarginCustom").value);
    state.pump.margin = v > 0 && v <= 5 ? v : 1;
  } else {
    wrap.classList.add("hidden");
    state.pump.margin = Number(sel.value) || 1;
  }
  renderTable();
}

function rowsForTab() {
  let rows = state.tab === "streams" ? state.streams : state.blocks;
  // 设备页按家族筛;名称搜索只对流股页生效 —— 否则在流股页打过的关键词,
  // 切到设备页会"看不见地"把行筛掉。
  if (state.tab === "blocks") rows = rows.filter((r) => blockFamilyOf(r) === state.family);
  const q = state.tab === "streams" ? state.filter.trim().toLowerCase() : "";
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
  ensureFamily();
  renderFamilyBar();
  renderPumpBar();
  const cols = blockColumns();
  const rows = rowsForTab();
  const thead = document.querySelector("#table thead");
  const tbody = document.querySelector("#table tbody");
  const spec = state.pumpSpec;
  const margin = state.pump.margin || 1;
  // 表头:带余量的列把 ×1.10 写进标题;建议电机/NPSHₐ 的算法挂 title,来源是 /api/pump-spec
  thead.innerHTML = `<tr>${cols.map((c) => {
    const label = c.scaled && margin !== 1 ? `${c.label} ×${margin}` : c.label;
    const tip = c.tip === "motor" ? (spec ? spec.motor_rule : "")
      : c.tip === "npsh" ? (spec ? spec.npsh_rule : "") : "";
    return `<th class="${c.num ? "num" : ""}"${tip ? ` title="${esc(tip)}"` : ""}>${esc(label)}</th>`;
  }).join("")}</tr>`;
  tbody.innerHTML = rows.map((row) => {
    const isColumn = COLUMN_TYPE.test(row.type || "");
    const isPump = String(row.type || "").toLowerCase() === "pump";
    return `<tr data-block="${esc(row.name)}">${cols.map((c) => {
      if (c.status) return `<td>${blockBadge(row.status)}</td>`;
      if (c.balance) return `<td>${balanceBadge(row.balance_state)}</td>`;
      if (c.key === "name" && isPump) {
        return `<td class="name"><a href="#" class="pump-link" data-block="${esc(row.name)}">${esc(row.name)}</a></td>`;
      }
      if (c.key === "name" && isColumn) {
        return `<td class="name"><a href="#" class="col-link" data-block="${esc(row.name)}">${esc(row.name)}</a></td>`;
      }
      if (c.percent) {
        const v = row[c.key] === null || row[c.key] === undefined ? null : row[c.key] * 100;
        return `<td class="num">${fmt(v, c.digits)}</td>`;
      }
      if (c.npsh) {
        const v = pumpNpsh(row);
        const why = v === null ? npshWhy(row) : (spec ? spec.npsh_rule : "");
        return `<td class="num" data-npsh="1"${why ? ` title="${esc(why)}"` : ""}>${fmt(v, c.digits)}</td>`;
      }
      if (c.pvInput) {
        const pv = row[c.key];
        // 显示保留 3 位(与参数卡一致),避免 1.04824999… 这种长尾把格子撑爆
        const val = pv === null || pv === undefined ? "" : fmt(pv, 3);
        return `<td class="pv-cell"><input class="pv-input" type="number" step="0.001" min="0"
                   value="${val}" data-pv="${esc(row.name)}"
                   title="饱和蒸气压(bar):读取工况时自动取 Aspen 进口流股的泡点压力;这里填值会覆盖它。模型读不到时:物性组新建 pv → 加 PL、PBUB(bar)→ 报告选项→流股→物性组 选中 pv → 重置并运行,再重读工况"></td>`;
      }
      if (c.scaled) {
        const raw = row[c.key];
        const v = raw === null || raw === undefined ? null : raw * margin;
        let tip = raw === null || raw === undefined ? ""
          : `原值 ${fmt(raw, c.digits)} ${c.unit || ""}`;
        // 扬程多给一条自检:压升反算的扬程应该和 HEAD_CAL 吻合,差得多就说明口径有问题
        if (c.key === "head_m" && raw !== null && raw !== undefined
            && row.dp_bar !== null && row.dp_bar !== undefined && row.rho_kgm3 && spec) {
          const hCheck = (row.dp_bar * 1e5) / (row.rho_kgm3 * spec.g);
          tip = `HEAD_CAL = ${fmt(raw, 2)} m;压升 ${fmt(row.dp_bar, 2)} bar 反算 = ${fmt(hCheck, 2)} m`;
        }
        return `<td class="num"${tip ? ` title="${esc(tip)}"` : ""}>${fmt(v, c.digits)}</td>`;
      }
      const value = c.num ? fmt(row[c.key], c.digits) : esc(row[c.key] ?? "");
      // 说明 / 平衡说明这两列会被 ellipsis 截断,原文挂 title
      // (以前只有 balance_note 有,「说明」里放的是 Aspen 原始报错,截断了就看不到)
      const tipText = c.cls === "msg" ? row[c.key] : null;
      const title = tipText ? ` title="${esc(tipText)}"` : "";
      return `<td class="${c.cls || ""} ${c.num ? "num" : ""}"${title}>${value}</td>`;
    }).join("")}</tr>`;
  }).join("");

  tbody.querySelectorAll(".col-link").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      openColumn(a.dataset.block);
    });
  });
  tbody.querySelectorAll(".pump-link").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      openPump(a.dataset.block);
    });
  });
  // 台账摘要:统计当前家族的焓平衡四态(表格只显示这一个家族,不能拿全量数字糊弄)
  const bs = { ok: 0, bad: 0, no_data: 0, skip: 0 };
  rows.forEach((b) => {
    if (b.balance_state && bs[b.balance_state] !== undefined) bs[b.balance_state] += 1;
  });
  let count = `${rows.length} 行`;
  // 后端版本比静态文件旧(改了 Python 没重启)时,行里没有 family,会全落到「其他」。
  // 与其让人对着一个只有「其他」的页面猜,不如把原因写在计数行上。
  if (state.blocks.length && state.blocks.every((b) => !b.family)) {
    count += " · 后端未更新,请重启服务";
  }
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
  // 泵参数条只在「设备 → 泵」显示:切到流股 / 对比页必须跟着收,
  // 否则停留在泵页时那行说明会一直挂在别的页面上。
  renderPumpBar();
  const exportBtn = document.getElementById("exportFamily");
  if (exportBtn) exportBtn.disabled = !state.current;
  if (state.tab === "streams") renderStreams();
  else if (state.tab === "blocks") renderBlocks();
  else renderCompare();
  syncTableEmpty();
}

/* 表画完再决定 #empty 露不露。
   原来这里无条件 add("hidden"),把「还没有工况」的引导也一起藏了 ——
   空库点进流股页只剩一张只有表头、写着「0 行」的白表,没有任何下一步。 */
function syncTableEmpty() {
  const el = document.getElementById("empty");
  if (!el) return;
  const rows = document.querySelectorAll("#table tbody tr").length;
  if (rows) { hideEmpty(); return; }
  if (!state.current) { showEmpty(); return; }   // 空库:复用引导文案与「载入示例」入口
  if (state.tab === "compare" && !state.compare.data) {
    showTableHint("还没有对比结果。点右上角 ⇄ 选择基础工况与对比工况。");
    return;
  }
  if (state.tab === "compare") {
    showTableHint("这两个工况没有可对比的行。");
    return;
  }
  if (state.tab === "blocks") {
    showTableHint("这个设备类型下没有可显示的行。换一个类型页签,或清空左上角的筛选。");
    return;
  }
  showTableHint("没有匹配的行。清空左上角的筛选再试。");
}

/* 有工况但这一屏没有行:这不是"没有工况",别复用 showEmpty 的错误语义 */
function showTableHint(text) {
  const el = document.getElementById("empty");
  if (!el) return;
  state.emptyText = "";
  state.emptyIsError = false;
  el.textContent = text;
  el.classList.remove("hidden");
}

/* 点抽屉外面的空白处关掉详情。
   判定顺序:抽屉内 → 不动;可交互元素 → 不动(那是要干别的事);
   另一台设备的入口 → 由它自己"换人",这里也不管。
   两个坑:打开抽屉的那一次点击会冒泡到 document(用时间戳跳过);
   拖选文字时的 click 不该收面板(看选区是否为空)。 */
const DRAWER_CLICKABLE = "a, button, input, select, textarea, label, thead, .btn, .icon-btn, .chart-svg";
let drawerOpenedAt = 0;

function markDrawerOpened() {
  drawerOpenedAt = Date.now();
}

function isOutsideDrawerClick(evt) {
  if (Date.now() - drawerOpenedAt < 120) return false;      // 就是刚打开它的那一下
  const sel = window.getSelection && window.getSelection();
  if (sel && !sel.isCollapsed) return false;                // 正在选字
  const t = evt.target;
  if (!t || !t.closest) return false;
  if (t.closest(".column-panel")) return false;             // 抽屉内部
  if (t.closest(DRAWER_CLICKABLE)) return false;            // 按钮/链接/输入框/表头/图表
  return true;
}

/* Esc 关抽屉:三个抽屉都有 ✕ 按钮,键盘用户也该能关 */
function closeDataDrawers() {
  let closed = false;
  ["columnPanel", "pumpPanel", "compPanel"].forEach((id) => {
    const el = document.getElementById(id);
    if (el && !el.classList.contains("hidden")) {
      el.classList.add("hidden");
      closed = true;
    }
  });
  return closed;
}

/* ------------------------------------------------------------ 塔剖面 */

/* 画布尺寸跟着"两图并排"走:抽屉内容宽 584,两列各 ≈286。
   viewBox 收窄到 300×150 后缩放比 ≈0.95,轴文字 11px 落到 ~10.5px;
   若沿用 480×170 再并排,文字会被压回 6px 级(第 1 批刚修好的正是这个)。
   padL 要留够最长的刻度(如 1282.4),否则左边缘会切字。 */
const COL_CHART = { width: 300, height: 150, padL: 54, padR: 12, padT: 16, padB: 22 };
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

  return `<svg viewBox="0 0 ${width} ${height}" class="chart-svg">
    <line x1="${padL}" y1="${padT + h}" x2="${width - padR}" y2="${padT + h}" class="axis-line"/>
    ${paths}
    <line class="guide" x1="0" y1="${padT}" x2="0" y2="${padT + h}"
          stroke="${cssVar('--chart-guide')}" stroke-dasharray="4 3" stroke-width="1" opacity="0"/>
    ${markers}
    <text x="${padL - 6}" y="${padT + 6}" class="axis" text-anchor="end">${max.toFixed(1)}</text>
    <text x="${padL - 6}" y="${padT + h}" class="axis" text-anchor="end">${min.toFixed(1)}</text>
  </svg>`;
}

/* 图表顶上那一行:标题 + 图例。
   图例原来画在 SVG 里、紧挨着左侧最大刻度,并排后单图只有 285px,那一角就挤了。
   挪成 HTML 一行后,标题靠左、图例靠右,和曲线画布彻底分开,也能换行。 */
function chartHead(title, series) {
  const items = series.map((s) =>
    `<span class="lg"><i style="background:${s.color}"></i>${esc(s.label)}</span>`).join("");
  return `<div class="chart-head">
    <span class="chart-title">${esc(title)}</span>
    <span class="chart-legend">${items}</span>
  </div>`;
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

/* 图 ↔ 表联动:高亮同一级,并把它滚到可见区。
   只滚表格自己的容器 —— scrollIntoView 会把整条可滚动祖先链都滚一遍,
   抽屉跟着滚、图表就被顶出视口(40 级塔实测面板 scrollTop 0→395)。 */
function highlightRow(idx) {
  const rows = document.querySelectorAll("#columnTable tbody tr");
  rows.forEach((tr, i) => tr.classList.toggle("hl", i === idx));
  if (idx === null) return;
  const tr = rows[idx];
  const wrap = tr && tr.closest(".table-wrap");
  if (!wrap) return;
  const head = wrap.querySelector("thead");
  const headH = head ? head.offsetHeight : 0;   // 表头 sticky,别把行藏在它下面
  const wr = wrap.getBoundingClientRect();
  const rr = tr.getBoundingClientRect();
  if (rr.top - headH < wr.top) {
    wrap.scrollTop += rr.top - headH - wr.top;
  } else if (rr.bottom > wr.bottom) {
    wrap.scrollTop += rr.bottom - wr.bottom;
  }
}

/* ------------------------------------------------------------ 泵参数卡 */

/* 中联水泵选型页。**不带任何参数** —— 工艺参数不进厂商日志和浏览器历史,
   流量/扬程由用户自己敲进去。 */
const ZHONGLIAN_URL = "https://www.cipump.com/sbxx.html";

/* 泵参数卡:把要抄进中联表单的那几个数摆出来。余量只影响显示,不改库 */
function openPump(block) {
  const row = state.blocks.find((b) => b.name === block);
  if (!row) return;
  ["columnPanel", "compPanel"].forEach((id) =>
    document.getElementById(id).classList.add("hidden"));
  const panel = document.getElementById("pumpPanel");
  document.getElementById("pumpTitle").textContent = `泵参数 · ${block}`;
  panel.classList.remove("hidden");
  markDrawerOpened();
  panel.scrollTop = 0;

  const margin = state.pump.margin || 1;
  const spec = state.pumpSpec;
  const scaled = (v) => (v === null || v === undefined ? null : v * margin);
  const npsh = pumpNpsh(row);
  // 来源只写一个词,细节放悬浮提示里,别把抽屉塞满
  const kv = (k, v, source, tip = "") =>
    `<tr><td class="name">${esc(k)}</td><td class="num">${v}</td>`
    + `<td class="msg"${tip ? ` title="${esc(tip)}"` : ""}>${esc(source)}</td></tr>`;
  const marginTag = margin === 1 ? "" : ` ×${margin}`;

  const rows = [
    kv("流量 m³/h", fmt(scaled(row.flow_m3h), 2), `Aspen${marginTag}`,
       row.flow_m3h == null ? "没读到" : `原值 ${fmt(row.flow_m3h, 2)}`),
    kv("扬程 m", fmt(scaled(row.head_m), 2), `Aspen${marginTag}`,
       row.head_m == null ? "没读到" : `原值 ${fmt(row.head_m, 2)}`),
    kv("轴功率 kW", fmt(row.power_kw, 2), "Aspen", "BRAKE_POWER,反向工况为负"),
    kv("建议电机 kW", fmt(row.motor_kw, 1), "计算", spec ? spec.motor_rule : ""),
    kv("效率 %", fmt(row.pump_eff == null ? null : row.pump_eff * 100, 1), "Aspen", "CEFF"),
    kv("进口压力 bar", fmt(row.p_in_bar, 3), "进口流股"),
    kv("出口压力 bar", fmt(row.p_out_bar, 3), "出口流股"),
    kv("压升 bar", fmt(row.dp_bar, 3), "Aspen", "IN_PRES"),
    kv("密度 kg/m³", fmt(row.rho_kgm3, 1), "计算", "进口流股 m/V;两相或缺流量时用 RHO_LIQ×MW_LIQ"),
    kv("进口汽化率", fmt(row.pump_vap_frac, 4), "进口流股", "> 0 视为两相,不给 NPSHₐ"),
    kv("p_v bar", row.pv_bar == null ? "—" : fmt(row.pv_bar, 3), pvSourceText(row),
       row.pv_bar == null
         ? "模型没给出泡点压力:物性组新建 pv → 加 PL、PBUB(bar)→ 报告选项→流股→物性组 选中 pv → 重置并运行,再重读工况;详见 docs/pump-selection-plan.md §3.6"
         : "单相液相时泡点压力就是蒸气压"),
    kv("NPSHₐ m", fmt(npsh, 2), "计算", npsh == null ? npshWhy(row) : (spec ? spec.npsh_rule : "")),
  ];
  document.querySelector("#pumpTable thead").innerHTML =
    '<tr><th>参数</th><th class="num">值</th><th>来源</th></tr>';
  document.querySelector("#pumpTable tbody").innerHTML = rows.join("");
}

async function openColumn(block) {
  const panel = document.getElementById("columnPanel");
  document.getElementById("compPanel").classList.add("hidden");  // 两个抽屉不叠着
  document.getElementById("pumpPanel").classList.add("hidden");
  document.getElementById("columnTitle").textContent = `塔剖面 · ${block}`;
  panel.classList.remove("hidden");
  markDrawerOpened();
  panel.scrollTop = 0;   // 看另一座塔时别带着上一座的滚动位置

  // 先给占位:接口慢或失败时原来是一块白板(openColumn 的 reject 没人接)
  document.getElementById("columnCharts").innerHTML = '<p class="comp-note">正在读取…</p>';
  document.querySelector("#columnTable thead").innerHTML = "";
  document.querySelector("#columnTable tbody").innerHTML = "";

  let rows;
  try {
    rows = await withBusy("columnPanel",
      () => api(`/api/cases/${state.current.id}/columns/${encodeURIComponent(block)}`));
  } catch (err) {
    document.getElementById("columnCharts").innerHTML =
      `<div class="comp-error">读不到 ${esc(block)} 的塔剖面:${esc(err.message)}</div>`;
    return;
  }
  state.column = { block, rows };
  columnState.rows = rows;
  columnState.charts = [];

  const temps = rows.map((r) => ({ x: r.stage, y: r.temperature_c }));
  const liq = rows.map((r) => ({ x: r.stage, y: r.liq_flow }));
  const vap = rows.map((r) => ({ x: r.stage, y: r.vap_flow }));

  const chartsEl = document.getElementById("columnCharts");
  // 系列定义提到外面:顶部标题行的图例和曲线用的是同一份 label 与颜色,别写两遍
  const tempSeries = [{ points: temps, color: cssVar("--chart-1"), label: "温度" }];
  const flowSeries = [
    { points: liq, color: cssVar("--chart-1"), label: "液" },
    { points: vap, color: cssVar("--chart-2"), label: "气" },
  ];
  chartsEl.innerHTML =
    `<figure class="chart">${chartHead("温度", tempSeries)}${chartSVG(tempSeries)}</figure>`
    + `<figure class="chart">${chartHead("液/气流量", flowSeries)}${chartSVG(flowSeries)}</figure>`;

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

const PHASE_COLORS = () => ["--chart-1", "--chart-2", "--chart-3", "--chart-4", "--chart-5", "--chart-6"].map(cssVar);
const PHASE_REST_COLOR = () => cssVar("--chart-rest");

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
      <span class="phase-legend">无${esc(label)}</span>
    </div>`;
  }

  // 占比 <1.5% 的段在 12px 高的色条上不到 2px,画出来只是一根毛刺(实测 0.9/0.5/0.1px),
  // 合并进「其他」;具体数字仍然在图例里给。全都 <1.5% 时至少留第一段,别画成一条灰条。
  const MIN_SHARE = 0.015;
  const big = items.filter((c) => c.v / total >= MIN_SHARE);
  const head = big.length ? big.slice(0, 6) : items.slice(0, 1);
  const headSet = new Set(head);
  const tail = items.filter((c) => !headSet.has(c));
  const tailSum = tail.reduce((s, c) => s + c.v, 0);

  const segs = head.map((c, i) =>
    `<i class="phase-seg" style="width:${(c.v / total * 100).toFixed(3)}%;background:${PHASE_COLORS()[i % 6]}"></i>`
  ).join("") + (tail.length
    ? `<i class="phase-seg" style="width:${(tailSum / total * 100).toFixed(3)}%;background:${PHASE_REST_COLOR()}"></i>`
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
  markDrawerOpened();

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
  let next;
  try {
    next = await withBusy("tableWrap",
      () => api(`/api/compare?base=${baseId}&other=${otherId}`));
  } catch (err) {
    // 同样保留上一次的结果(清空只会让人以为"没数据"),但摘要里必须写清本次失败
    document.getElementById("compareSummary").textContent =
      `对比失败:${err.message}`
      + (state.compare.data ? "(表格里仍是上一次的对比结果)" : "");
    syncTableEmpty();
    return;
  }
  state.compare.data = next;
  updateCompareSummary();
  renderCompare();
  syncTableEmpty();
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
  renderPumpBar();      // 离开泵页立刻收掉参数条,不等表格重绘
  syncToolbar(tab);
  closeDataDrawers();   // 三个抽屉一起收(原来漏了泵参数)
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
   左边原本放筛选框的位置,在对比页换成「流股 / 设备」开关,
   在设备页换成「类型页签」—— 三态一起管,免得某个控件"看不见地"还在生效。 */
function syncToolbar(tab) {
  const isCompare = tab === "compare";
  const isBlocks = tab === "blocks";
  document.getElementById("compareHint").classList.toggle("hidden", isCompare);
  document.getElementById("compareControls").classList.toggle("hidden", !isCompare);
  document.getElementById("compareScope").classList.toggle("hidden", !isCompare);
  document.getElementById("filter").classList.toggle("hidden", isCompare || isBlocks);
  document.getElementById("blockFamilies").classList.toggle("hidden", !isBlocks);
  document.getElementById("count").classList.toggle("hidden", isCompare);
  // 设备页右上角「导出 Excel」:只导当前分类;没有工况时禁用
  const exportBtn = document.getElementById("exportFamily");
  if (exportBtn) {
    exportBtn.classList.toggle("hidden", !isBlocks);
    exportBtn.disabled = !state.current;
  }
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
  const comp = document.getElementById("readComposition");
  if (comp) comp.disabled = busy;
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
    <button class="read-close" type="button" title="关闭"><svg class="ico"><use href="#i-close"/></svg></button>`;
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
    <button class="read-close" type="button" title="关闭"><svg class="ico"><use href="#i-close"/></svg></button>`;
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
    const withComp = !!(document.getElementById("readComposition") || {}).checked;
    const res = await fetch("/api/read", writeInit({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ include_composition: withComp }),
    }));
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

/* 流程图几何常量。颜色不在这里 —— 全部走 style.css 的 --flow-* token,
   屏幕、图例、导出图读同一份,免得改一处忘两处。 */
/* 流程图节点状态的读屏文字。和 style.css 的 --flow-*-line 一一对应 */
const FLOW_STATE_TEXT = {
  ok: "通过", bad: "超差", no_data: "无法校验", skip: "不校验", unknown: "未知",
};

const FLOW = {
  nodeW: 126, nodeH: 40, gapX: 46, gapY: 16, rowGap: 76,
  chipW: 88, chipH: 22, chipGap: 22,
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
        <path d="M 0 0 L 10 5 L 0 10 z" fill="${cssVar('--flow-edge')}"
              stroke="${cssVar('--panel')}" stroke-width="1.8" paint-order="stroke"></path>
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
    // tabindex/role:SVG 里的 <g> 默认不可聚焦,键盘用户选不到设备。
    // aria-label 把"位号 + 类型 + 状态"念出来,别只念一个位号。
    const stateText = FLOW_STATE_TEXT[n.state] || "未知";
    parts.push(`<g class="${nodeCls(n.name)} st-${n.state || "unknown"}"
        data-node="${esc(n.name)}" transform="translate(${p.x} ${p.y})"
        tabindex="0" role="button" aria-label="${esc(n.name)} ${esc(n.type || "")} ${stateText}">
        <title>${esc(tip)}</title>
        <rect class="node-body" width="${p.w}" height="${p.h}" rx="8"></rect>
        <rect width="6" height="${p.h}" rx="3" class="flow-stripe"></rect>
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
  /* 图例直接引用 token:和屏幕上节点用的是同一份值,不再各写一份调色板。
     「无法校验」的方块也画成虚线 —— 和节点上的虚线框对应,缩略图里不至于看混。 */
  const items = [
    ["通过", "var(--flow-ok-line)", "", "var(--panel)"],
    ["超差", "var(--flow-bad-line)", "", "var(--panel)"],
    ["无法校验", "var(--flow-nodata-line)", "border-style:dashed;", "var(--panel)"],
    ["不校验", "var(--flow-idle-line)", "", "var(--panel)"],
    ["进料 / 产品", "var(--flow-idle-line)", "border-style:dashed;", "var(--surface-hover)"],
  ];
  el.innerHTML = items.map(([text, color, extra, bg]) =>
    `<span><i style="border-color:${color};background:${bg};${extra}"></i>${text}</span>`).join("");
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

/* 导出的 SVG 是独立文件,读不到 style.css —— 所以导出时把当前生效的 token 值
   取出来内联一份。以前这里抄了一份硬编码 + 一句"改 style.css 记得同步",
   字体栈还和屏幕不一致(屏幕 Segoe UI 优先、导出 YaHei 优先,同一串设备名宽度不同)。 */
function flowExportCss() {
  const v = cssVar;
  return `
  .flow-edge { fill: none; stroke: ${v("--flow-edge")}; stroke-width: 1.2; }
  .flow-edge.dim { opacity: .1; }
  .flow-edge.on { stroke: ${v("--accent")}; stroke-width: 2; }
  .flow-label { font-size: 9.5px; fill: ${v("--muted")}; paint-order: stroke;
    stroke: ${v("--panel")}; stroke-width: 2.5px; }
  .flow-node .node-body { fill: ${v("--panel")}; fill-opacity: .82;
    stroke: ${v("--flow-idle-line")}; stroke-width: 1.2; }
  .flow-node .flow-stripe { fill: ${v("--muted")}; stroke: none; }
  .flow-node text { font-family: ${v("--font-sans")}; font-size: 14.5px;
    font-weight: 600; fill: ${v("--text")}; }
  .flow-node.st-ok .node-body { stroke: ${v("--flow-ok-line")}; }
  .flow-node.st-ok .flow-stripe { fill: ${v("--ok-text")}; }
  .flow-node.st-bad .node-body { stroke: ${v("--flow-bad-line")}; }
  .flow-node.st-bad .flow-stripe { fill: ${v("--err-text")}; }
  .flow-node.st-no_data .node-body { stroke: ${v("--flow-nodata-line")}; stroke-dasharray: 4 3; }
  .flow-node.st-no_data .flow-stripe { fill: ${v("--warn-text")}; }
  .flow-node.st-skip .node-body { stroke: ${v("--flow-idle-line")}; }
  .flow-node.st-skip .flow-stripe { fill: ${v("--flow-idle-line")}; }
  .flow-node.dim { opacity: .18; }
  .flow-node.on .node-body { stroke: ${v("--accent-300")}; stroke-width: 1.6; fill-opacity: .92; }
  .flow-node.center .node-body { fill: ${v("--accent-soft")}; fill-opacity: 1;
    stroke: ${v("--accent")}; stroke-width: 2.6; }
  .flow-node.center .flow-stripe { fill: ${v("--accent")}; }
  .flow-node.center text { fill: ${v("--accent")}; font-weight: 700; }
  .flow-boundary rect { fill: ${v("--surface-hover")}; stroke: ${v("--flow-idle-line")};
    stroke-width: 1.2; stroke-dasharray: 3 3; }
  .flow-boundary text { font-size: 10.5px; fill: ${v("--muted")}; }
`;
}

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

  // 导出图固定用浅色主题:深色图贴进 Word/PPT 会变成一整块黑
  const html = document.documentElement;
  const keepTheme = html.dataset.theme;
  html.dataset.theme = "light";
  const exportCss = flowExportCss();
  html.dataset.theme = keepTheme || "light";
  const style = document.createElementNS(SVG_NS, "style");
  style.textContent = exportCss;
  // 白底:SVG 默认透明,贴进 Word/PPT 会变成看不清的"白线浮在白纸"
  const bg = document.createElementNS(SVG_NS, "rect");
  bg.setAttribute("x", view.x);
  bg.setAttribute("y", view.y);
  bg.setAttribute("width", view.w);
  bg.setAttribute("height", view.h);
  bg.setAttribute("fill", cssVar("--panel"));
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
    ctx.fillStyle = cssVar("--panel");
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
  // 失败时不清空表:旧数据留着更有用,但必须说清它是哪一份,别被当成新工况读
  const prevId = state.current ? state.current.id : null;
  showNotice(`正在加载工况 #${id}…`);
  let payload;
  try {
    payload = await withBusy("tableWrap", () => Promise.all([
      api(`/api/cases/${id}`),
      api(`/api/cases/${id}/streams`),
      api(`/api/cases/${id}/blocks`),
      // 波及范围是锦上添花:接口不通(老版本服务)不该拖垮整页
      api(`/api/cases/${id}/impact`).catch(() => null),
    ]));
  } catch (err) {
    showNotice(`工况 #${id} 没加载成功:${err.message}。`
      + (prevId ? `表格里仍是 #${prevId} 的数据。` : ""), { err: true });
    return;
  }
  const [detail, streams, blocks, impact] = payload;
  state.current = detail;
  state.streams = streams;
  state.blocks = blocks;
  state.impact = impact;
  // 换工况:家族回到默认、关键词清空 —— 否则会停在旧家族/旧关键词上,新工况显示 0 行
  state.family = null;
  state.filter = "";
  const filterBox = document.getElementById("filter");
  if (filterBox) filterBox.value = "";
  const inheritMsg = document.getElementById("pumpInheritMsg");
  if (inheritMsg) inheritMsg.textContent = "";
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
  } else if (state.page === "report") {
    state.report.picked = new Set(); // 换工况先清勾选,免得把旧位号带过去
    renderReport();
  } else if (state.page === "home") {
    renderHome();
  } else if (state.tab !== "compare") {
    renderTable();
  }
  clearNotice();
  resetTableScroll();   // 换工况也从第一行看起
}

/* ------------------------------------------------------------ 衡算报告页 */

/* 勾选状态:空集合 = 全部设备(不逼用户先全选一遍) */
state.report = { data: null, picked: new Set(), filter: "", busy: false,
                 removed: new Set() };  // 本次人工删掉的工段

function reportNotice(text, kind = "") {
  const el = document.getElementById("reportNotice");
  if (!el) return;
  if (!text) { el.classList.add("hidden"); el.textContent = ""; return; }
  el.classList.remove("hidden");
  el.classList.toggle("warn", kind === "warn");
  el.textContent = text;
}

function reportPickedDevices() {
  return state.blocks || [];
}

function reportSelectedCount() {
  return state.report.picked.size;
}

/* 按家族批量勾/取消:只维护一套「设备名集合」,家族按钮只是快捷方式 */
function reportToggleFamily(key) {
  const members = reportPickedDevices()
    .filter((b) => (b.family || "") === key)
    .map((b) => b.name);
  if (!members.length) return;
  const allOn = members.every((n) => state.report.picked.has(n));
  members.forEach((n) => (allOn ? state.report.picked.delete(n) : state.report.picked.add(n)));
  renderReportPick();
}

function renderReportPick() {
  const famBox = document.getElementById("reportFamilies");
  const listBox = document.getElementById("reportBlocks");
  const hint = document.getElementById("reportPickHint");
  if (!famBox || !listBox) return;

  const blocks = reportPickedDevices();
  state.report.picked = new Set(
    [...state.report.picked].filter((n) => blocks.some((b) => b.name === n)));

  famBox.innerHTML = "";
  (state.families || []).forEach((fam) => {
    const members = blocks.filter((b) => (b.family || "") === fam.key);
    if (!members.length) return;
    const on = members.filter((b) => state.report.picked.has(b.name)).length;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn" + (on === members.length ? " primary" : "");
    btn.textContent = `${fam.label} ${on}/${members.length}`;
    btn.title = on === members.length ? "再点一次取消这一族的勾选" : "勾选这一族的全部设备";
    btn.addEventListener("click", () => reportToggleFamily(fam.key));
    famBox.appendChild(btn);
  });

  const keyword = (state.report.filter || "").trim().toLowerCase();
  const shown = blocks.filter((b) => !keyword
    || String(b.name).toLowerCase().includes(keyword)
    || String(b.type || "").toLowerCase().includes(keyword));

  listBox.innerHTML = "";
  shown.forEach((b) => {
    const label = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = state.report.picked.has(b.name);
    box.addEventListener("change", () => {
      if (box.checked) state.report.picked.add(b.name);
      else state.report.picked.delete(b.name);
      renderReportPick();
    });
    const name = document.createElement("span");
    name.textContent = b.name;
    const ty = document.createElement("span");
    ty.className = "ty";
    ty.textContent = b.type || "";
    label.appendChild(box);
    label.appendChild(name);
    label.appendChild(ty);
    listBox.appendChild(label);
  });
  if (!shown.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "没有匹配的设备";
    listBox.appendChild(empty);
  }

  if (hint) {
    const n = reportSelectedCount();
    hint.textContent = n
      ? `已勾选 ${n} 台(逐设备表只出这些)`
      : `未勾选 = 全部 ${blocks.length} 台`;
  }
}

/* 界面上的 textarea 是边界流股的唯一真相:手改过的内容原样提交 */
function reportCollectSections() {
  const parts = {};
  document.querySelectorAll("#reportSections .report-section").forEach((row) => {
    const key = row.dataset.key;
    if (!key) return;
    const label = row.querySelector(".report-label");
    const inBox = row.querySelector(".report-in");
    const outBox = row.querySelector(".report-out");
    parts[key] = {
      label: label ? label.value.trim() : "",
      in: inBox ? inBox.value.trim() : "",
      out: outBox ? outBox.value.trim() : "",
    };
  });
  return { parts, deleted: [...state.report.removed] };
}

function reportRenderSections(payload) {
  const box = document.getElementById("reportSections");
  const hint = document.getElementById("reportSectionHint");
  if (!box) return;
  const parts = (payload && payload.merged) || {};
  const keys = Object.keys(parts);
  if (payload && payload.deleted && payload.deleted.length) {
    reportNotice(`已删除的工段:${payload.deleted.join("、")}（保存在 data/config/sections.json）`);
  }

  if (hint) {
    hint.textContent = payload.auto
      ? "当前是自动草稿,未人工确认"
      : "已载入 data/config/sections.json 的人工确认结果";
  }

  box.innerHTML = "";
  if (!keys.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "这个工况没有推出任何工段(设备位号里没有段号),可以先确认设备命名。";
    box.appendChild(empty);
    return;
  }

  keys.forEach((key) => {
    const info = parts[key] || {};
    const row = document.createElement("div");
    row.className = "report-section";
    row.dataset.key = key;

    const head = document.createElement("div");
    head.className = "report-section-head";
    const input = document.createElement("input");
    input.className = "report-label";
    input.value = info.label || key;
    input.title = "工段标题,报告小节就用它";
    const chip = document.createElement("span");
    chip.className = "report-chip";
    chip.textContent = `${(info.in || []).length} 进 / ${(info.out || []).length} 出`;
    head.appendChild(input);
    head.appendChild(chip);

    const del = document.createElement("button");
    del.type = "button";
    del.className = "report-del";
    del.textContent = "删除此工段";
    del.title = "从本次报告里去掉这个工段;保存后下次也不会再从草稿里冒出来";
    del.addEventListener("click", () => {
      state.report.removed.add(key);
      row.remove();
      const left = box.querySelectorAll(".report-section").length;
      reportNotice(left
        ? `已删除「${key} 工段」，还剩 ${left} 个；记得点「保存工段边界」。`
        : "所有工段都删掉了。保存后报告只出全流程表；点「重置为草稿」可恢复。");
      if (hint) hint.textContent = `已删除 ${state.report.removed.size} 个工段（未保存）`;
    });
    head.appendChild(del);

    const fields = document.createElement("div");
    fields.className = "report-fields";
    [["in", "进界流股(逗号或空格分隔)"], ["out", "出界流股(逗号或空格分隔)"]].forEach(([field, text]) => {
      const label = document.createElement("label");
      label.textContent = text;
      const area = document.createElement("textarea");
      area.className = field === "in" ? "report-in" : "report-out";
      area.value = (info[field] || []).join(", ");
      area.spellcheck = false;
      label.appendChild(area);
      fields.appendChild(label);
    });

    row.appendChild(head);
    row.appendChild(fields);
    box.appendChild(row);
  });
}

async function renderReport() {
  const c = state.current;
  reportNotice("");
  if (!c) {
    reportRenderSections({ merged: {}, auto: true });
    reportNotice("还没有工况:先在左上角读取一个工况,或载入示例。", "warn");
    return;
  }
  renderReportPick();
  try {
    const payload = await api(`/api/cases/${c.id}/sections`);
    state.report.data = payload;
    reportRenderSections(payload);
    if (payload.error) reportNotice(payload.error, "warn");
  } catch (err) {
    reportNotice(`工段边界没读到:${err.message}`, "warn");
  }
}

async function saveReportSections() {
  const c = state.current;
  if (!c) { reportNotice("还没有工况", "warn"); return; }
  const btn = document.getElementById("reportSave");
  if (btn) btn.disabled = true;
  try {
    const body = reportCollectSections();
    const payload = await apiPost(`/api/cases/${c.id}/sections`, body);
    if (!payload.ok) {
      reportNotice(`没保存:${(payload.errors || []).join(";")}`, "warn");
      return;
    }
    const fresh = await api(`/api/cases/${c.id}/sections`);
    state.report.data = fresh;
    state.report.removed = new Set();
    reportRenderSections(fresh);
    reportNotice(`已保存到 ${payload.config.path}(工况 #${c.id})`);
  } catch (err) {
    reportNotice(`没保存:${err.message}`, "warn");
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* kind: mass 物料册 / energy 能量册。两个按钮共用这一个下载函数 */
function downloadReport(kind) {
  const c = state.current;
  if (!c) { reportNotice("还没有工况", "warn"); return; }
  const picked = [...state.report.picked];
  const query = new URLSearchParams({ kind });
  if (picked.length) query.set("blocks", picked.join(","));
  // 直接用地址栏下载:文件名走 Content-Disposition,和「导出 Excel」一致
  window.location.href = `/api/cases/${c.id}/balance-report.docx?${query}`;
  reportNotice(kind === "energy"
    ? "正在生成能量衡算册,请留意浏览器下载栏。"
    : "正在生成物料衡算册,请留意浏览器下载栏。");
}

function bindReportPage() {
  const save = document.getElementById("reportSave");
  if (save) save.addEventListener("click", () => saveReportSections());

  const reset = document.getElementById("reportReset");
  if (reset) {
    reset.addEventListener("click", () => {
      const payload = state.report.data || { merged: {}, auto: true };
      state.report.removed = new Set();
      reportRenderSections({ merged: payload.draft || {}, auto: true });
      reportNotice("已恢复成自动草稿(删除过的工段也回来了),还没保存");
    });
  }

  const dlMass = document.getElementById("reportDownloadMass");
  if (dlMass) dlMass.addEventListener("click", () => downloadReport("mass"));
  const dlEnergy = document.getElementById("reportDownloadEnergy");
  if (dlEnergy) dlEnergy.addEventListener("click", () => downloadReport("energy"));

  const filter = document.getElementById("reportFilter");
  if (filter) {
    filter.addEventListener("input", () => {
      state.report.filter = filter.value;
      renderReportPick();
    });
  }

  const all = document.getElementById("reportPickAll");
  if (all) {
    all.addEventListener("click", () => {
      state.report.picked = new Set(reportPickedDevices().map((b) => b.name));
      renderReportPick();
    });
  }

  const none = document.getElementById("reportPickNone");
  if (none) {
    none.addEventListener("click", () => {
      state.report.picked = new Set();
      renderReportPick();
    });
  }
}

/* ------------------------------------------------------------ 路由与首页 */

const PAGES = ["home", "streams", "blocks", "compare", "flow", "pipes", "report"];
const PAGE_TITLE = { streams: "流股", blocks: "设备", compare: "对比", flow: "流程", report: "衡算报告" };

/* 地址栏是唯一真相:#/streams、#/blocks/塔名(直接打开某座塔的剖面) */
function readHashPage() {
  const hash = location.hash.replace("#", "").split("/")[0];
  return PAGES.includes(hash) ? hash : "home";
}

function setPage(page, { push = true } = {}) {
  if (!PAGES.includes(page)) page = "home";
  // 换页就把详情收掉:数据页的抽屉是挂在 #pageData 里的,
  // 只是把整页藏起来的话,绕一圈回到设备页它又会冒出来
  closeDataDrawers();
  state.page = page;
  document.querySelectorAll(".side-nav .nav-item").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === page));

  const isHome = page === "home";
  const isFlow = page === "flow";
  const isPipes = page === "pipes";       // 管道页自带一套参数面板与渲染(见 pipe.js)
  const isReport = page === "report";     // 衡算报告页:工段边界 + 设备勾选
  document.getElementById("pageHome").classList.toggle("hidden", !isHome);
  document.getElementById("pageFlow").classList.toggle("hidden", !isFlow);
  document.getElementById("pagePipes").classList.toggle("hidden", !isPipes);
  document.getElementById("pageReport").classList.toggle("hidden", !isReport);
  document.getElementById("pageData").classList.toggle("hidden",
    isHome || isFlow || isPipes || isReport);
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
  if (isReport) {
    renderReport();    // 进页才拉工段边界,顺便按当前工况重建设备勾选
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
      <h3>判定依据</h3>
      <div class="muted" style="margin-bottom:8px">${c.converged ? "可用于统计对比" : "不建议做定量结论"}</div>
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
    await fetch("/api/shutdown", writeInit({ method: "POST" }));
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

  document.querySelectorAll(".side-nav .nav-item").forEach((btn) => {
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

  // 泵参数条:余量一改就重算表格(只改显示,不写库)
  ["pumpMargin", "pumpMarginCustom"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("input", readPumpBar);
  });

  // p_v 是逐台输入框,表格会整块重绘,所以用事件委托挂一次就够
  const blockTable = document.querySelector("#table tbody");
  if (blockTable) {
    blockTable.addEventListener("input", (e) => {
      const box = e.target.closest("input[data-pv]");
      if (!box) return;
      box.classList.remove("pv-bad");
      schedulePvSave(box.dataset.pv, box.value);
    });
  }

  const inheritBtn = document.getElementById("pumpInherit");
  if (inheritBtn) inheritBtn.addEventListener("click", inheritPv);

  document.getElementById("baseSelect").addEventListener("change", runCompare);
  document.getElementById("otherSelect").addEventListener("change", runCompare);
  document.querySelectorAll("#compareScope button").forEach((b) =>
    b.addEventListener("click", () => setCompareScope(b.dataset.scope)));
  document.getElementById("compareHint").addEventListener("click", () => setPage("compare"));
  document.getElementById("closeColumn").addEventListener("click", () =>
    document.getElementById("columnPanel").classList.add("hidden"));
  document.getElementById("closeComp").addEventListener("click", () =>
    document.getElementById("compPanel").classList.add("hidden"));
  document.getElementById("closePump").addEventListener("click", () =>
    document.getElementById("pumpPanel").classList.add("hidden"));

  // 点抽屉外面的空白处也能关(三个抽屉共用;判定见 isOutsideDrawerClick)
  document.addEventListener("click", (e) => {
    if (isOutsideDrawerClick(e)) closeDataDrawers();
  });

  // 泵参数条上的「?」操作指南:悬浮展开、点击固定,点外面 / Esc 收起
  const pvHelp = document.getElementById("pvHelp");
  const pvHelpBtn = document.getElementById("pvHelpBtn");
  const pvHelpBody = document.getElementById("pvHelpBody");
  if (pvHelp && pvHelpBtn && pvHelpBody) {
    const setPvHelp = (open) => {
      pvHelpBody.classList.toggle("show", open);
      pvHelpBtn.setAttribute("aria-expanded", open ? "true" : "false");
      if (!open) delete pvHelp.dataset.pinned;
    };
    pvHelp.addEventListener("mouseenter", () => setPvHelp(true));
    pvHelp.addEventListener("mouseleave", () => {
      if (pvHelp.dataset.pinned !== "1") setPvHelp(false);
    });
    pvHelpBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = !pvHelpBody.classList.contains("show");
      setPvHelp(open);
      if (open) pvHelp.dataset.pinned = "1";
    });
    document.addEventListener("click", (e) => {
      if (!pvHelp.contains(e.target)) setPvHelp(false);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") setPvHelp(false);
    });
  }

  bindReportPage();

  // 设备页右上角「导出 Excel」:当前分类的设备明细(泵分类 = 选型要抄的那几列)
  const exportFamily = document.getElementById("exportFamily");
  if (exportFamily) {
    exportFamily.addEventListener("click", async () => {
      if (!state.current) return;
      const family = state.family || "column";
      const url = `/api/cases/${state.current.id}/export-family.xlsx?family=${encodeURIComponent(family)}`;
      exportFamily.disabled = true;
      try {
        const res = await fetch(url);
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.error || `HTTP ${res.status}`);
        }
        const blob = await res.blob();
        const cd = res.headers.get("Content-Disposition") || "";
        const m = /filename="([^"]+)"/.exec(cd);
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = m ? m[1] : `Casebook_${family}.xlsx`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      } catch (err) {
        alert(`导出失败:${err.message}`);
      } finally {
        exportFamily.disabled = false;
      }
    });
  }

  // 打开中联选型:只开页面,不拼参数(noopener/noreferrer,别让外站碰到本页)
  document.getElementById("pumpZhonglian").addEventListener("click", () => {
    window.open(ZHONGLIAN_URL, "_blank", "noopener,noreferrer");
  });

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
  // 缩放只有滚轮和「适应窗口」两个入口:+/- 按钮已去掉(有滚轮时纯占位)
  document.getElementById("flowFit").addEventListener("click", fitFlow);
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
    // Esc 只处理当前真正开着的东西。原来无条件 clearFlowFocus():
    // 在任何页面按 Esc 都会重画一遍流程图,还抢在弹窗自己的 Esc 之前处理。
    // 缩放按钮与快捷键:滚轮之外的第二条通道(键盘、触屏都要有)
    const zoomIn = document.getElementById("flowZoomIn");
    const zoomOut = document.getElementById("flowZoomOut");
    if (zoomIn) zoomIn.addEventListener("click", () => zoomFlow(1.2));
    if (zoomOut) zoomOut.addEventListener("click", () => zoomFlow(1 / 1.2));

    // 节点用 Tab 选中后,回车/空格等同点击
    flowSvg.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const g = e.target.closest && e.target.closest(".flow-node");
      if (!g || !g.dataset.node) return;
      e.preventDefault();
      flowSvg.focus({ preventScroll: true });
      focusFlow(g.dataset.node);
    });

    document.addEventListener("keydown", (e) => {
      const typing = e.target && e.target.closest
        && e.target.closest("input, select, textarea");
      // 流程页的缩放快捷键(在输入框里打字时不抢)
      if (state.page === "flow" && !typing) {
        if (e.key === "+" || e.key === "=") { e.preventDefault(); zoomFlow(1.2); return; }
        if (e.key === "-" || e.key === "_") { e.preventDefault(); zoomFlow(1 / 1.2); return; }
        if (e.key === "0") { e.preventDefault(); fitFlow(); return; }
      }
      if (e.key !== "Escape") return;
      if (state.page === "flow") {
        if (state.flow.focus) clearFlowFocus();
        return;
      }
      closeDataDrawers();   // 数据页的三个抽屉(塔剖面 / 泵参数 / 流股组成)
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
  initTheme();
  bindEvents();

  try {
    await refreshCases(null);
  } catch (err) {
    showEmpty(`读不到工况列表:${err.message}\n\n后台服务可能没起来,重新双击桌面快捷方式。`);
    return;
  }

  const page = readHashPage();
  await loadFamilies();
  await loadPumpSpec();

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
  // 设备页地址:#/blocks/<家族>/<设备> 直接定位;#/blocks/<设备> 是老写法,只认塔剖面
  if (page === "blocks" && hashParts.length >= 3 && hashParts[2]) {
    if (state.families.some((f) => f.key === hashParts[1])) {
      state.family = hashParts[1];
      renderTable();
    }
    openColumn(hashParts[2]);
  } else if (page === "blocks" && hashParts[1]) {
    openColumn(hashParts[1]);
  }

  await resumeReadIfRunning();
}

init();
