/* Casebook 视图 · pump

泵参数抽屉：参数卡、设计扬程（ΔZ / Σhf / λ）、电机选型与提示词。

读写 state：pump、pumpDesign、pumpSelection、pumpFold、pumpSpec；只读 current。
抽屉开合与焦点管理也在这里（表格页与组成页共用）。

依赖由 import 明说；本文件不含内联脚本与全局隐式耦合。
*/

import { npshWhy, pumpNpsh, pvSourceText } from "./table.js";
import { api, apiPost, esc, fmt, fmtVisc } from "../lib/core.js";
import { state } from "../lib/store.js";
import { reportNotice } from "./report.js";


/* 点抽屉外面的空白处关掉详情。
   判定顺序:抽屉内 → 不动;可交互元素 → 不动(那是要干别的事);
   另一台设备的入口 → 由它自己"换人",这里也不管。
   两个坑:打开抽屉的那一次点击会冒泡到 document(用时间戳跳过);
   拖选文字时的 click 不该收面板(看选区是否为空)。 */
export const DRAWER_CLICKABLE = "a, button, input, select, textarea, label, thead, .btn, .icon-btn, .chart-svg";
export let drawerOpenedAt = 0;

export function markDrawerOpened() {
  drawerOpenedAt = Date.now();
}

export function isOutsideDrawerClick(evt) {
  if (Date.now() - drawerOpenedAt < 120) return false;      // 就是刚打开它的那一下
  const sel = window.getSelection && window.getSelection();
  if (sel && !sel.isCollapsed) return false;                // 正在选字
  const t = evt.target;
  if (!t || !t.closest) return false;
  if (t.closest(".column-panel")) return false;             // 抽屉内部
  if (t.closest(DRAWER_CLICKABLE)) return false;            // 按钮/链接/输入框/表头/图表
  return true;
}

/* Esc 关抽屉:三个抽屉都有 ✕ 按钮,键盘用户也该能关。
   restore=true 时把焦点还给打开它的那一行(屏幕阅读器/键盘用户才回得到原位);
   换页、换页签、点空白这些"用户已经去了别处"的收法不还,否则焦点会被抢回来。 */
export function closeDataDrawers({ restore = false } = {}) {
  let closed = false;
  ["columnPanel", "pumpPanel", "compPanel"].forEach((id) => {
    const el = document.getElementById(id);
    if (el && !el.classList.contains("hidden")) {
      el.classList.add("hidden");
      closed = true;
    }
  });
  if (closed && restore) restoreDrawerFocus();
  return closed;
}

/* 抽屉的焦点接管 —— 与 pipe.js 的 focusIntoModal / restoreModalFocus 同一口径。
   抽屉不是 modal(点外面能关),所以不做焦点陷阱,但必须:
   打开时把焦点移进去(否则 Tab 还在背后的表格里走),关闭时还回触发它的那一行。 */
export const drawerFocus = { trigger: null };

export function focusIntoDrawer(panel) {
  if (!panel) return;
  const active = document.activeElement;
  // 从另一个抽屉跳过来时不覆盖触发点:还回去的应该是最初那一行
  if (!active || !active.closest || !active.closest(".column-panel")) {
    drawerFocus.trigger = active;
  }
  panel.focus({ preventScroll: true });
}

export function restoreDrawerFocus() {
  const t = drawerFocus.trigger;
  drawerFocus.trigger = null;
  if (!t || !document.contains(t)) return;
  if (t.closest && t.closest(".column-panel")) return;   // 触发点自己在抽屉里,别还
  if (typeof t.focus === "function") t.focus({ preventScroll: true });
}

/* ------------------------------------------------------------ 泵参数卡 */

/* 中联水泵选型页。**不带任何参数** —— 工艺参数不进厂商日志和浏览器历史,
   流量/扬程由用户自己敲进去。 */
export const ZHONGLIAN_URL = "https://www.cipump.com/sbxx.html";

/* 把中联表单要的三个数合成一行:粘贴比照抄不容易错。
   数值用**显示值**(含余量系数),和参数卡上看到的一致。 */
export function pumpSpecLine(row) {
  const margin = state.pump.margin || 1;
  const scaled = (v, d) => (v === null || v === undefined ? "—" : fmt(v * margin, d));
  const motor = row.motor_kw === null || row.motor_kw === undefined ? "—" : fmt(row.motor_kw, 1);
  return `流量 ${scaled(row.flow_m3h, 2)} m³/h · 扬程 ${scaled(row.head_m, 2)} m · 配套功率 ${motor} kW`;
}

/* 剪贴板 API 需要安全上下文,127.0.0.1 算安全上下文;失败时退回 textarea 方案 */
export async function copyPlainText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (err) {
    const box = document.createElement("textarea");
    box.value = text;
    box.style.position = "fixed";
    box.style.opacity = "0";
    document.body.appendChild(box);
    box.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (e) {
      ok = false;
    }
    document.body.removeChild(box);
    return ok;
  }
}

