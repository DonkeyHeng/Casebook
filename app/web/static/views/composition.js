/* Casebook 视图 · composition

塔剖面详情与流股组成 / CAS 编辑器。

读写 state：column、streams；只读 current。塔剖面图本身在 views/column.js。

依赖由 import 明说；本文件不含内联脚本与全局隐式耦合。
*/

import { focusIntoDrawer, markDrawerOpened } from "./pump.js";
import { api, apiPost, esc, fmt } from "../lib/core.js";
import { state } from "../lib/store.js";
import { cssVar, withBusy } from "../lib/ui.js";
import { chartHead, chartIndex, chartSVG, columnState, setHover } from "./column.js";


export async function openColumn(block) {
  const panel = document.getElementById("columnPanel");
  document.getElementById("compPanel").classList.add("hidden");  // 三个抽屉不叠着
  document.getElementById("pumpPanel").classList.add("hidden");
  document.getElementById("heatPanel").classList.add("hidden");
  document.getElementById("columnTitle").textContent = `塔剖面 · ${block}`;
  document.getElementById("columnSub").textContent = "左栏:逐级温度与液/气流量 · 右栏:逐级塔板读数";
  panel.classList.remove("hidden");
  markDrawerOpened();
  focusIntoDrawer(panel);
  // 看另一座塔时别带着上一座的滚动位置(现在滚动的是左右两列,不是面板本身)
  panel.querySelectorAll(".pump-modal-col").forEach((col) => { col.scrollTop = 0; });

  // 先渲染占位:接口慢或失败时此前为空白(openColumn 的 reject 未处理)。
  document.getElementById("columnCharts").innerHTML = '<p class="comp-note">正在读取…</p>';
  document.querySelector("#columnTable thead").innerHTML = "";
  document.querySelector("#columnTable tbody").innerHTML = "";

  let rows;
  try {
    rows = await withBusy("columnPanel",
      () => api(`/api/cases/${state.current.id}/columns/${encodeURIComponent(block)}`));
  } catch (err) {
    document.getElementById("columnCharts").innerHTML =
      `<div class="comp-error">无法读取 ${esc(block)} 的塔剖面:${esc(err.message)}</div>`;
    return;
  }
  state.column = { block, rows };
  columnState.rows = rows;
  columnState.charts = [];
  document.getElementById("columnSub").textContent =
    `左栏:逐级温度与液/气流量 · 右栏:逐级塔板读数 · 共 ${rows.length} 级`;

  const temps = rows.map((r) => ({ x: r.stage, y: r.temperature_c }));
  const liq = rows.map((r) => ({ x: r.stage, y: r.liq_flow }));
  const vap = rows.map((r) => ({ x: r.stage, y: r.vap_flow }));

  const chartsEl = document.getElementById("columnCharts");
  const chartHint = document.getElementById("columnChartHint");
  // 流量单位随 Aspen 的设置变(kmol/hr、kg/hr 都可能),直接取读数自带的单位,不写死
  const flowUnit = rows[0]?.liq_flow_unit ? ` ${rows[0].liq_flow_unit}` : "";
  if (chartHint) chartHint.textContent = `横轴:级序 · 纵轴:温度 ℃ / 流量${flowUnit}`;
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
  const hint = document.getElementById("columnTableHint");
  if (hint) hint.textContent = `${rows.length} 级 · 列多时可横向滚动`;
  thead.innerHTML = `<tr><th scope="col">级</th><th scope="col" class="num">温度 ℃</th><th scope="col" class="num">压力 bar</th>
    <th scope="col" class="num">液流量</th><th scope="col" class="num">气流量</th><th scope="col" class="num">热负荷 kW</th></tr>`;
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

/* 摩尔分数含 1e-9 量级痕量:固定 6 位小数会归零,
   故小值用科学计数法,其余保留 6 位。 */
export function fmtFrac(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v !== "number") return esc(v);
  if (v !== 0 && Math.abs(v) < 1e-4) return v.toExponential(2);
  return v.toFixed(6);
}

/* 百分比按量级调整精度(99.6% / 0.21% / 0.0007%),避免痕量显示为 0.00%。 */
export function pctText(fraction) {
  if (fraction === null || fraction === undefined) return "—";
  const p = fraction * 100;
  if (p === 0) return "0";
  if (p >= 10) return `${p.toFixed(1)}%`;
  if (p >= 0.1) return `${p.toFixed(2)}%`;
  if (p >= 0.001) return `${p.toFixed(3)}%`;
  return `${p.toExponential(1)}%`;
}

export const PHASE_COLORS = () => ["--chart-1", "--chart-2", "--chart-3", "--chart-4", "--chart-5", "--chart-6"].map(cssVar);
export const PHASE_REST_COLOR = () => cssVar("--chart-rest");

