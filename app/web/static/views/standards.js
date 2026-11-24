/* Casebook 视图 · standards

管路标准表(3 张)与换热器标准表(2 张)的统一导入。

为什么收在一处:
统一导入:此前管道页与换热 L2 各维护一套导入流程(编码识别、列名预检、报错),
易漏改;现收敛为页面级 #stdModal,首页「标准表」卡片为唯一入口,
其他页面缺表时仅提示「去首页 › 标准表」。

数据来源:
- GET/POST /api/pipe-standards  (GB/T 17395 表 1、表 4 + GB/T 20801.1 表 B.1)
- GET/POST /api/hx-standards    (GB/T 28712.2 表 5/表 7 + 壳程/管程组合)
导入的文件只写在本机,数值受著作权保护,不随项目分发。

读写 state:无(标准表是本机全局配置,与当前工况无关)。
*/

import { esc } from "../lib/core.js";
import { copyPlainText } from "./pump.js";
import { saveFromUrl } from "../download.js";


const FAMILIES = {
  pipe: {
    key: "pipe",
    title: "管路标准表",
    status: "/api/pipe-standards",
    prompt: "/api/pipe-standards-prompt",
    template: "/api/pipe-standards-template.zip",
    done: "导入后立即生效;返回「管道」页,状态转为「已录」,管表自动计算。",
  },
  hx: {
    key: "hx",
    title: "换热器标准表",
    status: "/api/hx-standards",
    prompt: "/api/hx-standards-prompt",
    template: "/api/hx-standards-template.zip",
    done: "导入后立即生效;返回设备 › 换热,几何初选按新标准行重排。",
  },
};

const STD_INFO = { pipe: null, hx: null };   // 各自最近一次 GET 的结果
const STD_PICKED = { pipe: {}, hx: {} };    // 文件名 → {text, fileName, rows, warn, error}

/* 换热那组接口把「加载结果」放在 state 字段里(管道那组是 status)。
   这里用一个小取值函数,业务代码不用记字段名。 */
const STD_STATE_FIELD = "state";

function loadStateOf(data) {
  return (data && data[STD_STATE_FIELD]) || null;
}

let focusTrigger = null;


/* ------------------------------------------------------------ 首页卡片 */

export function initStandards() {
  document.getElementById("homeStdOpen")?.addEventListener("click", () => openStandards());

  const modal = document.getElementById("stdModal");
  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeStandards();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !modal.classList.contains("hidden")) closeStandards();
    });
  }
  document.getElementById("stdClose")?.addEventListener("click", closeStandards);

  // 别处(管道页的缺表卡片、换热 L2)只放一个「去首页 › 标准表」按钮,
  // 统一在这里接:按钮带 data-goto-std="pipe|hx",省得每个页面各绑一次。
  document.addEventListener("click", (e) => {
    const btn = e.target.closest && e.target.closest("[data-goto-std]");
    if (!btn) return;
    e.preventDefault();
    goToStandards(btn.dataset.gotoStd || "pipe");
  });

  document.querySelectorAll("#stdModal .std-family").forEach((box) => {
    const family = box.dataset.family;
    box.querySelector("[data-std-prompt]")?.addEventListener("click", () => copyPrompt(family));
    box.querySelector("[data-std-template]")?.addEventListener("click", (e) => downloadTemplate(e, family));
    box.querySelector("[data-std-import]")?.addEventListener("click", () => importFamily(family));
  });

  refreshStandardsCard();
}

/* 缺表时从别的页跳过来:先切回首页(走侧栏那个正规的切页入口),再开窗。
   不在这里自己拼 hash —— 路由只有 setPage 一条口径。 */
export function goToStandards(family = "pipe") {
  const nav = document.querySelector('.side-nav .nav-item[data-tab="home"]');
  if (nav) nav.click();
  openStandards(family);
}

export async function refreshStandardsCard() {
  const box = document.getElementById("homeStdStatus");
  await Promise.all([loadStatus("pipe", { quiet: true }), loadStatus("hx", { quiet: true })]);
  if (!box) return;
  box.innerHTML = `<div class="home-std-row">${
    ["pipe", "hx"].map((f) => `<div class="home-std-line">${statusLine(f)}</div>`).join("")
  }</div>`;
}

