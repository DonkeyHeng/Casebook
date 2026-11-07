/* 管道页:按参数实时算的管表(结果不写库,口径在 app/design/pipe.py)
 *
 * 为什么单独一个文件:
 *   1. app.js 已经 2100 行,管道页自带参数状态机与请求防串号逻辑,混进去更难读;
 *   2. 页面依赖的字段与校验码都在这一个文件里,和 app/design/pipe.py 对照着看即可;
 *   3. 只跟 app.js 借 state / esc / fmt / api,不反向依赖,删掉这个文件整页就退回原状。
 *
 * 与后端的约定(接口 GET /api/cases/{id}/pipes):
 *   - 200 → {rows, missing, summary, validation:{findings:[{stream,code,level,message,field}],summary},
 *            options, tables}
 *   - 409 → 标准表没录全,body 里有 error 与 tables(改参数不会让它变好)
 *   - 200 但 tables.ready === false → 只有壁厚序列(T2)缺,表能出、壁厚列是「—」
 */

/* ------------------------------------------------------------ 常量 */

/* 默认流速必须与 app/design/pipe.py 的 DEFAULT_VELOCITY 一致(定稿口径见 pipe-selection-plan.md §5 第 3 步:
   液 1.5、气 15、两相 8、泵入口 0.8)。改了那边要同步这里,并改 docs。 */
const PIPE_DEFAULT_PARAMS = {
  liquid: 1.5, gas: 15, two: 8, pump_inlet: 0.8,
  factor: 1.1, floor: 1.0, max_dn: 600, material: "S30408",
};

/* 推荐流速区间:与 pipe.py 的 VELOCITY_RANGE 同步,只用于 title 提示,不参与计算 */
const PIPE_VELOCITY_RANGE = {
  liquid: [1.0, 2.5], gas: [10, 25], two: [5, 10], pump_inlet: [0.5, 1.0],
};

const PIPE_DN_LADDER = [10, 15, 20, 25, 32, 40, 50, 65, 80, 100, 125, 150,
  200, 250, 300, 350, 400, 450, 500, 600];

const PIPE_MATERIAL_FALLBACK = ["S30408", "S31603", "20#", "Q345B"];

const PIPE_PRESSURE_UNITS = {
  kpa: { label: "kPa", factor: 1000, digits: 2 },
  mpa: { label: "MPa", factor: 1, digits: 3 },
  bar: { label: "bar", factor: 10, digits: 3 },
};

const PIPE_PHASE_TEXT = { liquid: "液体", gas: "气体", two: "气液共存" };

/* 校验码 → 中文名 + 该标在哪一列。列名与 finding.field 对齐(后端已经给 field,
   这张表只是给人看的说明,不用它决定标哪一格) */
const PIPE_CODE_TEXT = {
  V1: "编号口径与 DN 不一致", V2: "实际流速超推荐区间", V3: "所需壁厚超过标准壁厚",
  V4: "等级号压力不足", V5: "两相按单相流速选型", V6: "真空流股",
  V7: "超出材料使用温度", V8: "泵出口按泵入口流速", V9: "高温未加保温代号",
};

/* 列定义:group=calc 的列勾选「显示计算过程」才显示 —— 那一组就是每个数字怎么来的 */
const PIPE_COLUMNS = [
  { key: "stream", label: "流股", cls: "sticky-col pipe-name" },
  { key: "temperature", label: "温度 ℃", num: true, digits: 2 },
  { key: "pressure", label: "压力 MPa", num: true },
  { key: "phase", label: "状态" },
  { key: "vol_flow", label: "体积流量 m³/h", num: true, digits: 4 },
  { key: "velocity", label: "流速 m/s", num: true, digits: 2 },
  { key: "dn", label: "公称直径", num: true, cls: "pipe-answer" },
  { key: "wall_mm", label: "壁厚 mm", num: true, digits: 1 },
  { key: "material", label: "管材" },
  { key: "type", label: "类型" },
  { key: "grade_code", label: "等级号", mono: true },
  { key: "findings", label: "校验", cls: "pipe-chips" },
  { key: "warnings", label: "备注" },
  { key: "od_mm", label: "管外径 mm", num: true, digits: 1, group: "calc" },
  { key: "design_pressure_mpa", label: "设计压力", num: true, group: "calc" },
  { key: "design_temp_c", label: "设计温度 ℃", num: true, digits: 2, group: "calc" },
  { key: "stress_mpa", label: "许用应力 MPa", num: true, digits: 1, group: "calc" },
  { key: "t_calc_mm", label: "计算厚度 mm", num: true, digits: 2, group: "calc" },
  { key: "t_required_mm", label: "加裕量后 mm", num: true, digits: 2, group: "calc" },
  { key: "min_wall_mm", label: "最小壁厚下限 mm", num: true, digits: 1, group: "calc" },
];

const PIPE_DEBOUNCE_MS = 350;

/* ------------------------------------------------------------ 状态 */