/* 选泵提示词的正文由后端下发,JS 里不留第二份:这段字含 $…$、|、× 与反引号,
   内联进模板字符串要反复转义,且必然与导出的「选泵提示词」工作表漂移。
   首次点击才请求,之后走 state 缓存。 */
export async function fetchPumpPromptText() {
  if (state.pump.promptText) return state.pump.promptText;
  const res = await fetch("/api/pump-prompt");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  const text = typeof data.text === "string" ? data.text : "";
  if (!text) throw new Error("提示词为空");
  state.pump.promptText = text;
  return text;
}

/* 复制成功后的操作指引浮层。不自动消失 —— 里面的步骤要照着做 */
export function showPumpPromptTip() {
  const tip = document.getElementById("pumpPromptTip");
  if (!tip) return;
  tip.classList.remove("hidden");
  tip.focus();
}

export function closePumpPromptTip() {
  const tip = document.getElementById("pumpPromptTip");
  if (tip) tip.classList.add("hidden");
}

/* 泵参数卡:把要抄进中联表单的那几个数摆出来。余量只影响显示,不改库 */
/* 详细参数折叠头收起时的一行摘要。缺数据走 fmt 的「—」,不拿 0 顶替。 */
export function pumpParamsBrief(row) {
  return `流量 ${fmt(row.flow_m3h, 2)} m³/h · 压升扬程 ${fmt(row.head_m, 2)} m`
    + ` · 建议电机 ${fmt(row.motor_kw, 1)} kW`;
}

export function fillPumpParamsBrief(row) {
  const brief = document.getElementById("pumpParamsBrief");
  if (brief) brief.textContent = pumpParamsBrief(row);
}

/* 悬浮窗顶部的切泵下拉。只列泵,不把塔/换热器混进来;切换时走同一个 openPump,
   这样表格、设计扬程、选型台账都按新泵重读,而不是在旧状态上改个标题。 */
function fillPumpSwitch(current) {
  const sel = document.getElementById("pumpSwitch");
  if (!sel) return;
  const pumps = state.blocks
    .filter((b) => (b.family ? b.family === "pump" : b.type === "Pump"))
    .map((b) => b.name)
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b, "zh-CN"));
  if (!pumps.includes(current)) pumps.unshift(current);
  sel.innerHTML = pumps.map((name) =>
    `<option value="${esc(name)}"${name === current ? " selected" : ""}>${esc(name)}</option>`).join("");
  sel.value = current;
  sel.disabled = pumps.length < 2;
  sel.onchange = () => {
    if (sel.value && sel.value !== current) openPump(sel.value);
  };
}

export function openPump(block) {
  const row = state.blocks.find((b) => b.name === block);
  if (!row) return;
  ["columnPanel", "compPanel"].forEach((id) =>
    document.getElementById(id).classList.add("hidden"));
  const panel = document.getElementById("pumpPanel");
  document.getElementById("pumpTitle").textContent = `泵参数 · ${block}`;
  fillPumpSwitch(block);
  panel.classList.remove("hidden");
  markDrawerOpened();
  focusIntoDrawer(panel);
  // 悬浮窗左右两列各自滚动,回到顶部而不是找已经不存在的整条滚动容器
  panel.querySelectorAll(".pump-modal-col").forEach((col) => { col.scrollTop = 0; });

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
    // 名称写明"压升":Aspen 的 HEAD_CAL 只等于 Δp/(ρg),不含位压头与管路阻力。
    // 实测(2026-11-15)两例:head_m 与 Δp/(ρg) 差 0.0015 m / 0.143 m,就是纯压升那一项。
    kv("压升扬程 m", fmt(scaled(row.head_m), 2), `Aspen${marginTag}`,
       row.head_m == null
         ? "没读到"
         : `原值 ${fmt(row.head_m, 2)};这只是 Δp/(ρg),不含位压头与管路阻力。`
           + "设计扬程 = 本值 + ΔZ + Σhf,见下方「设计扬程」"),
    kv("轴功率 kW", fmt(row.power_kw, 2), "Aspen", "BRAKE_POWER,反向工况为负"),
    kv("建议电机 kW", fmt(row.motor_kw, 1), "计算", spec ? spec.motor_rule : ""),
    kv("效率 %", fmt(row.pump_eff == null ? null : row.pump_eff * 100, 1), "Aspen", "CEFF"),
    kv("进口压力 bar", fmt(row.p_in_bar, 3), "进口流股"),
    kv("出口压力 bar", fmt(row.p_out_bar, 3), "出口流股"),
    kv("压升 bar", fmt(row.dp_bar, 3), "Aspen", "IN_PRES"),
    kv("密度 kg/m³", fmt(row.rho_kgm3, 1), "计算", "进口流股 m/V;两相或缺流量时用 RHO_LIQ×MW_LIQ"),
    kv("粘度 cP", fmtVisc(row.mu_cp), "进口流股",
       row.mu_cp == null
         ? "模型没给出粘度:物性组加 MUMX(cP)并重跑,再重读工况;下方「管路阻力」也可手填"
         : "MUMX(混合物粘度);泵设计里算 Re 与 Σhf 用它"),
    kv("进口汽化率", fmt(row.pump_vap_frac, 4), "进口流股", "> 0 视为两相,不给 NPSHₐ"),
    kv("p_v bar", row.pv_bar == null ? "—" : fmt(row.pv_bar, 3), pvSourceText(row),
       row.pv_bar == null
         ? "模型没给出泡点压力:物性组新建 CBPROP → 加 PL、PBUB(bar)与 MUMX(cP)→ 报告选项→流股→物性组 选中 CBPROP → 重置并运行,再重读工况;详见 docs/pump-selection-plan.md §3.6"
         : "单相液相时泡点压力就是蒸气压"),
    kv("NPSHₐ m", fmt(npsh, 2), "计算", npsh == null ? npshWhy(row) : (spec ? spec.npsh_rule : "")),
  ];
  document.querySelector("#pumpTable thead").innerHTML =
    '<tr><th scope="col">参数</th><th scope="col" class="num">值</th><th scope="col">来源</th></tr>';
  document.querySelector("#pumpTable tbody").innerHTML = rows.join("");
  fillPumpParamsBrief(row);

  const copyBtn = document.getElementById("copyPumpSpec");
  if (copyBtn) {
    copyBtn.textContent = "复制选型参数";
    copyBtn.onclick = async () => {
      const ok = await copyPlainText(pumpSpecLine(row));
      copyBtn.textContent = ok ? "已复制" : "复制失败,请手动选中";
      setTimeout(() => {
        copyBtn.textContent = "复制选型参数";
      }, 700);
    };
  }

  loadPumpDesign(row);
  loadPumpSelection(row);
}

