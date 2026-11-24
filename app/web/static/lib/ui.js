/* 界面外壳的公用件:主题、顶部提示条、徽章与判定条。

被 app.js 与各视图模块共用。纯 DOM 与纯样式,不认识业务。
*/

import { esc } from "../lib/core.js";
import { state } from "../lib/store.js";


/* ------------------------------------------------------------ 外观 */

/* 三态:auto(跟随系统)/ light / dark。
   CSS 只认 html[data-theme] 的明确值(auto 由这里解析),所以样式表里
   只需要一套 dark,不用把 prefers-color-scheme 抄两遍。 */
export const THEME_KEY = "casebook-theme";
export const THEME_MODES = ["auto", "light", "dark"];
export const THEME_TEXT = { auto: "跟随系统", light: "浅色", dark: "深色" };
export const THEME_ICON = { auto: "#i-auto", light: "#i-sun", dark: "#i-moon" };
export const themeMedia = window.matchMedia("(prefers-color-scheme: dark)");
export let themeMode = "auto";

export function applyTheme() {
  const resolved = themeMode === "auto"
    ? (themeMedia.matches ? "dark" : "light")
    : themeMode;
  document.documentElement.dataset.theme = resolved;
  const label = document.getElementById("themeLabel");
  if (label) label.textContent = THEME_TEXT[themeMode];
  const use = document.querySelector("#themeIcon use");
  if (use) use.setAttribute("href", THEME_ICON[themeMode]);
  const btn = document.getElementById("themeBtn");
  if (btn) btn.title = `外观:${THEME_TEXT[themeMode]} · 点击切换`;
}

export function cycleTheme() {
  const i = THEME_MODES.indexOf(themeMode);
  themeMode = THEME_MODES[(i + 1) % THEME_MODES.length];
  try { localStorage.setItem(THEME_KEY, themeMode); } catch (e) { /* 隐私模式:不记也照样能切 */ }
  applyTheme();
}

export function initTheme() {
  const saved = (() => { try { return localStorage.getItem(THEME_KEY); } catch (e) { return null; } })();
  themeMode = THEME_MODES.includes(saved) ? saved : "auto";
  applyTheme();
  const btn = document.getElementById("themeBtn");
  if (btn) btn.addEventListener("click", cycleTheme);
  // 跟随系统时,系统切换了要跟着变
  themeMedia.addEventListener("change", () => { if (themeMode === "auto") applyTheme(); });
}

/* 读取设计 token 的实际值。图表配色只在 style.css 定义一次,
   JS 不复制调色板,避免两处取值漂移。 */
export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/* 数据页请求提示条(#dataNotice):加载中标明数据来源,失败时标明表格仍为旧工况。
   换工况时下拉已指向新工况而表格未更新,须由本条提示区分。 */
export function showNotice(text, { err = false } = {}) {
  const el = document.getElementById("dataNotice");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("err", Boolean(err));
  el.classList.remove("hidden");
}

export function clearNotice() {
  const el = document.getElementById("dataNotice");
  if (!el) return;
  el.textContent = "";
  el.classList.add("hidden");
  el.classList.remove("err");
}

/* 忙态:为目标区块挂 aria-busy(样式降低不透明度),读屏同步播报。
   复位必须走 finally,否则一次异常会让区块永久停留在加载态。 */
export async function withBusy(ids, fn) {
  const els = (Array.isArray(ids) ? ids : [ids])
    .map((id) => document.getElementById(id)).filter(Boolean);
  els.forEach((el) => el.setAttribute("aria-busy", "true"));
  try {
    return await fn();
  } finally {
    els.forEach((el) => el.removeAttribute("aria-busy"));
  }
}

/* 写请求统一携带 X-Casebook-Client:跨站请求会因自定义头触发预检而被拦截;
   服务端在 do_POST / do_DELETE 校验该头(见 server.py::_guard)。 */


/* 粘度按量级定位数:同一列既有 0.1156 cP 也有 123.4 cP,
   固定小数位会损失有效数字。 */