const pipeState = {
  caseId: null,          // 这份结果是哪份工况算的
  params: { ...PIPE_DEFAULT_PARAMS },
  view: { unit: "kpa", showCalc: false, onlyBad: false, filter: "" },
  data: null,            // 接口返回体
  status: "idle",        // idle / loading / ok / invalid / not-ready / error
  error: "",
  tables: null,          // 三张标准表录入状态
  setup: null,           // 表没录时后端给的补录说明(要哪两份标准、放哪、什么格式)
  tablesLoading: false,
  seq: 0,                // 只认最后一次请求
  timer: null,
  abort: null,
  lastQuery: null,
  drift: null,
};

/* ------------------------------------------------------------ 请求 */

function pipeQuery(params) {
  const q = new URLSearchParams();
  ["liquid", "gas", "two", "pump_inlet", "factor", "floor", "max_dn"].forEach((k) => {
    if (Number.isFinite(params[k])) q.set(k, String(params[k]));
  });
  if (params.material) q.set("material", params.material);
  return q.toString();
}

/* 管道接口单独一套:app.js 的 api() 会把非 2xx 的 body 丢掉,
   而 409 的 body(缺哪张标准表)正是要显示给人的正文。 */
async function pipeFetch(path, signal) {
  const res = await fetch(path, { signal });
  const body = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, body };
}

/* ------------------------------------------------------------ 参数面板 */

const PIPE_PARAM_FIELDS = [
  { id: "ppLiquid", key: "liquid", min: 0.05 },
  { id: "ppGas", key: "gas", min: 0.5 },
  { id: "ppTwo", key: "two", min: 0.5 },
  { id: "ppPumpInlet", key: "pump_inlet", min: 0.05 },
  { id: "ppFactor", key: "factor", min: 1 },
  { id: "ppFloor", key: "floor", min: 0 },
  { id: "ppMaxDn", key: "max_dn", min: 10, int: true },
];

/* 从面板读参数:留空 → 当场写回默认值;填错 → 标红并返回出错的 key。
   有错就一个请求都不发 —— 服务端会把认不出的值静默换成默认值(server.py 的 _f()),
   那样"界面显示 1.5、实际按 15 算"永远查不出来。 */
function readPipeParamsFromDom() {
  const params = pipeState.params;
  const bad = [];
  PIPE_PARAM_FIELDS.forEach(({ id, key, min, int }) => {
    const el = document.getElementById(id);
    if (!el) return;
    const raw = el.value.trim();
    if (raw === "") {
      params[key] = PIPE_DEFAULT_PARAMS[key];
      el.value = String(PIPE_DEFAULT_PARAMS[key]);
      el.classList.remove("invalid");
      return;
    }
    let v = Number(raw);
    if (int) v = Math.round(v);
    const invalid = !Number.isFinite(v) || v < min;
    el.classList.toggle("invalid", invalid);
    if (invalid) { bad.push(key); return; }
    params[key] = v;
  });
  const mat = document.getElementById("ppMaterial");
  if (mat && mat.value) params.material = mat.value;
  return bad;
}

function applyPipeParamsToDom() {
  PIPE_PARAM_FIELDS.forEach(({ id, key }) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.value = String(pipeState.params[key] ?? "");
    el.classList.remove("invalid");
  });
  const mat = document.getElementById("ppMaterial");
  if (mat) mat.value = pipeState.params.material;
  const unit = document.getElementById("ppUnit");
  if (unit) unit.value = pipeState.view.unit;
}

/* 下拉选项尽量来自"服务端有什么":核对过的 DN、表 B.1 里录了哪些牌号 */
function fillPipeSelects(tables) {
  const maxDn = document.getElementById("ppMaxDn");
  if (maxDn) {
    const ladder = PIPE_DN_LADDER.slice();
    const verified = (tables && tables.dn_to_od && tables.dn_to_od.verified) || [];
    verified.forEach((dn) => { if (!ladder.includes(dn)) ladder.push(dn); });
    ladder.sort((a, b) => a - b);
    const keep = pipeState.params.max_dn;
    maxDn.innerHTML = ladder.map((dn) => `<option value="${dn}">DN${dn}</option>`).join("");
    maxDn.value = String(ladder.includes(keep) ? keep : 600);
  }
  const mat = document.getElementById("ppMaterial");
  if (mat) {
    const grades = (tables && tables.stress && tables.stress.grades) || [];
    const list = grades.length ? grades : PIPE_MATERIAL_FALLBACK;
    const suffix = grades.length ? "" : "(候选,未核对)";
    mat.innerHTML = list.map((g) => `<option value="${esc(g)}">${esc(g)}${suffix}</option>`).join("");
    if (list.includes(pipeState.params.material)) mat.value = pipeState.params.material;
  }
  // 管道等级表只是**可选的覆盖**:没配就全部按上面选的默认材质,不要让人以为漏了什么
  const hint = document.getElementById("pipeClassHint");
  if (hint) {
    const pc = (tables && tables.piping_class) || {};
    hint.textContent = pc.configured
      ? `等级表 ${pc.rule_count} 条`
      : "等级表:未配置(全部按默认材质)";
  }
}

function initPipePanel() {
  applyPipeParamsToDom();
  fillPipeSelects(null);
  bindPipeEvents();
}

