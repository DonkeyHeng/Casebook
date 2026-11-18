/* Casebook 视图 · shell

应用外壳：工况列表与载入、页面路由与 hash、首页、空态、检查更新、退出。

读写 state：current、cases、page、emptyText、emptyIsError、bannerCollapsed、
compare、impact；载入工况时同步重置各页子状态（streams / blocks / filter /
family / tab / flow.caseId / report.picked）。setPage() 是唯一的页面切换入口，
各页渲染函数由它调度。

依赖由 import 明说；本文件不含内联脚本与全局隐式耦合。
*/

import { renderFlow } from "./flow.js";
import { closeDataDrawers } from "./pump.js";
import { fillCompareSelects, renderTable, resetTableScroll, setTab } from "./table.js";
import { api, apiDelete, esc, writeInit } from "../lib/core.js";
import { renderPipes } from "../pipe.js";
import { state } from "../lib/store.js";
import { SEVERITY, badge, clearNotice, renderBanner, showNotice, withBusy } from "../lib/ui.js";
import { renderReport } from "./report.js";


/* ------------------------------------------------------------ 初始化 */

export const EMPTY_HINT = [
  "还没有工况。点左上角「读取当前工况」,从已经打开的 Aspen 里读一次。",
  "",
  "没有 Aspen 也能先看界面:点下面的「载入示例工况」。",
].join("\n");

export function showEmpty(text) {
  const msg = text || EMPTY_HINT;
  state.emptyText = msg;
  state.emptyIsError = Boolean(text);
  const el = document.getElementById("empty");
  el.textContent = msg;
  el.classList.remove("hidden");
  // 首页是独立页面,数据页隐藏时用户看不到 #empty —— 同步刷一次首页卡片
  if (state.page === "home") renderHome();
}

export function hideEmpty() {
  state.emptyText = "";
  state.emptyIsError = false;
  document.getElementById("empty").classList.add("hidden");
}

/* 没有工况时三个按钮点了也没反应,不如直接禁用,免得像"坏了" */
export function setCaseButtonsEnabled(on) {
  document.getElementById("exportBtn").disabled = !on;
  document.getElementById("aiPackBtn").disabled = !on;
  document.getElementById("deleteBtn").disabled = !on;
}

