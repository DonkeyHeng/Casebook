/* Casebook 前端入口

只做三件事：import 各视图模块（显式依赖）、绑定一次性事件、init()。

所有页面实现都在 views/ 下，共享状态在 lib/store.js，跨文件工具在 lib/。
数据流：state 是唯一共享数据源，每个视图模块的 docstring 写明自己读写哪些字段。

依赖由 import 明说；本文件不含内联脚本与全局隐式耦合。
*/

import { openColumn, openComposition } from "./views/composition.js";
import { applyFlowView, clearFlowFocus, drawFlow, exportFlowPng, exportFlowSvg, fitFlow, flowExportSvg, focusFlow, zoomFlow } from "./views/flow.js";
import { ZHONGLIAN_URL, closeDataDrawers, closePumpPromptTip, copyPlainText, fetchPumpPromptText, isOutsideDrawerClick, openPump, showPumpPromptTip } from "./views/pump.js";
import { resumeReadIfRunning, startRead } from "./views/read.js";
import { checkForUpdate, closeUpdatePop, deleteCurrentCase, hideEmpty, loadAppVersion, loadCase, positionUpdatePop, quitApp, readHashPage, refreshCases, setPage, showEmpty, updatePopOpen } from "./views/shell.js";
import { inheritPv, loadFamilies, loadPumpSpec, readPumpBar, renderCompare, renderTable, runCompare, schedulePvSave, scheduleTableRender, setCompareScope } from "./views/table.js";
import { api, apiPost } from "./lib/core.js";
import { initPipePanel } from "./pipe.js";
import { state } from "./lib/store.js";
import { initTheme } from "./lib/ui.js";
import { bindReportPage } from "./views/report.js";