/* ------------------------------------------------------------ 设计扬程辅助

   Aspen 的泵扬程只有压升那一项。真实选型还要加:
     ΔZ  位压头(进出口标高差)—— 几何信息,Aspen 里没有
     Σhf 管路阻力 —— 要管长/管件/λ,属于水力计算
   两项都只能人工给,工具只做加法与 1.1 取整,不估算(项目一贯口径)。 */
export const PUMP_DESIGN_MARGIN = 1.1; // 手册惯例;两处设计算例都用 1.1
export const LAMBDA_LABELS = { colebrook: "Colebrook", gu: "顾毓珍", manual: "手填 λ" };

/* 手填值统一规范:非有限数字一律 null —— 缺数据不拿 0 或常见值顶替。 */
export function pumpDesignNum(v) {
  return typeof v === "number" && isFinite(v) ? v : null;
}

/* 「设计扬程」「选型台账」都收进 <details>:收起时只留一行标题 + 结论摘要,
   泵参数卡不再被两长条输入挤满。同一会话里记住展开状态,切设备回来不用反复点开;
   注意 innerHTML 重写不会清掉 <details> 的 open 属性。 */
export function bindPumpFold(box, key) {
  if (!box) return;
  // 逐项补默认,兼容只有 design/selection 两个 key 的旧 state
  if (!state.pumpFold) state.pumpFold = {};
  if (state.pumpFold.params === undefined) state.pumpFold.params = true;
  if (state.pumpFold.design === undefined) state.pumpFold.design = true;
  if (state.pumpFold.selection === undefined) state.pumpFold.selection = true;
  if (!box.dataset.foldBound) {
    box.dataset.foldBound = "1";
    box.addEventListener("toggle", () => {
      state.pumpFold[key] = box.open;
    });
  }
  box.open = !!state.pumpFold[key];
}

/* 常用管件的局部阻力系数 ζ。数值取自设计手册的常用值(同一份算例里两种取法并存),
   只作为"数管件"的辅助,勾完仍可手改 Σζ —— 不给任何默认数量,没勾就是 0。 */
export const PUMP_FITTINGS = [
  { key: "el90", label: "90° 弯头", zeta: 0.75 },
  { key: "gate", label: "闸阀", zeta: 0.9 },
  { key: "globe", label: "截止阀", zeta: 9.5 },
  { key: "check", label: "止回阀", zeta: 2.0 },
  { key: "reducer", label: "变径管", zeta: 0.04 },
];

export function fittingsZeta(side) {
  const key = side || state.pumpDesign.openSide || "suction";
  const picks = (state.pumpDesign.edits && state.pumpDesign.edits[key]
    && state.pumpDesign.edits[key].fittings) || {};
  return PUMP_FITTINGS.reduce((sum, f) => sum + (picks[f.key] || 0) * f.zeta, 0);
}

/* 把勾出来的管件合计算进 Σζ(只写当前展开那一侧,不覆盖另一侧) */
export function applyFittings(side) {
  const key = side || state.pumpDesign.openSide || "suction";
  const total = fittingsZeta(key);
  const ed = editOf(key);
  ed.zeta_sum = total > 0 ? Math.round(total * 100) / 100 : null;
  const zetaInput = document.querySelector(`[data-side="${key}"][data-key="zeta_sum"]`);
  if (zetaInput) zetaInput.value = ed.zeta_sum === null ? "" : ed.zeta_sum;
}

