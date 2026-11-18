/* Casebook 视图 · flow

流程图页：布局、缩放、聚焦、SVG/PNG 导出。

读写 state：flow（data / layout / view / focus / center / showLabels）；只读 current。

依赖由 import 明说；本文件不含内联脚本与全局隐式耦合。
*/

import { balanceBadge } from "./table.js";
import { api, esc, fmt } from "../lib/core.js";
import { state } from "../lib/store.js";
import { cssVar } from "../lib/ui.js";


/* ------------------------------------------------------------ 流程页(拓扑图)

   零依赖手写 SVG:先按"进料可达距离"分层(这份模型是闭环,没有入度 0 的设备,
   不能用 Kahn),层太多就折行(蛇形,奇偶行反向),再缩放/平移,点设备聚焦上下游 2 层。
   为什么不用图库:项目约定零构建、依赖越少越好;67 个节点自己算完全够用。 */

/* 流程图几何常量。颜色不在这里 —— 全部走 style.css 的 --flow-* token,
   屏幕、图例、导出图读同一份,免得改一处忘两处。 */
/* 流程图节点状态的读屏文字。和 style.css 的 --flow-*-line 一一对应 */
export const FLOW_STATE_TEXT = {
  ok: "通过", bad: "超差", no_data: "无法校验", skip: "不校验", unknown: "未知",
};

export const FLOW = {
  nodeW: 126, nodeH: 40, gapX: 46, gapY: 16, rowGap: 76,
  chipW: 88, chipH: 22, chipGap: 22,
};

export function flowAdj(data) {
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

export function layoutFlow(data, viewAspect = 1.8) {
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

export function flowSvgEl() { return document.getElementById("flowSvg"); }

export function applyFlowView() {
  const svg = flowSvgEl();
  const view = state.flow.view;
  if (!svg || !view) return;
  svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
}

export function flowCanvasAspect() {
  const wrap = document.getElementById("flowWrap");
  if (!wrap || !wrap.clientHeight) return 1.8;
  return wrap.clientWidth / wrap.clientHeight;
}

/* 把矩形按画布长宽比扩成铺满的视口 —— 否则 preserveAspectRatio 会留白 */
export function fitBoxToAspect(box, aspect) {
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

export function fitFlow() {
  fitFlowTo(null);
}

/* names 为空 = 全图;否则只贴合这些设备(点击聚焦时用)
   centerName:聚焦时以这台设备为视口中心,并保证至少放大 MIN_FOCUS_ZOOM 倍 */
export function fitFlowTo(names, centerName = null) {
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

export function zoomFlow(factor, cx = 0.5, cy = 0.5) {
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

export function drawFlow() {
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

export function renderFlowLegend() {
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

export function flowNeighbors(name) {
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

export function setFlowPanel(name) {
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

export function focusFlow(name) {
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

export function clearFlowFocus() {
  state.flow.focus = null;
  state.flow.center = null;
  drawFlow();
  setFlowPanel(null);
  requestAnimationFrame(() => requestAnimationFrame(fitFlow));
  if (location.hash.startsWith("#flow/")) history.replaceState(null, "", "#flow");
}

export async function renderFlow() {
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
export function flowExportCss() {
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

export const SVG_NS = "http://www.w3.org/2000/svg";

/* 把当前视图(缩放/聚焦后的样子)做成一个能单独打开的 SVG 文本 */
export function flowExportSvg() {
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

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

export function flowFileName(ext) {
  const c = state.current;
  const day = new Date().toISOString().slice(0, 10);
  return `flow-case${c ? c.id : "x"}-${day}.${ext}`;
}

export function flowExportMissing() {
  alert("流程页还没有图。先读取一个工况,等流程图出来再导出。");
}

export function exportFlowSvg() {
  const out = flowExportSvg();
  if (!out) { flowExportMissing(); return; }
  downloadBlob(new Blob([out.text], { type: "image/svg+xml;charset=utf-8" }),
    flowFileName("svg"));
}

/* PNG 走"SVG → Image → canvas → toBlob",不引第三方库;
   2 倍像素是为了贴进报告/PPT 时不发虚 */
export function exportFlowPng() {
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