/* 所有按钮都只绑定一次 —— 即使库里一条工况都没有,也得能点「读取当前工况」 */
function bindEvents() {
  initPipePanel();      // 管道页的参数面板与事件(pipe.js)

  document.getElementById("updateBtn").addEventListener("click", checkForUpdate);
  document.getElementById("updateClose").addEventListener("click", () => {
    closeUpdatePop({ byUser: true });
  });
  // 点浮层与触发按钮之外的地方都算「空白」;打开那一下的冒泡由 contains 判断挡掉
  document.addEventListener("click", (event) => {
    if (!updatePopOpen) return;
    const pop = document.getElementById("updateNote");
    const btn = document.getElementById("updateBtn");
    if (pop && pop.contains(event.target)) return;
    if (btn && btn.contains(event.target)) return;
    closeUpdatePop({ byUser: true });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && updatePopOpen) closeUpdatePop({ byUser: true });
  });
  window.addEventListener("resize", () => {
    if (updatePopOpen) positionUpdatePop();
  });

  document.getElementById("caseSelect").addEventListener("change", (e) => {
    loadCase(Number(e.target.value));
  });

  document.querySelectorAll(".side-nav .nav-item").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      setPage(btn.dataset.tab);
    });
  });

  // 浏览器前进/后退、手改地址栏 → 跟着切页
  window.addEventListener("hashchange", () => {
    const page = readHashPage();
    if (page !== state.page) setPage(page, { push: false });
  });

  document.getElementById("filter").addEventListener("input", (e) => {
    state.filter = e.target.value;
    // 对比页有自己的渲染入口,其余两种走同一帧合并
    if (state.tab === "compare") renderCompare();
    else scheduleTableRender();
  });

  // 泵参数条:余量一改就重算表格(只改显示,不写库)
  ["pumpMargin", "pumpMarginCustom"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("input", readPumpBar);
  });

  // p_v 与表格里的可点名称都挂在 #table tbody 上:表格整块重绘,委托一次就够。
  // 以前每次重绘都要给每一行重绑 —— 120 行就是 120 个监听器,还都要被回收一遍。
  const tableBody = document.querySelector("#table tbody");
  if (tableBody) {
    tableBody.addEventListener("input", (e) => {
      const box = e.target.closest("input[data-pv]");
      if (!box) return;
      box.classList.remove("pv-bad");
      schedulePvSave(box.dataset.pv, box.value);
    });
    tableBody.addEventListener("click", (e) => {
      const link = e.target.closest(".stream-link, .col-link, .pump-link");
      if (!link) return;
      e.preventDefault();
      if (link.classList.contains("stream-link")) openComposition(link.dataset.stream);
      else if (link.classList.contains("col-link")) openColumn(link.dataset.block);
      else openPump(link.dataset.block);
    });
  }

  const inheritBtn = document.getElementById("pumpInherit");
  if (inheritBtn) inheritBtn.addEventListener("click", inheritPv);

  document.getElementById("baseSelect").addEventListener("change", runCompare);
  document.getElementById("otherSelect").addEventListener("change", runCompare);
  document.querySelectorAll("#compareScope button").forEach((b) =>
    b.addEventListener("click", () => setCompareScope(b.dataset.scope)));
  document.getElementById("compareHint").addEventListener("click", () => setPage("compare"));
  // 三个抽屉的 ✕:与 Esc 同一条路径,关掉并把焦点还给打开它的那一行
  ["closeColumn", "closeComp", "closePump"].forEach((id) => {
    const btn = document.getElementById(id);
    if (btn) btn.addEventListener("click", () => closeDataDrawers({ restore: true }));
  });

  // 点抽屉外面的空白处也能关(三个抽屉共用;判定见 isOutsideDrawerClick)
  document.addEventListener("click", (e) => {
    if (isOutsideDrawerClick(e)) closeDataDrawers();
  });

  // 泵参数条上的「?」操作指南:悬浮展开、点击固定,点外面 / Esc 收起
  const pvHelp = document.getElementById("pvHelp");
  const pvHelpBtn = document.getElementById("pvHelpBtn");
  const pvHelpBody = document.getElementById("pvHelpBody");
  if (pvHelp && pvHelpBtn && pvHelpBody) {
    const setPvHelp = (open) => {
      pvHelpBody.classList.toggle("show", open);
      pvHelpBtn.setAttribute("aria-expanded", open ? "true" : "false");
      if (!open) delete pvHelp.dataset.pinned;
    };
    pvHelp.addEventListener("mouseenter", () => setPvHelp(true));
    pvHelp.addEventListener("mouseleave", () => {
      if (pvHelp.dataset.pinned !== "1") setPvHelp(false);
    });
    pvHelpBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = !pvHelpBody.classList.contains("show");
      setPvHelp(open);
      if (open) pvHelp.dataset.pinned = "1";
    });
    document.addEventListener("click", (e) => {
      if (!pvHelp.contains(e.target)) setPvHelp(false);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") setPvHelp(false);
    });
  }

  // 泵页「复制选泵提示词」:正文从接口取,复制成功后弹操作指引浮层。
  // 接口挂了(改了 Python 没重启)就把原因写在按钮上,不拿旧文案顶替。
  const promptBtn = document.getElementById("pumpPromptCopy");
  if (promptBtn) {
    const btnLabel = promptBtn.textContent;
    const btnTitle = promptBtn.title;
    let resetTimer = 0;
    const flash = (text, tip) => {
      promptBtn.textContent = text;
      if (tip) promptBtn.title = tip;
      clearTimeout(resetTimer);
      resetTimer = setTimeout(() => {
        promptBtn.textContent = btnLabel;
        promptBtn.title = btnTitle;
      }, 2000);
    };
    promptBtn.addEventListener("click", async () => {
      try {
        const text = await fetchPumpPromptText();
        if (await copyPlainText(text)) {
          flash("已复制");
          showPumpPromptTip();
        } else {
          flash("复制失败,请手动选中");
        }
      } catch (err) {
        flash("需重启服务", `提示词接口不可用(${err.message});重启一次服务后重试`);
      }
    });

    const promptTip = document.getElementById("pumpPromptTip");
    if (promptTip) {
      const closeBtn = document.getElementById("pumpPromptTipClose");
      if (closeBtn) closeBtn.addEventListener("click", closePumpPromptTip);
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && !promptTip.classList.contains("hidden")) closePumpPromptTip();
      });
      document.addEventListener("click", (e) => {
        if (promptTip.classList.contains("hidden")) return;
        if (promptTip.contains(e.target) || promptBtn.contains(e.target)) return;
        closePumpPromptTip();
      });
    }
  }

  bindReportPage();

  // 设备页右上角「导出 Excel」:当前分类的设备明细(泵分类 = 选型要抄的那几列)
  const exportFamily = document.getElementById("exportFamily");
  if (exportFamily) {
    exportFamily.addEventListener("click", async () => {
      if (!state.current) return;
      const family = state.family || "column";
      const url = `/api/cases/${state.current.id}/export-family.xlsx?family=${encodeURIComponent(family)}`;
      exportFamily.disabled = true;
      try {
        const res = await fetch(url);
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.error || `HTTP ${res.status}`);
        }
        const blob = await res.blob();
        const cd = res.headers.get("Content-Disposition") || "";
        const m = /filename="([^"]+)"/.exec(cd);
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = m ? m[1] : `Casebook_${family}.xlsx`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      } catch (err) {
        alert(`导出失败:${err.message}`);
      } finally {
        exportFamily.disabled = false;
      }
    });
  }

  // 打开中联选型:只开页面,不拼参数(noopener/noreferrer,别让外站碰到本页)
  document.getElementById("pumpZhonglian").addEventListener("click", () => {
    window.open(ZHONGLIAN_URL, "_blank", "noopener,noreferrer");
  });

  document.getElementById("readBtn").addEventListener("click", startRead);

  // 导出 Excel:给人看
  document.getElementById("exportBtn").addEventListener("click", () => {
    if (!state.current) return;
    window.location.href = `/api/cases/${state.current.id}/export.xlsx`;
  });

  // 导出 AI 分析包:给 AI 读(Markdown 说明 + CSV)
  document.getElementById("aiPackBtn").addEventListener("click", () => {
    if (!state.current) return;
    window.location.href = `/api/cases/${state.current.id}/export.ai.zip`;
  });

  document.getElementById("quitBtn").addEventListener("click", quitApp);

  // ---- 流程页:工具栏 + 缩放平移 + 聚焦 ----
  // 缩放只有滚轮和「适应窗口」两个入口:+/- 按钮已去掉(有滚轮时纯占位)
  document.getElementById("flowFit").addEventListener("click", fitFlow);
  const labelsBtn = document.getElementById("flowLabels");
  labelsBtn.addEventListener("click", () => {
    state.flow.showLabels = !state.flow.showLabels;
    labelsBtn.classList.toggle("primary", state.flow.showLabels);
    if (state.flow.layout) drawFlow();
  });
  document.getElementById("flowPanelClose").addEventListener("click", clearFlowFocus);
  document.getElementById("flowExportSvg").addEventListener("click", exportFlowSvg);
  document.getElementById("flowExportPng").addEventListener("click", exportFlowPng);
  window.addEventListener("resize", () => {
    if (state.page === "flow") fitFlow();
  });

  const flowSvg = document.getElementById("flowSvg");
  if (flowSvg) {
    /* 交互全部走 pointer 事件,不用 click:
       拖动结束后浏览器仍会补一个 click,若把"空白处 click"当取消聚焦,
       用户拖到远处看邻居、一松手就会被拉回全图。改成 pointerup 里按位移判定。 */
    let drag = null;
    const DRAG_THRESHOLD = 4;      // 像素:超过就算拖动,不算点击

    flowSvg.addEventListener("wheel", (e) => {
      if (!state.flow.view) return;
      e.preventDefault();
      const rect = flowSvg.getBoundingClientRect();
      zoomFlow(e.deltaY < 0 ? 1.12 : 1 / 1.12,
        (e.clientX - rect.left) / rect.width, (e.clientY - rect.top) / rect.height);
    }, { passive: false });

    flowSvg.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      const node = e.target.closest("[data-node]");
      drag = {
        x: e.clientX, y: e.clientY,
        view: state.flow.view ? { ...state.flow.view } : null,
        moved: false,
        node: node ? node.dataset.node : null,
        onBoundary: !!e.target.closest("[data-boundary]"),
        fromBackground: e.target === flowSvg || !!e.target.closest("g.flow-edges"),
      };
      if (drag.view) flowSvg.classList.add("dragging");
    });

    window.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x;
      const dy = e.clientY - drag.y;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) > DRAG_THRESHOLD) drag.moved = true;
      if (!drag.view) return;
      const rect = flowSvg.getBoundingClientRect();
      state.flow.view.x = drag.view.x - dx * drag.view.w / rect.width;
      state.flow.view.y = drag.view.y - dy * drag.view.h / rect.height;
      applyFlowView();
    });

    const endDrag = (e) => {
      const d = drag;
      drag = null;
      flowSvg.classList.remove("dragging");
      if (!d) return;
      if (d.moved) return;                       // 拖动:只平移,不改聚焦
      if (d.node) { focusFlow(d.node); return; }
      // 点空白才取消聚焦;点到边界芯片、连线时不误取消
      if (d.fromBackground && state.flow.focus) clearFlowFocus();
    };
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", (e) => {
      drag = null;
      flowSvg.classList.remove("dragging");
    });
    flowSvg.addEventListener("dblclick", fitFlow);
    // Esc 只处理当前真正开着的东西。原来无条件 clearFlowFocus():
    // 在任何页面按 Esc 都会重画一遍流程图,还抢在弹窗自己的 Esc 之前处理。
    // 缩放按钮与快捷键:滚轮之外的第二条通道(键盘、触屏都要有)
    const zoomIn = document.getElementById("flowZoomIn");
    const zoomOut = document.getElementById("flowZoomOut");
    if (zoomIn) zoomIn.addEventListener("click", () => zoomFlow(1.2));
    if (zoomOut) zoomOut.addEventListener("click", () => zoomFlow(1 / 1.2));

    // 节点用 Tab 选中后,回车/空格等同点击
    flowSvg.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const g = e.target.closest && e.target.closest(".flow-node");
      if (!g || !g.dataset.node) return;
      e.preventDefault();
      flowSvg.focus({ preventScroll: true });
      focusFlow(g.dataset.node);
    });

    document.addEventListener("keydown", (e) => {
      const typing = e.target && e.target.closest
        && e.target.closest("input, select, textarea");
      // 流程页的缩放快捷键(在输入框里打字时不抢)
      if (state.page === "flow" && !typing) {
        if (e.key === "+" || e.key === "=") { e.preventDefault(); zoomFlow(1.2); return; }
        if (e.key === "-" || e.key === "_") { e.preventDefault(); zoomFlow(1 / 1.2); return; }
        if (e.key === "0") { e.preventDefault(); fitFlow(); return; }
      }
      if (e.key !== "Escape") return;
      if (state.page === "flow") {
        if (state.flow.focus) clearFlowFocus();
        return;
      }
      closeDataDrawers({ restore: true });   // 数据页的三个抽屉(塔剖面 / 泵参数 / 流股组成)
    });
  }
  document.getElementById("deleteBtn").addEventListener("click", deleteCurrentCase);
}