export const LINE_DN_LADDER = [
  15, 20, 25, 32, 40, 50, 65, 80, 100, 125, 150, 200, 250, 300, 350, 400, 450, 500,
  600, 700, 800, 900, 1000,
];

function lineOf(side) {
  return (state.pumpDesign.view || {})[side] || null;
}

function editOf(side) {
  const d = state.pumpDesign;
  if (!d.edits) d.edits = {};
  if (!d.edits[side]) {
    const line = lineOf(side) || {};
    const run = line.run || {};
    d.edits[side] = {
      l_m: pumpDesignNum(run.l_m),
      zeta_sum: pumpDesignNum(run.zeta_sum ?? line.zeta_sum),
      dz_m: pumpDesignNum(run.dz_m),
      mu_cp: pumpDesignNum(run.mu_cp),
      fittings: { ...(run.fittings || {}) },
      lambda_method: run.lambda_method || "colebrook",
      lambda_manual: pumpDesignNum(run.lambda_manual),
      dn_override: run.dn_override || null,
      custom: !!(run.od_override_mm && run.wall_override_mm),
      od_override_mm: pumpDesignNum(run.od_override_mm),
      wall_override_mm: pumpDesignNum(run.wall_override_mm),
    };
  }
  return d.edits[side];
}

function lineStatusText(line) {
  if (!line) return "未载入";
  return {
    linked: "已关联",
    manual: "人工 DN",
    unset: "待填 L / Σζ",
    unlinkable: "无法关联",
    missing: "缺这一侧",
    ambiguous: "待指定流股",
  }[line.status] || line.status;
}

function editFittingsZeta(side) {
  const e = editOf(side);
  return PUMP_FITTINGS.reduce(
    (sum, f) => sum + (Number(e.fittings && e.fittings[f.key]) || 0) * f.zeta,
    0,
  );
}

export function pumpDesignTotal(row) {
  const view = state.pumpDesign.view;
  const head = view ? pumpDesignNum(view.totals && view.totals.head_m) : pumpDesignNum(row.head_m);
  const total = view ? pumpDesignNum(view.totals && view.totals.design_head_m) : null;
  if (head === null || total === null) return null;
  return total;
}

/* 只刷新结论行:输入时不重画表格,避免光标被踢走 */
export function refreshPumpDesignSum(row) {
  const el = document.getElementById("pumpDesignSum");
  if (!el) return;
  const view = state.pumpDesign.view;
  if (!view) {
    el.textContent = "管路记录载入中…";
    return;
  }
  const totals = view.totals || {};
  const npsh = view.npsh || {};
  const head = pumpDesignNum(totals.head_m) ?? pumpDesignNum(row.head_m);
  const npshText = npsh.corrected_m == null
    ? `<span class="muted">NPSHₐ(修正) 待吸入段</span>`
    : `NPSHₐ(修正) <b>${fmt(npsh.corrected_m, 2)} m</b>`
      + ` <span class="pump-sel-state-mini" data-state="${esc(npsh.state || "unknown")}">${
        { ok: "满足", bad: "不满足", unknown: "无法判定" }[npsh.state] || "无法判定"
      }</span>`;
  if (head === null) {
    el.textContent = "压升扬程没读到,算不了";
    return;
  }
  if (totals.design_head_m == null) {
    el.innerHTML =
      `${fmt(head, 2)} m + 已计阻力 ${fmt(totals.partial_hf_m, 2)} m`
      + ` + 已计 ΔZ ${fmt(totals.partial_dz_m, 2)} m · <span class="muted">两侧未齐,设计扬程不给</span>`;
    return;
  }
  el.innerHTML =
    `${fmt(head, 2)} + ΔZ ${fmt(totals.dz_m, 2)} + Σhf ${fmt(totals.hf_m, 2)}`
    + ` = <b>${fmt(totals.design_head_m, 2)} m</b> · ×${PUMP_DESIGN_MARGIN} 取整 `
    + `<b>${fmt(totals.selection_head_m, 0)} m</b><br>${npshText}`;
}

function lineInput(side, key, value, ph, label) {
  return `<input class="pump-design-input pump-line-input" data-side="${side}"
    data-key="${key}" inputmode="decimal" placeholder="${ph}"
    value="${value === null || value === undefined ? "" : value}" aria-label="${esc(label)}">`;
}