export const SEVERITY = {
  ok: { cls: "ok", text: "收敛" },
  warning: { cls: "warn", text: "有告警" },
  error: { cls: "err", text: "有错误" },
  // 不复用 .empty:.empty 为表格空态样式(padding 40px / 居中),
  // 套用后判定条会变成居中的大卡片。改用专属 idle 类,见 .verdict.idle。
  empty: { cls: "idle", text: "没有结果" },
};

export function badge(severity) {
  const s = SEVERITY[severity] || SEVERITY.warning;
  return `<span class="badge ${s.cls}">${s.text}</span>`;
}

export function blockBadge(status) {
  const cls = status === 1 ? "err" : status === 2 ? "warn" : "ok";
  const text = status === 1 ? "错误" : status === 2 ? "告警" : "正常";
  return `<span class="badge ${cls}">${text}</span>`;
}


/* ------------------------------------------------------------ 顶部 */

export function renderBanner() {
  const el = document.getElementById("verdict");
  const c = state.current;
  // 对比页也保留这块位置:切换页签时顶部不跳,布局更稳
  if (!c) { el.className = "verdict hidden"; return; }

  const s = SEVERITY[c.severity] || SEVERITY.warning;
  const list = c.reasons || [];
  const reasons = list.map((r) => `<div class="reason">· ${esc(r)}</div>`).join("");
  const hasReasons = list.length > 0;

  el.className = `verdict ${s.cls}` + (state.bannerCollapsed && hasReasons ? " collapsed" : "");
  el.innerHTML = `
    <div class="verdict-head">
      <span class="verdict-meta">
        <strong>${s.text}</strong>　流股 ${c.stream_count} 条 / 设备 ${c.block_count} 个${
          c.run_id ? `　运行 ID ${esc(c.run_id)}` : ""}
      </span>
      ${hasReasons ? `<button class="verdict-toggle" id="verdictToggle" type="button"
              title="${state.bannerCollapsed ? "展开问题清单" : "收起问题清单"}">
        ${state.bannerCollapsed ? "展开 ▾" : "收起 ▴"}
      </button>` : ""}
    </div>
    <div class="verdict-say">${esc(bannerVerdict(c))}</div>
    ${hasReasons ? `<div class="verdict-body">${reasons}</div>` : ""}`;

  // 没有理由就不给「展开」—— 点开是空的,比没有按钮更让人困惑
  const toggle = document.getElementById("verdictToggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      state.bannerCollapsed = !state.bannerCollapsed;
      renderBanner();
    });
  }
}

/* 横幅结论:只给可用性判断,不复述状态码。
   置于 .verdict-body 之外,折叠时明细收起、结论保留。 */
export function bannerVerdict(c) {
  const errors = (c.block_errors || []).length;
  const warns = (c.block_warnings || []).length;
  const badTears = (c.tear || []).filter(
    (t) => typeof t.error === "number" && t.tolerance && t.error > t.tolerance);

  if (c.severity === "empty") {
    return "无结果数据:模型未在 Aspen 运行,或运行结果为空。完成仿真后重新读取。";
  }
  if (c.severity === "ok") {
    return "无报错:可用于定量分析(收率、能耗、工况对比)。";
  }
  if (c.severity === "warning") {
    const bits = [];
    if (warns) bits.push(`${warns} 个模块告警`);
    if (badTears.length) bits.push(`${badTears.length} 条撕裂流误差超过容差`);
    const what = bits.length ? bits.join(" + ") : "有情况需要核对";
    return `${what};无报错。数据可用,引用时注明。`;
  }
  // error:说清楚到底错在哪。撕裂流已经不参与 error 判定(见 reader.judge 的注释),
  // 剩下的硬信号只有模块级 BLKSTAT=1 和 CVSTAT≠0。
  if (errors) {
    return `本次运行 ${errors} 个模块报错:不宜用于收率、能耗或工况对比;其余流股仅作定性参考。`;
  }
  return "收敛块报错(CVSTAT 非 0):不宜作定量结论。";
}