/* 首页空态的「载入示例工况」:把内置示例库装进当前库(等价于 --sample,先备份原库) */
async function installSampleData() {
  const ok = confirm(
    "载入内置示例工况?\n\n"
    + "会先用示例数据覆盖当前工况库,原库自动备份成 cases.db.bak。\n"
    + "如果你已经读过自己的模型,点「取消」即可。"
  );
  if (!ok) return;

  const btn = document.getElementById("loadSampleBtn");
  const label = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "正在载入…"; }
  try {
    const res = await apiPost("/api/sample/install");
    hideEmpty();
    await refreshCases(res.case_id ?? null);
    if (state.cases.length) {
      await loadCase(res.case_id ?? state.cases[0].id);
    } else {
      showEmpty("示例库装好了,但没读到工况。刷新一下页面试试。");
    }
  } catch (err) {
    alert(`载入示例失败:${err.message}`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label || "载入示例工况(无需 Aspen)"; }
  }
}

async function init() {
  initTheme();
  bindEvents();
  loadAppVersion();   // 本地常量,不联网;失败也不影响主流程

  try {
    await refreshCases(null);
  } catch (err) {
    showEmpty(`读不到工况列表:${err.message}\n\n后台服务可能没起来,重新双击桌面快捷方式。`);
    return;
  }

  const page = readHashPage();
  await loadFamilies();
  await loadPumpSpec();

  if (!state.cases.length) {
    setPage(page, { push: false });
    await resumeReadIfRunning();
    return;
  }

  // ?case=19 可以指定打开哪个工况(方便分享链接/回看某个旧工况)
  const wantCase = Number(new URLSearchParams(location.search).get("case"));
  const initialCase = state.cases.some((c) => c.id === wantCase) ? wantCase : state.cases[0].id;
  await loadCase(initialCase);
  setPage(page, { push: false });
  if (page === "compare") await runCompare();

  const hashParts = location.hash.replace("#", "").split("/");
  // 设备页地址:#/blocks/<家族>/<设备> 直接定位;#/blocks/<设备> 是老写法,只认塔剖面
  if (page === "blocks" && hashParts.length >= 3 && hashParts[2]) {
    if (state.families.some((f) => f.key === hashParts[1])) {
      state.family = hashParts[1];
      renderTable();
    }
    openColumn(hashParts[2]);
  } else if (page === "blocks" && hashParts[1]) {
    openColumn(hashParts[1]);
  }

  await resumeReadIfRunning();
}

init();
