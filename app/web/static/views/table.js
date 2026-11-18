/* Casebook 视图 · table

数据页表格：流股表 / 设备表 / 对比表 / 家族页签 / 泵列与 p_v 存盘。

读写 state：streams、blocks、tab、family、families、compare、impact、
pumpSpec、filter；只读 current / cases。跨页刷新统一走
renderTable() / scheduleTableRender()。

依赖由 import 明说；本文件不含内联脚本与全局隐式耦合。
*/

import { closeDataDrawers } from "./pump.js";
import { hideEmpty, showEmpty } from "./shell.js";
import { api, esc, fmt, fmtDelta, fmtVisc, writeInit } from "../lib/core.js";
import { state } from "../lib/store.js";
import { badge, blockBadge, renderBanner, withBusy } from "../lib/ui.js";


export const COMPARE_FIELDS = [
  { key: "temperature_c", label: "温度 ℃", digits: 2 },
  { key: "pressure_bar", label: "压力 bar", digits: 3 },
  { key: "mass_flow_kgh", label: "流量 kg/h", digits: 1 },
  { key: "vap_frac", label: "汽化率", digits: 3 },
];

/* 设备对比字段。⚠️ 必须与 server.py 的 block_fields 一致:
   接口加了而这里不加,界面就看不到;接口不加而这里加,值永远是空。 */
export const BLOCK_COMPARE_FIELDS = [
  { key: "duty_kw", label: "热负荷 kW", digits: 2 },
  { key: "power_kw", label: "功率 kW", digits: 2 },
  { key: "nstage", label: "级数", digits: 0 },
  { key: "top_temp_c", label: "顶温 ℃", digits: 1 },
  { key: "bottom_temp_c", label: "底温 ℃", digits: 1 },
  { key: "dh_kw", label: "ΔH kW", digits: 2 },
  { key: "head_m", label: "扬程 m", digits: 2 },
  { key: "flow_m3h", label: "流量 m³/h", digits: 2 },
];

export const COLUMN_TYPE = /RadFrac|Absorber/;

/* 设备页按家族出列:同一张表,列随家族变。
   目的很直接:泵页不该出现"级数""热负荷",塔页也不该出现"功率"。
   键必须与 app/design/block_family.py 的 FAMILIES 一致。 */
export const BLOCK_COLS = {
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
  // 粘度:进口流股 MUMX(混合物粘度),泵设计里算 Re 与 Σhf 用;模型没挂 MUMX 时为空
  visc:    { key: "mu_cp", label: "粘度 cP", num: true, visc: true },
};

/* 塔页保持原样(用户对塔页满意):只去掉"类型"和"功率"两个恒定/无关列 */
export const FAMILY_COLS = {
  // 塔:只有级数与顶/底温是逐塔独有的;热负荷(塔是冷凝+再沸两块)、面积、ΔH 在塔上是恒定空列
  column: ["name", "status", "nstage", "top", "bottom", "cond", "reb", "message"],
  // 换热:面积=HX_AREAC/AREAC2/AREAP;冷/热侧进出口温度来自连接流股
  heat:   ["name", "type", "status", "duty", "area", "inT", "outT", "coldIn", "coldOut",
           "dh", "ref", "balance", "note", "message"],
  // 分离:出口温度 + 出口汽化率(Flash/Decanter 最该看的量)
  flash:  ["name", "type", "status", "duty", "outT", "vapOut", "dh", "ref", "balance", "message"],
  react:  ["name", "type", "status", "duty", "outT", "message"],
  // 泵页:流量/扬程/效率/轴功率/建议电机/粘度/p_v/NPSHₐ;级数和热负荷在这里没有意义
  // 注意 head 列显示的是"压升扬程"(Aspen HEAD_CAL),不含位压头与管路阻力
  pump:   ["name", "status", "flow", "head", "eff", "power", "motor", "visc", "pv", "npsh", "message"],
  // 涡轮机:只关心流量/扬程/回收功率;NPSHₐ、选型电机对透平没有意义
  turbine: ["name", "type", "status", "flow", "head", "power", "message"],
  move:   ["name", "type", "status", "power", "inT", "outT", "dh", "ref", "balance", "message"],
  mix:    ["name", "type", "status", "outT", "message"],
  other:  ["name", "type", "status", "message"],
};

/* /api/block-families 不通时的兜底:只有显示名。
   分页签靠后端下发的 row.family,所以兜底不影响归类,只影响页签文字与顺序。 */