function bindPipeEvents() {
  const form = document.getElementById("pipeParams");
  if (!form || form.dataset.bound === "1") return;
  form.dataset.bound = "1";

  form.addEventListener("submit", (e) => {
    e.preventDefault();                 // 回车不该刷新页面
    onPipeParamInput({ type: "submit" });
  });
  PIPE_PARAM_FIELDS.forEach(({ id }) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("input", onPipeParamInput);
    el.addEventListener("change", onPipeParamInput);
  });
  const mat = document.getElementById("ppMaterial");
  if (mat) mat.addEventListener("change", onPipeParamInput);

  const unit = document.getElementById("ppUnit");
  if (unit) unit.addEventListener("change", (e) => {
    pipeState.view.unit = e.target.value;   // 只换显示,不重算
    drawPipeTable();
  });
  const showCalc = document.getElementById("ppShowCalc");
  if (showCalc) showCalc.addEventListener("change", (e) => {
    pipeState.view.showCalc = e.target.checked;
    drawPipeTable();
  });
  const onlyBad = document.getElementById("ppOnlyBad");
  if (onlyBad) onlyBad.addEventListener("change", (e) => {
    pipeState.view.onlyBad = e.target.checked;
    drawPipeTable();
  });
  const filter = document.getElementById("ppFilter");
  if (filter) filter.addEventListener("input", (e) => {
    pipeState.view.filter = e.target.value; // 纯前端筛选,不请求
    drawPipeTable();
  });

  const reset = document.getElementById("pipeReset");
  if (reset) reset.addEventListener("click", () => {
    pipeState.params = { ...PIPE_DEFAULT_PARAMS };
    applyPipeParamsToDom();
    runPipeCalc({ force: true });
  });
  const recalc = document.getElementById("pipeRecalc");
  if (recalc) recalc.addEventListener("click", () => {
    if (!readPipeParamsFromDom().length) runPipeCalc({ force: true });
  });

  // 导出:整份工况的 Excel / AI 包(里面已含「管道」「管道计算书」与 pipe_schedule.csv)
  const expX = document.getElementById("pipeExportXlsx");
  if (expX) expX.addEventListener("click", () => {
    const c = state.current;
    if (!c) return;
    window.location.href = `/api/cases/${c.id}/export.xlsx`;
  });
  const expAi = document.getElementById("pipeExportAi");
  if (expAi) expAi.addEventListener("click", () => {
    const c = state.current;
    if (!c) return;
    window.location.href = `/api/cases/${c.id}/export.ai.zip`;
  });

  // ---- 管道等级表(可选覆盖) ----
  const open = document.getElementById("pipeClassOpen");
  if (open) open.addEventListener("click", openPipeClass);
  const close = document.getElementById("pipeClassClose");
  if (close) close.addEventListener("click", closePipeClass);
  const cancel = document.getElementById("pipeClassCancel");
  if (cancel) cancel.addEventListener("click", closePipeClass);
  const add = document.getElementById("pipeClassAdd");
  if (add) add.addEventListener("click", () => addPipeClassRow({}));
  const save = document.getElementById("pipeClassSave");
  if (save) save.addEventListener("click", savePipeClass);
  const clear = document.getElementById("pipeClassClear");
  if (clear) clear.addEventListener("click", clearPipeClass);

  // ---- 管道计算说明(纯文字弹窗,不依赖接口数据) ----
  const docOpen = document.getElementById("pipeDocOpen");
  if (docOpen) docOpen.addEventListener("click", openPipeDoc);
  const docClose = document.getElementById("pipeDocClose");
  if (docClose) docClose.addEventListener("click", closePipeDoc);
  const docModal = document.getElementById("pipeDocModal");
  if (docModal) {
    // 点遮罩关闭;点内容区(含表格里的文字)不关
    docModal.addEventListener("click", (e) => {
      if (e.target === docModal) closePipeDoc();
    });
  }
}

/* ------------------------------------------------------------ 管道计算说明 */

function openPipeDoc() {
  const modal = document.getElementById("pipeDocModal");
  if (!modal) return;
  modal.classList.remove("hidden");
  document.addEventListener("keydown", pipeDocEsc);
}

function closePipeDoc() {
  const modal = document.getElementById("pipeDocModal");
  if (modal) modal.classList.add("hidden");
  document.removeEventListener("keydown", pipeDocEsc);
}

function pipeDocEsc(e) {
  if (e.key === "Escape") closePipeDoc();
}

/* ------------------------------------------------------------ 管道等级表弹窗 */

const PIPE_CLASS_GRADES = ["20#", "Q345A", "Q345B", "S30408", "S31603"];

let pipeClassAutoOpened = false;   // #pipes/class 深链只自动开一次

function openPipeClass() {
  const modal = document.getElementById("pipeClassModal");
  if (!modal) return;
  modal.classList.remove("hidden");
  showPipeClassError("");
  api("/api/piping-classes").then((data) => {
    const grades = (data && data.grades) || PIPE_CLASS_GRADES;
    const defaultSel = document.getElementById("pipeClassDefault");
    if (defaultSel) {
      defaultSel.innerHTML = '<option value="">(不指定,走页面上的默认管材)</option>'
        + grades.map((g) => `<option value="${esc(g)}">${esc(g)}</option>`).join("");
      defaultSel.value = (data && data.default && data.default.grade) || "";
    }
    const tbody = document.querySelector("#pipeClassTable tbody");
    if (tbody) tbody.innerHTML = "";
    ((data && data.rules) || []).forEach((rule) => addPipeClassRow(rule, grades));
    if (!((data && data.rules) || []).length) addPipeClassRow({}, grades);
  }).catch((err) => showPipeClassError(`读不到当前配置:${err.message}`));
}

