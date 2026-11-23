/* 前端渲染最小回归(Node + DOM 桩,不需要浏览器 / 不需要 Aspen)。

   用法:node tools/check_render.mjs

   为什么要有这个文件
   ------------------
   `node --check` 只查语法。原生 JS 前端的模板字符串如果在拼 innerHTML 时
   读到 state 里缺失的字段,异常会让整块区域静默空白 —— 语法完全没问题,
   页面却是空的。这里把**真实的模块源码**去掉 import/export 与末尾 init()
   后拼成一段,装进 vm 用 DOM 桩跑一遍纯渲染函数与保存逻辑,把这类空白挡在提交前。

   前端现在是 ES Module:入口 `app.js` 只做事件绑定与 init,页面实现在
   `views/`(table / pump / composition / read / flow / shell / column / report),
   共享状态在 `lib/store.js`。vm 不支持 import,所以这里按模块顺序拼接并去掉
   模块语法 —— 跑的还是同一份源码,只是换了装载方式;模块间的 import 可达性
   由上面的静态检查兜住。`pipe.js` 不进这轮覆盖(它要真浏览器,归
   tools/ui_sweep.py),两个入口给空实现。

   覆盖分组:
     ①~⑤ 原有:renderPumpDesign / savePumpDesign / pumpDesignNum
        —— 设计扬程卡渲染、事件绑定、缺字段不空白、保存请求体、全空清除、数值兜底。
     ⑥  renderStreams    流股数据表格(state → #table thead / tbody / #count)
     ⑦  renderHome       首页仪表盘(state.current / blocks / impact)
     ⑧  renderFamilyBar  设备家族页签(state.blocks / families / family)
   每组都验:渲染非空、关键节点标识、不出现 undefined / NaN、state 缺字段不静默空白。

   注意:脚本里的数值只是渲染桩值(非实测读数),只为触发模板分支,不写库。
*/

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const STATIC = path.join(ROOT, "app", "web", "static");

/* 按依赖顺序拼:工具 → 共享状态 → 界面外壳 → 各视图模块 → 主入口。
   视图模块之间有少量互相引用（都发生在函数体内），拼接后同处一个作用域，
   与浏览器里 ES Module 的 live binding 等价；真实模块图由 ui_sweep 兜底。 */
const MODULES = [
  path.join(STATIC, "lib", "core.js"),
  path.join(STATIC, "lib", "store.js"),
  path.join(STATIC, "lib", "ui.js"),
  path.join(STATIC, "views", "column.js"),
  path.join(STATIC, "views", "report.js"),
  path.join(STATIC, "views", "table.js"),
  path.join(STATIC, "views", "pump.js"),
  path.join(STATIC, "views", "heat.js"),
  path.join(STATIC, "views", "composition.js"),
  path.join(STATIC, "views", "read.js"),
  path.join(STATIC, "views", "flow.js"),
  path.join(STATIC, "views", "shell.js"),
  path.join(STATIC, "download.js"),
  path.join(STATIC, "app.js"),
];

function stripModuleSyntax(src) {
  return src
    .replace(/^\s*import\s[\s\S]*?from\s+"[^"]*";\s*$/gm, "")
    .replace(/^export\s+/gm, "");
}

const appRaw = fs.readFileSync(MODULES[MODULES.length - 1], "utf8");
// 末尾的 init() 会拉接口、绑定整页事件;这里只需要函数声明本身
const appHeadless = appRaw.replace(/^init\(\);\s*$/m, "");
if (appHeadless === appRaw) {
  console.error("✗ 没找到文件末尾的 init(); —— app.js 结构变了,请同步更新本脚本");
  process.exit(1);
}

/* ── 模块依赖静态检查 ───────────────────────────────────────────────
   vm 拼接后所有模块同处一个作用域,"引用了别的模块的名字却忘了 import"
   在这里查不出来,只有真浏览器会报 ReferenceError。所以先静态对一遍:
   ① 每条 import 的名字必须存在于目标模块的顶层定义;
   ② 每个模块里出现过的、定义在其它模块的顶层名字,必须出现在它的
      import 列表里(app.js 的 bindEvents / init / installSampleData 是
      入口私有,不对外导出,见外的引用只会来自注释,排除)。 */
function topDefs(src) {
  const defs = new Set();
  const re =
    /^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)|^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)/gm;
  for (let m; (m = re.exec(src)); ) defs.add(m[1] || m[2]);
  return defs;
}

