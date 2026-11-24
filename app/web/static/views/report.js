/* 衡算报告页:勾选设备、渲染章节、保存工段边界、导出 Word。*/

import { api, apiPost } from "../lib/core.js";
import { state } from "../lib/store.js";
import { inShell, saveFromUrl } from "../download.js";


/* ------------------------------------------------------------ 衡算报告页 */

/* 勾选状态:空集合 = 全部设备(不逼用户先全选一遍) */
state.report = { data: null, picked: new Set(), filter: "", busy: false,
                 removed: new Set() };  // 本次人工删掉的工段

export function reportNotice(text, kind = "") {
  const el = document.getElementById("reportNotice");
  if (!el) return;
  if (!text) { el.classList.add("hidden"); el.textContent = ""; return; }
  el.classList.remove("hidden");
  el.classList.toggle("warn", kind === "warn");
  el.textContent = text;
}

export function reportPickedDevices() {
  return state.blocks || [];
}

export function reportSelectedCount() {
  return state.report.picked.size;
}

/* 按家族批量勾/取消:只维护一套「设备名集合」,家族按钮只是快捷方式 */
export function reportToggleFamily(key) {
  const members = reportPickedDevices()
    .filter((b) => (b.family || "") === key)
    .map((b) => b.name);
  if (!members.length) return;
  const allOn = members.every((n) => state.report.picked.has(n));
  members.forEach((n) => (allOn ? state.report.picked.delete(n) : state.report.picked.add(n)));
  renderReportPick();
}

export function renderReportPick() {
  const famBox = document.getElementById("reportFamilies");
  const listBox = document.getElementById("reportBlocks");
  const hint = document.getElementById("reportPickHint");
  if (!famBox || !listBox) return;

  const blocks = reportPickedDevices();
  state.report.picked = new Set(
    [...state.report.picked].filter((n) => blocks.some((b) => b.name === n)));

  famBox.innerHTML = "";
  (state.families || []).forEach((fam) => {
    const members = blocks.filter((b) => (b.family || "") === fam.key);
    if (!members.length) return;
    const on = members.filter((b) => state.report.picked.has(b.name)).length;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn" + (on === members.length ? " primary" : "");
    btn.textContent = `${fam.label} ${on}/${members.length}`;
    btn.title = on === members.length ? "再点一次取消这一族的勾选" : "勾选这一族的全部设备";
    btn.addEventListener("click", () => reportToggleFamily(fam.key));
    famBox.appendChild(btn);
  });

  const keyword = (state.report.filter || "").trim().toLowerCase();
  const shown = blocks.filter((b) => !keyword
    || String(b.name).toLowerCase().includes(keyword)
    || String(b.type || "").toLowerCase().includes(keyword));

  listBox.innerHTML = "";
  shown.forEach((b) => {
    const label = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = state.report.picked.has(b.name);
    box.addEventListener("change", () => {
      if (box.checked) state.report.picked.add(b.name);
      else state.report.picked.delete(b.name);
      renderReportPick();
    });
    const name = document.createElement("span");
    name.textContent = b.name;
    const ty = document.createElement("span");
    ty.className = "ty";
    ty.textContent = b.type || "";
    label.appendChild(box);
    label.appendChild(name);
    label.appendChild(ty);
    listBox.appendChild(label);
  });
  if (!shown.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "无匹配设备";
    listBox.appendChild(empty);
  }

  if (hint) {
    const n = reportSelectedCount();
    hint.textContent = n
      ? `已勾选 ${n} 台(逐设备表仅出这些)`
      : `未勾选:全部 ${blocks.length} 台`;
  }
}

/* 界面上的 textarea 是边界流股的唯一真相:手改过的内容原样提交 */
export function reportCollectSections() {
  const parts = {};
  document.querySelectorAll("#reportSections .report-section").forEach((row) => {
    const key = row.dataset.key;
    if (!key) return;
    const label = row.querySelector(".report-label");
    const inBox = row.querySelector(".report-in");
    const outBox = row.querySelector(".report-out");
    parts[key] = {
      label: label ? label.value.trim() : "",
      in: inBox ? inBox.value.trim() : "",
      out: outBox ? outBox.value.trim() : "",
    };
  });
  return { parts, deleted: [...state.report.removed] };
}

export function reportRenderSections(payload) {
  const box = document.getElementById("reportSections");
  const hint = document.getElementById("reportSectionHint");
  if (!box) return;
  const parts = (payload && payload.merged) || {};
  const keys = Object.keys(parts);
  if (payload && payload.deleted && payload.deleted.length) {
    reportNotice(`已删除工段:${payload.deleted.join("、")}(写入 data/config/sections.json)`);
  }

  if (hint) {
    hint.textContent = payload.auto
      ? "当前为自动草稿(未人工确认)"
      : "已载入人工确认结果(data/config/sections.json)";
  }

  box.innerHTML = "";
  if (!keys.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "未生成任何工段:设备位号缺段号。请先核对设备命名。";
    box.appendChild(empty);
    return;
  }

  keys.forEach((key) => {
    const info = parts[key] || {};
    const row = document.createElement("div");
    row.className = "report-section";
    row.dataset.key = key;

    const head = document.createElement("div");
    head.className = "report-section-head";
    const input = document.createElement("input");
    input.className = "report-label";
    input.value = info.label || key;
    input.title = "工段标题:用于报告小节标题";
    const chip = document.createElement("span");
    chip.className = "report-chip";
    chip.textContent = `${(info.in || []).length} 进 / ${(info.out || []).length} 出`;
    head.appendChild(input);
    head.appendChild(chip);

    const del = document.createElement("button");
    del.type = "button";
    del.className = "report-del";
    del.textContent = "删除此工段";
    del.title = "移除本工段;保存后不再由草稿恢复";
    del.addEventListener("click", () => {
      state.report.removed.add(key);
      row.remove();
      const left = box.querySelectorAll(".report-section").length;
      reportNotice(left
        ? `已删除工段「${key}」,剩余 ${left} 个;请点「保存工段边界」。`
        : "工段已全部删除:保存后仅出全流程表;「重置为草稿」可恢复。");
      if (hint) hint.textContent = `已删除 ${state.report.removed.size} 个工段(未保存)`;
    });
    head.appendChild(del);

    const fields = document.createElement("div");
    fields.className = "report-fields";
    [["in", "进界流股(逗号 / 空格分隔)"], ["out", "出界流股(逗号 / 空格分隔)"]].forEach(([field, text]) => {
      const label = document.createElement("label");
      label.textContent = text;
      const area = document.createElement("textarea");
      area.className = field === "in" ? "report-in" : "report-out";
      area.value = (info[field] || []).join(", ");
      area.spellcheck = false;
      label.appendChild(area);
      fields.appendChild(label);
    });

    row.appendChild(head);
    row.appendChild(fields);
    box.appendChild(row);
  });
}