function statusLine(family) {
  const data = STD_INFO[family];
  if (!data) return `<span class="muted">${esc(FAMILIES[family].title)}:无法读取状态</span>`;
  const files = data.files || [];
  const has = files.filter((f) => f.exists).length;
  const ok = has === files.length;
  const loaded = loadStateOf(data);
  const extra = family === "hx" && loaded
    ? ` · ${esc(loaded.label || loaded[STD_STATE_FIELD] || "")}${
      loaded.rows ? ` · ${loaded.rows} 条` : ""
    }${data.version ? ` (${esc(data.version)})` : ""}`
    : "";
  return `<span class="home-std-name">${esc(FAMILIES[family].title)}</span>
    <span class="badge ${ok ? "ok" : "err"}">${ok ? "已录" : "缺 " + (files.length - has) + " 张"}</span>
    <span class="muted">${has} / ${files.length} 个文件${esc(extra)}${
    data.dir ? ` · ${esc(data.dir)}` : ""}</span>`;
}


/* ------------------------------------------------------------ 弹窗开合 */

export async function openStandards(family = "pipe") {
  const modal = document.getElementById("stdModal");
  if (!modal) return;
  focusTrigger = document.activeElement;
  modal.classList.remove("hidden");
  ["pipe", "hx"].forEach((key) => {
    STD_PICKED[key] = {};
    showNotice(key, "error", "");
    showNotice(key, "warn", "");
    showNotice(key, "ok", "");
    const msg = msgEl(key);
    if (msg) msg.textContent = "";
  });
  await Promise.all([loadStatus("pipe"), loadStatus("hx")]);
  const target = modal.querySelector(`.std-family[data-family="${family}"]`);
  if (target) target.scrollIntoView({ block: "nearest" });
  const first = modal.querySelector(".close");
  if (first) first.focus();
}

export function closeStandards() {
  const modal = document.getElementById("stdModal");
  if (modal) modal.classList.add("hidden");
  if (focusTrigger && typeof focusTrigger.focus === "function") focusTrigger.focus();
  focusTrigger = null;
}


/* ------------------------------------------------------------ 状态与文件列表 */

async function loadStatus(family, { quiet = false } = {}) {
  const cfg = FAMILIES[family];
  try {
    const res = await fetch(cfg.status);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    STD_INFO[family] = await res.json();
  } catch (err) {
    STD_INFO[family] = null;
    if (!quiet) showNotice(family, "error", `无法读取${cfg.title}状态:${err.message}(后台服务是否已启动?)`);
    return;
  }
  if (!quiet) {
    renderStatus(family);
    renderFiles(family);
  }
}

function renderStatus(family) {
  const el = document.querySelector(`.std-family[data-family="${family}"] [data-std-status]`);
  const badgeEl = document.querySelector(`.std-family[data-family="${family}"] [data-std-badge]`);
  const data = STD_INFO[family];
  if (!el || !data) return;
  const files = data.files || [];
  const has = files.filter((f) => f.exists).length;
  const ok = has === files.length;
  const loaded = loadStateOf(data);
  if (family === "hx" && loaded) {
    // ok 时 reason 与 label 是同一句话,别把它念两遍;只有非 ok 才补原因
    const why = loaded[STD_STATE_FIELD] === "ok" ? "" : loaded.reason;
    el.textContent = `${has} / ${files.length} 个文件已录 · ${loaded.label || ""}${
      why ? `(${why})` : ""}${
      data.loaded_rows ? ` · 生效 ${data.loaded_rows} 条` : ""}`;
  } else {
    el.textContent = ok
      ? `${files.length} 张表均已录入;再次导入将覆盖为新版本`
      : `已录 ${has} / ${files.length} 张;补齐缺失 CSV 后即可使用`;
  }
  el.classList.toggle("ok", ok);
  if (badgeEl) {
    badgeEl.textContent = ok ? "已录" : `缺 ${files.length - has} 张`;
    badgeEl.className = `badge ${ok ? "ok" : "err"}`;
  }
}

