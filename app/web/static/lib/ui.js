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
  if (btn) btn.title = `外观:${THEME_TEXT[themeMode]}(点一下切换)`;
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

/* 读设计 token 的实际值:图表颜色只在 style.css 里定义一次。
   以前 JS 里另抄一份调色板,改 CSS 忘了改这里两边就不一致。 */
export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/* 数据页顶部的请求提示条(#dataNotice):
   加载中说明"正在读哪一份",失败说明"屏幕上这批数字还是旧的"。
   换工况时下拉已经指到新工况,而表格还是上一份 —— 没有这一条就容易被误读。 */
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

/* 忙态:给要等的那块挂 aria-busy(样式里是淡淡降透明),读屏也能念出来。
   必须 try/finally —— 少这一次复位,一次失败就会永远停在"正在加载"。 */
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

/* 写请求统一带这个头:跨站页面加自定义头会触发预检,而服务端不回应预检 → 浏览器直接不发。
   服务端 do_POST / do_DELETE 会校验 X-Casebook-Client(见 server.py::_guard)。 */


/* 粘度按量级给位数:稀溶液 0.1156 cP 与 123.4 cP 挤在同一列,
   固定 4 位会写成 123.4350,固定 2 位又会把最有意义的尾部抹掉。 */


export const SEVERITY = {
  ok: { cls: "ok", text: "收敛" },
  warning: { cls: "warn", text: "有告警" },
  error: { cls: "err", text: "有错误" },
  // 不能叫 "empty":CSS 里 .empty 是空表占位(padding:40px / text-align:center),
  // 判定条套上后整块被撑成巨型居中卡片(实测截图)。用专属 idle 类,样式见 .verdict.idle。
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

/* 横幅里那句"所以能不能用" —— 只说结论,不复述状态码。
   放在 .verdict-body 外面,折叠时它仍然可见:收起的只是明细,结论不该被收起。 */
export function bannerVerdict(c) {
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