export const FAMILY_FALLBACK = [
  { key: "column", label: "塔" }, { key: "heat", label: "换热" },
  { key: "flash", label: "分离" }, { key: "react", label: "反应" },
  { key: "pump", label: "泵" }, { key: "turbine", label: "涡轮机" },
  { key: "move", label: "流体输送" },
  { key: "mix", label: "混合分流" }, { key: "other", label: "其他" },
];

/* ------------------------------------------------------------ 工具 */


/* ------------------------------------------------------------ 表格 */

/* ------------------------------------------------------------ 设备家族 */

export function blockFamilyOf(row) {
  return row.family || "other";
}

export function familyCounts() {
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
export function ensureFamily() {
  const seen = familyCounts();
  if (state.family && seen.get(state.family)) return;
  const first = state.families.find((f) => seen.get(f.key));
  state.family = seen.get("column") ? "column" : (first ? first.key : "column");
}

/* 换设备类型 = 换了一批设备,旧的详情(塔剖面/泵参数/组成)不再对应任何一行。
   不收起来的话,切到换热页还挂着塔的剖面,看着像"这些数属于换热器"。 */
export function setFamily(key) {
  state.family = key;
  closeDataDrawers();
  state.column = null;
  renderTable();
  resetTableScroll();
}

export function renderFamilyBar() {
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

export function blockColumns() {
  const keys = FAMILY_COLS[state.family] || FAMILY_COLS.other;
  return keys.map((k) => BLOCK_COLS[k]).filter(Boolean);
}

export async function loadFamilies() {
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
export async function loadPumpSpec() {
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
export function pumpNpsh(row) {
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
export function pvSourceText(row) {
  if (row.pv_bar === null || row.pv_bar === undefined) return "—";
  if (row.pv_source === "aspen") return "Aspen 泡点压力";
  if (row.pv_source === "manual") return "手填";
  return "老工况未标注";
}

/* 为什么这格是「—」:直接写在悬浮提示里,别让人猜 */
export function npshWhy(row) {
  if (!state.pumpSpec) return "泵口径常量没取到(后端可能是旧版本)";
  const vap = row.pump_vap_frac;
  if (vap !== null && vap !== undefined && vap > 0.001) return "进口是两相,不计算汽蚀余量";
  if (row.pv_bar === null || row.pv_bar === undefined) {
    return "缺 p_v:模型没给出泡点压力 —— 物性组新建 CBPROP → 加 PL、PBUB(bar)与 MUMX(cP)→ 报告选项→流股→物性组 选中 CBPROP → 重置并运行,再重读工况;也可直接在这格手填";
  }
  return "缺进口压力或密度";
}

/* p_v 逐台保存:输入停下来 0.5 s 再发,避免边打字边打接口 */
export const pvTimers = new Map();

export function schedulePvSave(block, text) {
  clearTimeout(pvTimers.get(block));
  pvTimers.set(block, setTimeout(() => savePv(block, text), 500));
}

export async function savePv(block, text) {
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
export function updateNpshCell(block) {
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
export async function inheritPv() {
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

export function renderPumpBar() {
  const bar = document.getElementById("pumpBar");
  if (!bar) return;
  const show = state.tab === "blocks" && state.family === "pump";
  bar.classList.toggle("hidden", !show);
  if (!show) closePvHelp();
}

/* 「?」指南收起来(切页 / 切家族时也要收,不然回来它还开着) */
export function closePvHelp() {
  const help = document.getElementById("pvHelp");
  const body = document.getElementById("pvHelpBody");
  const btn = document.getElementById("pvHelpBtn");
  if (body) body.classList.remove("show");
  if (btn) btn.setAttribute("aria-expanded", "false");
  if (help) delete help.dataset.pinned;
}

/* 参数条只改"显示",不写库:余量乘到流量/扬程上 */
export function readPumpBar() {
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
  scheduleTableRender();   // 自定义余量是逐键输入的,合并到一帧
}

export function rowsForTab() {
  let rows = state.tab === "streams" ? state.streams : state.blocks;
  // 设备页按家族筛;名称搜索只对流股页生效 —— 否则在流股页打过的关键词,
  // 切到设备页会"看不见地"把行筛掉。
  if (state.tab === "blocks") rows = rows.filter((r) => blockFamilyOf(r) === state.family);
  const q = state.filter.trim().toLowerCase();
  if (q) rows = rows.filter((r) => String(r.name).toLowerCase().includes(q));

  if (state.tab === "blocks") {
    const rank = (s) => (s === 1 ? 0 : s === 2 ? 1 : 2);
    rows = [...rows].sort((a, b) => rank(a.status) - rank(b.status)
      || String(a.name).localeCompare(String(b.name)));
  }
  return rows;
}

export function renderStreams() {
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
  thead.innerHTML = `<tr>${cols.map((c) => `<th scope="col" class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("")}</tr>`;
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

  // 流股名的点击统一由 #table tbody 上的事件委托处理(见 bindEvents),
  // 每次重绘都重绑的话,120 行就是 120 个监听器
  let count = `${rows.length} 行`;
  const sm = state.impact && state.impact.summary;
  if (sm && sm.error) count += ` · ${sm.error} 条受报错影响`;
  else if (sm && sm.warning) count += ` · ${sm.warning} 条仅告警`;
  document.getElementById("count").textContent = count;
}

export function renderBlocks() {
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
    return `<th scope="col" class="${c.num ? "num" : ""}"${tip ? ` title="${esc(tip)}"` : ""}>${esc(label)}</th>`;
  }).join("")}</tr>`;
  tbody.innerHTML = rows.map((row) => {
    const isColumn = COLUMN_TYPE.test(row.type || "");
    // 用后端下发的 family 判,不只看 type:位号 K 开头的透平 type 也是 "Pump",
    // 但它在「涡轮机」页签里,不该弹泵参数卡
    const isPump = row.family === "pump";
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
      if (c.visc) {
        const v = row[c.key];
        const why = v === null || v === undefined
          ? "模型没给出进口流股粘度:物性组加 MUMX(cP)并重跑,再重读工况"
          : "进口流股 MUMX(混合物粘度);泵设计里算 Re 与 Σhf 用它";
        return `<td class="num" title="${esc(why)}">${fmtVisc(v)}</td>`;
      }
      if (c.pvInput) {
        const pv = row[c.key];
        // 显示保留 3 位(与参数卡一致),避免 1.04824999… 这种长尾把格子撑爆
        const val = pv === null || pv === undefined ? "" : fmt(pv, 3);
        return `<td class="pv-cell"><input class="pv-input" type="number" step="0.001" min="0"
                   value="${val}" data-pv="${esc(row.name)}"
                   title="饱和蒸气压(bar):读取工况时自动取 Aspen 进口流股的泡点压力;这里填值会覆盖它。模型读不到时:物性组新建 CBPROP → 加 PL、PBUB(bar)与 MUMX(cP)→ 报告选项→流股→物性组 选中 CBPROP → 重置并运行,再重读工况"></td>`;
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

  // 塔名 / 泵名的点击同样走 #table tbody 上的事件委托(见 bindEvents)
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

export function balanceBadge(bstate) {
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
export function impactMap() {
  const map = new Map();
  const rows = (state.impact && state.impact.streams) || [];
  rows.forEach((s) => map.set(s.stream, s));
  return map;
}

/* "受影响"那一格的徽标:点了直接跳到流程页并聚焦到出问题的设备 */
export function impactChip(item) {
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

export function renderCompare() {
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
    `<th scope="col" class="${i === 0 ? "" : "num"}">${esc(h)}</th>`).join("")}</tr>`;

  // 对比数据是接口给的;缺字段时只显示「—」,不抛 TypeError 让整块表格空白
  const q = String(state.filter || "").trim().toLowerCase();
  const rows = (data.streams || []).filter(
    (r) => !q || String(r.name || "").toLowerCase().includes(q));

  tbody.innerHTML = rows.map((r) => {
    const cells = [`<td class="name">${esc(r.name)}</td>`];
    const delta = r.delta || {};
    COMPARE_FIELDS.forEach((f) => {
      const a = r.base ? r.base[f.key] : null;
      const b = r.other ? r.other[f.key] : null;
      const d = delta[f.key] ?? null;
      const cls = d === null || d === undefined || d === 0 ? "" : "changed";
      cells.push(`<td class="num">${fmt(a, f.digits)}</td>`);
      cells.push(`<td class="num">${fmt(b, f.digits)}</td>`);
      cells.push(`<td class="num ${cls}">${fmtDelta(d, f.digits)}</td>`);
    });
    return `<tr>${cells.join("")}</tr>`;
  }).join("");

  const s = data.summary || {};
  const onlyA = (s.only_base || []).length;
  const onlyB = (s.only_other || []).length;
  document.getElementById("count").textContent =
    `${rows.length} 行 · 共同 ${s.common ?? "—"} · 有变化 ${s.changed ?? "—"}`
    + (onlyA ? ` · 仅 A ${onlyA}` : "")
    + (onlyB ? ` · 仅 B ${onlyB}` : "");
}

/* 设备对比:负荷/功率/级数/顶底温/ΔH 各出一组 A/B/Δ;状态与焓平衡各一列(变了就显示 A → B) */
export function renderCompareBlocks(thead, tbody, data) {
  const head = ["设备", "类型", "状态", "焓平衡"];
  BLOCK_COMPARE_FIELDS.forEach((f) => head.push(`${f.label} A`, `${f.label} B`, "Δ"));
  thead.innerHTML = `<tr>${head.map((h, i) =>
    `<th scope="col" class="${i === 0 ? "cmp-name" : i < 4 ? "" : "num"}">${esc(h)}</th>`).join("")}</tr>`;

  const q = String(state.filter || "").trim().toLowerCase();
  const rows = (data.blocks || []).filter(
    (r) => !q || String(r.name || "").toLowerCase().includes(q));

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
    const delta = r.delta || {};
    BLOCK_COMPARE_FIELDS.forEach((f) => {
      const a = r.base ? r.base[f.key] : null;
      const b = r.other ? r.other[f.key] : null;
      const d = delta[f.key] ?? null;
      const cls = d === null || d === undefined || d === 0 ? "" : "changed";
      cells.push(`<td class="num">${fmt(a, f.digits)}</td>`);
      cells.push(`<td class="num">${fmt(b, f.digits)}</td>`);
      cells.push(`<td class="num ${cls}">${fmtDelta(d, f.digits)}</td>`);
    });
    return `<tr>${cells.join("")}</tr>`;
  }).join("");

  const s = data.summary || {};
  const onlyA = (s.block_only_base || []).length;
  const onlyB = (s.block_only_other || []).length;
  document.getElementById("count").textContent =
    `${rows.length} 行 · 共同 ${s.block_common ?? "—"} · 有变化 ${s.block_changed ?? "—"}`
    + (onlyA ? ` · 仅 A ${onlyA}` : "")
    + (onlyB ? ` · 仅 B ${onlyB}` : "");
}

export function renderTable() {
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

/* 一帧只重画一次:筛选框与余量输入都是逐键触发的,直接 renderTable() 的话
   打一个 5 字的词要重绘 5 遍(120 行 × 11 列 ≈ 1300 个单元格)。
   合并到下一帧后,连续按键只留下最后状态,与"看得见的延迟"没有区别。 */
export let tableRenderFrame = 0;
export function scheduleTableRender() {
  if (tableRenderFrame) return;
  tableRenderFrame = requestAnimationFrame(() => {
    tableRenderFrame = 0;
    renderTable();
  });
}

/* 表画完再决定 #empty 露不露。
   原来这里无条件 add("hidden"),把「还没有工况」的引导也一起藏了 ——
   空库点进流股页只剩一张只有表头、写着「0 行」的白表,没有任何下一步。 */
export function syncTableEmpty() {
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
export function showTableHint(text) {
  const el = document.getElementById("empty");
  if (!el) return;
  state.emptyText = "";
  state.emptyIsError = false;
  el.textContent = text;
  el.classList.remove("hidden");
}


/* ------------------------------------------------------------ 对比 */

export function fillCompareSelects() {
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

export async function runCompare() {
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
export function updateCompareSummary() {
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

export function setCompareScope(scope) {
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
export function resetTableScroll() {
  const wrap = document.getElementById("tableWrap");
  if (wrap) wrap.scrollTop = 0;
}

export function setTab(tab) {
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
export function syncToolbar(tab) {
  const isCompare = tab === "compare";
  const isBlocks = tab === "blocks";
  document.getElementById("compareHint").classList.toggle("hidden", isCompare);
  document.getElementById("compareControls").classList.toggle("hidden", !isCompare);
  document.getElementById("compareScope").classList.toggle("hidden", !isCompare);
  // 设备页也放开搜索框(2026-11-15):79 台泵里找一台,不放开只能翻页
  document.getElementById("filter").classList.toggle("hidden", isCompare);
  document.getElementById("blockFamilies").classList.toggle("hidden", !isBlocks);
  document.getElementById("count").classList.toggle("hidden", isCompare);
  // 设备页右上角「导出 Excel」:只导当前分类;没有工况时禁用
  const exportBtn = document.getElementById("exportFamily");
  if (exportBtn) {
    exportBtn.classList.toggle("hidden", !isBlocks);
    exportBtn.disabled = !state.current;
  }
}
