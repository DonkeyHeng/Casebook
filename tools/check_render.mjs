/* 前端渲染最小回归(Node + DOM 桩,不需要浏览器 / 不需要 Aspen)。

   用法:node tools/check_render.mjs

   为什么要有这个文件
   ------------------
   `node --check` 只查语法。原生 JS 前端的模板字符串如果在拼 innerHTML 时
   读到 state 里缺失的字段,异常会让整块区域静默空白 —— 语法完全没问题,
   页面却是空的。这里把**真实的 app.js** 去掉末尾 init() 后装进 vm,
   用 DOM 桩跑一遍纯渲染函数与保存逻辑,把这类空白挡在提交前。

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
const SRC = path.join(ROOT, "app", "web", "static", "app.js");

const raw = fs.readFileSync(SRC, "utf8");
// 末尾的 init() 会拉接口、绑定整页事件;这里只需要函数声明本身
const headless = raw.replace(/^init\(\);\s*$/m, "");
if (headless === raw) {
  console.error("✗ 没找到文件末尾的 init(); —— app.js 结构变了,请同步更新本脚本");
  process.exit(1);
}

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

// ① 完整 state:渲染结果不能是空白,关键节点与回填值都要在
vm.runInContext(`
  state.pumpDesign = {
    dz: 9.45, hf: 5.93, l: 40, zeta: 17.43,
    idMm: 52.5, muCp: 0.6, epsMm: 0.05, calc: null, idNote: "",
    fittings: { el90: 3, gate: 1, globe: 0, check: 1, reducer: 0 },
  };
`, ctx);
vm.runInContext("renderPumpDesign(__row)", ctx);
const html = box.innerHTML;
ok("完整 state 渲染结果非空", html.length > 0);
has("渲染出设计扬程块", html, "设计扬程");
has("渲染出管件数量区", html, "常用管件");
has("渲染出结果行容器", html, "pumpDesignSum");
has("渲染出管长输入", html, 'data-key="l"');
has("渲染出 Σζ 输入", html, 'data-key="zeta"');
has("渲染出管件输入", html, 'data-fit="el90"');
has("管件数量回填到输入框", html, 'value="3"');
ok("渲染结果不含 undefined", !html.includes("undefined"));
ok("渲染结果不含 NaN", !html.includes("NaN"));
ok("结果行有内容", byId("pumpDesignSum").innerHTML.includes("m"));

const inputs = box._lastQueries[".pump-design-input"] || [];
ok(
  "7 个手填输入都绑了 input / change",
  inputs.length === 7 && inputs.every(
    (el) => (el.listeners.input || []).length && (el.listeners.change || []).length
  ),
  `实际 ${inputs.length} 个`,
);
const fits = box._lastQueries[".pump-fit-input"] || [];
ok(
  "5 个管件输入都绑了 change",
  fits.length === 5 && fits.every((el) => (el.listeners.change || []).length),
  `实际 ${fits.length} 个`,
);

// ② 缺字段的 state(老服务 / 半初始化):不能整块静默空白
vm.runInContext("state.pumpDesign = { dz: null, hf: null };", ctx);
vm.runInContext("renderPumpDesign(__row)", ctx);
const sparse = box.innerHTML;
ok("缺字段时不静默空白", sparse.length > 0);
ok("缺字段时不出现 undefined", !sparse.includes("undefined"));
ok("缺字段时不出现 NaN", !sparse.includes("NaN"));

// ③ 保存请求:管长 / Σζ / 管件数量都要带上
vm.runInContext(`
  state.pumpDesign = {
    dz: 9.45, hf: 5.93, l: 40, zeta: 17.43,
    idMm: 52.5, muCp: 0.6, epsMm: 0.05, calc: null, idNote: "",
    fittings: { el90: 3, gate: 1, globe: 0, check: 1, reducer: 0 },
  };
`, ctx);
capture.length = 0;
await vm.runInContext("savePumpDesign(__row)", ctx);
const post = capture[0];
ok("保存请求发到 design-head", Boolean(post && post.url.includes("/design-head")));
ok("请求体带管长 l_m", Boolean(post && post.body.l_m === 40));
ok(
  "请求体带管件数量",
  Boolean(post && post.body.zeta_json && post.body.zeta_json.fittings
    && post.body.zeta_json.fittings.el90 === 3),
);
ok("请求体带手填 Σζ", Boolean(post && post.body.zeta_json
  && post.body.zeta_json.zeta_sum === 17.43));

// ④ 全部清空:发清除请求,而不是留一条全 0 的空挂账
vm.runInContext(`
  state.pumpDesign = {
    dz: null, hf: null, l: null, zeta: null,
    idMm: null, muCp: null, epsMm: null, calc: null, idNote: "",
    fittings: { el90: 0, gate: 0, globe: 0, check: 0, reducer: 0 },
  };
`, ctx);
capture.length = 0;
await vm.runInContext("savePumpDesign(__row)", ctx);
const cleared = capture[0];
ok(
  "全空时发清除请求",
  Boolean(cleared && cleared.body.dz_m === null && cleared.body.hf_m === null
    && cleared.body.l_m === null && cleared.body.zeta_json === null),
);

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

// ⑨ 两个可折叠区:收起时只留 summary 标题 + 结论摘要,展开状态在同会话内记忆
console.log("\n[新增组 ⑨] 折叠面板 —— 设计扬程 / 选型台账(details + summary)");
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
has("⑨ 设计扬程有 summary 折叠头", foldHtml, '<summary class="pump-fold-summary"');
has("⑨ 折叠头保留标题", foldHtml, "pump-fold-title");
has("⑨ 折叠头有展开箭头", foldHtml, "pump-fold-mark");
has("⑨ 收起时仍显示扬程摘要", foldHtml, "选型");
ok("⑨ 设计扬程默认收起", designFold.open === false);
ok("⑨ 设计扬程折叠头不含 undefined", !foldHtml.includes("undefined"));
vm.runInContext("state.pumpFold.design = true;", ctx);
vm.runInContext("renderPumpDesign(__row)", ctx);
ok("⑨ 展开状态被记住", designFold.open === true);
vm.runInContext("state.pumpFold.design = false;", ctx);

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
has("⑨ 台账有 summary 折叠头", selFoldHtml, '<summary class="pump-fold-summary"');
has("⑨ 台账收起时显示型号", selFoldHtml, "IS65-50-160");
has("⑨ 台账收起时显示三态", selFoldHtml, "pump-sel-state-mini");
ok("⑨ 台账默认收起", selFold.open === false);
ok("⑨ 台账折叠头不含 undefined", !selFoldHtml.includes("undefined"));

const roundBase = passed;   // 本轮新增(⑩/⑪/⑫)前的基线:原有 76 条

// ⑩ λ 口径控件:renderPumpDesign 新增的 pump-design-lambda 区块 + 保存/计算请求体
console.log("[新增组 ⑩] λ 口径控件 —— select / manual 输入框 / 保存与计算请求");
vm.runInContext(`
  state.current = { id: 1 };
  state.pumpFold = { design: false, selection: false };
  state.pumpDesign = {
    dz: 9.45, hf: 5.93, l: 40, zeta: 17.43,
    idMm: 52.5, muCp: 0.6, epsMm: 0.05, calc: null, idNote: "",
    fittings: { el90: 3, gate: 1, globe: 0, check: 1, reducer: 0 },
    manual: { idMm: null, muCp: null, epsMm: null },
    lambdaMethod: "colebrook", lambdaManual: null,
  };
`, ctx);
vm.runInContext("renderPumpDesign(__row)", ctx);
const lamHtml0 = box.innerHTML;
has("⑩ 渲染出 λ 口径区块", lamHtml0, "pump-design-lambda");
has("⑩ 渲染出口径 select", lamHtml0, '<select class="pump-design-select" data-key="lambdaMethod"');
has("⑩ 含 Colebrook option", lamHtml0, 'value="colebrook"');
has("⑩ 含顾毓珍 option", lamHtml0, 'value="gu"');
has("⑩ 含手填 option", lamHtml0, 'value="manual"');
has("⑩ 默认 colebrook 选中", lamHtml0, 'value="colebrook" selected');
ok("⑩ gu 未选中", lamHtml0.includes('<option value="gu">'));
ok("⑩ manual 未选中", lamHtml0.includes('<option value="manual">'));
ok("⑩ 非 manual 不渲染 λ 值输入框", !lamHtml0.includes('data-key="lambdaManual"'));
has("⑩ 有 muted 口径说明", lamHtml0, "默认 Colebrook");
ok("⑩ 默认渲染不含 undefined", !lamHtml0.includes("undefined"));
ok("⑩ 默认渲染不含 NaN", !lamHtml0.includes("NaN"));
const lamLabels = vm.runInContext("Object.keys(LAMBDA_LABELS).join(',')", ctx);
ok("⑩ LAMBDA_LABELS 含三口径", lamLabels === "colebrook,gu,manual", lamLabels);

// gu:仍不出现 λ 值输入框
vm.runInContext("state.pumpDesign.lambdaMethod = 'gu';", ctx);
vm.runInContext("renderPumpDesign(__row)", ctx);
const lamHtmlGu = box.innerHTML;
ok("⑩ gu 口径选中", lamHtmlGu.includes('value="gu" selected'));
ok("⑩ gu 时仍不渲染 λ 值输入框", !lamHtmlGu.includes('data-key="lambdaManual"'));
ok("⑩ gu 渲染不含 undefined", !lamHtmlGu.includes("undefined"));
ok("⑩ gu 渲染不含 NaN", !lamHtmlGu.includes("NaN"));

// manual:出现 λ 值输入框并回填(0.0314 为渲染桩值,非实测读数)
vm.runInContext("state.pumpDesign.lambdaMethod = 'manual'; state.pumpDesign.lambdaManual = 0.0314;", ctx);
vm.runInContext("renderPumpDesign(__row)", ctx);
const lamHtmlMan = box.innerHTML;
ok("⑩ manual 口径选中", lamHtmlMan.includes('value="manual" selected'));
has("⑩ manual 出现 λ 值输入框", lamHtmlMan, 'data-key="lambdaManual"');
has("⑩ λ 值回填(渲染桩值)", lamHtmlMan, 'value="0.0314"');
ok("⑩ manual 渲染不含 undefined", !lamHtmlMan.includes("undefined"));
ok("⑩ manual 渲染不含 NaN", !lamHtmlMan.includes("NaN"));

// 保存请求体带 lambda { method, manual }
capture.length = 0;
await vm.runInContext("savePumpDesign(__row)", ctx);
const lamPost = capture[0];
ok("⑩ 保存请求体带 lambda.method", Boolean(lamPost && lamPost.body.zeta_json
  && lamPost.body.zeta_json.lambda && lamPost.body.zeta_json.lambda.method === "manual"));
ok("⑩ manual 时 lambda.manual 有值(渲染桩值)", Boolean(lamPost && lamPost.body.zeta_json
  && lamPost.body.zeta_json.lambda && lamPost.body.zeta_json.lambda.manual === 0.0314));

// hasLambda:非默认口径即使其它字段全空也不算「空挂账」
vm.runInContext(`
  state.pumpDesign.dz = null; state.pumpDesign.hf = null;
  state.pumpDesign.l = null; state.pumpDesign.zeta = null;
  state.pumpDesign.fittings = { el90: 0, gate: 0, globe: 0, check: 0, reducer: 0 };
  state.pumpDesign.manual = { idMm: null, muCp: null, epsMm: null };
  state.pumpDesign.lambdaMethod = "gu"; state.pumpDesign.lambdaManual = null;
`, ctx);
capture.length = 0;
await vm.runInContext("savePumpDesign(__row)", ctx);
const guOnlyPost = capture[0];
ok("⑩ 仅 gu 口径也发非清空请求(hasLambda)", Boolean(guOnlyPost && guOnlyPost.body.zeta_json
  && guOnlyPost.body.zeta_json.lambda.method === "gu"));

// 切换 option 的 change:写 state + 重渲染 + 触发保存
vm.runInContext(`
  state.pumpDesign.lambdaMethod = "colebrook"; state.pumpDesign.lambdaManual = 0.0314;
  state.pumpDesign.dz = 9.45; state.pumpDesign.hf = 5.93;
  state.pumpDesign.l = 40; state.pumpDesign.zeta = 17.43;
  state.pumpDesign.fittings = { el90: 3, gate: 1, globe: 0, check: 1, reducer: 0 };
`, ctx);
capture.length = 0;
vm.runInContext("renderPumpDesign(__row)", ctx);
const lamSelects = box._lastQueries[".pump-design-select"] || [];
ok("⑩ select 绑了 change 监听",
  lamSelects.length === 1 && (lamSelects[0].listeners.change || []).length >= 1);
if (lamSelects.length && (lamSelects[0].listeners.change || []).length) {
  const lamSelect = lamSelects[0];
  lamSelect.value = "manual";
  lamSelect.listeners.change[0]({ target: lamSelect });
}
ok("⑩ change 写 state.lambdaMethod",
  vm.runInContext("state.pumpDesign.lambdaMethod", ctx) === "manual");
ok("⑩ change 触发重渲染出 λ 值输入框", box.innerHTML.includes('data-key="lambdaManual"'));
ok("⑩ change 触发保存请求带 lambda.method", Boolean(capture[0] && capture[0].body.zeta_json
  && capture[0].body.zeta_json.lambda.method === "manual"));

// computeHeadLoss:manual 且 λ 为空 → 不发请求 + 提示
vm.runInContext(`
  state.pumpDesign.l = 40; state.pumpDesign.zeta = 17.43;
  state.pumpDesign.lambdaMethod = "manual"; state.pumpDesign.lambdaManual = null;
`, ctx);
capture.length = 0;
await vm.runInContext("computeHeadLoss(__row)", ctx);
ok("⑩ manual 且 λ 为空时不发 head-loss 请求", capture.length === 0);
has("⑩ manual 且 λ 为空时提示先填 λ 值", byId("pumpCalcLine").textContent, "先填 λ 值");

// computeHeadLoss:manual 且 λ 有值 → 请求体带 method 与 lam(0.0314 为渲染桩值,非实测读数)
vm.runInContext("state.pumpDesign.lambdaManual = 0.0314;", ctx);
capture.length = 0;
await vm.runInContext("computeHeadLoss(__row)", ctx);
const lossPost = capture[0];
ok("⑩ manual 有 λ 时发 head-loss 请求", Boolean(lossPost && lossPost.url.includes("head-loss")));
ok("⑩ head-loss 请求体带 method", Boolean(lossPost && lossPost.body.method === "manual"));
ok("⑩ head-loss 请求体带 lam(渲染桩值)", Boolean(lossPost && lossPost.body.lam === 0.0314));

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
    dz: null, hf: null, l: null, zeta: null, idMm: null, muCp: null, epsMm: null,
    idNote: "", calc: { u_ms: 1.2, re: 50000, lam: 0.023, missing: [], method: "colebrook" },
    fittings: { el90: 0, gate: 0, globe: 0, check: 0, reducer: 0 },
    manual: { idMm: null, muCp: null, epsMm: null },
    lambdaMethod: "colebrook", lambdaManual: null,
  };   // 计算结果里没有 eps_d
`, ctx);
vm.runInContext("renderPumpDesign(__row)", ctx);
const calcHtml = byId("pumpDesign").innerHTML;
has("⑬ 缺 eps_d 时结果行仍渲染", calcHtml, "u = ");
has("⑬ 缺 eps_d 时用 — 占位", calcHtml, "ε/d = —");
ok("⑬ 缺 eps_d 不出现 NaN", !calcHtml.includes("NaN"));
ok("⑬ 缺 eps_d 不出现 undefined", !calcHtml.includes("undefined"));

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

console.log(`\n新增覆盖:${passed - basePassed} 条断言(⑥ 流股表 / ⑦ 首页 / ⑧ 家族页签 / ⑨ 折叠面板 / ⑩ λ 口径 / ⑪ 对比页 / ⑫ 报告勾选 / ⑬ 缺字段兜底 / ⑭ 详细参数折叠)`);
console.log(`本轮新增:${passed - roundBase} 条断言(⑩ / ⑪ / ⑫ / ⑬ / ⑭)`);
console.log(`前端渲染最小回归:${passed} 条通过`);
if (failures.length) {
  for (const item of failures) console.error(`  ✗ ${item}`);
  console.error(`✗ ${failures.length} 条不通过`);
  process.exit(1);
}
console.log("✓ 全部通过");