function renderFiles(family) {
  const box = document.querySelector(`.std-family[data-family="${family}"] [data-std-files]`);
  const data = STD_INFO[family];
  if (!box || !data) return;
  const files = data.files || [];
  box.innerHTML = files.map((f) => {
    const got = STD_PICKED[family][f.name];
    const badge = f.exists ? '<span class="badge ok">已录</span>'
      : '<span class="badge err">未录</span>';
    let rowState;
    if (got && got.error) {
      rowState = `<span class="pipe-std-bad">${esc(got.error)}</span>`;
    } else if (got) {
      const warn = got.warn ? ` <span class="pipe-std-warn">${esc(got.warn)}</span>` : "";
      rowState = `<span class="pipe-std-good">已选中 ${esc(got.fileName)} · ${got.rows} 条</span>${warn}`;
    } else if (f.exists) {
      rowState = `本机已有 ${f.rows} 条${f.mtime ? ` · ${esc(f.mtime)}` : ""}`;
    } else {
      rowState = "本机无此文件";
    }
    const opt = (f.optional || []).length
      ? `<span class="muted">(可空列:${(f.optional || []).map((c) => `<code>${esc(c)}</code>`).join("、")})</span>`
      : "";
    return `<div class="pipe-std-row${got && !got.error ? " picked" : ""}" data-name="${esc(f.name)}">
      <div class="pipe-std-head"><b>${esc(f.label)}</b> ${badge}
        <span class="muted">${esc(f.from)}</span></div>
      <div class="pipe-std-cols muted">需要列:${(f.columns || []).map((c) => `<code>${esc(c)}</code>`).join("、")}${opt}
        <code class="pipe-std-file">${esc(f.name)}</code></div>
      <div class="pipe-std-state">${rowState}</div>
      <label class="btn pipe-std-pick">选文件<input type="file" accept=".csv,text/csv" data-std="${esc(f.name)}" hidden></label>
    </div>`;
  }).join("");
  box.querySelectorAll('input[type="file"]').forEach((inp) => {
    inp.addEventListener("change", (e) => onFile(family, inp.dataset.std, e.target.files[0]));
  });
  const btn = document.querySelector(`.std-family[data-family="${family}"] [data-std-import]`);
  if (btn) {
    const any = Object.values(STD_PICKED[family]).some((p) => !p.error);
    btn.disabled = !any;
    btn.textContent = "导入并立即生效";
  }
}


/* ------------------------------------------------------------ 选文件与导入 */

/* Excel 二次保存常把 CSV 变成 GBK/UTF-16;先认 BOM,无 BOM 再按 UTF-8 → gb18030 降级。
   UTF-16 必须最先判:它的字节序列用 UTF-8 硬解会先炸,不能留到最后兜底。 */
async function readCsv(file) {
  const buf = await file.arrayBuffer();
  const u8 = new Uint8Array(buf);
  const has = (a, b, c) => u8.length >= 3 && u8[0] === a && u8[1] === b && u8[2] === c;
  const decode = (label, bytes, fatal = false) => new TextDecoder(label, { fatal }).decode(bytes);
  if (has(0xef, 0xbb, 0xbf)) return decode("utf-8", u8.subarray(3));
  if (u8.length >= 2 && u8[0] === 0xff && u8[1] === 0xfe) return decode("utf-16le", u8.subarray(2));
  if (u8.length >= 2 && u8[0] === 0xfe && u8[1] === 0xff) return decode("utf-16be", u8.subarray(2));
  try {
    return decode("utf-8", u8, true);
  } catch {
    try {
      return decode("gb18030", u8, true);
    } catch {
      throw new Error("无法识别文件编码(支持 UTF-8 / GBK / UTF-16;请以 Excel 另存为 CSV UTF-8)");
    }
  }
}

function fileSpec(family, name) {
  return ((STD_INFO[family] && STD_INFO[family].files) || []).find((f) => f.name === name) || null;
}

/* 前端只做瞬时反馈(空文件 / 乱码 / 明显缺列),真正的判定在服务端。 */
function precheck(family, name, text) {
  if (!text || !text.trim()) return { error: "文件为空" };
  if (text.includes("\ufffd")) {
    return { error: "含乱码:编码可能非 UTF-8。请用 Excel「另存为 → CSV UTF-8」后重试" };
  }
  if (text.length > 8 * 1024 * 1024) return { error: "文件超过 8 MB,不符合标准表格式" };
  const lines = text.replace(/^\ufeff/, "").split(/\r?\n/).filter((l) => l.trim());
  const spec = fileSpec(family, name);
  const need = (spec && spec.columns) || [];
  const header = (lines[0] || "").split(",").map((s) => s.replace(/^"|"$/g, "").trim());
  const missing = need.filter((c) => !header.includes(c));
  const rows = Math.max(0, lines.length - 1);
  const warn = missing.length
    ? `表头缺列:${missing.join("、")}(当前表头 ${header.slice(0, 6).join("、")});以服务端校验为准`
    : "";
  return { rows, warn };
}