function renderLineRow(side, label) {
  const line = lineOf(side);
  if (!line) {
    return `<tr><td>${label}</td><td colspan="9" class="muted">管路记录载入中…</td></tr>`;
  }
  if (line.status === "missing") {
    return `<tr><td>${label}</td><td colspan="9" class="muted">${esc(line.reason || "泵没有这一侧流股")}</td></tr>`;
  }
  if (line.status === "ambiguous") {
    const opts = (line.streams || []).map((s) =>
      `<option value="${esc(s)}">${esc(s)}</option>`).join("");
    return `<tr><td>${label}</td><td colspan="9">
      <span class="muted">${esc(line.reason)}</span>
      <select class="pump-design-select pump-pick" data-side="${side}" aria-label="指定${label}流股">
        <option value="">请选择</option>${opts}
      </select></td></tr>`;
  }
  const e = editOf(side);
  const dn = line.dn == null ? "—" : `DN${line.dn}`;
  const manual = line.dn_source === "manual" || line.dn_source === "custom";
  return `<tr data-line-side="${side}">
    <td>${label}</td>
    <td><a href="#" class="pump-line-jump" data-stream="${esc(line.stream)}"
        title="到「管道」页看这条管子的选型">${esc(line.stream)}</a></td>
    <td class="num">${dn}${manual ? ' <span class="badge warn">人工</span>' : ""}</td>
    <td class="num">${lineInput(side, "l_m", e.l_m, "未填", "管长 L")}</td>
    <td class="num">${lineInput(side, "zeta_sum", e.zeta_sum, "未填", "Σζ")}</td>
    <td class="num" title="${esc(line.mu_source === "manual" ? "手填覆盖值" : "进口流股 MUMX(混合物粘度),自动取值")}">${
      fmtVisc(line.mu_cp)}</td>    <td class="num">${lineInput(side, "dz_m", e.dz_m, "如 −1.5", "ΔZ")}</td>
    <td class="num">${line.hf_m == null
      ? `<span class="muted" title="缺 ${esc((line.missing || []).join("、"))}">—</span>`
      : `<span title="u=${fmt(line.u_ms, 2)} m/s · Re=${fmt(line.re, 0)} · λ=${fmt(line.lam, 4)}">${
        fmt(line.hf_m, 3)}</span>`}</td>
    <td class="pump-line-state">
      <span class="badge ${line.status === "linked" ? "ok" : "warn"}"
            title="${esc((line.reason || (line.missing || []).join("、")))}">${
        line.status === "linked"
          ? "已关联"
          : ((line.missing || []).length ? `缺 ${(line.missing || []).length} 项` : lineStatusText(line))
      }</span>
      <button class="link-btn pump-line-more" data-side="${side}"
              title="设置常用管件与管径">编辑</button></td>
  </tr>`;
}

function renderLineDetail(side, label) {
  const line = lineOf(side);
  if (!line) return "";
  const e = editOf(side);
  const dnOpts = ["", ...LINE_DN_LADDER].map((dn) => {
    const v = dn === "" ? "" : String(dn);
    const sel = e.dn_override === null || e.dn_override === undefined
      ? (dn === "" ? " selected" : "")
      : (String(e.dn_override) === v ? " selected" : "");
    return `<option value="${v}"${sel}>${dn === "" ? "自动(跟随管道页)" : `DN${dn}`}</option>`;
  }).join("");
  const customOpt = `<option value="custom"${e.custom ? " selected" : ""}>自定义外径 / 壁厚</option>`;
  const muShown = e.mu_cp === null || e.mu_cp === undefined
    ? (line.mu_cp == null ? "" : fmtVisc(line.mu_cp))
    : e.mu_cp;
  return `<div class="pump-line-detail" data-detail="${side}">
    <p class="pump-line-current">当前修改:<b>${label}段</b> · 流股 ${esc(line.stream)}</p>
    <div class="pump-design-inputs">
      <label>管径
        <select class="pump-design-select pump-line-dn" data-side="${side}" aria-label="管径">
          ${dnOpts}${customOpt}
        </select>
      </label>
      ${e.custom ? `
        <label>外径 mm ${lineInput(side, "od_override_mm", e.od_override_mm, "如 168.3", "外径")}</label>
        <label>壁厚 mm ${lineInput(side, "wall_override_mm", e.wall_override_mm, "如 7.1", "壁厚")}</label>`
      : ""}
      <label>粘度 cP ${lineInput(
        side,
        "mu_cp",
        muShown,
        line.mu_cp == null ? "自动" : "",
        "粘度",
      )}</label>
      <label>λ 口径
        <select class="pump-design-select pump-line-lambda" data-side="${side}" aria-label="λ 口径">
          <option value="colebrook"${e.lambda_method === "colebrook" ? " selected" : ""}>Colebrook</option>
          <option value="gu"${e.lambda_method === "gu" ? " selected" : ""}>顾毓珍</option>
          <option value="manual"${e.lambda_method === "manual" ? " selected" : ""}>手填</option>
        </select>
      </label>
      ${e.lambda_method === "manual"
        ? `<label>λ 值 ${lineInput(side, "lambda_manual", e.lambda_manual, "如 0.028", "λ")}</label>`
        : ""}
    </div>
    <div class="pump-design-fits">
      <span class="muted">常用管件(填数量,自动求和)</span>
      ${PUMP_FITTINGS.map((f) => {
        const n = Number(e.fittings[f.key]) || 0;
        return `<label title="ζ = ${f.zeta}">${f.label} ×${f.zeta}
          <input class="pump-fit-input" data-side="${side}" data-fit="${f.key}" type="number"
                 min="0" step="1" inputmode="numeric" value="${n}" aria-label="${f.label} 数量"></label>`;
      }).join("")}
      <span class="muted">合计 Σζ = ${fmt(editFittingsZeta(side), 2)}</span>
    </div>
    ${line.dn_source === "custom" ? '<p class="pump-design-note">自定义外径 / 壁厚按专项设计,壁厚不再自动圆整。</p>' : ""}
  </div>`;
}