function checkModuleDeps() {
  const files = [...MODULES, path.join(STATIC, "pipe.js")];
  const sources = new Map(files.map((f) => [path.basename(f), fs.readFileSync(f, "utf8")]));
  const defsByFile = new Map([...sources].map(([f, s]) => [f, topDefs(s)]));
  const allDefs = new Set([...defsByFile.values()].flatMap((d) => [...d]));
  // installSampleData 已从 app.js 移到 views/shell.js 并导出(空态首屏会直接调它),
  // 所以不再算"入口私有函数":谁引用它就必须 import,否则这里会报。
  const ENTRY_PRIVATE = new Set(["bindEvents", "init"]);
  const problems = [];
  for (const [file, src] of sources) {
    // 先剥注释再找引用:注释里提到的函数名不算引用(实测占误报的大头)
    const code = src.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/(?:^|\s)\/\/[^\n]*/g, " ");
    // 再剥字符串(含跨行模板):class 名、URL 里的词不算引用;${} 内的调用
    // 走下面的"调用形式"检查,不受影响
    const bare = code
      .replace(/`(?:\\.|[^\\`])*`/gs, "``")
      .replace(/(["'])(?:\\.|(?!\1)[^\\])*\1/g, '""')
      // 局部 const/let/var/function 定义不是对外部名字的引用,一并抹掉
      .replace(/\b(?:const|let|var|function)\s+[A-Za-z_$][\w$]*/g, "kw");
    const imported = new Set();
    for (const m of src.matchAll(/import\s*\{([^}]*)\}\s*from\s*"[^"]+"/g)) {
      const target = path.basename(m[0].match(/from\s*"([^"]+)"/)[1]);
      const tdefs = defsByFile.get(target);
      for (const raw of m[1].split(",")) {
        const name = raw.trim().split(/\s+as\s+/)[0].trim();
        if (!name) continue;
        imported.add(name);
        if (!tdefs) problems.push(`${file}: import 的目标 ${target} 不在检查清单里`);
        else if (!tdefs.has(name))
          problems.push(`${file}: import { ${name} } — ${target} 里没有这个名字`);
      }
    }
    const own = defsByFile.get(file);
    for (const name of allDefs) {
      if (own.has(name) || imported.has(name) || ENTRY_PRIVATE.has(name)) continue;
      const callHit = new RegExp(`\\b${name}\\s*\\(`).test(code);
      const wordHit = new RegExp(`\\b${name}\\b`).test(bare);
      if (callHit || wordHit)
        problems.push(`${file}: 引用了 ${name}(定义在别的模块)但没有 import`);
    }
  }
  if (problems.length) {
    console.error("✗ 前端模块依赖检查不过:");
    for (const p of problems) console.error("  ✗ " + p);
    process.exit(1);
  }
  console.log("前端模块依赖检查:import 全部可达,无漏 import");
}

checkModuleDeps();

const headless = [
  "/* pipe.js 的两个入口:本轮不覆盖管道页,给空实现 */",
  "function initPipePanel() {}",
  "function renderPipes() {}",
  ...MODULES.slice(0, -1).map((file) => stripModuleSyntax(fs.readFileSync(file, "utf8")).trim()),
  stripModuleSyntax(appHeadless).trim(),
].join("\n\n");

let passed = 0;
const failures = [];
function ok(label, cond, detail = "") {
  if (cond) passed += 1;
  else failures.push(`${label}${detail ? " — " + detail : ""}`);
}
function has(label, hay, needle) {
  ok(label, String(hay).includes(needle), `缺少 ${needle}`);
}

/* 只实现 app.js 在渲染路径上真正用到的那几个 DOM 接口。
   querySelectorAll 的结果缓存下来,外部才能断言事件有没有绑上。 */
class El {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this._lastQueries = {};
    this._innerHTML = "";
    this.children = [];   // appendChild 攒下来,renderReportPick 追加节点时才能断言
    this.value = "";
    this.textContent = "";
    this.title = "";
    this.scrollTop = 0;
    this.classList = {
      add() {}, remove() {}, toggle() {}, contains() { return false; },
    };
  }
  set innerHTML(v) { this._innerHTML = String(v); this.children = []; }  // 重写 innerHTML 清空子节点(与真实 DOM 一致)
  get innerHTML() { return this._innerHTML; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener() {}
  setAttribute(k, v) { this[k] = v; }
  getAttribute(k) { return this[k]; }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) { return child; }
  querySelector() { return null; }
  querySelectorAll(selector) {
    let found = [];
    if (selector === ".pump-design-input") {
      found = ["dz", "hf", "l", "zeta", "idMm", "muCp", "epsMm"].map((key) => {
        const el = new El("input");
        el.dataset.key = key;
        return el;
      });
    } else if (selector === ".pump-fit-input") {
      found = ["el90", "gate", "globe", "check", "reducer"].map((key) => {
        const el = new El("input");
        el.dataset.fit = key;
        return el;
      });
    } else if (selector === ".pump-design-select") {
      // λ 口径下拉:renderPumpDesign 给它绑 change,桩里造一个 select 供断言触发
      const el = new El("select");
      el.dataset.key = "lambdaMethod";
      found = [el];
    }
    this._lastQueries[selector] = found;
    return found;
  }
  focus() {}
  click() {}
  closest() { return null; }
  getBoundingClientRect() {
    return { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0 };
  }
}

const elements = new Map();
function byId(id) {
  if (!elements.has(id)) elements.set(id, new El());
  return elements.get(id);
}

const documentStub = {
  documentElement: new El("html"),
  body: new El("body"),
  getElementById: byId,
  querySelector(selector) {
    if (selector === '[data-key="zeta"]') return byId("__zeta-input");
    // 表格类渲染用 #table thead / #table tbody 取容器:缓存成稳定桩,
    // 外部才能读回 innerHTML 做断言
    if (selector === "#table thead") return byId("__table-thead");
    if (selector === "#table tbody") return byId("__table-tbody");
    return null;
  },
  querySelectorAll() { return []; },
  addEventListener() {},
  createElement(tag) { return new El(tag); },
  createElementNS(_ns, tag) { return new El(tag); },
};

const locationStub = { hash: "", search: "", href: "" };
const matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
});
const windowStub = {
  matchMedia,
  addEventListener() {}, removeEventListener() {},
  location: locationStub,
  localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
  open() {}, close() {},
  devicePixelRatio: 1,
};

const capture = [];
const ctx = vm.createContext({
  console,
  document: documentStub,
  window: windowStub,
  location: locationStub,
  navigator: { userAgent: "node" },
  localStorage: windowStub.localStorage,
  matchMedia,
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  clearInterval: () => {},
  requestAnimationFrame: () => 0,
  fetch: async () => ({ ok: true, json: async () => ({}) }),
  URL,
  URLSearchParams,
  Blob: class {},
  FormData: class {},
  Image: class {},
  WebSocket: class {},
  alert: () => {},
  confirm: () => true,
});
ctx.__captured = capture;
ctx.__row = { name: "P-STUB", head_m: 12.5 };

try {
  vm.runInContext(headless, ctx, { filename: "app.js" });
} catch (err) {
  console.error(`✗ 加载 app.js 失败: ${err.message}`);
  process.exit(1);
}

// 网络写操作改成捕获请求体;页面提示函数不需要真 DOM
vm.runInContext(`
  state.current = { id: 1 };
  apiPost = async function (url, body) { __captured.push({ url, body }); return { ok: true }; };
  reportNotice = function () {};
`, ctx);

const box = byId("pumpDesign");

// ① 完整视图:两张小表、输入、结论都要在;来源提示可辨
vm.runInContext(`
  const mk = (stream, dn, hf, dz, l, zeta, fittings) => ({
    stream, status: "linked", dn, od_mm: dn === 100 ? 114.3 : 88.9, wall_mm: 4,
    id_mm: dn === 100 ? 106.3 : 80.9, dn_source: "auto", missing: [], version: 0,
    hf_m: hf, u_ms: 2.1, re: 12000, lam: 0.028,
    run: { l_m: l, zeta_sum: zeta, dz_m: dz, fittings, lambda_method: "colebrook" },
  });
  state.pumpDesign = {
    view: {
      suction: mk("S1", 100, 1.23, 9.45, 40, 17.43, { el90: 3, gate: 1 }),
      discharge: mk("S2", 80, 0.77, 0, 20, 5, {}),
      totals: { head_m: 20, dz_m: 9.45, hf_m: 2, design_head_m: 31.45,
                selection_head_m: 35, partial_hf_m: 2, partial_dz_m: 9.45 },
      npsh: { device_m: 15.29, corrected_m: 11.06, state: "ok", threshold_m: 3.5,
              margin_used_m: 0.5, margin_is_default: true, reason: "渲染桩值" },
      run_sources: {}, tables_error: "",
    },
    versions: {}, edits: {}, openSide: null, legacy: null, source: null,
  };
`, ctx);
vm.runInContext("renderPumpDesign(__row)", ctx);
const html = box.innerHTML;
ok("完整 state 渲染结果非空", html.length > 0);
has("渲染出两侧管段表", html, "pump-line-table");
has("渲染出结果行容器", html, "pumpDesignSum");
has("渲染出管长输入", html, 'data-key="l_m"');
has("渲染出 Σζ 输入", html, 'data-key="zeta_sum"');
has("渲染出 ΔZ 输入", html, 'data-key="dz_m"');
has("渲染出 DN 跳转", html, "pump-line-jump");
ok("渲染结果不含 undefined", !html.includes("undefined"));
ok("渲染结果不含 NaN", !html.includes("NaN"));
ok("结果行有内容", byId("pumpDesignSum").innerHTML.includes("31.45"));

// 展开管件区:数量回填、输入都绑 change
vm.runInContext("state.pumpDesign.openSide = 'suction'; renderPumpDesign(__row)", ctx);
has("渲染出管件数量区", box.innerHTML, "常用管件");
has("渲染出管件输入", box.innerHTML, 'data-fit="el90"');
has("管件数量回填到输入框", box.innerHTML, 'value="3"');
const fits = box._lastQueries[".pump-fit-input"] || [];
ok("5 个管件输入都绑了 change", fits.length === 5
  && fits.every((el) => (el.listeners.change || []).length), `实际 ${fits.length} 个`);

// ①b 值借自同模型旧版本时:必须标出来源,不能让人以为是本版填的
vm.runInContext(`
  state.pumpDesign.view.run_sources = {
    S1: { case_id: 66, read_at: "2026-11-19 10:30:00", match: "same-path" },
  };
`, ctx);
vm.runInContext("renderPumpDesign(__row)", ctx);
ok("借来的参数渲染出来源提示", box.innerHTML.includes("来自同模型旧版本 #66"));
has("来源提示带时间", box.innerHTML, "2026-11-19 10:30");
has("来源提示用统一样式", box.innerHTML, "pump-design-note");
ok("来源提示写明保存后写回当前工况", box.innerHTML.includes("保存后写入当前工况"));
vm.runInContext("state.pumpDesign.view.run_sources = {}; renderPumpDesign(__row)", ctx);
ok("本版自己填的不出现来源提示", !box.innerHTML.includes("pump-design-note"));

// ② 没载入视图(老服务 / 半初始化):不能整块静默空白
vm.runInContext("state.pumpDesign = { view: null, edits: {}, openSide: null };", ctx);
vm.runInContext("renderPumpDesign(__row)", ctx);
const sparse = box.innerHTML;
ok("缺视图时不静默空白", sparse.length > 0);
ok("缺视图时不出现 undefined", !sparse.includes("undefined"));
ok("缺视图时不出现 NaN", !sparse.includes("NaN"));

// ⑤ 缺数据一律 null,不拿 0 或常见值顶替
const nums = vm.runInContext(
  "[pumpDesignNum(null), pumpDesignNum('x'), pumpDesignNum(NaN), pumpDesignNum(2.5)]",
  ctx,
);
ok("非有限数字一律 null", JSON.stringify(nums) === "[null,null,null,2.5]",
  JSON.stringify(nums));

/* =====================================================================
   新增覆盖:三组纯渲染函数。只在本文件扩展 DOM 桩,不改 app.js。
   三处都只读 state + 拼 innerHTML,不 await api(),不碰 SVG / Canvas;
   数值都是明显的渲染桩值(非实测读数),只为触发模板分支。
   ===================================================================== */
const basePassed = passed;   // 记录原有 22 条,便于末尾统计新增条数

// ⑥ 流股数据表格:state.streams / tab / filter / impact → thead、tbody、count
console.log("\n[新增组 ⑥] renderStreams —— 流股数据表格(state → thead/tbody/count)");
vm.runInContext(`
  state.tab = "streams";
  state.filter = "";
  state.impact = null;
  state.streams = [
    { name: "S-STUB-1", temperature_c: 120.5, pressure_bar: 3.25,
      mass_flow_kgh: 1000.5, mole_flow_kmolh: 20.25, vap_frac: 0.75,
      vol_flow_m3h: 88.5, enthalpy_kjkg: -2500.5, enthalpy_kjkmol: -50000.5,
      enthalpy_flow_kw: -694.5 },
    { name: "S-STUB-2", temperature_c: 80.25, pressure_bar: 1.013,
      mass_flow_kgh: 500.25, mole_flow_kmolh: 10.5, vap_frac: 0,
      vol_flow_m3h: 44.25, enthalpy_kjkg: -1200.25, enthalpy_kjkmol: -24000.5,
      enthalpy_flow_kw: -166.75 },
  ];
`, ctx);
vm.runInContext("renderStreams()", ctx);
const theadHtml = byId("__table-thead").innerHTML;
const tbodyHtml = byId("__table-tbody").innerHTML;
const streamCount = byId("count").textContent;
ok("⑥ 流股表 thead 非空", theadHtml.length > 0);
ok("⑥ 流股表 tbody 非空", tbodyHtml.length > 0);
has("⑥ 表头含温度列", theadHtml, "温度 ℃");
has("⑥ 表头含质量焓列", theadHtml, "质量焓 kJ/kg");
has("⑥ 行含流股链接节点", tbodyHtml, 'class="stream-link"');
has("⑥ 行含流股名标识", tbodyHtml, 'data-stream="S-STUB-1"');
has("⑥ 计数行回填行数", streamCount, "2 行");
ok("⑥ 表体不含 undefined", !tbodyHtml.includes("undefined"));
ok("⑥ 表体不含 NaN", !tbodyHtml.includes("NaN"));

// 缺字段:数值列全缺,靠 fmt / esc 兜底,不能整块静默空白
vm.runInContext(`
  state.streams = [{ name: "S-STUB-SPARSE" }];
  state.impact = null;
`, ctx);
vm.runInContext("renderStreams()", ctx);
const sparseThead = byId("__table-thead").innerHTML;
const sparseTbody = byId("__table-tbody").innerHTML;
ok("⑥ 缺字段时 thead 不静默空白", sparseThead.length > 0);
ok("⑥ 缺字段时 tbody 不静默空白", sparseTbody.length > 0);
has("⑥ 缺字段时仍渲染出该行", sparseTbody, "S-STUB-SPARSE");
ok("⑥ 缺字段时不出现 undefined", !sparseTbody.includes("undefined"));
ok("⑥ 缺字段时不出现 NaN", !sparseTbody.includes("NaN"));

// ⑦ 首页仪表盘:state.current / blocks / impact → homeCards、homeProblems
console.log("\n[新增组 ⑦] renderHome —— 首页仪表盘(state.current / blocks / impact)");
vm.runInContext(`
  state.current = {
    id: 1, model_name: "MODEL-STUB", model_path: "", read_at: "2026-01-01 00:00:00",
    run_id: "RUN-STUB", elapsed_sec: 1.25,
    stream_count: 2, block_count: 1, connection_count: 2,
    converged: true, reasons: ["桩值:仅用于渲染"], status: { BLKSTAT: 0 },
    severity: "ok",
  };
  state.blocks = [
    { name: "B-STUB-1", type: "Pump", status: 1, message: "桩值:模块报错",
      balance_state: "ok", nstage: null },
  ];
  state.impact = { summary: { total: 1, error: 1, warning: 0 } };
  state.emptyText = "";
  state.emptyIsError = false;
`, ctx);
vm.runInContext("renderHome()", ctx);
const homeHtml = byId("homeCards").innerHTML;
const problemsHtml = byId("homeProblems").innerHTML;
ok("⑦ 首页卡片非空", homeHtml.length > 0);
has("⑦ 含当前工况卡", homeHtml, "当前工况");
has("⑦ 含规模卡", homeHtml, "规模");
has("⑦ 含判定依据卡", homeHtml, "判定依据");
has("⑦ 含焓平衡自检卡", homeHtml, "焓平衡自检");
has("⑦ 含指标块 metric-row", homeHtml, "metric-row");
has("⑦ 回填模型名", homeHtml, "MODEL-STUB");
has("⑦ 异常模块列表含位号", problemsHtml, "B-STUB-1");
ok("⑦ 首页不含 undefined", !homeHtml.includes("undefined"));
ok("⑦ 首页不含 NaN", !homeHtml.includes("NaN"));

// 缺字段:半初始化 current 只有 id,blocks / impact 为空,靠 ?? / || / esc 兜底
vm.runInContext(`
  state.current = { id: 1 };
  state.blocks = [];
  state.impact = null;
`, ctx);
vm.runInContext("renderHome()", ctx);
const sparseHome = byId("homeCards").innerHTML;
ok("⑦ 缺字段时首页不静默空白", sparseHome.length > 0);
has("⑦ 缺字段时仍渲染出仪表盘骨架", sparseHome, "焓平衡自检");
ok("⑦ 缺字段时不出现 undefined", !sparseHome.includes("undefined"));
ok("⑦ 缺字段时不出现 NaN", !sparseHome.includes("NaN"));

// 空库:current 为 null 时走引导分支,不能只剩一张白卡
vm.runInContext("state.current = null;", ctx);
vm.runInContext("renderHome()", ctx);
const emptyHome = byId("homeCards").innerHTML;
ok("⑦ 无工况时首页不静默空白", emptyHome.length > 0);
has("⑦ 无工况时渲染出引导卡片", emptyHome, "还没有工况");
// 空态按钮的回调是 views/shell.js 导出的 installSampleData;谁引用谁 import,
// 漏 import 时浏览器里首屏会直接抛 ReferenceError(2026-11-19 空库首屏事故)
ok("⑦ 空态给出载入示例按钮", !!byId("loadSampleBtn"));

// ⑦b 判定条:empty 状态必须用 verdict 专属类名,不能撞上 .empty 空表占位样式
//     (padding:40px + text-align:center 会把首屏判定条撑成巨型居中卡片)
console.log("\n[新增组 ⑦b] renderBanner —— 空结果判定条类名不撞 .empty");
vm.runInContext(`
  state.current = {
    id: 2, model_name: "EMPTY-STUB", model_path: "", read_at: "2026-01-01 00:00:00",
    run_id: "", elapsed_sec: 0,
    stream_count: 3, block_count: 2, connection_count: 0,
    converged: false, reasons: ["桩值:没有结果"], status: {},
    severity: "empty",
  };
  state.bannerCollapsed = false;
  renderBanner();
`, ctx);
const verdictClass = byId("verdict").className;
ok("⑦b 空结果判定条不套 .empty 类", !/(^|\s)empty(\s|$)/.test(verdictClass));
has("⑦b 空结果用 idle 类", verdictClass, "idle");
has("⑦b 空结果仍显示原判定文案", byId("verdict").innerHTML, "没有结果");

// ⑧ 设备家族页签:state.blocks / families / family → blockFamilies
console.log("\n[新增组 ⑧] renderFamilyBar —— 设备家族页签(state.blocks / families / family)");
vm.runInContext(`
  state.blocks = [
    { name: "P-STUB-1", family: "pump", status: 1 },
    { name: "P-STUB-2", family: "pump", status: 2 },
    { name: "C-STUB-1", family: "column", status: 1 },
  ];
  state.families = [
    { key: "column", label: "塔" },
    { key: "pump", label: "泵" },
  ];
  state.family = "pump";
`, ctx);
vm.runInContext("renderFamilyBar()", ctx);
const famHtml = byId("blockFamilies").innerHTML;
ok("⑧ 家族页签非空", famHtml.length > 0);
has("⑧ 含泵页签标识", famHtml, 'data-family="pump"');
has("⑧ 含塔页签标识", famHtml, 'data-family="column"');
has("⑧ 页签显示家族名", famHtml, "泵");
has("⑧ 当前页签带 active", famHtml, 'class="active"');
has("⑧ 页签显示设备计数", famHtml, "<b>2</b>");
ok("⑧ 家族页签不含 undefined", !famHtml.includes("undefined"));
ok("⑧ 家族页签不含 NaN", !famHtml.includes("NaN"));

// 缺字段:家族项半初始化(缺 label)、state.family 还没挑,esc 兜底不能整块空白
vm.runInContext(`
  state.blocks = [{ name: "P-STUB-1", family: "pump" }];
  state.families = [{ key: "pump" }];
  state.family = null;
`, ctx);
vm.runInContext("renderFamilyBar()", ctx);
const sparseFam = byId("blockFamilies").innerHTML;
ok("⑧ 缺字段时页签不静默空白", sparseFam.length > 0);
has("⑧ 缺字段时仍渲染出 data-family", sparseFam, 'data-family="pump"');
ok("⑧ 缺字段时不出现 undefined", !sparseFam.includes("undefined"));
ok("⑧ 缺字段时不出现 NaN", !sparseFam.includes("NaN"));

// ⑨ 设计扬程 / 选型台账:悬浮窗里不再折叠,标题常驻、内容直接可见
console.log("\n[新增组 ⑨] 常驻区块 —— 设计扬程 / 选型台账");
vm.runInContext(`
  state.pumpFold = { design: false, selection: false };
  state.pumpDesign = {
    dz: 9.45, hf: 5.93, l: 40, zeta: 17.43,
    idMm: 52.5, muCp: 0.6, epsMm: 0.05, calc: null, idNote: "",
    fittings: { el90: 3, gate: 1, globe: 0, check: 1, reducer: 0 },
    manual: { idMm: null, muCp: null, epsMm: null },
  };
`, ctx);
vm.runInContext("renderPumpDesign(__row)", ctx);
const designFold = byId("pumpDesign");
const foldHtml = designFold.innerHTML;
has("⑨ 设计扬程有常驻标题", foldHtml, '<div class="pump-block-head"');
has("⑨ 标题文字保留", foldHtml, "设计扬程 / 管路阻力");
ok("⑨ 不再出现折叠箭头", !foldHtml.includes("pump-fold-mark"));
has("⑨ 标题行仍显示扬程摘要", foldHtml, "选型");
ok("⑨ 设计扬程内容不含 undefined", !foldHtml.includes("undefined"));

vm.runInContext(`
  renderPumpSelection(__row, {
    saved: { model_no: "IS65-50-160" },
    npsh_state: "ok", npsh_reason: "渲染桩值", margin_is_default: true,
    margin_used_m: 0.5, flow_m3h: 4.32, design_head_m: 13.0,
    selection_head_m: 15, npsh_a_m: 21.16, motor_suggested_kw: 1.5,
  });
`, ctx);
const selFold = byId("pumpSelection");
const selFoldHtml = selFold.innerHTML;
has("⑨ 台账有常驻标题", selFoldHtml, '<div class="pump-block-head"');
has("⑨ 台账打开时显示型号", selFoldHtml, "IS65-50-160");
has("⑨ 台账标题行显示三态", selFoldHtml, "pump-sel-state-mini");
has("⑨ 台账内容直接可见", selFoldHtml, "pump-sel-live");
ok("⑨ 台账内容不含 undefined", !selFoldHtml.includes("undefined"));
ok("⑨ 本版自己填的台账不出现来源提示", !selFoldHtml.includes("pump-design-note"));

vm.runInContext(`
  renderPumpSelection(__row, {
    saved: { model_no: "IS65-50-160" },
    npsh_state: "ok", npsh_reason: "渲染桩值", margin_is_default: true,
    margin_used_m: 0.5, flow_m3h: 4.32, design_head_m: 13.0,
    selection_head_m: 15, npsh_a_m: 21.16, motor_suggested_kw: 1.5,
    sources: { selection: { case_id: 66, read_at: "2026-11-19 10:30:00" }, design: null },
  });
`, ctx);
const selSrcHtml = selFold.innerHTML;
ok("⑨ 台账借自旧版本时标来源", selSrcHtml.includes("台账来自同模型旧版本 #66"));
has("⑨ 台账来源带时间", selSrcHtml, "2026-11-19 10:30");
ok("⑨ 台账来源提示也写明保存写回", selSrcHtml.includes("保存后写入当前工况"));

const roundBase = passed;   // 本轮新增(⑩/⑪)前的基线

// ⑩ 两侧管段小表:DN / L / Σζ / ΔZ / Σhf,保存走 /lines/{stream}
console.log("[新增组 ⑩] 泵两侧管段 —— 两行小表 / 管件与管径 / 保存请求");
vm.runInContext(`
  state.current = { id: 1 };
  state.pumpFold = { design: false, selection: false };
  const line = (stream, dn, hf, role) => ({
    stream, role, status: "linked", reason: "", dn, od_mm: dn === 100 ? 114.3 : 88.9,
    wall_mm: 4, id_mm: dn === 100 ? 106.3 : 80.9, dn_source: "auto",
    flow_m3h: 40, rho_kgm3: 1000, mu_cp: 0.6, mu_source: "aspen",
    u_ms: 2.1, re: 12000, lam: 0.028, hf_m: hf, l_m: 10, zeta_sum: 5,
    dz_m: 2, lambda_method: "colebrook", fittings: { el90: 3, gate: 1 },
    missing: [], note: "", version: 0,
    run: { l_m: 10, zeta_sum: 5, dz_m: 2, fittings: { el90: 3, gate: 1 },
           lambda_method: "colebrook", version: 0 },
  });
  state.pumpDesign = {
    view: {
      suction: line("S1", 100, 1.23, "suction"),
      discharge: line("S2", 80, 0.77, "discharge"),
      totals: { head_m: 20, dz_m: 4, hf_m: 2, design_head_m: 26,
                selection_head_m: 29, partial_hf_m: 2, partial_dz_m: 4 },
      npsh: { device_m: 15.29, corrected_m: 11.06, state: "ok", threshold_m: 3.5,
              margin_used_m: 0.5, margin_is_default: true, reason: "渲染桩值" },
      run_sources: {}, tables_error: "",
    },
    versions: {}, edits: {}, openSide: null, legacy: null, source: null,
  };
`, ctx);
vm.runInContext("renderPumpDesign(__row)", ctx);
const lineHtml = box.innerHTML;
has("⑩ 渲染出两侧小表", lineHtml, "pump-line-table");
has("⑩ 表内直接显示自动粘度", lineHtml, "μ cP");
has("⑩ 粘度回填 Aspen 值", lineHtml, "0.6000");
has("⑩ 默认打开吸入段详情", lineHtml, 'data-detail="suction"');
has("⑩ 详情标注当前修改流股", lineHtml, "当前修改");
has("⑩ 吸入那一行", lineHtml, ">吸入<");
has("⑩ 排出那一行", lineHtml, ">排出<");
has("⑩ DN 可跳转", lineHtml, 'class="pump-line-jump"');
has("⑩ 管长输入", lineHtml, 'data-key="l_m"');
has("⑩ Σζ 输入", lineHtml, 'data-key="zeta_sum"');
has("⑩ ΔZ 输入", lineHtml, 'data-key="dz_m"');
has("⑩ 状态徽标", lineHtml, "已关联");
has("⑩ 设计扬程结论", lineHtml, "26.00");
ok("⑩ 渲染不含 undefined", !lineHtml.includes("undefined"));
ok("⑩ 渲染不含 NaN", !lineHtml.includes("NaN"));

// 展开管件 / 管径:人工 DN 只从标准系列选,超出才自定义外径 + 壁厚
vm.runInContext("state.pumpDesign.openSide = 'suction';", ctx);
vm.runInContext("renderPumpDesign(__row)", ctx);
const detailHtml = box.innerHTML;
has("⑩ 展开管件面板", detailHtml, 'data-detail="suction"');
has("⑩ 管件数量输入", detailHtml, 'data-fit="el90"');
has("⑩ 管径下拉", detailHtml, 'class="pump-design-select pump-line-dn"');
has("⑩ 管径含自动", detailHtml, "自动(跟随管道页)");
has("⑩ 管径含 DN100", detailHtml, "DN100");
has("⑩ 管径含自定义", detailHtml, "自定义外径 / 壁厚");
ok("⑩ 读到的粘度直接进输入框", detailHtml.includes('data-key="mu_cp"')
  && detailHtml.includes('value="0.6000"'));
ok("⑩ 保存过的 L/Σζ 从 run 回填", detailHtml.includes('data-key="l_m"')
  && detailHtml.includes('value="10"') && detailHtml.includes('value="5"'));
ok("⑩ 读到粘度不再用自动占位", !detailHtml.includes("自动 0.6"));

// 手填 λ:出现 λ 值输入框并回填(0.0314 为渲染桩值,非实测读数)
vm.runInContext(`
  state.pumpDesign.edits.suction.lambda_method = "manual";
  state.pumpDesign.edits.suction.lambda_manual = 0.0314;
`, ctx);
vm.runInContext("renderPumpDesign(__row)", ctx);
const lamHtmlMan = box.innerHTML;
has("⑩ manual 口径选中", lamHtmlMan, 'value="manual" selected');
has("⑩ manual 出现 λ 值输入框", lamHtmlMan, 'data-key="lambda_manual"');
has("⑩ λ 值回填(渲染桩值)", lamHtmlMan, 'value="0.0314"');

// 保存:两侧各发一次 /lines/{stream},带 version
vm.runInContext(`
  state.pumpDesign.edits.suction.lambda_method = "colebrook";
  state.pumpDesign.edits.suction.lambda_manual = null;
  state.pumpDesign.edits.discharge = JSON.parse(JSON.stringify(state.pumpDesign.edits.suction));
`, ctx);
capture.length = 0;
await vm.runInContext("savePumpDesign(__row)", ctx);
const linePosts = capture.filter((c) => c.url.includes("/lines/"));
ok("⑩ 两侧各发一次保存请求", linePosts.length === 2, `实际 ${linePosts.length}`);
ok("⑩ 吸入保存到 /lines/S1", linePosts.some((c) => c.url.endsWith("/lines/S1")));
ok("⑩ 排出保存到 /lines/S2", linePosts.some((c) => c.url.endsWith("/lines/S2")));
ok("⑩ 请求体带版本次", Boolean(linePosts[0] && linePosts[0].body.version === 0));
ok("⑩ 请求体带 Σζ", Boolean(linePosts[0] && linePosts[0].body.zeta_sum === 5));

// 未勾管件、未手填 0:Σζ 必须保存 null,而不是 0,否则会给出"只有沿程"的假 Σhf
vm.runInContext(`
  state.pumpDesign.view = {
    suction: { stream: "S1", version: 0, run: {}, zeta_sum: null },
    discharge: { stream: "S2", version: 0, run: {}, zeta_sum: null },
  };
  const ed = { l_m: 12, zeta_sum: null, dz_m: 2, fittings: {}, lambda_method: "colebrook",
               lambda_manual: null, dn_override: null, custom: false };
  state.pumpDesign.edits = { suction: { ...ed }, discharge: { ...ed } };
  applyFittings("suction");
`, ctx);
capture.length = 0;
await vm.runInContext("savePumpDesign(__row)", ctx);
const nullZetaPosts = capture.filter((c) => c.url.includes("/lines/"));
ok("⑩ 未填管件保存 Σζ=null", Boolean(
  nullZetaPosts.length === 2 && nullZetaPosts.every((c) => c.body.zeta_sum === null)
), JSON.stringify(nullZetaPosts.map((c) => c.body.zeta_sum)));

// 保存后真实页面会重新拉 design-head;测试桩不返回数据,这里补回视图再继续
vm.runInContext(`
  state.pumpDesign.view = {
    suction: { stream: "S1", status: "linked", dn: 100, dn_source: "auto", hf_m: 1.23,
               missing: [], version: 0, run: {} },
    discharge: { stream: "S2", status: "linked", dn: 80, dn_source: "auto", hf_m: 0.77,
                 missing: [], version: 0, run: {} },
    totals: { head_m: 20, dz_m: 4, hf_m: 2, design_head_m: 26, selection_head_m: 29,
              partial_hf_m: 2, partial_dz_m: 4 },
    npsh: { device_m: 15.29, corrected_m: 11.06, state: "ok", threshold_m: 3.5,
            margin_used_m: 0.5, margin_is_default: true, reason: "渲染桩值" },
    run_sources: {}, tables_error: "",
  };
`, ctx);

// 待指定流股:渲染下拉,不硬猜
vm.runInContext(`
  state.pumpDesign.view.suction = { status: "ambiguous", stream: null,
    streams: ["S1", "S1b"], reason: "泵有 2 条进口流股,请指定", missing: [] };
  state.pumpDesign.openSide = null;
`, ctx);
vm.runInContext("renderPumpDesign(__row)", ctx);
has("⑩ 待指定渲染下拉", box.innerHTML, 'class="pump-design-select pump-pick"');
has("⑩ 下拉列出候选", box.innerHTML, "S1b");
ok("⑩ 待指定不渲染该侧输入", !box.innerHTML.includes('data-side="suction" data-key="l_m"'));

// 缺一侧:只说缺,不给设计扬程
vm.runInContext(`
  state.pumpDesign.view.suction = { status: "missing", stream: null, streams: [],
    reason: "泵缺进口流股,无法关联管路", missing: [] };
  state.pumpDesign.view.totals.design_head_m = null;
  state.pumpDesign.view.totals.selection_head_m = null;
`, ctx);
vm.runInContext("renderPumpDesign(__row)", ctx);
has("⑩ 缺一侧写明原因", box.innerHTML, "泵缺进口流股");
has("⑩ 缺一侧只给已计阻力", box.innerHTML, "两侧未齐");

// 台账同时给进口基准值与修正值(判据用修正值)
vm.runInContext(`
  renderPumpSelection(__row, {
    saved: { model_no: "IS65-50-160" },
    npsh_state: "ok", npsh_reason: "渲染桩值", margin_is_default: true,
    margin_used_m: 0.5, flow_m3h: 4.32, design_head_m: 26,
    selection_head_m: 29, npsh_a_m: 15.29, npsh_a_corrected_m: 11.06,
    motor_suggested_kw: 1.5,
    sources: { selection: null, design: null },
  });
`, ctx);
has("⑩ 台账保留进口基准 NPSHₐ", selFold.innerHTML, "NPSHₐ(进口基准)");
has("⑩ 台账显示修正 NPSHₐ", selFold.innerHTML, "NPSHₐ(修正)");

// ⑪ 对比页:state.compare / state.filter → #table thead/tbody/count(纯同步,不 await)
// 数值均为渲染桩值(非实测读数),只为构造完整 data 触发模板分支
console.log("[新增组 ⑪] renderCompare —— 对比页(流股 / 设备 scope)");
vm.runInContext(`
  state.filter = "";
  state.compare = {
    baseId: 1, otherId: 2, scope: "streams",
    data: {
      streams: [
        { name: "C-STUB-1",
          base: { temperature_c: 120.5, pressure_bar: 3.25, mass_flow_kgh: 1000.5, vap_frac: 0.75 },
          other: { temperature_c: 121.0, pressure_bar: 3.30, mass_flow_kgh: 1010.5, vap_frac: 0.76 },
          delta: { temperature_c: 0.5, pressure_bar: 0.05, mass_flow_kgh: 10, vap_frac: 0.01 } },
        { name: "C-STUB-2",
          base: { temperature_c: 80.25, pressure_bar: 1.013, mass_flow_kgh: 500.25, vap_frac: 0 },
          other: { temperature_c: 81.0, pressure_bar: 1.02, mass_flow_kgh: 505.25, vap_frac: 0.01 },
          delta: { temperature_c: 0.75, pressure_bar: 0.007, mass_flow_kgh: 5, vap_frac: 0.01 } },
      ],
      summary: { common: 2, changed: 2, only_base: ["S-OLD-STUB"], only_other: [] },
    },
  };
`, ctx);
vm.runInContext("renderCompare()", ctx);
const cmpThead = byId("__table-thead").innerHTML;
const cmpTbody = byId("__table-tbody").innerHTML;
const cmpCount = byId("count").textContent;
ok("⑪ 流股对比 thead 非空", cmpThead.length > 0);
ok("⑪ 流股对比 tbody 非空", cmpTbody.length > 0);
has("⑪ 表头含流股列", cmpThead, "流股");
has("⑪ 表头含温度 A 列", cmpThead, "温度 ℃ A");
has("⑪ 表头含温度 B 列", cmpThead, "温度 ℃ B");
has("⑪ 行含流股名", cmpTbody, "C-STUB-1");
has("⑪ 行含第二条流股名", cmpTbody, "C-STUB-2");
has("⑪ 有变化单元格标 changed", cmpTbody, 'class="num changed"');
has("⑪ 计数回填行数", cmpCount, "2 行");
has("⑪ 计数含共同/有变化", cmpCount, "共同 2 · 有变化 2");
has("⑪ 计数含仅 A", cmpCount, "仅 A 1");
ok("⑪ 流股对比不含 undefined", !(cmpThead + cmpTbody + cmpCount).includes("undefined"));
ok("⑪ 流股对比不含 NaN", !(cmpThead + cmpTbody + cmpCount).includes("NaN"));

// scope === "blocks":走设备对比分支
vm.runInContext(`
  state.compare.scope = "blocks";
  state.compare.data = {
    blocks: [
      { name: "B-STUB-1", type: "Pump",
        status: { base: 1, other: 2 }, balance: { base: "ok", other: "bad" },
        base: { duty_kw: 10.5, power_kw: 2.5, nstage: 1, top_temp_c: 80.0,
                bottom_temp_c: 120.0, dh_kw: 0.5, head_m: 30.0, flow_m3h: 4.32 },
        other: { duty_kw: 11.0, power_kw: 2.6, nstage: 1, top_temp_c: 80.5,
                 bottom_temp_c: 120.5, dh_kw: 0.6, head_m: 30.5, flow_m3h: 4.5 },
        delta: { duty_kw: 0.5, power_kw: 0.1, nstage: 0, top_temp_c: 0.5,
                 bottom_temp_c: 0.5, dh_kw: 0.1, head_m: 0.5, flow_m3h: 0.18 } },
    ],
    summary: { block_common: 1, block_changed: 1, block_only_base: [], block_only_other: [] },
  };
`, ctx);
vm.runInContext("renderCompare()", ctx);
const blkThead = byId("__table-thead").innerHTML;
const blkTbody = byId("__table-tbody").innerHTML;
const blkCount = byId("count").textContent;
ok("⑪ 设备对比 thead 非空", blkThead.length > 0);
ok("⑪ 设备对比 tbody 非空", blkTbody.length > 0);
has("⑪ 设备表头含 cmp-name", blkThead, "cmp-name");
has("⑪ 设备表头含热负荷 A 列", blkThead, "热负荷 kW A");
has("⑪ 设备行含位号", blkTbody, "B-STUB-1");
has("⑪ 设备行含类型", blkTbody, "Pump");
has("⑪ 状态变化显示错误徽标", blkTbody, "错误");
has("⑪ 设备计数回填", blkCount, "1 行 · 共同 1 · 有变化 1");
ok("⑪ 设备对比不含 undefined", !(blkThead + blkTbody + blkCount).includes("undefined"));
ok("⑪ 设备对比不含 NaN", !(blkThead + blkTbody + blkCount).includes("NaN"));

// 空 data:走清空分支
vm.runInContext("state.compare.data = null;", ctx);
vm.runInContext("renderCompare()", ctx);
ok("⑪ 空 data 清空 thead", byId("__table-thead").innerHTML === "");
ok("⑪ 空 data 清空 tbody", byId("__table-tbody").innerHTML === "");
ok("⑪ 空 data 清空 count", byId("count").textContent === "");

// ⑫ 报告勾选:renderReportPick 用 createElement + appendChild 追加节点
// (getElementById → byId 已覆盖 reportFamilies / reportBlocks / reportPickHint;
//  createElement 桩已存在;本轮补的是 appendChild 把子节点攒进 children 供断言)
console.log("[新增组 ⑫] renderReportPick —— 报告设备勾选(家族按钮 / 设备列表 / 提示)");
vm.runInContext(`
  state.blocks = [
    { name: "R-STUB-1", type: "Pump", family: "pump" },
    { name: "R-STUB-2", type: "Pump", family: "pump" },
    { name: "R-STUB-3", type: "RadFrac", family: "column" },
  ];
  state.families = [ { key: "pump", label: "泵" }, { key: "column", label: "塔" } ];
  state.report.picked = new Set(["R-STUB-1"]);
  state.report.filter = "";
`, ctx);
vm.runInContext("renderReportPick()", ctx);
const famBox = byId("reportFamilies");
const listBox = byId("reportBlocks");
const hint = byId("reportPickHint");
const famTexts = famBox.children.map((c) => c.textContent).join("|");
const listTexts = listBox.children.map(
  (c) => (c.children || []).map((g) => g.textContent).join("")).join("|");
ok("⑫ 家族按钮渲染非空", famBox.children.length > 0);
ok("⑫ 家族按钮数 = 2", famBox.children.length === 2);
has("⑫ 家族按钮含泵计数", famTexts, "泵 1/2");
has("⑫ 家族按钮含塔计数", famTexts, "塔 0/1");
ok("⑫ 设备列表渲染非空", listBox.children.length > 0);
ok("⑫ 设备列表数 = 3", listBox.children.length === 3);
ok("⑫ 每条 label 挂 3 个子节点", listBox.children.every((c) => c.children.length === 3));
has("⑫ 设备名渲染", listTexts, "R-STUB-1");
ok("⑫ checkbox 子节点存在", listBox.children[0].children[0].tagName === "INPUT");
ok("⑫ checkbox 回填勾选态", listBox.children[0].children[0].checked === true);
has("⑫ 提示显示已勾选台数", hint.textContent, "已勾选 1 台");
ok("⑫ 报告勾选不含 undefined", !(famTexts + listTexts + hint.textContent).includes("undefined"));
ok("⑫ 报告勾选不含 NaN", !(famTexts + listTexts + hint.textContent).includes("NaN"));

// 无匹配 + 未勾选:设备列表只留空提示 div
vm.runInContext("state.report.picked = new Set(); state.report.filter = 'NO-MATCH-STUB';", ctx);
vm.runInContext("renderReportPick()", ctx);
ok("⑫ 无匹配时设备列表只留一条空提示", listBox.children.length === 1);
has("⑫ 无匹配时显示没有匹配的设备", listBox.children[0].textContent, "没有匹配的设备");
has("⑫ 未勾选时提示全部台数", hint.textContent, "未勾选 = 全部 3 台");
ok("⑫ 无匹配分支不含 undefined/NaN",
  !(listBox.children[0].textContent + hint.textContent).includes("undefined")
  && !(listBox.children[0].textContent + hint.textContent).includes("NaN"));

/* ⑬ 组:子代理交付时如实报告的两处兜底缺陷,本轮已在 app.js 修掉。
   数值全是渲染桩值(非实测读数),只为触发「接口数据缺字段」的分支。 */
console.log("\n[新增组 ⑬] 缺字段兜底 —— 对比页 / 计算结果行不空白也不出 NaN");
vm.runInContext(`
  state.filter = undefined;
  state.compare = {
    scope: "streams",
    data: { streams: [ { name: "SPARSE-STUB" } ] },  // 无 base / other / delta / summary
  };
`, ctx);
let sparseErr = null;
try { vm.runInContext("renderCompare()", ctx); } catch (e) { sparseErr = e.message; }
ok("⑬ 流股对比缺字段不抛异常", sparseErr === null, sparseErr || "");
const spHead = byId("__table-thead").innerHTML;
const spBody = byId("__table-tbody").innerHTML;
const spCount = byId("count").textContent;
ok("⑬ 缺字段时表头仍非空", spHead.length > 0);
ok("⑬ 缺字段时表格行仍非空", spBody.length > 0);
has("⑬ 缺字段时行名仍在", spBody, "SPARSE-STUB");
has("⑬ 缺 summary 时计数用 — 占位", spCount, "—");
ok("⑬ 缺字段对比不出现 undefined", !(spHead + spBody + spCount).includes("undefined"));
ok("⑬ 缺字段对比不出现 NaN", !(spHead + spBody + spCount).includes("NaN"));

vm.runInContext(`
  state.compare.scope = "blocks";
  state.compare.data = { blocks: [ { name: "B-SPARSE-STUB" } ] };
`, ctx);
let sparseBlockErr = null;
try { vm.runInContext("renderCompare()", ctx); } catch (e) { sparseBlockErr = e.message; }
ok("⑬ 设备对比缺字段不抛异常", sparseBlockErr === null, sparseBlockErr || "");
const sbHead = byId("__table-thead").innerHTML;
const sbBody = byId("__table-tbody").innerHTML;
const sbCount = byId("count").textContent;
has("⑬ 设备对比缺字段仍渲染行", sbBody, "B-SPARSE-STUB");
ok("⑬ 设备对比缺字段不出现 undefined/NaN",
  !(sbHead + sbBody + sbCount).includes("undefined")
  && !(sbHead + sbBody + sbCount).includes("NaN"));

vm.runInContext(`
  state.pumpDesign = {
    view: {
      suction: { stream: "S1", status: "unset", dn: 100, dn_source: "auto",
                 hf_m: null, missing: ["管长 L", "管件系数 Σζ"], version: 0, run: {} },
      discharge: { stream: "S2", status: "linked", dn: 80, dn_source: "auto",
                   hf_m: 0.8, missing: [], version: 0, run: {} },
      totals: { head_m: 20, dz_m: null, hf_m: null, design_head_m: null,
                selection_head_m: null, partial_hf_m: 0.8, partial_dz_m: 0 },
      npsh: { device_m: null, corrected_m: null, state: "unknown", reason: "缺 p_v",
              margin_used_m: 0.5, margin_is_default: true },
      run_sources: {}, tables_error: "",
    },
    edits: {}, openSide: null, legacy: null,
  };
`, ctx);
vm.runInContext("renderPumpDesign(__row)", ctx);
const calcHtml = byId("pumpDesign").innerHTML;
has("⑬ 缺输入时写清缺什么", calcHtml, "缺 管长 L");
has("⑬ 缺输入时仍渲染结果行", calcHtml, "两侧未齐");
ok("⑬ 缺输入不出现 NaN", !calcHtml.includes("NaN"));
ok("⑬ 缺输入不出现 undefined", !calcHtml.includes("undefined"));

/* ⑭ 组:第三个折叠面板「详细参数」的摘要与展开状态。
   数值全是渲染桩值(非实测读数),只为触发模板分支。 */
console.log("\n[新增组 ⑭] 详细参数折叠 —— 摘要 / 展开状态 / 缺字段兜底");
const briefFull = vm.runInContext(
  "pumpParamsBrief({ flow_m3h: 4.32, head_m: 10.0, motor_kw: 1.5 })", ctx);
has("⑭ 摘要含流量", briefFull, "4.32");
has("⑭ 摘要含压升扬程", briefFull, "10.00");
has("⑭ 摘要含建议电机", briefFull, "1.5");
ok("⑭ 完整摘要不含 undefined/NaN",
  !String(briefFull).includes("undefined") && !String(briefFull).includes("NaN"));

const briefEmpty = vm.runInContext("pumpParamsBrief({})", ctx);
has("⑭ 缺数据用 — 占位", briefEmpty, "—");
ok("⑭ 缺字段摘要不含 undefined/NaN",
  !String(briefEmpty).includes("undefined") && !String(briefEmpty).includes("NaN"));

vm.runInContext(
  "fillPumpParamsBrief({ flow_m3h: 4.32, head_m: 10.0, motor_kw: 1.5 })", ctx);
has("⑭ 摘要写进折叠头", byId("pumpParamsBrief").textContent, "4.32");

vm.runInContext("state.pumpFold = { design: false, selection: false };", ctx);
vm.runInContext(
  'bindPumpFold(document.getElementById("pumpParams"), "params");', ctx);
ok("⑭ 旧 state 缺 params 时补默认展开", byId("pumpParams").open === true);
ok("⑭ state 补齐三个 key", vm.runInContext(
  "state.pumpFold.params === true && state.pumpFold.design === false"
  + " && state.pumpFold.selection === false", ctx));
vm.runInContext(
  'state.pumpFold.params = false; bindPumpFold(document.getElementById("pumpParams"), "params");', ctx);
ok("⑭ 收起状态被记住", byId("pumpParams").open === false);
vm.runInContext("state.pumpFold.params = true;", ctx);

console.log("\n[新增组 ⑮] 泵面板结构 —— 居中悬浮窗 / 双列 / 切泵");
const indexHtml = fs.readFileSync(path.join(ROOT, "app", "web", "index.html"), "utf8");
has("⑮ 泵面板用悬浮窗类", indexHtml, "pump-modal");
has("⑮ 顶部有切泵下拉", indexHtml, 'id="pumpSwitch"');
has("⑮ 左列放详细参数", indexHtml, "pump-modal-left");
has("⑮ 右列放设计扬程与台账", indexHtml, "pump-modal-right");
const pumpJsSrc = fs.readFileSync(
  path.join(ROOT, "app", "web", "static", "views", "pump.js"), "utf8");
has("⑮ 闸阀 ζ 改为全开值 0.17", pumpJsSrc, "zeta: 0.17");
has("⑮ λ 口径提示带过渡区说明", pumpJsSrc, "lambda_note");
has("⑮ 反向/自流不给选型扬程", pumpJsSrc, "反向/自流");

console.log(`\n新增覆盖:${passed - basePassed} 条断言(⑥ 流股表 / ⑦ 首页 / ⑧ 家族页签 / ⑨ 常驻区块 / ⑩ λ 口径 / ⑪ 对比页 / ⑫ 报告勾选 / ⑬ 缺字段兜底 / ⑭ 详细参数折叠 / ⑮ 泵面板结构)`);
console.log(`本轮新增:${passed - roundBase} 条断言(⑩ / ⑪ / ⑫ / ⑬ / ⑭ / ⑮)`);
console.log(`前端渲染最小回归:${passed} 条通过`);
if (failures.length) {
  for (const item of failures) console.error(`  ✗ ${item}`);
  console.error(`✗ ${failures.length} 条不通过`);
  process.exit(1);
}
console.log("✓ 全部通过");