export async function refreshCases(preferId = null) {
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

export async function loadCase(id) {
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

/* ------------------------------------------------------------ 路由与首页 */

export const PAGES = ["home", "streams", "blocks", "compare", "flow", "pipes", "report"];
export const PAGE_TITLE = { streams: "流股", blocks: "设备", compare: "对比", flow: "流程", report: "衡算报告" };

/* 地址栏是唯一真相:#/streams、#/blocks/塔名(直接打开某座塔的剖面) */
export function readHashPage() {
  const hash = location.hash.replace("#", "").split("/")[0];
  return PAGES.includes(hash) ? hash : "home";
}

export function setPage(page, { push = true } = {}) {
  if (!PAGES.includes(page)) page = "home";
  // 换页就把详情收掉:数据页的抽屉是挂在 #pageData 里的,
  // 只是把整页藏起来的话,绕一圈回到设备页它又会冒出来
  closeDataDrawers();
  state.page = page;
  // aria-current 与 .active 同步:视觉上"你在这页"只靠底色,读屏听不出来
  document.querySelectorAll(".side-nav .nav-item").forEach((b) => {
    const on = b.dataset.tab === page;
    b.classList.toggle("active", on);
    if (on) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });

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

export function setCaseInfo() {
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

export function balanceCounts() {
  const counts = { ok: 0, bad: 0, no_data: 0, skip: 0 };
  state.blocks.forEach((b) => {
    if (b.balance_state && counts[b.balance_state] !== undefined) counts[b.balance_state] += 1;
  });
  return counts;
}

/* 首页仪表盘:工况信息 + 规模 + 判定 + 焓平衡 + 异常模块 */
export function renderHome() {
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

  /* 卡片画完再对一次判定条:首页既然有数据,判定条就不该是 hidden。
     它俩是一个整体(横幅说"能不能用",卡片说"用了哪些数"),任何一路把横幅
     收起来都是不自洽的状态。renderBanner 很便宜(几百个字符),多画一次换掉
     一整类"横幅不见了但卡片还在"的怪状态。 */
  if (state.current) renderBanner();
}

/* 删除当前工况:不可恢复,所以确认框里把"删的是哪一个"写全(模型名 + 时间 + 规模)*/
export async function deleteCurrentCase() {
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

/* ================================================================
   侧栏「检查更新」:只有用户点按钮才产生出站请求。
   结果放在按按钮位置定位的浮层里,不占侧栏布局;点空白 / Esc / ✕ 关闭。
   服务端固定访问 GitHub 的 /releases/latest,只带版本号做 User-Agent,
   不发送任何本地工况数据;不下载、不解压、不安装(见 app/api/update_check.py)。
   ================================================================ */
export let updateReleasesUrl = "https://github.com/DonkeyHeng/Casebook/releases";
export let updatePopOpen = false;
export let updateDismissed = false;   // 检查途中被用户关掉:结果回来不再自动弹出

export function verLabel(value) {
  const text = String(value ?? "").trim();
  return text.startsWith("v") ? text : `v${text}`;
}

/* 远端返回的地址一律过白名单:只认本仓库的 Releases 前缀。
   万一 GitHub 改了重定向格式,页面也不会出现来路不明的链接。 */
export function updateLink(url, text) {
  const base = updateReleasesUrl;
  const safe = typeof url === "string" && (url === base || url.startsWith(`${base}/`))
    ? url : base;
  return `<a href="${esc(safe)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>`;
}

/* 浮层贴在按钮上方;上方放不下就翻到下方。宽度按视口收窄,窄屏不溢出。 */
export function positionUpdatePop() {
  const pop = document.getElementById("updateNote");
  const btn = document.getElementById("updateBtn");
  if (!pop || !btn) return;
  const rect = btn.getBoundingClientRect();
  const width = Math.min(300, window.innerWidth - 24);
  pop.style.width = `${width}px`;
  const height = pop.offsetHeight;
  const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
  const above = rect.top - height - 8;
  const top = above >= 12 ? above : rect.bottom + 8;
  pop.style.left = `${left}px`;
  pop.style.top = `${Math.max(12, Math.min(top, window.innerHeight - height - 12))}px`;
}

export function openUpdatePop() {
  const pop = document.getElementById("updateNote");
  if (!pop) return;
  pop.classList.remove("hidden");
  updatePopOpen = true;
  positionUpdatePop();
  pop.focus({ preventScroll: true });   // 打开时焦点移进来,关闭时还给按钮
}

/* byUser:用户主动关闭(点空白 / Esc / ✕)。关掉之后,还在路上的检查结果不再自动弹窗 */
export function closeUpdatePop({ byUser = false } = {}) {
  const pop = document.getElementById("updateNote");
  if (!pop) return false;
  if (byUser) updateDismissed = true;
  if (!updatePopOpen) return false;
  pop.classList.add("hidden");
  updatePopOpen = false;
  if (byUser) {
    const btn = document.getElementById("updateBtn");
    if (btn) btn.focus({ preventScroll: true });
  }
  return true;
}

export function showUpdateNote(html, { err = false } = {}) {
  const pop = document.getElementById("updateNote");
  const body = document.getElementById("updateNoteBody");
  if (!pop || !body) return;
  pop.classList.toggle("err", err);
  body.innerHTML = html;
  if (!updateDismissed) {
    openUpdatePop();
  } else if (updatePopOpen) {
    positionUpdatePop();   // 已开着但内容高度变了:重新贴一次
  }
}

/* 当前版本只读本地 /api/version,不联网;离线也能显示。 */
export async function loadAppVersion() {
  const el = document.getElementById("appVersion");
  if (!el) return;
  try {
    const res = await fetch("/api/version");
    const data = await res.json();
    el.textContent = data.version ? verLabel(data.version) : "—";
    if (typeof data.releases_url === "string" && data.releases_url.startsWith("https://github.com/")) {
      updateReleasesUrl = data.releases_url.replace(/\/$/, "");
    }
  } catch (err) {
    el.textContent = "—";
  }
}

export async function checkForUpdate() {
  const btn = document.getElementById("updateBtn");
  const label = document.getElementById("updateBtnLabel");
  const fallback = () => `也可手动打开 ${updateLink(updateReleasesUrl, "Releases 页面")} 查看。`;
  updateDismissed = false;   // 新的一次检查:之前手动关掉的不影响这次展示
  if (btn) btn.disabled = true;
  if (label) label.textContent = "正在检查…";
  showUpdateNote("正在向 GitHub 查询最新版本号…");
  try {
    const res = await fetch("/api/update/check", writeInit({ method: "POST" }));
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `请求失败:${res.status}`);
    if (typeof data.releases_url === "string" && data.releases_url.startsWith("https://github.com/")) {
      updateReleasesUrl = data.releases_url.replace(/\/$/, "");
    }
    if (!data.ok) {
      showUpdateNote(`检查失败:${esc(data.message || "网络不可用")}。<br>${fallback()}`, { err: true });
      return;
    }
    if (data.has_update === true) {
      const how = data.frozen
        ? "下载 Windows 包后解压覆盖,<strong>保留自己的 data/ 目录</strong>。"
        : "下载源码包解压覆盖(或 git pull),<strong>保留自己的 data/ 目录</strong>。";
      showUpdateNote(
        `发现新版本 <strong>${esc(verLabel(data.latest))}</strong>`
        + `（当前 ${esc(verLabel(data.current))}）。<br>`
        + `${updateLink(data.release_url, "打开下载页")}　${how}<br>${fallback()}`
      );
    } else if (data.has_update === false) {
      showUpdateNote(
        `当前 ${esc(verLabel(data.current))} · GitHub 最新 ${esc(verLabel(data.latest))},`
        + `未发现更新版本。<br>${fallback()}`
      );
    } else {
      showUpdateNote(
        `当前 ${esc(verLabel(data.current))},GitHub 上最新为 ${esc(verLabel(data.latest))}。<br>`
        + `${esc(data.message || "版本号无法自动比对")}<br>${fallback()}`
      );
    }
  } catch (err) {
    // fetch 本身失败时浏览器抛的是 "Failed to fetch" 这类英文串,不能直接给用户看
    const raw = String((err && err.message) || "");
    const reason = /^(failed to fetch|networkerror|load failed|fetch failed)$/i.test(raw)
      ? "无法连接到本地服务" : (raw || "未知错误");
    showUpdateNote(`检查失败:${esc(reason)}。<br>${fallback()}`, { err: true });
  } finally {
    if (btn) btn.disabled = false;
    if (label) label.textContent = "检查更新";
  }
}

/* 退出:停服务并关掉窗口(应用窗口模式下 window.close() 生效) */
export async function quitApp() {
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