export function renderPumpDesign(row) {
  const box = document.getElementById("pumpDesign");
  if (!box) return;
  const view = state.pumpDesign.view;
  const totals = (view && view.totals) || {};
  const npsh = (view && view.npsh) || {};
  const brief = view
    ? (totals.design_head_m == null
      ? `<span class="muted">两侧未齐 · 已计 Σhf ${fmt(totals.partial_hf_m, 2)} m</span>`
      : `<span class="muted">${fmt(totals.design_head_m, 2)} m → 选型 ${fmt(totals.selection_head_m, 0)} m</span>`)
    : '<span class="muted">载入中…</span>';
  const src = view && view.run_sources && Object.keys(view.run_sources).length
    ? `<p class="pump-design-note">管段记录来自同模型旧版本 #${
      esc(String(Object.values(view.run_sources)[0].case_id || ""))}（${
      esc(String(Object.values(view.run_sources)[0].read_at || "").slice(0, 16))}）；保存后写入当前工况。</p>`
    : "";
  const legacy = state.pumpDesign.legacy;
  const legacyNote = legacy && legacy.needs_choice
    ? `<p class="pump-design-note">这台泵还有旧的一组 Σhf（${fmt(legacy.hf_m, 2)} m）没归到吸入 / 排出。
        <button class="link-btn pump-legacy-open">迁移</button></p>`
    : "";
  const tablesError = state.pumpDesign.view && state.pumpDesign.view.tables_error
    ? `<p class="pump-design-note">管道选型未完成:${esc(state.pumpDesign.view.tables_error)}</p>`
    : "";
  const open = state.pumpDesign.openSide || "suction";
  box.innerHTML = `
    <div class="pump-block-head" title="两侧管段共用「管道」页的选型结果">
      <span class="pump-block-title">设计扬程 / 管路阻力</span>
      ${brief}
    </div>
    ${src}${legacyNote}${tablesError}
    <table class="pump-line-table">
      <thead><tr><th scope="col">侧</th><th scope="col">流股</th><th scope="col">DN</th>
        <th scope="col">L m</th><th scope="col">Σζ</th><th scope="col">μ cP</th><th scope="col">ΔZ m</th>
        <th scope="col">Σhf m</th><th scope="col">状态</th></tr></thead>
      <tbody>${renderLineRow("suction", "吸入")}${renderLineRow("discharge", "排出")}</tbody>
    </table>
    ${open ? renderLineDetail(open, open === "suction" ? "吸入" : "排出") : ""}
    <div class="pump-design-actions">
      <button class="link-btn" id="pumpLineSave">保存两侧管段</button>
      <span class="muted pump-design-calc">DN 与流速跟随管道页;人工选 DN 后该管段冻结</span>
    </div>
    <p class="pump-design-sum" id="pumpDesignSum"></p>`;

  box.querySelectorAll(".pump-line-input").forEach((input) => {
    input.addEventListener("input", (e) => {
      const raw = e.target.value.trim();
      const parsed = raw === "" ? null : Number(raw);
      const key = e.target.dataset.key;
      editOf(e.target.dataset.side)[key] =
        parsed !== null && Number.isFinite(parsed) ? parsed : null;
      refreshPumpDesignSum(row);
    });
  });
  box.querySelectorAll(".pump-line-more").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.pumpDesign.openSide = btn.dataset.side;
      renderPumpDesign(row);
    });
  });
  box.querySelectorAll(".pump-fit-input").forEach((input) => {
    input.addEventListener("change", (e) => {
      const side = e.target.dataset.side;
      const count = Math.floor(Number(e.target.value));
      const ed = editOf(side);
      ed.fittings[e.target.dataset.fit] = Number.isFinite(count) && count > 0 ? count : 0;
      ed.zeta_sum = Math.round(editFittingsZeta(side) * 100) / 100 || null;
      renderPumpDesign(row);
    });
  });
  box.querySelectorAll(".pump-line-lambda").forEach((sel) => {
    sel.addEventListener("change", (e) => {
      editOf(e.target.dataset.side).lambda_method = e.target.value;
      renderPumpDesign(row);
    });
  });
  box.querySelectorAll(".pump-line-dn").forEach((sel) => {
    sel.addEventListener("change", (e) => {
      const ed = editOf(e.target.dataset.side);
      if (e.target.value === "custom") {
        ed.custom = true;
        ed.dn_override = null;
      } else {
        ed.custom = false;
        ed.dn_override = e.target.value === "" ? null : Number(e.target.value);
        ed.od_override_mm = null;
        ed.wall_override_mm = null;
      }
      renderPumpDesign(row);
    });
  });
  box.querySelectorAll(".pump-pick").forEach((sel) => {
    sel.addEventListener("change", async (e) => {
      if (!e.target.value) return;
      try {
        await apiPost(
          `/api/cases/${state.current.id}/pumps/${encodeURIComponent(row.name)}/streams`,
          { [e.target.dataset.side]: e.target.value },
        );
        await loadPumpDesign(row);
        loadPumpSelection(row);
      } catch (err) {
        reportNotice(`流股没保存:${err.message}`, "warn");
      }
    });
  });
  box.querySelectorAll(".pump-line-jump").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      // 用事件解耦:pump.js 不反向 import pipe.js(两者本来就有依赖方向)
      window.dispatchEvent(new CustomEvent("casebook:pipe-jump", { detail: a.dataset.stream }));
    });
  });
  const saveBtn = document.getElementById("pumpLineSave");
  if (saveBtn) saveBtn.onclick = () => savePumpDesign(row);
  const legacyBtn = box.querySelector(".pump-legacy-open");
  if (legacyBtn) legacyBtn.onclick = () => migrateLegacy(row);
  refreshPumpDesignSum(row);
}