function closePipeClass() {
  const modal = document.getElementById("pipeClassModal");
  if (modal) modal.classList.add("hidden");
}

function showPipeClassError(text) {
  const el = document.getElementById("pipeClassError");
  if (!el) return;
  if (!text) { el.classList.add("hidden"); el.textContent = ""; return; }
  el.classList.remove("hidden");
  el.textContent = text;
}

/* 一行 = 一条规则:前缀(逗号分隔) / 介质(逗号分隔) / 牌号 / 腐蚀裕量 / 说明 */
function addPipeClassRow(rule, grades) {
  const tbody = document.querySelector("#pipeClassTable tbody");
  if (!tbody) return;
  const list = grades || PIPE_CLASS_GRADES;
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><input class="pc-prefix" placeholder="如 CW,CWR" value="${esc((rule.stream_prefix || []).join(","))}"></td>
    <td><input class="pc-medium" placeholder="如 PL" value="${esc((rule.medium || []).join(","))}"></td>
    <td><select class="pc-grade">${list.map((g) =>
      `<option value="${esc(g)}"${g === rule.grade ? " selected" : ""}>${esc(g)}</option>`).join("")}</select></td>
    <td><input class="pc-c2" type="number" step="0.1" min="0" max="20" value="${rule.corrosion_mm ?? 0}"></td>
    <td><input class="pc-note" placeholder="可选" value="${esc(rule.note || "")}"></td>
    <td><button class="btn pc-del" type="button" title="删掉这一条">✕</button></td>`;
  tr.querySelector(".pc-del").addEventListener("click", () => tr.remove());
  tbody.appendChild(tr);
}

function collectPipeClass() {
  const rows = [...document.querySelectorAll("#pipeClassTable tbody tr")];
  const rules = rows.map((tr) => {
    const split = (sel) => (tr.querySelector(sel).value || "")
      .split(/[,,\s]+/).map((s) => s.trim()).filter(Boolean);
    return {
      stream_prefix: split(".pc-prefix"),
      medium: split(".pc-medium").map((s) => s.toUpperCase()),
      grade: tr.querySelector(".pc-grade").value,
      corrosion_mm: Number(tr.querySelector(".pc-c2").value || 0),
      note: tr.querySelector(".pc-note").value.trim(),
    };
  }).filter((r) => r.stream_prefix.length || r.medium.length);
  const defaultGrade = document.getElementById("pipeClassDefault").value;
  return { rules, default: defaultGrade ? { grade: defaultGrade, corrosion_mm: 0 } : null };
}

async function savePipeClass() {
  const payload = collectPipeClass();
  const res = await fetch("/api/piping-classes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || !body.ok) {
    showPipeClassError("保存失败:" + ((body.errors || []).join(";") || `HTTP ${res.status}`));
    return;
  }
  closePipeClass();
  pipeState.tables = null;         // 状态变了,重新拉一次
  if (state.current) loadPipeTableStatus().then(() => runPipeCalc({ force: true }));
}

async function clearPipeClass() {
  const res = await fetch("/api/piping-classes", { method: "DELETE" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || !body.ok) {
    showPipeClassError("清除失败:" + ((body.errors || []).join(";") || `HTTP ${res.status}`));
    return;
  }
  closePipeClass();
  pipeState.tables = null;
  if (state.current) loadPipeTableStatus().then(() => runPipeCalc({ force: true }));
}

function onPipeParamInput(evt) {
  const bad = readPipeParamsFromDom();
  if (bad.length) {
    pipeState.status = "invalid";
    pipeState.error = "有参数填得不对(标红的格子),改好就会自动重算。";
    renderPipeNotice();
    return;
  }
  clearTimeout(pipeState.timer);
  const immediate = evt && (evt.type === "change" || evt.type === "submit");
  if (immediate) runPipeCalc();
  else pipeState.timer = setTimeout(runPipeCalc, PIPE_DEBOUNCE_MS);
}

/* ------------------------------------------------------------ 计算 */

async function runPipeCalc({ force = false } = {}) {
  const c = state.current;
  clearTimeout(pipeState.timer);
  if (!c) { pipeState.status = "idle"; pipeState.data = null; renderPipes(); return; }

  const qs = pipeQuery(pipeState.params);
  if (!force && pipeState.status === "ok" && pipeState.caseId === c.id
      && pipeState.lastQuery === qs) return;

  if (pipeState.abort) pipeState.abort.abort();   // 掐掉上一枪,别让慢响应盖掉新结果
  const seq = ++pipeState.seq;
  const abort = new AbortController();
  pipeState.abort = abort;
  pipeState.status = "loading";
  renderPipeNotice();            // 只更新状态条,旧表先留着,避免整页闪白
  renderPipeStatus();

  let out;
  try {
    out = await pipeFetch(`/api/cases/${c.id}/pipes?${qs}`, abort.signal);
  } catch (err) {
    if (err.name === "AbortError" || seq !== pipeState.seq) return;
    pipeState.status = "error";
    pipeState.error = `连不上后台服务:${err.message}(服务可能已经关掉,重新启动后再试)`;
    renderPipes();
    return;
  }
  if (seq !== pipeState.seq) return;

  if (out.body && out.body.tables) pipeState.tables = out.body.tables;

  if (out.status === 409) {
    pipeState.status = "not-ready";
    pipeState.error = out.body.error || "标准表未录入,暂时算不出管道表。";
    pipeState.setup = out.body.setup || null;
    pipeState.data = null;
    renderPipes();
    return;
  }
  if (!out.ok) {
    pipeState.status = "error";
    pipeState.error = out.body.error || `请求失败:${out.status}`;
    pipeState.data = null;
    renderPipes();
    return;
  }

  pipeState.data = out.body;
  pipeState.status = "ok";
  pipeState.caseId = c.id;
  pipeState.lastQuery = qs;
  pipeState.drift = pipeOptionsDrift(pipeState.params, out.body.options);
  renderPipes();
}

/* 发出去的口径 vs 服务端回显的口径:不相等说明它没认我们的值 */
function pipeOptionsDrift(sent, got) {
  if (!got) return null;
  const v = got.velocity || {};
  const pairs = [
    ["液相", sent.liquid, v.liquid], ["气相", sent.gas, v.gas],
    ["两相", sent.two, v.two], ["泵入口", sent.pump_inlet, v.pump_inlet],
    ["系数", sent.factor, got.pressure_factor], ["下限", sent.floor, got.pressure_floor_mpa],
    ["DN 上限", sent.max_dn, got.max_dn],
  ];
  const diff = pairs.filter(([, a, b]) => b !== undefined && Math.abs(Number(a) - Number(b)) > 1e-9);
  return diff.length ? diff : null;
}

/* ------------------------------------------------------------ 渲染 */

function pipeStreamIndex() {
  const map = new Map();
  (state.streams || []).forEach((s) => map.set(s.name, s));
  return map;
}

function pipeFindingMap() {
  const map = new Map();
  const v = pipeState.data && pipeState.data.validation;
  ((v && v.findings) || []).forEach((f) => {
    if (!map.has(f.stream)) map.set(f.stream, []);
    map.get(f.stream).push(f);
  });
  return map;
}

/* 实际流速:按选中管子的内径反算,式子与 pipe.py 的 actual_velocity() 相同 */
function pipeVelocity(row) {
  if (!row.od_mm || !row.wall_mm || !row.vol_flow_m3h) return null;
  const id = row.od_mm - 2 * row.wall_mm;
  if (id <= 0) return null;
  const area = Math.PI * Math.pow(id / 1000, 2) / 4;
  return (row.vol_flow_m3h / 3600) / area;
}

function pipePressure(valueMpa) {
  if (valueMpa === null || valueMpa === undefined) return "—";
  const u = PIPE_PRESSURE_UNITS[pipeState.view.unit] || PIPE_PRESSURE_UNITS.kpa;
  return fmt(valueMpa * u.factor, u.digits);
}

/* 设计压力多半被下限钳到 1.0 MPa,不写清会被当成算错 */
function pipePressureTitle(row) {
  const bits = [];
  if (row.vacuum) bits.push("真空流股:内压按最小标准壁厚,外压 0.1 MPa 另行校核");
  if (row.gauge_pressure_mpa !== null && row.gauge_pressure_mpa !== undefined) {
    bits.push(`表压 ${fmt(row.gauge_pressure_mpa, 3)} MPa`);
  }
  if (row.design_pressure_mpa !== null && row.design_pressure_mpa !== undefined) {
    bits.push(`设计 ${fmt(row.design_pressure_mpa, 3)} MPa`);
    if (Math.abs(row.design_pressure_mpa - pipeState.params.floor) < 1e-9) {
      bits.push(`已钳到下限 ${fmt(pipeState.params.floor, 2)} MPa`);
    }
  }
  return bits.join(";");
}

function pipeFindingChips(list) {
  if (!list.length) return '<span class="muted">—</span>';
  return list.map((f) => `<span class="badge ${f.level === "error" ? "err" : "warn"} pipe-chip"`
    + ` data-stream="${esc(f.stream)}" title="${esc(f.code + " " + f.message)}"`
    + `>${esc(f.code)}</span>`).join(" ");
}

function pipeCellValue(row, col, ctx) {
  const s = ctx.streams.get(row.stream) || {};
  switch (col.key) {
    case "stream": return esc(row.stream);
    case "temperature": return fmt(s.temperature_c, 2);
    case "pressure": return fmt(s.pressure_bar === undefined ? null : s.pressure_bar / 10, 2);
    case "phase": {
      const text = PIPE_PHASE_TEXT[row.phase] || "—";
      const tip = row.phase === "two" ? "气液共存:需专项核算" : "";
      return `<span class="badge idle" title="${tip}">${text}</span>`;
    }
    case "vol_flow": return fmt(row.vol_flow_m3h, 2);
    case "velocity": return fmt(pipeVelocity(row), 2);
    case "dn": return row.dn === null || row.dn === undefined ? "—" : `DN${row.dn}`;
    case "material": return esc(row.material || "—");
    case "type": return "无缝";        // 本期口径 Φw=1,焊管以后再加参数
    case "grade_code": return row.grade_code
      ? `<span class="mono" title="压力代号 + 顺序号 + 材质代号,规则见 docs/pipe-class-codes.md">${esc(row.grade_code)}</span>`
      : '<span class="badge idle">待确认</span>';
    case "findings": return pipeFindingChips(ctx.hits.get(row.stream) || []);
    case "warnings": {
      const list = row.warnings || [];
      if (!list.length) return '<span class="muted">—</span>';
      return `<span title="${esc(list.join("\n"))}">${esc(list[0])}` +
        (list.length > 1 ? ` <span class="muted">+${list.length - 1}</span>` : "") + "</span>";
    }
    case "design_pressure_mpa": return pipePressure(row.design_pressure_mpa);
    default:
      return col.num ? fmt(row[col.key], col.digits ?? 2) : esc(row[col.key] ?? "—");
  }
}

function pipeCellHtml(row, col, ctx) {
  const list = (ctx.hits.get(row.stream) || []).filter((f) => f.field === col.key);
  const overDn = col.key === "dn" && row.dn !== null && row.dn !== undefined
    && row.dn > pipeState.params.max_dn;
  const hit = list.some((f) => f.level === "error") ? "hit-err"
    : (list.length || overDn) ? "hit-warn" : "";
  const titles = list.map((f) => `${f.code} ${f.message}`);
  if (overDn) titles.push(`DN${row.dn} 超出本期上限 DN${pipeState.params.max_dn},需专项确认`);
  if (col.key === "pressure" || col.key === "design_pressure_mpa") {
    const t = pipePressureTitle(row);
    if (t) titles.push(t);
  }
  const cls = [col.cls || "", col.num ? "num" : "", col.mono ? "mono" : "", hit]
    .filter(Boolean).join(" ");
  const title = titles.length ? ` title="${esc(titles.join("\n"))}"` : "";
  return `<td class="${cls}"${title}>${pipeCellValue(row, col, ctx)}</td>`;
}

function pipeVisibleRows(hits) {
  const q = pipeState.view.filter.trim().toLowerCase();
  return ((pipeState.data && pipeState.data.rows) || []).filter((row) => {
    if (q && !String(row.stream).toLowerCase().includes(q)) return false;
    if (!pipeState.view.onlyBad) return true;
    const list = hits.get(row.stream) || [];
    const overDn = row.dn !== null && row.dn !== undefined && row.dn > pipeState.params.max_dn;
    return list.length > 0 || overDn;
  });
}

/* 表体 + 汇总。返回显示的行数 */
function drawPipeTable() {
  const data = pipeState.data;
  const thead = document.querySelector("#pipeTable thead");
  const tbody = document.querySelector("#pipeTable tbody");
  const wrap = document.getElementById("pipeTableWrap");
  if (!thead || !tbody) return 0;

  if (!data) {
    thead.innerHTML = "";
    tbody.innerHTML = "";
    return 0;
  }

  const cols = PIPE_COLUMNS.filter((c) => c.group !== "calc" || pipeState.view.showCalc);
  thead.innerHTML = `<tr>${cols.map((c) => `<th scope="col" class="${
    [c.num ? "num" : "", c.cls || "", c.mono ? "mono" : ""].filter(Boolean).join(" ")
  }">${esc(c.label)}</th>`).join("")}</tr>`;

  const hits = pipeFindingMap();
  const ctx = { streams: pipeStreamIndex(), hits };
  const rows = pipeVisibleRows(hits);

  tbody.innerHTML = rows.map((row) => {
    const list = hits.get(row.stream) || [];
    // 行背景只跟校验发现走:壁厚未录入时 rows[].warnings 每行都有,拿它染色会全表黄
    const rowCls = list.some((f) => f.level === "error") ? "row-err"
      : list.length ? "row-warn" : "";
    return `<tr class="${rowCls}" data-stream="${esc(row.stream)}">${
      cols.map((c) => pipeCellHtml(row, c, ctx)).join("")}</tr>`;
  }).join("");

  tbody.querySelectorAll(".pipe-chip").forEach((b) => {
    b.addEventListener("click", () => highlightPipeRow(b.dataset.stream));
  });

  renderPipeCount(rows.length);
  return rows.length;
}

function highlightPipeRow(stream) {
  const tbody = document.querySelector("#pipeTable tbody");
  if (!tbody) return;
  tbody.querySelectorAll("tr.pipe-row-hl").forEach((tr) => tr.classList.remove("pipe-row-hl"));
  const tr = [...tbody.rows].find((x) => x.dataset.stream === stream);
  if (!tr) return;                       // 被筛掉了:不硬跳,免得看起来像点了没反应
  tr.classList.add("pipe-row-hl");
  tr.scrollIntoView({ block: "center" });
}

function renderPipeCount(shown) {
  const el = document.getElementById("pipeCount");
  const d = pipeState.data;
  if (!el) return;
  if (!d) { el.textContent = ""; return; }
  const s = d.summary || {};
  const v = (d.validation && d.validation.summary) || {};
  el.textContent = `${shown} / ${(d.rows || []).length} 行`
    + (s.missing ? ` · 未选型 ${s.missing}` : "")
    + (v.error ? ` · 错误 ${v.error}` : "")
    + (v.warn ? ` · 警告 ${v.warn}` : "");
}

function renderPipeMissing() {
  const card = document.getElementById("pipeMissing");
  if (!card) return;
  const list = (pipeState.data && pipeState.data.missing) || [];
  if (!list.length) { card.classList.add("hidden"); return; }
  const idx = pipeStreamIndex();
  document.getElementById("pipeMissingTitle").textContent = `未选型的流股(${list.length})`;
  document.getElementById("pipeMissingBody").innerHTML = `<ul class="pipe-missing">${
    list.map((m) => {
      const s = idx.get(m.stream) || {};
      return `<li><b>${esc(m.stream)}</b> <span class="muted">${esc(m.reason)}</span>`
        + ` <span class="muted">Q=${fmt(s.vol_flow_m3h, 2)} m³/h</span></li>`;
    }).join("")}</ul>`;
  card.classList.remove("hidden");
}

function renderPipeFindings() {
  const card = document.getElementById("pipeFindings");
  if (!card) return;
  const v = pipeState.data && pipeState.data.validation;
  const items = (v && v.findings) || [];
  if (!items.length) { card.classList.add("hidden"); return; }

  const groups = new Map();
  items.forEach((f) => {
    if (!groups.has(f.code)) groups.set(f.code, []);
    groups.get(f.code).push(f);
  });
  document.getElementById("pipeFindingsTitle").textContent =
    `校验发现(${items.length} 条:错误 ${v.summary.error} · 警告 ${v.summary.warn})`;
  document.getElementById("pipeFindingsBody").innerHTML = [...groups.entries()].map(
    ([code, list]) => `
    <div class="pipe-finding-group">
      <h4><span class="badge ${list[0].level === "error" ? "err" : "warn"}">${esc(code)}</span>
        ${esc(PIPE_CODE_TEXT[code] || "")} · ${list.length} 条</h4>
      <ul>${list.map((f) => `<li><a href="#" class="inline-link" data-stream="${esc(f.stream)}"
        >${esc(f.stream)}</a> ${esc(f.message)}</li>`).join("")}</ul>
    </div>`).join("");

  document.querySelectorAll("#pipeFindingsBody a[data-stream]").forEach((a) => {
    a.addEventListener("click", (e) => { e.preventDefault(); highlightPipeRow(a.dataset.stream); });
  });
  card.classList.remove("hidden");
}

function renderPipeNotice() {
  const el = document.getElementById("pipeNotice");
  if (!el) return;
  let cls = "";
  let text = "";
  // 「正在重算」不再插一条横幅:快速改参数时会一闪而过,体验差。
  // 计算中改由参数条右侧的 #pipeStatus 表示(见 renderPipeStatus)。
  if (pipeState.status === "invalid") {
    cls = "warn"; text = pipeState.error;
  } else if (pipeState.status === "error") {
    cls = "err"; text = pipeState.error;
  } else if (pipeState.status === "ok" && pipeState.drift) {
    cls = "warn";
    text = `服务端实际用了另一组口径:${pipeState.drift.map(([k, a, b]) => `${k} ${a} → ${b}`).join("、")}`;
  }
  if (!text) { el.className = "pipe-notice hidden"; el.innerHTML = ""; return; }
  el.className = `pipe-notice ${cls}`;
  el.innerHTML = `<span>${esc(text)}</span>`;
}

/* 参数条右侧的状态标记:位置固定、文字短,不插横幅也不整表变淡
   - 只有连续计算超过 400 ms 才出现「更新中…」,避免快速输入时闪一下
   - 算完显示「已更新」1.6 s 后自动消失 */
let pipeStatusTimer = null;
let pipeStatusClear = null;
function renderPipeStatus() {
  const el = document.getElementById("pipeStatus");
  if (!el) return;
  clearTimeout(pipeStatusTimer);
  clearTimeout(pipeStatusClear);
  el.className = "pipe-status";
  el.textContent = "";
  if (pipeState.status === "loading") {
    pipeStatusTimer = setTimeout(() => {
      if (pipeState.status !== "loading") return;
      el.textContent = "更新中…";
      el.className = "pipe-status busy";
    }, 400);
    return;
  }
  if (pipeState.status === "ok") {
    el.textContent = "已更新";
    el.className = "pipe-status ok";
    pipeStatusClear = setTimeout(() => {
      el.textContent = "";
      el.className = "pipe-status";
    }, 1600);
  }
}

/* 200 但 ready=false:只可能是壁厚序列(T2)没录 —— 表能出、DN 与等级号有效,只有壁厚是「—」 */
function renderPipeTablesHint() {
  const el = document.getElementById("pipeTablesHint");
  if (!el) return;
  const t2 = pipeState.tables && pipeState.tables.od_to_walls;
  if (!pipeState.data || !t2 || t2.loaded) { el.classList.add("hidden"); return; }
  el.className = "pipe-notice warn";
  el.innerHTML = "<span><b>标准壁厚序列(T2)未录入</b>:壁厚列会全是「—」,备注里每行也会写一句。"
    + "DN、等级号、设计压力不受影响。"
    + "补录办法:把 GB/T 17395-2024 的表 1、表 4 整理成 "
    + "<code>t1-17395-table1.csv</code> / <code>t4-17395-table4.csv</code>,"
    + "放进 <code>data/preview/std/docx/</code>,重启服务后重算"
    + "(页码见 <code>docs/pipe-standards-index.md</code>)。</span>";
}

/* 409 卡片:三张标准表录入状态 + 下一步。这里要明确说「改参数没用」,并把补录办法列全 */
function pipeBlockedHtml(message, tables, setup) {
  const t = tables || {};
  const t1 = t.dn_to_od || {};
  const t2 = t.od_to_walls || {};
  const t3 = t.stress || {};
  const line = (label, obj, extra) => {
    const ok = Boolean(obj && obj.loaded);
    return `<li><span class="badge ${ok ? "ok" : "err"}">${ok ? "已录" : "未录"}</span> `
      + `${label}<span class="muted">${extra || ""}</span></li>`;
  };
  const guide = setup ? `<div class="pipe-setup">
      <h4>怎么补上</h4>
      <p class="muted">${esc(setup.why)}</p>
      <table class="pipe-setup-files">
        <thead><tr><th>放到 <code>${esc(setup.dir)}</code> 下的文件</th><th>来自哪张表</th><th>必需列</th></tr></thead>
        <tbody>${(setup.files || []).map((f) => `<tr>
          <td><code>${esc(f.name)}</code></td><td>${esc(f.from)}</td><td>${esc(f.columns)}</td>
        </tr>`).join("")}</tbody>
      </table>
      <ol class="pipe-setup-steps">${(setup.steps || []).map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
      <p class="muted">表格页码、分页结构与数据校验方法见 <code>${esc(setup.doc)}</code>${
        setup.prompt ? `;要交给 AI 帮着抄数,可以用 <code>${esc(setup.prompt)}</code> 里的提示词` : ""
      }。</p>
    </div>` : "";
  return `<div class="pipe-blocked">
    <h3>管道表暂时算不出来:标准表没录全</h3>
    <p class="muted">${esc(message)}</p>
    <ul class="pipe-table-status">
      ${line("DN → 外径(T1)", t1, t1.loaded ? ` ${t1.count} 档,其中 ${(t1.verified || []).length} 档已在标准里核对` : "")}
      ${line("外径 → 标准壁厚(T2)", t2, t2.loaded ? ` ${t2.count} 个外径` : "")}
      ${line("许用应力(T3)", t3, t3.loaded ? ` ${t3.count} 个温度点,牌号 ${(t3.grades || []).join("、")}` : "")}
    </ul>
    <p class="muted">表没录全之前,这一页不会显示任何「估出来」的管径 —— 宁可空着。</p>
    ${guide}
  </div>`;
}

async function loadPipeTableStatus() {
  if (pipeState.tables || pipeState.tablesLoading) return;
  pipeState.tablesLoading = true;
  try { pipeState.tables = await api("/api/pipe-tables"); } catch (err) { /* 老服务没这接口,忽略 */ }
  pipeState.tablesLoading = false;
  if (!state.current) renderPipes();
}

/* 页面入口:进页 / 换工况 / 算完都走这里 */
function renderPipes() {
  const empty = document.getElementById("pipeEmpty");
  const c = state.current;
  if (!empty) return;

  // #pipes/class 可直接打开等级表弹窗(方便把配置页加入书签)
  if (!pipeClassAutoOpened && location.hash.endsWith("/class")) {
    pipeClassAutoOpened = true;
    openPipeClass();
  }

  const hideExtras = () => {
    ["pipeMissing", "pipeFindings", "pipeTablesHint"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.classList.add("hidden");
    });
  };

  if (!c) {
    pipeState.data = null; pipeState.caseId = null; pipeState.status = "idle";
    renderPipeNotice();
    drawPipeTable();
    document.getElementById("pipeCount").textContent = "";
    hideExtras();
    empty.innerHTML = "还没有工况。先读取一个工况,这一页会按它的流股自动生成管道表。";
    empty.classList.remove("hidden");
    loadPipeTableStatus();
    return;
  }

  if (pipeState.caseId !== c.id) {
    pipeState.data = null; pipeState.lastQuery = null;
    if (pipeState.status === "ok") pipeState.status = "idle";
  }
  if (pipeState.status === "idle") { runPipeCalc(); return; }   // 算完会再回来渲染

  fillPipeSelects(pipeState.tables);
  renderPipeNotice();
  renderPipeStatus();

  if (pipeState.status === "not-ready") {
    drawPipeTable();
    hideExtras();
    empty.innerHTML = pipeBlockedHtml(pipeState.error, pipeState.tables, pipeState.setup);
    empty.classList.remove("hidden");
    document.getElementById("pipeCount").textContent = "";
    return;
  }
  if (pipeState.status === "error") {
    drawPipeTable();
    empty.innerHTML = esc(pipeState.error)
      + "<br><br>后台服务可能没起来,重新启动后再试。";
    empty.classList.remove("hidden");
    return;
  }

  empty.classList.add("hidden");
  drawPipeTable();
  renderPipeTablesHint();
  renderPipeMissing();
  renderPipeFindings();
}