async function onFile(family, name, file) {
  if (!file) return;
  showNotice(family, "error", "");
  showNotice(family, "ok", "");
  try {
    const text = await readCsv(file);
    STD_PICKED[family][name] = { text, fileName: file.name, ...precheck(family, name, text) };
  } catch (err) {
    STD_PICKED[family][name] = { error: `无法读取文件:${err.message}` };
  }
  renderFiles(family);
}

async function importFamily(family) {
  const cfg = FAMILIES[family];
  const files = {};
  Object.entries(STD_PICKED[family]).forEach(([name, p]) => {
    if (!p.error) files[name] = p.text;
  });
  if (!Object.keys(files).length) {
    showNotice(family, "error", "请先选择至少一个 CSV 文件");
    return;
  }
  const btn = document.querySelector(`.std-family[data-family="${family}"] [data-std-import]`);
  if (btn) { btn.disabled = true; btn.textContent = "导入中…"; }
  showNotice(family, "error", "");
  showNotice(family, "warn", "");
  showNotice(family, "ok", "");
  try {
    const res = await fetch(cfg.status, {
      method: "POST",
      headers: { "X-Casebook-Client": "1", "Content-Type": "application/json" },
      body: JSON.stringify({ files }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok || !body.ok) {
      const errs = (body.errors || []).join("\n") || `导入失败:HTTP ${res.status}`;
      showNotice(family, "error", errs);
      return;
    }
    STD_PICKED[family] = {};
    showNotice(family, "ok", `已写入 ${Object.keys(files).length} 个文件;${cfg.done}`);
    if ((body.warnings || []).length) {
      showNotice(family, "warn", `已导入;${body.warnings.length} 处需复核(不影响使用):\n${body.warnings.join("\n")}`);
    }
    await loadStatus(family);
    await refreshStandardsCard();
    if (family === "pipe") {
      // 管道页可能正缓存着旧表:让它自己清缓存重算,这里不直接碰它的状态
      window.dispatchEvent(new CustomEvent("casebook:pipe-standards-changed"));
    }
  } catch (err) {
    showNotice(family, "error", `导入失败:${err.message}(后台服务是否已启动?)`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "导入并立即生效"; }
    renderFiles(family);
  }
}


/* ------------------------------------------------------------ 模板与提示词 */

async function downloadTemplate(event, family) {
  event.preventDefault();
  const url = FAMILIES[family].template;
  if (await saveFromUrl(url)) return;
  window.location.href = url;   // 浏览器模式:退回原生下载
}

async function copyPrompt(family) {
  const cfg = FAMILIES[family];
  const msg = msgEl(family);
  const say = (text) => { if (msg) msg.textContent = text; };
  say("正在获取提示词…");
  try {
    const res = await fetch(cfg.prompt);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const text = typeof data.text === "string" ? data.text : "";
    if (!text) throw new Error("提示词为空");
    const ok = await copyPlainText(text);
    say(ok
      ? `已复制 ${text.length} 字;粘贴至 AI 对话框,并附标准表截图或文本`
      : "复制失败:浏览器拒绝写入剪贴板;请按 docs 中对应填写说明手动复制");
  } catch (err) {
    say(`获取提示词失败:${err.message}(后台服务是否已启动?)`);
  }
}


/* ------------------------------------------------------------ 小工具 */

function msgEl(family) {
  return document.querySelector(`.std-family[data-family="${family}"] [data-std-msg]`);
}

function noticeEl(family, kind) {
  return document.querySelector(`.std-family[data-family="${family}"] [data-std-${kind}]`);
}

function showNotice(family, kind, text) {
  const el = noticeEl(family, kind);
  if (!el) return;
  if (!text) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  el.classList.remove("hidden");
  el.textContent = text;
}