/* 旧单一 Σhf 只能归到一侧或登记为合计;合计不拆,修正 NPSH 判 unknown(D7)。 */
async function migrateLegacy(row) {
  const legacy = state.pumpDesign.legacy || {};
  const pick = window.prompt(
    `旧 Σhf ${fmt(legacy.hf_m, 2)} m 归到哪一侧?输入 suction / discharge / total(合计)`,
    "suction",
  );
  if (!pick) return;
  if (!["suction", "discharge", "total"].includes(pick)) {
    reportNotice("只能填 suction / discharge / total", "warn");
    return;
  }
  try {
    const res = await apiPost(
      `/api/cases/${state.current.id}/pump-lines/migrate`,
      { block: row.name, split: pick },
    );
    if (res.warnings && res.warnings.length) reportNotice(res.warnings.join(";"), "warn");
    await loadPumpDesign(row);
    loadPumpSelection(row);
  } catch (err) {
    reportNotice(`迁移失败:${err.message}`, "warn");
  }
}

/* 两侧一起存:各自带 version,冲突就整次退回,不留半新半旧。 */
export async function savePumpDesign(row) {
  const view = state.pumpDesign.view;
  if (!view) return;
  const sides = ["suction", "discharge"];
  try {
    for (const side of sides) {
      const line = view[side] || {};
      if (!line.stream) continue;
      const e = editOf(side);
      const body = {
        l_m: pumpDesignNum(e.l_m),
        zeta_sum: pumpDesignNum(e.zeta_sum),
        dz_m: pumpDesignNum(e.dz_m),
        mu_cp: pumpDesignNum(e.mu_cp),
        fittings: e.fittings || {},
        lambda_method: ["colebrook", "gu", "manual"].includes(e.lambda_method)
          ? e.lambda_method : "colebrook",
        lambda_manual: e.lambda_method === "manual" ? pumpDesignNum(e.lambda_manual) : null,
        dn_override: e.dn_override ?? null,
        od_override_mm: e.custom ? pumpDesignNum(e.od_override_mm) : null,
        wall_override_mm: e.custom ? pumpDesignNum(e.wall_override_mm) : null,
        version: line.version || 0,
      };
      await apiPost(
        `/api/cases/${state.current.id}/lines/${encodeURIComponent(line.stream)}`,
        body,
      );
    }
  } catch (err) {
    reportNotice(`管段没保存:${err.message}`, "warn");
    return;
  }
  await loadPumpDesign(row);
  loadPumpSelection(row);
}

export async function loadPumpDesign(row) {
  const keepSide = (state.pumpDesign && state.pumpDesign.openSide) || "suction";
  state.pumpDesign = {
    view: null,
    versions: {},
    edits: {},
    openSide: keepSide,
    legacy: null,
    source: null,
  };
  renderPumpDesign(row);
  try {
    const saved = await api(`/api/cases/${state.current.id}/pumps/${encodeURIComponent(row.name)}/design-head`);
    state.pumpDesign.view = saved.lines || null;
    state.pumpDesign.legacy = saved.legacy || null;
    state.pumpDesign.source = saved.source || null;
    renderPumpDesign(row);
  } catch (err) {
    // 读不到就是没填过 / 老服务:保持空,不影响主功能
  }
}

/* ------------------------------------------------------------ 泵选型台账

   台账只存人工决定的字段;数值一律由后端按当前工况实时算(compute_view),
   不从任何外部 Excel 导入、不反推、不落库。三态只说
   「满足 / 不满足 / 无法判定」,不写「验过」这类断言 —— 工具没有型谱数据。 */