/* 一条相态条:横条按比例分色(前 6 个组分 + 其他),右边写 Top3 百分比 */
export function phaseRow(label, key, comps, sum) {
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

/* 气液比:按摩尔基准。Aspen 汽化率(VFRAC)为摩尔基准,
   乘以总摩尔流量即得两相摩尔流量;质量基准需组分分子量,抽屉内无该数据,故不给出。
   组分条表示气相组成,本条表示气相量。 */
export function flowSplitRow(row) {
  const vf = row && typeof row.vap_frac === "number" ? row.vap_frac : null;
  const total = row && typeof row.mole_flow_kmolh === "number" ? row.mole_flow_kmolh : null;
  if (vf === null || total === null) return "";

  const vap = total * vf;
  const liq = total - vap;
  const share = Math.min(100, Math.max(0, vf * 100));
  const ratio = vf >= 1 ? null : vf <= 0 ? 0 : vf / (1 - vf);
  const ratioText = ratio === null ? "全气相" : ratio === 0 ? "全液相" : `气/液 ${ratio.toFixed(2)}`;
  const legend = `液相 ${fmt(liq, 2)} · 气相 ${fmt(vap, 2)} kmol/h · ${ratioText}`;

  return `<div class="phase-row flow-row">
    <span class="phase-label">气/液</span>
    <span class="phase-bar">
      <i class="phase-seg" style="width:${(100 - share).toFixed(2)}%;background:${cssVar("--chart-1")}"></i>
      <i class="phase-seg" style="width:${share.toFixed(2)}%;background:${cssVar("--chart-2")}"></i>
    </span>
    <span class="phase-legend" title="按摩尔流量拆分:总摩尔流量 × 汽化率 = 气相,余下为液相。气/液 = 汽化率 /（1 − 汽化率)">${esc(legend)}</span>
  </div>`;
}

export const compState = { seq: 0, stream: null };   // stream:重绘(切零组分/CAS)时还要知道是哪条流股

export async function openComposition(stream) {
  const panel = document.getElementById("compPanel");
  document.getElementById("columnPanel").classList.add("hidden");
  document.getElementById("compTitle").textContent = `流股组成 · ${stream}`;
  document.getElementById("compBody").innerHTML =
    '<p class="comp-note">正在读取…<br>(工况中已存则直接取;未存则从当前打开的 Aspen 读取一次,约 0.3 秒)</p>';
  panel.classList.remove("hidden");
  markDrawerOpened();
  focusIntoDrawer(panel);

  compState.stream = stream;   // 之后的重绘(切零组分、补 CAS)要靠它取气液比
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
  renderComposition(data, stream);
}

/* 含量为 0 的组分默认收起来:一屏几十行 0 既占地方又没信息量。
   仅将数值 0 视为空;null 表示未读到,不得按 0 隐藏(否则缺数据被伪装为无数据)。 */
export let compShowAll = false;

export function compIsZero(c) {
  const isZero = (v) => typeof v === "number" && v === 0;
  return isZero(c.x_liq) && isZero(c.y_vap);
}

/* CAS 校验位:从右往左 ×1,2,3… 求和取个位。算不对多半是抄错了。 */
export function casCheckOk(raw) {
  const digits = String(raw || "").replace(/-/g, "");
  if (!/^\d{5,10}$/.test(digits)) return false;
  const check = Number(digits.slice(-1));
  const rest = digits.slice(0, -1).split("").reverse();
  const sum = rest.reduce((s, d, i) => s + Number(d) * (i + 1), 0);
  return sum % 10 === check;
}

export function renderComposition(data, streamName) {
  const body = document.getElementById("compBody");
  const comps = data.components || [];
  const zeroCount = comps.filter(compIsZero).length;
  const shown = compShowAll ? comps : comps.filter((c) => !compIsZero(c));
  const missingCas = comps.filter((c) => !c.cas);
  const note = data.source === "live"
    ? '<p class="comp-note live">刚从当前打开的 Aspen 读出来,已存入本工况(下次打开直接取,不再连 Aspen)。</p>'
    : '<p class="comp-note">来自本工况已存的数据。</p>';

  const rows = shown.map((c) => {
    const zero = (v) => (v === 0 ? " comp-zero" : "");
    // 组分名显示规则(2026-11-15):CAS 命中字典时,首行中文名、次行 CAS,悬浮显示 Aspen 原名;
    //   无 CAS 的自定义组分仅显示 Aspen 原名,不提供悬浮,避免误示为已识别。
    const cn = c.cn ? String(c.cn) : "";
    const cas = c.cas ? String(c.cas) : "";
    // 悬浮仅显示 Aspen 原名(2026-11-15):页面已含中文名与 CAS,悬浮用于对应模型内命名。
    // 手填 CAS 常无对应中文名:首行用原名,次行仍显示 CAS,避免因缺少中文名而隐藏 CAS。
    const head = cn || c.component;
    const title = cn ? esc(c.component) : "";
    const nameCell =
      cn || cas
        ? `<td class="name comp-name"${title ? ` title="${title}"` : ""}>` +
          `<span class="cn">${esc(head)}</span>` +
          (cas ? `<span class="cas">${esc(cas)}</span>` : "") +
          `</td>`
        : `<td class="name">${esc(c.component)}</td>`;
    return `<tr>
      ${nameCell}
      <td class="num${zero(c.x_liq)}">${fmtFrac(c.x_liq)}</td>
      <td class="num${zero(c.y_vap)}">${fmtFrac(c.y_vap)}</td>
    </tr>`;
  }).join("");

  const tools = [];
  if (zeroCount) {
    tools.push(
      `<button class="link-btn" id="compZeroToggle">${compShowAll ? "隐藏" : "显示"}含量为 0 的 ${zeroCount} 个组分</button>`
    );
  }
  if (missingCas.length) {
    tools.push(`<button class="link-btn" id="compCasBtn">补录 CAS（${missingCas.length} 个）</button>`);
  }
  const emptyRows = shown.length
    ? rows
    : '<tr><td class="name" colspan="3">该流股所有组分含量均为 0</td></tr>';

  body.innerHTML = `${note}
    ${tools.length ? `<div class="comp-tools">${tools.join("")}</div>` : ""}
    <div class="comp-table-wrap">
      <table>
        <thead><tr><th scope="col">组分</th><th scope="col" class="num">液相摩尔分数 x</th><th scope="col" class="num">气相摩尔分数 y</th></tr></thead>
        <tbody>${emptyRows}</tbody>
      </table>
    </div>
    <div class="comp-foot">
      ${phaseRow("液相", "x_liq", comps, data.sum_x)}
      ${phaseRow("气相", "y_vap", comps, data.sum_y)}
      ${flowSplitRow(state.streams.find((r) => r.name === (streamName || compState.stream)))}
      <p class="comp-sum">共 ${data.count} 个组分${
        zeroCount && !compShowAll ? ` · 显示 ${shown.length} 个` : ""} · 合计 x = ${fmt(data.sum_x, 4)} · 合计 y = ${fmt(data.sum_y, 4)}${
        data.cas_total ? ` · CAS ${data.cas_found}/${data.cas_total}` : ""}${
        data.run_id ? ` · 运行 ID ${esc(data.run_id)}` : ""}</p>
    </div>`;

  const zeroToggle = document.getElementById("compZeroToggle");
  if (zeroToggle) {
    zeroToggle.addEventListener("click", () => {
      compShowAll = !compShowAll;
      renderComposition(data);
    });
  }
  const casBtn = document.getElementById("compCasBtn");
  if (casBtn) casBtn.addEventListener("click", () => renderCasEditor(data));
}

/* 手填 CAS:仅列无 CAS 的组分。
   存到按模型挂的 component_cas 表,重读工况不会丢;读得到时仍以读数为准。 */
export function renderCasEditor(data) {
  const body = document.getElementById("compBody");
  const missing = (data.components || []).filter((c) => !c.cas);
  const rows = missing
    .map(
      (c) => `<div class="cas-edit-row">
        <span class="cas-edit-name" title="${esc(c.component)}">${esc(c.component)}</span>
        <input class="cas-edit-input" data-comp="${esc(c.component)}" placeholder="如 2134-29-4"
               aria-label="${esc(c.component)} 的 CAS 号">
      </div>`
    )
    .join("");

  body.innerHTML = `
    <div class="comp-tools"><button class="link-btn" id="casBack">← 返回组成</button></div>
    <p class="comp-note">以下组分在 Aspen 与模型文件中均无 CAS。<br>
    手填值按<b>模型</b>保存,重读工况不丢失;有读数时以读数为准。</p>
    <div class="cas-edit-list">${rows}</div>
    <div class="comp-tools">
      <button class="link-btn" id="casSave">保存全部</button>
      <span class="cas-edit-msg" id="casMsg"></span>
    </div>`;

  document.getElementById("casBack").addEventListener("click", () => renderComposition(data));

  let forceSave = false;
  document.getElementById("casSave").addEventListener("click", async () => {
    const msg = document.getElementById("casMsg");
    const inputs = Array.from(document.querySelectorAll(".cas-edit-input"));
    const todo = inputs
      .map((el) => ({ comp: el.dataset.comp, raw: el.value.trim() }))
      .filter((item) => item.raw);
    if (!todo.length) {
      msg.textContent = "没有填写任何 CAS";
      return;
    }
    const bad = todo.filter((item) => !casCheckOk(item.raw));
    if (bad.length && !forceSave) {
      forceSave = true;
      msg.textContent = `${bad.length} 个 CAS 校验位对不上(可能抄错),再点一次仍要保存`;
      return;
    }
    msg.textContent = "保存中…";
    try {
      for (const item of todo) {
        await apiPost(`/api/cases/${state.current.id}/components/cas`, {
          component: item.comp,
          cas: item.raw,
        });
      }
      await openComposition(data.stream);
    } catch (err) {
      msg.textContent = err.message;
    }
  });
}
