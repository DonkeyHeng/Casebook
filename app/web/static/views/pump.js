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

export function openPump(block) {
  const row = state.blocks.find((b) => b.name === block);
  if (!row) return;
  ["columnPanel", "compPanel"].forEach((id) =>
    document.getElementById(id).classList.add("hidden"));
  const panel = document.getElementById("pumpPanel");
  document.getElementById("pumpTitle").textContent = `泵参数 · ${block}`;
  panel.classList.remove("hidden");
  markDrawerOpened();
  focusIntoDrawer(panel);
  // 抽屉本身不滚,滚的是里面那条唯一的滚动容器
  const scroll = panel.querySelector(".pump-panel-scroll");
  if (scroll) scroll.scrollTop = 0;

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
  bindPumpFold(document.getElementById("pumpParams"), "params");

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
  if (state.pumpFold.design === undefined) state.pumpFold.design = false;
  if (state.pumpFold.selection === undefined) state.pumpFold.selection = false;
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

export function fittingsZeta() {
  const picks = state.pumpDesign.fittings || {};
  return PUMP_FITTINGS.reduce((sum, f) => sum + (picks[f.key] || 0) * f.zeta, 0);
}

/* 把勾出来的管件合计算进 Σζ。手改 Σζ 之后不会被覆盖,除非再动一次管件数量。 */
export function applyFittings() {
  const total = fittingsZeta();
  state.pumpDesign.zeta = total > 0 ? Math.round(total * 100) / 100 : null;
  const zetaInput = document.querySelector('[data-key="zeta"]');
  if (zetaInput) zetaInput.value = state.pumpDesign.zeta === null ? "" : state.pumpDesign.zeta;
}

export function pumpDesignTotal(row) {
  const head = typeof row.head_m === "number" ? row.head_m : null;
  const dz = state.pumpDesign.dz;
  const hf = state.pumpDesign.hf;
  if (head === null || dz === null || hf === null) return null;
  return head + dz + hf;
}

/* 只刷新结果那一行:输入时不重画整块,避免光标被踢走 */
export function refreshPumpDesignSum(row) {
  const el = document.getElementById("pumpDesignSum");
  if (!el) return;
  const head = typeof row.head_m === "number" ? row.head_m : null;
  const total = pumpDesignTotal(row);
  if (head === null) {
    el.textContent = "压升扬程没读到,算不了";
    return;
  }
  if (total === null) {
    el.innerHTML = `${fmt(head, 2)} m + 待填 = 待算`;
    return;
  }
  const padded = Math.ceil(total * PUMP_DESIGN_MARGIN);
  el.innerHTML =
    `${fmt(head, 2)} + ${fmt(state.pumpDesign.dz, 2)} + ${fmt(state.pumpDesign.hf, 2)}` +
    ` = <b>${fmt(total, 2)} m</b> · ×${PUMP_DESIGN_MARGIN} 取整 <b>${padded} m</b>`;
}

export function renderPumpDesign(row) {
  const box = document.getElementById("pumpDesign");
  if (!box) return;
  const head = typeof row.head_m === "number" ? row.head_m : null;
  const total = pumpDesignTotal(row);
  const padded = total === null ? null : Math.ceil(total * PUMP_DESIGN_MARGIN);
  const val = (key, ph) =>
    `<input class="pump-design-input" data-key="${key}" inputmode="decimal" placeholder="${ph}"
            value="${state.pumpDesign[key] === null || state.pumpDesign[key] === undefined ? "" : state.pumpDesign[key]}"
            aria-label="${key}">`;
  if (!state.pumpDesign.fittings) {
    state.pumpDesign.fittings = { el90: 0, gate: 0, globe: 0, check: 0, reducer: 0 };
  }
  const calc = state.pumpDesign.calc;
  const calcLine = !calc
    ? "填管长与 Σζ 后可自动算 Re / λ / Σhf"
    : calc.missing && calc.missing.length
      ? `缺:${calc.missing.join("、")}`
      : `u = ${fmt(calc.u_ms, 2)} m/s · Re = ${fmt(calc.re, 0)} · λ = ${fmt(calc.lam, 4)}` +
        ` · ε/d = ${calc.eps_d == null || !isFinite(calc.eps_d)
          ? "—" : (calc.eps_d * 1000).toFixed(2) + "‰"}` +
        (calc.method ? ` · ${LAMBDA_LABELS[calc.method] || calc.method}` : "");
  const brief = total === null
    ? '<span class="muted">待填 ΔZ / Σhf</span>'
    : `<span class="muted">${fmt(total, 2)} m → 选型 ${padded} m</span>`;
  // 参数借自同模型旧版本时标出来源(模型保存一次哈希就变,不能让人以为是本版填的)
  const designSrc = state.pumpDesign.source;
  const designSrcNote = designSrc
    ? `<p class="pump-design-note">ΔZ / Σhf / 管长等来自同模型旧版本 #${designSrc.case_id}（${esc(String(designSrc.read_at || "").slice(0, 16))}）；再次保存后写入当前工况。</p>`
    : "";
  box.innerHTML = `
    <summary class="pump-fold-summary" title="ΔZ / L / Σζ 由你填,工具不估算">
      <span class="pump-fold-title">设计扬程</span>
      ${brief}
      <span class="pump-fold-mark" aria-hidden="true"></span>
    </summary>
    ${designSrcNote}
    <div class="pump-design-inputs">
      <label>ΔZ m ${val("dz", "如 9.45")}</label>
      <label>Σhf m ${val("hf", "如 5.93")}</label>
    </div>
    <div class="pump-design-inputs">
      <label>管长 L m ${val("l", "如 40")}</label>
      <label>Σζ ${val("zeta", "如 17.43")}</label>
      <label${state.pumpDesign.idNote ? ` title="${esc(state.pumpDesign.idNote)}"` : ""}>内径 mm ${
        val("idMm", state.pumpDesign.idNote ? "超档,手填" : "自动")}</label>
      <label>粘度 cP ${val("muCp", "自动")}</label>
      <label>粗糙度 mm ${val("epsMm", "0.05")}</label>
    </div>
    <div class="pump-design-inputs pump-design-lambda">
      <label>λ 口径
        <select class="pump-design-select" data-key="lambdaMethod" aria-label="λ 口径">
          <option value="colebrook"${state.pumpDesign.lambdaMethod === "colebrook" ? " selected" : ""}>Colebrook(默认)</option>
          <option value="gu"${state.pumpDesign.lambdaMethod === "gu" ? " selected" : ""}>顾毓珍</option>
          <option value="manual"${state.pumpDesign.lambdaMethod === "manual" ? " selected" : ""}>手填</option>
        </select>
      </label>
      ${state.pumpDesign.lambdaMethod === "manual"
        ? `<label>λ 值 ${val("lambdaManual", "如 0.028")}</label>`
        : ""}
      <span class="muted">默认 Colebrook;两份算例口径不同,别混用</span>
    </div>
    ${state.pumpDesign.idNote
      ? `<p class="pump-design-note">内径没给:${esc(state.pumpDesign.idNote)}</p>`
      : ""}
    <div class="pump-design-fits">
      <span class="muted">常用管件(填数量,自动求和)</span>
      ${PUMP_FITTINGS.map((f) => {
        const n = state.pumpDesign.fittings[f.key] || 0;
        return `<label title="ζ = ${f.zeta}">${f.label} ×${f.zeta}
          <input class="pump-fit-input" data-fit="${f.key}" type="number" min="0" step="1"
                 inputmode="numeric" value="${n}" aria-label="${f.label} 数量"></label>`;
      }).join("")}
      <span class="muted">合计 Σζ = ${fmt(fittingsZeta(), 2)}</span>
    </div>
    <div class="pump-design-actions">
      <button class="link-btn" id="pumpCalcHf">按管路算 Σhf</button>
      <span class="muted pump-design-calc" id="pumpCalcLine">${calcLine}</span>
    </div>
    <p class="pump-design-sum" id="pumpDesignSum"></p>`;

  box.querySelectorAll(".pump-design-input").forEach((input) => {
    input.addEventListener("input", (e) => {
      const key = e.target.dataset.key;
      const raw = e.target.value.trim();
      const parsed = raw === "" ? null : Number(raw);
      const value = parsed !== null && isFinite(parsed) ? parsed : null;
      state.pumpDesign[key] = value;
      // 这三项原本是自动取值;只有人改过才记进 overrides,免得模型换了还显示旧值
      if (key === "idMm" || key === "muCp" || key === "epsMm") {
        state.pumpDesign.manual[key] = value;
      }
      refreshPumpDesignSum(row);
    });
    input.addEventListener("change", () => savePumpDesign(row));
  });
  box.querySelectorAll(".pump-fit-input").forEach((input) => {
    input.addEventListener("change", (e) => {
      const key = e.target.dataset.fit;
      const count = Math.floor(Number(e.target.value));
      state.pumpDesign.fittings[key] = Number.isFinite(count) && count > 0 ? count : 0;
      applyFittings();
      renderPumpDesign(row);
      savePumpDesign(row);   // 管件数量也按模型+位号存,刷新不丢
    });
  });
  box.querySelectorAll(".pump-design-select").forEach((sel) => {
    sel.addEventListener("change", (e) => {
      const v = e.target.value;
      state.pumpDesign.lambdaMethod = ["colebrook", "gu", "manual"].includes(v)
        ? v : "colebrook";
      renderPumpDesign(row);   // 手填 λ 输入框随方法显隐
      savePumpDesign(row);     // 口径也按模型 + 位号存,刷新不丢
    });
  });
  const calcBtn = document.getElementById("pumpCalcHf");
  if (calcBtn) calcBtn.onclick = () => computeHeadLoss(row);
  bindPumpFold(box, "design");
  refreshPumpDesignSum(row);
}

/* 按管路参数算 Σhf:人只给 L 与 Σζ(内径/粘度/粗糙度可覆盖),公式部分交给后端 */
export async function computeHeadLoss(row) {
  const d = state.pumpDesign;
  const line = document.getElementById("pumpCalcLine");
  if (d.l === null) {
    if (line) line.textContent = "先填管长 L";
    return;
  }
  if (d.zeta === null) {
    if (line) line.textContent = "先填 Σζ(弯头/阀门的局部阻力之和)";
    return;
  }
  const method = ["colebrook", "gu", "manual"].includes(d.lambdaMethod)
    ? d.lambdaMethod : "colebrook";
  const lam = pumpDesignNum(d.lambdaManual);
  if (method === "manual" && lam === null) {
    if (line) line.textContent = "选手填口径时先填 λ 值";
    return;
  }
  if (line) line.textContent = "计算中…";
  try {
    const res = await apiPost(
      `/api/cases/${state.current.id}/pumps/${encodeURIComponent(row.name)}/head-loss`,
      {
        l_m: d.l, zeta_sum: d.zeta, d_mm: d.idMm, eps_mm: d.epsMm, mu_cp: d.muCp,
        method, ...(method === "manual" ? { lam } : {}),
      }
    );
    d.calc = res;
    if (typeof res.hf_m === "number") {
      d.hf = Math.round(res.hf_m * 100) / 100;
      const hfInput = document.querySelector('[data-key="hf"]');
      if (hfInput) hfInput.value = d.hf;
      savePumpDesign(row);
    }
    renderPumpDesign(row);
  } catch (err) {
    if (line) line.textContent = err.message;
  }
}

export async function loadPumpDesign(row) {
  state.pumpDesign = {
    dz: null, hf: null, l: null, zeta: null, idMm: null, muCp: null, epsMm: null, calc: null,
    idNote: "", source: null, fittings: { el90: 0, gate: 0, globe: 0, check: 0, reducer: 0 },
    manual: { idMm: null, muCp: null, epsMm: null },
    lambdaMethod: "colebrook", lambdaManual: null,
  };
  renderPumpDesign(row);
  const cid = state.current.id;
  const name = encodeURIComponent(row.name);
  try {
    const saved = await api(`/api/cases/${cid}/pumps/${name}/design-head`);
    state.pumpDesign.source = saved.source || null;
    state.pumpDesign.dz = pumpDesignNum(saved.dz_m);
    state.pumpDesign.hf = pumpDesignNum(saved.hf_m);
    state.pumpDesign.l = pumpDesignNum(saved.l_m);
    // zeta_json 服务端已解析成对象;老服务没这个字段时整块保持空,不猜值
    const z = saved.zeta_json && typeof saved.zeta_json === "object" ? saved.zeta_json : null;
    state.pumpDesign.zeta = z ? pumpDesignNum(z.zeta_sum) : null;
    if (z && z.fittings && typeof z.fittings === "object") {
      PUMP_FITTINGS.forEach((f) => {
        const n = z.fittings[f.key];
        state.pumpDesign.fittings[f.key] = Number.isInteger(n) && n > 0 ? n : 0;
      });
    }
    const ov = z && z.overrides && typeof z.overrides === "object" ? z.overrides : null;
    [["idMm", "id_mm"], ["muCp", "mu_cp"], ["epsMm", "eps_mm"]].forEach(([key, jsonKey]) => {
      const v = ov ? pumpDesignNum(ov[jsonKey]) : null;
      state.pumpDesign.manual[key] = v;
      if (v !== null) state.pumpDesign[key] = v;
    });
    const lam = z && z.lambda && typeof z.lambda === "object" ? z.lambda : null;
    if (lam) {
      state.pumpDesign.lambdaMethod = ["colebrook", "gu", "manual"].includes(lam.method)
        ? lam.method : "colebrook";
      state.pumpDesign.lambdaManual = pumpDesignNum(lam.manual);
    }
    renderPumpDesign(row);
  } catch (err) {
    // 读不到就是没填过 / 老服务:保持空,不影响主功能
  }
  try {
    const h = await api(`/api/cases/${cid}/pumps/${name}/hydraulics`);
    if (state.pumpDesign.idMm === null && typeof h.id_mm === "number") {
      state.pumpDesign.idMm = h.id_mm;
    }
    if (state.pumpDesign.muCp === null && typeof h.mu_cp === "number") {
      state.pumpDesign.muCp = h.mu_cp;
    }
    if (state.pumpDesign.epsMm === null && typeof h.eps_default_mm === "number") {
      state.pumpDesign.epsMm = h.eps_default_mm;
    }
    state.pumpDesign.idNote = typeof h.id_note === "string" ? h.id_note : "";
    renderPumpDesign(row);
  } catch (err) {
    // 标准表没录全 / 老服务:内径与粘度留给手填
  }
}

export async function savePumpDesign(row) {
  const d = state.pumpDesign;
  const dz = pumpDesignNum(d.dz), hf = pumpDesignNum(d.hf);
  const l = pumpDesignNum(d.l), zeta = pumpDesignNum(d.zeta);
  const fittings = {};
  PUMP_FITTINGS.forEach((f) => {
    const n = d.fittings && d.fittings[f.key];
    fittings[f.key] = Number.isInteger(n) && n > 0 ? n : 0;
  });
  const manual = d.manual || {};
  const overrides = {
    id_mm: pumpDesignNum(manual.idMm),
    mu_cp: pumpDesignNum(manual.muCp),
    eps_mm: pumpDesignNum(manual.epsMm),
  };
  const hasOverride = Object.values(overrides).some((v) => v !== null);
  const method = ["colebrook", "gu", "manual"].includes(d.lambdaMethod)
    ? d.lambdaMethod : "colebrook";
  const lambda = { method, manual: method === "manual" ? pumpDesignNum(d.lambdaManual) : null };
  const hasLambda = method !== "colebrook" || lambda.manual !== null;
  // 全空 = 清除这一行:发全 null 让后端删记录,别留一条空挂账
  const empty = dz === null && hf === null && l === null && zeta === null
    && !Object.values(fittings).some((n) => n > 0) && !hasOverride && !hasLambda;
  try {
    await apiPost(
      `/api/cases/${state.current.id}/pumps/${encodeURIComponent(row.name)}/design-head`,
      {
        dz_m: empty ? null : dz,
        hf_m: empty ? null : hf,
        l_m: empty ? null : l,
        zeta_json: empty ? null : { zeta_sum: zeta, fittings, overrides, lambda },
      }
    );
  } catch (err) {
    reportNotice(`设计扬程没保存:${err.message}`, "warn");
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
    <summary class="pump-fold-summary" title="只记「选了什么」,数值以当前工况为准">
      <span class="pump-fold-title">选型台账</span>
      ${modelBrief}
      <span class="pump-sel-state-mini" data-state="${esc(data.npsh_state)}">${stateText}</span>
      <span class="pump-fold-mark" aria-hidden="true"></span>
    </summary>
    ${srcNote}
    <div class="pump-sel-live">
      ${kv("流量", fmt(data.flow_m3h, 2), " m³/h")}
      ${kv("设计扬程", fmt(data.design_head_m, 2), " m")}
      ${kv("选型扬程", fmt(data.selection_head_m, 0), " m")}
      ${kv("NPSHₐ", fmt(data.npsh_a_m, 2), " m")}
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
  bindPumpFold(box, "selection");
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