export async function renderReport() {
  const c = state.current;
  reportNotice("");
  if (!c) {
    reportRenderSections({ merged: {}, auto: true });
    reportNotice("无工况:请先在左上读取工况,或载入示例。", "warn");
    return;
  }
  renderReportPick();
  try {
    const payload = await api(`/api/cases/${c.id}/sections`);
    state.report.data = payload;
    reportRenderSections(payload);
    if (payload.error) reportNotice(payload.error, "warn");
  } catch (err) {
    reportNotice(`无法读取工段边界:${err.message}`, "warn");
  }
}

export async function saveReportSections() {
  const c = state.current;
  if (!c) { reportNotice("无工况", "warn"); return; }
  const btn = document.getElementById("reportSave");
  if (btn) btn.disabled = true;
  try {
    const body = reportCollectSections();
    const payload = await apiPost(`/api/cases/${c.id}/sections`, body);
    if (!payload.ok) {
      reportNotice(`未保存:${(payload.errors || []).join(";")}`, "warn");
      return;
    }
    const fresh = await api(`/api/cases/${c.id}/sections`);
    state.report.data = fresh;
    state.report.removed = new Set();
    reportRenderSections(fresh);
    reportNotice(`已保存到 ${payload.config.path}(工况 #${c.id})`);
  } catch (err) {
    reportNotice(`未保存:${err.message}`, "warn");
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* kind: mass 物料册 / energy 能量册。两个按钮共用这一个下载函数 */
export async function downloadReport(kind) {
  const c = state.current;
  if (!c) { reportNotice("无工况", "warn"); return; }
  const picked = [...state.report.picked];
  const query = new URLSearchParams({ kind });
  if (picked.length) query.set("blocks", picked.join(","));
  const url = `/api/cases/${c.id}/balance-report.docx?${query}`;
  const label = kind === "energy" ? "能量衡算册" : "物料衡算册";
  if (inShell()) {
    // 桌面壳没有下载栏:主进程存进 data/exports/ 并打开文件夹
    reportNotice(`正在生成${label}…`);
    await saveFromUrl(url);
    return;
  }
  // 浏览器:直接用地址栏下载,文件名走 Content-Disposition,和「导出 Excel」一致
  window.location.href = url;
  reportNotice(`正在生成${label},请留意浏览器下载栏。`);
}

export function bindReportPage() {
  const save = document.getElementById("reportSave");
  if (save) save.addEventListener("click", () => saveReportSections());

  const inheritBtn = document.getElementById("reportInherit");
  if (inheritBtn) {
    inheritBtn.addEventListener("click", async () => {
      const c = state.current;
      if (!c) {
        reportNotice("无工况", "warn");
        return;
      }
      inheritBtn.disabled = true;
      try {
        const payload = await apiPost(`/api/cases/${c.id}/sections/inherit`, null);
        const fresh = await api(`/api/cases/${c.id}/sections`);
        state.report.data = fresh;
        state.report.removed = new Set();
        reportRenderSections(fresh);
        const how = payload.match ? "(同模型旧版本)" : "";
        reportNotice(
          `已沿用工况 #${payload.from_case}${how} 的 ${(payload.zones || []).length} 个工段;请核对后保存`
        );
      } catch (err) {
        reportNotice(`沿用失败:${err.message}`, "warn");
      } finally {
        inheritBtn.disabled = false;
      }
    });
  }

  const reset = document.getElementById("reportReset");
  if (reset) {
    reset.addEventListener("click", () => {
      const payload = state.report.data || { merged: {}, auto: true };
      state.report.removed = new Set();
      reportRenderSections({ merged: payload.draft || {}, auto: true });
      reportNotice("已恢复为自动草稿(删除的工段已还原),未保存");
    });
  }

  const dlMass = document.getElementById("reportDownloadMass");
  if (dlMass) dlMass.addEventListener("click", () => downloadReport("mass"));
  const dlEnergy = document.getElementById("reportDownloadEnergy");
  if (dlEnergy) dlEnergy.addEventListener("click", () => downloadReport("energy"));

  const filter = document.getElementById("reportFilter");
  if (filter) {
    filter.addEventListener("input", () => {
      state.report.filter = filter.value;
      renderReportPick();
    });
  }

  const all = document.getElementById("reportPickAll");
  if (all) {
    all.addEventListener("click", () => {
      state.report.picked = new Set(reportPickedDevices().map((b) => b.name));
      renderReportPick();
    });
  }

  const none = document.getElementById("reportPickNone");
  if (none) {
    none.addEventListener("click", () => {
      state.report.picked = new Set();
      renderReportPick();
    });
  }
}
