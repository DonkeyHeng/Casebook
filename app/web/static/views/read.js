/* Casebook 视图 · read

后台读 Aspen 的进度条：启动、轮询、完成与错误展示。

本模块不直接持有共享状态；读完后由 shell 的 loadCase / refreshCases 更新
state 并刷新各页，跨模块只调这两个动作。

依赖由 import 明说；本文件不含内联脚本与全局隐式耦合。
*/

import { loadCase, refreshCases } from "./shell.js";
import { runCompare } from "./table.js";
import { api, esc, writeInit } from "../lib/core.js";
import { state } from "../lib/store.js";
import { SEVERITY } from "../lib/ui.js";


/* ------------------------------------------------------------ 读取当前工况 */

/* 读数要 9–25 s(1000 流股约 76 s),所以不阻塞页面:
   POST /api/read 起一个后台任务 → 每 0.6 s 问一次 /api/read/status → 完成后自动切到新工况。 */
export const READ_POLL_MS = 600;
export const READ_DONE_HIDE_MS = 9000;
/* 读到这个秒数还没结束就提示"可能卡住了" —— 121 流股约 25 s,1000 流股约 76 s,
   2 分钟以上基本只有两种情况:Aspen 在忙/弹了对话框,或者许可证在排队。 */
export const READ_STALL_SEC = 120;
export const readState = { timer: null, hideTimer: null, busy: false };

export function readStatusEl() {
  return document.getElementById("readStatus");
}

export function setReadBusy(busy) {
  readState.busy = busy;
  const btn = document.getElementById("readBtn");
  btn.disabled = busy;
  btn.textContent = busy ? "读取中…" : "读取当前工况";
  const comp = document.getElementById("readComposition");
  if (comp) comp.disabled = busy;
}

export function hideReadStatus(delay = 0) {
  clearTimeout(readState.hideTimer);
  const hide = () => readStatusEl().classList.add("hidden");
  if (delay) readState.hideTimer = setTimeout(hide, delay);
  else hide();
}

/* 失败提示照原样显示:connect.py 里那句提示本来就是分好行的人话(该查哪几项) */
export function showReadError(message) {
  const el = readStatusEl();
  el.className = "read-status error";
  el.innerHTML = `<span class="read-text">${esc(message)}</span>
    <button class="read-close" type="button" title="关闭"><svg class="ico"><use href="#i-close"/></svg></button>`;
  el.querySelector(".read-close").addEventListener("click", () => hideReadStatus());
  el.classList.remove("hidden");
}

export function renderReadRunning(snap) {
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

export function fmtSeconds(sec) {
  const s = Math.round(sec);
  return s < 60 ? `${s} 秒` : `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
}

/* 从完整路径取文件名(Windows 反斜杠 / 正斜杠都认) */
export function baseName(path) {
  return String(path || "").split(/[\\/]/).pop() || "";
}

export function renderReadDone(snap) {
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

export async function startRead() {
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

export async function pollRead() {
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
export async function afterRead(caseId) {
  await refreshCases(caseId);
  if (!caseId) return;
  await loadCase(caseId);
  if (state.tab === "compare") await runCompare();
}

/* 页面刷新/重开时,后台可能正在读 —— 接着显示进度,不用让用户再点一次 */
export async function resumeReadIfRunning() {
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