export function renderPumpSelection(row, data) {
  const box = document.getElementById("pumpSelection");
  if (!box) return;
  const s = data.saved || {};
  // 台账可能来自同模型旧版本(模型保存一次哈希就变):必须标出来源,
  // 不能让人以为是自己这一版填的。保存后即写入当前工况。
  const selSrc = data.sources && data.sources.selection;
  const srcTime = selSrc ? String(selSrc.read_at || "").slice(0, 16) : "";
  const srcNote = selSrc
    ? `<p class="pump-design-note">台账来自同模型旧版本 #${selSrc.case_id}（${esc(srcTime)}）；保存后写入当前工况。</p>`
    : "";
  const input = (key, ph = "") =>
    `<input class="pump-design-input pump-sel-input" data-sel="${key}" placeholder="${ph}"
            value="${s[key] === null || s[key] === undefined ? "" : esc(String(s[key]))}"
            aria-label="${key}">`;
  const kv = (label, value, unit = "") =>
    `<span class="pump-sel-kv">${label} <b>${
      value === null || value === undefined ? "—" : esc(String(value)) + unit
    }</b></span>`;
  const stateText = { ok: "满足", bad: "不满足", unknown: "无法判定" }[data.npsh_state]
    || "无法判定";
  const modelBrief = s.model_no
    ? `<span class="muted">${esc(String(s.model_no))}</span>`
    : '<span class="muted">未填型号</span>';
  box.innerHTML = `
    <div class="pump-block-head" title="只记「选了什么」,数值以当前工况为准">
      <span class="pump-block-title">选型台账</span>
      ${modelBrief}
      <span class="pump-sel-state-mini" data-state="${esc(data.npsh_state)}">${stateText}</span>
    </div>
    ${srcNote}
    <div class="pump-sel-live">
      ${kv("流量", fmt(data.flow_m3h, 2), " m³/h")}
      ${kv("设计扬程", fmt(data.design_head_m, 2), " m")}
      ${kv("选型扬程", fmt(data.selection_head_m, 0), " m")}
      ${kv("NPSHₐ(装置)", fmt(data.npsh_a_m, 2), " m")}
      ${kv("NPSHₐ(修正)", fmt(data.npsh_a_corrected_m, 2), " m")}
      ${kv("建议电机", fmt(data.motor_suggested_kw, 1), " kW")}
    </div>
    <p class="pump-sel-state" data-state="${esc(data.npsh_state)}">
      <b>${stateText}</b> · ${esc(data.npsh_reason || "")}
      ${data.margin_is_default
        ? `<span class="muted">(余量用建议值 ${fmt(data.margin_used_m, 2)} m)</span>` : ""}
    </p>
    <div class="pump-design-inputs">
      <label>型号 ${input("model_no", "人工填写")}</label>
      <label>名称 ${input("pump_name")}</label>
      <label>材质 ${input("material")}</label>
    </div>
    <div class="pump-design-inputs">
      <label>台数 ${input("qty", "如 2")}</label>
      <label>配套电机 kW ${input("motor_kw")}</label>
      <label>NPSHᵣ m ${input("npshr_m")}</label>
      <label>余量 m ${input("margin_m", "0.5~1.0")}</label>
    </div>
    <div class="pump-design-inputs">
      <label>备注 ${input("note")}</label>
    </div>
    <div class="pump-design-actions">
      <button class="link-btn" id="pumpSelSave">保存台账</button>
      <span class="muted" id="pumpSelMsg">缺 p_v 或 NPSHᵣ 时只能判「无法判定」</span>
    </div>`;
  const btn = document.getElementById("pumpSelSave");
  if (btn) btn.onclick = () => savePumpSelection(row);
}

export async function loadPumpSelection(row) {
  const box = document.getElementById("pumpSelection");
  if (box) box.innerHTML = '<p class="muted">读取选型台账…</p>';
  try {
    const data = await api(
      `/api/cases/${state.current.id}/pumps/${encodeURIComponent(row.name)}/selection`
    );
    state.pumpSelection = data;
    renderPumpSelection(row, data);
  } catch (err) {
    if (box) box.innerHTML = `<p class="muted">台账读不到:${esc(err.message)}</p>`;
  }
}

export async function savePumpSelection(row) {
  const msg = document.getElementById("pumpSelMsg");
  const body = {};
  document.querySelectorAll(".pump-sel-input").forEach((input) => {
    const raw = input.value.trim();
    body[input.dataset.sel] = raw === "" ? null : raw;
  });
  for (const key of ["qty", "motor_kw", "npshr_m", "margin_m"]) {
    if (body[key] === null || body[key] === undefined) continue;
    const n = Number(body[key]);
    if (!isFinite(n)) {
      if (msg) msg.textContent = `${key} 必须是数字`;
      return;
    }
    body[key] = n;
  }
  try {
    await apiPost(
      `/api/cases/${state.current.id}/pumps/${encodeURIComponent(row.name)}/selection`,
      body
    );
    await loadPumpSelection(row);
  } catch (err) {
    if (msg) msg.textContent = `没保存:${err.message}`;
  }
}
