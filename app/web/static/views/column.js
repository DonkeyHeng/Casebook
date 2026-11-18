/* 塔剖面图:逐板温度曲线、悬停提示与行高亮。*/

import { esc, fmt } from "../lib/core.js";
import { cssVar } from "../lib/ui.js";


/* ------------------------------------------------------------ 塔剖面 */

/* 画布尺寸跟着"两图并排"走:抽屉内容宽 584,两列各 ≈286。
   viewBox 收窄到 300×150 后缩放比 ≈0.95,轴文字 11px 落到 ~10.5px;
   若沿用 480×170 再并排,文字会被压回 6px 级(第 1 批刚修好的正是这个)。
   padL 要留够最长的刻度(如 1282.4),否则左边缘会切字。 */
export const COL_CHART = { width: 300, height: 150, padL: 54, padR: 12, padT: 16, padB: 22 };
export const columnState = { rows: [], charts: [] };

export function chartSVG(series) {
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
export function chartHead(title, series) {
  const items = series.map((s) =>
    `<span class="lg"><i style="background:${s.color}"></i>${esc(s.label)}</span>`).join("");
  return `<div class="chart-head">
    <span class="chart-title">${esc(title)}</span>
    <span class="chart-legend">${items}</span>
  </div>`;
}

/* 鼠标位置 → 第几级(按列宽吸附,不需要对准点) */
export function chartIndex(svg, evt) {
  const { width, padL, padR } = COL_CHART;
  const n = Number(svg.dataset.n || 0);
  if (n < 1) return 0;
  const rect = svg.getBoundingClientRect();
  const x = ((evt.clientX - rect.left) / rect.width) * width;
  const w = width - padL - padR;
  const i = Math.round(((x - padL) / w) * (n - 1));
  return Math.max(0, Math.min(n - 1, i));
}

export function setHover(idx, evt) {
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

export function positionTip(tip, evt) {
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
export function highlightRow(idx) {
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
