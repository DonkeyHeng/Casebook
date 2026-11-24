/* Casebook 视图 · heat

换热器 L1 抽屉:左列只读结果(每项带来源),右列人工决定(管/壳分配、
污垢档、裕量、F、公用工程预设)。判定只到「参考」,界面不出现「合格」。

**溯源是硬要求**:面积、公用工程、U、LMTD 四项分别标 aspen / manual /
preset / assumed / computed,假设值不得显示成校核值。
*/

import { closeDataDrawers, focusIntoDrawer, markDrawerOpened } from "./pump.js";
import { api, apiDelete, apiPost, esc, fmt } from "../lib/core.js";
import { state } from "../lib/store.js";

const cache = { caseId: null, rows: null, config: null };
let currentRow = null;
let configTab = "fouling";

const VERDICT_TEXT = {
  blocked: ["拦截", "err"],
  unknown: ["缺数据", "warn"],
  reference: ["参考", "warn"],
  pass: ["通过", "ok"],
  fail: ["不合格", "err"],
};

export function heatCaseName(row) {
  return row?.case?.block || row?.block || "";
}

// 标题口径:仅有需求面积而无公称面积时写「需求面积(估算)」,不写「缺面积」。
function heatSourceText(row) {
  const area = (row.case && row.case.area) || {};
  let areaText;
  if (area.source === "aspen") areaText = "Aspen简捷";
  else if (area.source === "manual") areaText = "人工面积";
  else if (area.source === "edr") areaText = "EDR校核";
  else areaText = area.required_m2 ? "需求面积(估算)" : "缺面积";
  const utility = row.case && row.case.utility;
  const utilText = !utility
    ? "无公用工程"
    : utility.source === "aspen"
      ? "公用工程实测"
      : utility.source === "preset"
        ? "公用工程预设"
        : utility.source === "manual"
          ? "公用工程人工"
          : "缺公用工程温度";
  return `管壳式 L1 参考 · 面积来源 ${areaText} · ${utilText}`;
}

function verdictBadge(verdict) {
  const [text, cls] = VERDICT_TEXT[verdict] || ["未判定", "warn"];
  return `<span class="badge ${cls}" title="L1 仅给参考结论;合格判定需 EDR rating(本期未实现)">${text}</span>`;
}

function srcTag(source) {
  const labels = {
    aspen: "Aspen",
    manual: "人工",
    preset: "预设",
    assumed: "经验假设",
    computed: "自算",
    none: "无",
    edr: "EDR",
    builtin: "内置",
    rule: "规则",
    user: "用户",
  };
  return `<span class="muted">${esc(labels[source] || source || "未取得")}</span>`;
}

async function loadHeat(force = false) {
  if (!state.current?.id) throw new Error("未载入工况");
  if (!force && cache.caseId === state.current?.id && cache.rows) return cache;
  const [payload, config] = await Promise.all([
    api(`/api/cases/${state.current?.id}/heat`),
    cache.config ? Promise.resolve(cache.config) : api("/api/heat-config"),
  ]);
  cache.caseId = state.current?.id;
  cache.rows = payload.rows || [];
  cache.config = config;
  return cache;
}

function optionList(rows, selected) {
  return rows
    .map(([key, label]) => `<option value="${esc(key)}"${key === selected ? " selected" : ""}>${esc(label)}</option>`)
    .join("");
}

function foulingOptions(table, selected) {
  const blank = `<option value=""${selected ? "" : " selected"}>— 不选 —</option>`;
  const items = Object.entries(table || {})
    .map(([key, row]) => [key, `${row.label || key}(${fmt(row.value, 6)})`]);
  return blank + optionList(items, selected);
}

function renderHeatTable(row) {
  const c = row.case;
  const kv = (label, value, source, tip = "") =>
    `<tr><td class="name">${esc(label)}</td><td class="num">${value}</td>`
    + `<td class="msg"${tip ? ` title="${esc(tip)}"` : ""}>${source}</td></tr>`;

  const hot = c.hot || c.sides?.hot || c.sides?.process || {};
  const cold = c.cold || c.sides?.cold || c.sides?.utility || {};
  const area = c.area || {};
  const u = c.u || {};
  const lmtd = c.lmtd || {};

  const isHeatX = c.kind === "HeatX";
  const proc = c.sides?.process || hot;
  const util = c.sides?.utility || {};
  // 公用工程非物料流股:Aspen 仅在 \Data\Utilities\<U> 提供温度。
  // 判据:工艺侧有流股名;公用工程侧 stream 为空且带 utility_id。
  const utilLabel = util.utility_id ? `(${esc(util.utility_id)})` : "";
  const utilSource = util.source === "aspen"
    ? `Aspen 公用工程${util.utility_id ? ` ${esc(util.utility_id)}` : ""}`
    : util.source === "preset" ? "预设档"
      : util.source === "manual" ? "人工" : "无";

  const rows = [
    kv("热负荷 kW", fmt(c.duty_kw, 2), "Aspen"),
  ];
  if (isHeatX) {
    rows.push(
      kv(`热侧温度 ℃${hot.stream ? `(流股 ${esc(hot.stream)})` : ""}`,
        `${fmt(hot.t_in_c, 1)} → ${fmt(hot.t_out_c, 1)}`, "流股读数"),
      kv(`冷侧温度 ℃${cold.stream ? `(流股 ${esc(cold.stream)})` : ""}`,
        `${fmt(cold.t_in_c, 1)} → ${fmt(cold.t_out_c, 1)}`, "流股读数"),
    );
  } else {
    rows.push(
      kv(`工艺侧温度 ℃${proc.stream ? `(流股 ${esc(proc.stream)})` : ""}`,
        `${fmt(proc.t_in_c, 1)} → ${fmt(proc.t_out_c, 1)}`, "流股读数"),
      kv(`公用工程侧温度 ℃${utilLabel}`,
        `${fmt(util.t_in_c, 1)} → ${fmt(util.t_out_c, 1)}`, utilSource,
        "公用工程侧非物料流股:Aspen 仅提供 Utilities 下进出口温度"),
    );
  }
  rows.push(kv("ΔTm(自算) ℃", fmt(lmtd.self_c, 2), srcTag(lmtd.source), "逆流;未做 F 修正时按 F=1"));
  if (isHeatX) {
    // 这 6 项是 HeatX 专属节点(HX_DTLM/面积/HX_UAVD/HX_UA),Heater 在 Aspen 里没有,
    // 硬显示只会是一排"—"。
    rows.push(
      kv("ΔTm(Aspen) ℃", fmt(lmtd.aspen_c, 2), "Aspen HX_DTLM", "是否含 F 未验证"),
      kv("F 修正", fmt(lmtd.f, 3), lmtd.source === "computed" ? "未修正" : "Aspen"),
      kv("面积 m²", fmt(area.used_m2, 2), srcTag(area.source),
        area.source === "aspen" ? "Aspen 简捷面积:按假设 U 推算,非 rating 结果" : ""),
      kv("U 反算 W/(m²·K)", fmt(u.back_calc_wm2k, 1), "计算 Q/(A·ΔTm)"),
      kv("U(Aspen 设计) W/(m²·K)", fmt(u.aspen_wm2k, 1), "Aspen HX_UAVD",
        "取值 850:来自简捷设计中人工输入的 U"),
      kv("UA W/K", fmt(u.ua_wk, 1), "Aspen HX_UA"),
    );
  }
  rows.push(
    kv("需求面积 A_req m²", fmt(area.required_m2, 2), "计算",
      `U=${fmt(u.aspen_wm2k || u.assumed_wm2k, 0)};裕量 ${fmt(area.margin, 2)}`),
    kv("设计压力(管/壳) bar", `${fmt(c.design?.tube?.p_design_bar, 2)} / ${fmt(c.design?.shell?.p_design_bar, 2)}`, "规则"),
    kv("设计温度(管/壳) ℃", `${fmt(c.design?.tube?.t_design_c, 1)} / ${fmt(c.design?.shell?.t_design_c, 1)}`, "规则"),
    kv("允许压降(管/壳) bar", `${fmt(c.design?.tube?.dp_allow_bar, 2)} / ${fmt(c.design?.shell?.dp_allow_bar, 2)}`, "规则"),
    kv("污垢热阻(管/壳) m²·K/W", `${fmt(c.fouling?.tube, 6)} / ${fmt(c.fouling?.shell, 6)}`,
      c.fouling?.tube_key || c.fouling?.shell_key ? "查表" : "未选"),
  );
  if (isHeatX) {
    rows.push(kv("公用工程", c.utility ? `${c.utility.t_in_c} → ${c.utility.t_out_c} ℃` : "—",
      c.utility ? srcTag(c.utility.source) : "无"));
  }
  rows.push(
    kv("型号编码", row.model_code?.code || row.model_code?.code_partial || "—",
      row.model_code?.code ? "完整" : "缺项占位"),
    kv("判定", verdictBadge(c.verdict), c.verdict_note || ""),
  );

  document.querySelector("#heatTable thead").innerHTML =
    '<tr><th scope="col">参数</th><th scope="col" class="num">值</th><th scope="col">来源</th></tr>';
  document.querySelector("#heatTable tbody").innerHTML = rows.join("");

  const blockers = c.blockers || [];
  const warnings = c.warnings || [];
  document.getElementById("heatBlockers").innerHTML = [
    ...blockers.map((b, i) => `<p class="heat-block">⛔ ${esc(c.blocker_text?.[i] || b)}</p>`),
    ...warnings.map((w, i) => `<p class="heat-warn">⚠ ${esc(c.warning_text?.[i] || w)}</p>`),
  ].join("") || '<p class="muted">无拦截项与假设项。</p>';
}

function renderHeatForm(row) {
  const c = row.case;
  const stored = row.design_row || {};
  const cfg = cache.config || {};
  const fouling = cfg.fouling?.data || {};
  const presets = cfg["utility-presets"]?.data || {};
  const kind = c.kind;
  const tubeOptions = kind === "HeatX"
    ? [["hot", "热侧走管程"], ["cold", "冷侧走管程"]]
    : [["process", "工艺侧走管程"], ["utility", "公用工程走管程"]];
  const presetItems = Object.entries(presets).map(([key, p]) => [key, `${p.label || key}(${p.t_in_c}/${p.t_out_c} ℃)`]);

  const manual = stored.utility_mode === "manual";
  const form = `
    <div class="pump-block-head"><span class="pump-block-title">人工决定</span></div>
    <label class="heat-field">管程侧
      <select id="heatTube">${optionList(tubeOptions, c.tube_fluid)}</select>
    </label>
    <label class="heat-field">管程污垢档
      <select id="heatFoulTube">${foulingOptions(fouling, stored.fouling_tube_key)}</select>
    </label>
    <label class="heat-field">壳程污垢档
      <select id="heatFoulShell">${foulingOptions(fouling, stored.fouling_shell_key)}</select>
    </label>
    <label class="heat-field">面积裕量
      <input id="heatMargin" type="number" step="0.05" min="0" max="1" value="${esc(stored.margin ?? 0.30)}">
    </label>
    <label class="heat-field">F 修正(空=未修正)
      <input id="heatF" type="number" step="0.01" min="0.4" max="1" value="${stored.f_correction == null ? "" : esc(stored.f_correction)}">
    </label>
    ${kind === "HeatX" ? "" : `
      <label class="heat-field">公用工程预设
        <select id="heatPreset">${optionList([["", "— 按 Aspen / 不预设 —"], ...presetItems], stored.utility_key)}</select>
      </label>
      <label class="heat-field">公用工程手填温度 ℃
        <input id="heatUtilIn" type="number" step="0.1" placeholder="进口" value="${manual ? esc(stored.utility_t_in_c) : ""}">
      </label>
      <label class="heat-field">出口温度 ℃
        <input id="heatUtilOut" type="number" step="0.1" placeholder="出口" value="${manual ? esc(stored.utility_t_out_c) : ""}">
      </label>`}
    <p class="muted heat-note">优先级:公用工程节点读数 > 预设 > 手填;后两者标注来源。</p>
    <button id="heatSave" class="btn primary" type="button">保存这台</button>
    <button id="heatWord" class="btn" type="button" title="§9.2 冻结十五列的《换热器选型一览表》">导出 Word 一览表</button>
    <button id="heatExport" class="btn" type="button" title="按冷热侧/压降/污垢/几何分组的 EDR 录入清单">导出 EDR 清单</button>
    <p id="heatSaveMsg" class="muted"></p>
  `;
  document.getElementById("heatForm").innerHTML = form;
  document.getElementById("heatConfigBtn").onclick = openConfigEditor;
  document.getElementById("heatSave").addEventListener("click", () => saveHeat(row));
  document.getElementById("heatWord").addEventListener("click", () => {
    window.location.href = `/api/cases/${state.current?.id}/heat.docx`;
  });
  document.getElementById("heatExport").addEventListener("click", () => {
    window.location.href = `/api/cases/${state.current?.id}/heat-edr.xlsx`;
  });
}

async function saveHeat(row) {
  const msg = document.getElementById("heatSaveMsg");
  const cfg = cache.config || {};
  const fouling = cfg.fouling?.data || {};
  const values = { tube_fluid: document.getElementById("heatTube").value };

  const tubeKey = document.getElementById("heatFoulTube").value;
  const shellKey = document.getElementById("heatFoulShell").value;
  values.fouling_tube_key = tubeKey || null;
  values.fouling_shell_key = shellKey || null;
  values.fouling_tube = tubeKey ? Number(fouling[tubeKey]?.value) : null;
  values.fouling_shell = shellKey ? Number(fouling[shellKey]?.value) : null;

  const margin = document.getElementById("heatMargin").value;
  values.margin = margin === "" ? null : Number(margin);
  const f = document.getElementById("heatF").value;
  values.f_correction = f === "" ? null : Number(f);

  if (row.case.kind !== "HeatX") {
    const preset = document.getElementById("heatPreset").value;
    values.utility_key = preset || null;
    const tIn = document.getElementById("heatUtilIn").value;
    const tOut = document.getElementById("heatUtilOut").value;
    if (tIn !== "" && tOut !== "") {
      values.utility_mode = "manual";
      values.utility_t_in_c = Number(tIn);
      values.utility_t_out_c = Number(tOut);
      values.utility_phase = Math.abs(Number(tIn) - Number(tOut)) <= 1 ? "condensing" : "liquid";
    } else {
      values.utility_mode = preset ? "preset" : "none";
      values.utility_t_in_c = null;
      values.utility_t_out_c = null;
      values.utility_phase = null;
    }
  }

  try {
    await apiPost(`/api/cases/${state.current?.id}/heat/${encodeURIComponent(row.block)}/design`, values);
    msg.textContent = "已保存,正在刷新…";
    cache.rows = null;
    await refresh(state.blocks.find((b) => b.name === row.block));
  } catch (err) {
    msg.textContent = `保存失败:${err.message}`;
  }
}

async function refresh(blockRow) {
  const data = await loadHeat(true);
  const row = data.rows.find((r) => r.block === blockRow?.name) || data.rows[0];
  if (!row) return;
  openHeat(row.block);
}

export async function openHeat(block) {
  ["columnPanel", "compPanel", "pumpPanel"].forEach((id) =>
    document.getElementById(id)?.classList.add("hidden"));
  const panel = document.getElementById("heatPanel");
  panel.classList.remove("hidden");
  markDrawerOpened();
  focusIntoDrawer(panel);
  document.getElementById("heatTitle").textContent = `换热器选型 · ${block}`;
  document.getElementById("heatHint").textContent = "正在读取 L1 结果…";

  try {
    const data = await loadHeat();
    const row = data.rows.find((r) => r.block === block);
    if (!row) {
      document.getElementById("heatHint").textContent = `${block}:不在换热设备清单`;
      return;
    }
    document.getElementById("heatHint").textContent = heatSourceText(row);
    currentRow = row;
    renderHeatTable(row);
    renderHeatForm(row);
    renderL2Loading();
    loadDetail(block).then((detail) => {
      if (currentRow?.block === block) renderL2(detail);
    }).catch((err) => {
      document.getElementById("heatL2").innerHTML =
        `<div class="pump-block-head"><span class="pump-block-title">几何初选</span></div>
         <p class="heat-warn">⚠ 读取几何初选失败:${esc(err.message)}</p>`;
    });
  } catch (err) {
    document.getElementById("heatHint").textContent = `读取失败:${err.message}`;
  }
}

function loadDetail(block, force = false) {
  const base = `/api/cases/${state.current?.id}/heat/${encodeURIComponent(block)}`;
  return api(force ? `${base}?refresh=1` : base);
}

function renderL2Loading() {
  document.getElementById("heatL2").innerHTML =
    `<div class="pump-block-head"><span class="pump-block-title">L2 几何初选</span></div>
     <p class="muted">正在读取物性并枚举标准行…</p>`;
}

const L2_VERDICT = {
  pass: ["简捷通过", "ok"],
  reference: ["参考", "warn"],
  fail: ["未通过", "err"],
  unknown: ["缺数据", "warn"],
  blocked: ["已拦截", "err"],
};

function l2Badge(verdict) {
  const [text, cls] = L2_VERDICT[verdict] || ["未判定", "warn"];
  return `<span class="badge ${cls}">${text}</span>`;
}

function renderL2(detail) {
  const el = document.getElementById("heatL2");
  const l2 = detail?.l2 || {};
  const props = detail?.props || {};
  const std = detail?.std_state || {};
  const banners = [];

  if (std.state && std.state !== "ok") {
    banners.push(`<p class="heat-warn">⚠ 标准表:${esc(std.label || std.state)};${
      esc(std.reason || "")} · 已跳过规格圆整,仅给 A_req 与经验范围。</p>`);
  }
  if (props.source === "unavailable" && props.note) {
    banners.push(`<p class="heat-warn">⚠ 物性不可用:${esc(props.note).replace(/\n/g, "<br>")}</p>`);
  }
  if (props.missing?.length) {
    banners.push(`<p class="muted">物性缺失:${esc(props.missing.join("、"))}。<br>
      传热系数需物性齐备,当前仅给几何与面积。<br>
      补法:物性组加 CPMX(摩尔基)或 CPMASS(质量基)与 KMX;步骤见首页「Aspen 物性组」。返回本页点「重新读物性」。</p>`);
  }
  const utilSide = detail?.row?.case?.sides?.utility;
  if (utilSide && !utilSide.stream) {
    banners.push(utilSide.utility_id
      ? `<p class="muted">公用工程侧(${esc(utilSide.utility_id)})非物料流股:Aspen 仅在 Utilities 记录进出口温度,无 cp、λ、μ。<br>
        传热系数需两侧物性,当前仅算工艺侧,判定止于「缺数据」。</p>`
      : `<p class="muted"><b>未关联公用工程</b>(公用工程 ID 为空):缺冷/热侧温度,LMTD 与需求面积无法计算。<br>
        补法:在 Aspen 配置公用工程,或在右侧「公用工程」面板选择预设。</p>`);
  }
  const assumptions = l2.assumptions || [];
  if (assumptions.length) {
    banners.push(`<p class="muted">以下 ${assumptions.length} 条为程序假设,未经人工确认;
      结论上限为「参考」,不出「合格」:</p>
      <ul class="heat-assume">${assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>`);
  }

  const rows = (l2.candidates || []).map((c) => {
    const key = c.std_row_key || "—";
    const picked = c.std_row_key && c.std_row_key === detail?.manual?.std_row_key;
    return `<tr${picked ? ' class="is-picked"' : ""}>
      <td class="name">${esc(key)}</td>
      <td class="num">${fmt(c.area_std_m2, 1)}</td>
      <td class="num">${fmt(c.area_ratio, 2)}</td>
      <td class="num">${fmt(c.u_dirty_wm2k, 0)}</td>
      <td class="num">${fmt(c.tube?.dp_bar, 2)}/${fmt(c.shell?.dp_bar, 2)}</td>
      <td>${l2Badge(c.verdict)}</td>
      <td><button class="btn small" type="button" data-pick="${esc(key)}">采用</button></td>
    </tr>`;
  }).join("");

  const estimate = l2.estimate;
  const estimateHtml = estimate?.cases?.length
    ? `<table class="table-wrap small"><thead><tr><th scope="col">管径 mm</th><th scope="col">管长 mm</th>
        <th scope="col">管数范围</th><th scope="col">壳径估算 mm</th></tr></thead><tbody>
        ${estimate.cases.map((x) => `<tr><td>φ${fmt(x.tube_od_mm, 0)}</td>
          <td>${x.lengths_mm.map((v) => fmt(v, 0)).join(" / ")}</td>
          <td>${x.count_min} ~ ${x.count_max}</td>
          <td>${fmt(estimate.shell_mm?.[String(x.tube_od_mm)] ?? estimate.shell_mm?.[String(Number(x.tube_od_mm))], 0)}</td></tr>`).join("")}
        </tbody></table>
        <p class="muted">经验范围:仅估量级,非标准规格,不进型号。</p>`
    : "";

  const manual = detail?.manual;
  const manualHtml = manual
    ? `<p class="muted">人工覆盖:壳径 ${fmt(manual.shell_id_mm, 0)} mm、φ${fmt(manual.tube_od_mm, 0)}×${fmt(manual.tube_wall_mm, 1)}、
       长 ${fmt(manual.tube_length_mm, 0)} mm,${fmt(manual.tube_count, 0)} 根,${fmt(manual.tube_passes, 0)} 管程
       ${manual.verdict ? `· ${l2Badge(manual.verdict)}` : ""}
       <button id="heatClearGeom" class="btn small" type="button">清除覆盖</button></p>`
    : "";

  // 键格式说明取首个标准行作示例,避免用户从键值反推字段。
  const sample = (l2.candidates || []).find((c) => c.std_row_key) || null;
  const wall = sample?.tube_wall_mm == null ? "" : `×${sample.tube_wall_mm}`;
  // 无候选行(缺面积)时不显示,避免引用空表。
  const keyHelp = sample
    ? `<p class="muted heat-key">键格式:公称直径 | 管子外径 | 管心距 | 管程 | 管长(mm)。<br>
      示例 <span class="heat-key-code">${esc(sample.std_row_key)}</span> = DN${fmt(sample.dn_mm, 0)} 壳体、φ${fmt(sample.tube_od_mm, 0)}${wall} 管、管心距 ${fmt(sample.tube_pitch_mm, 0)} mm、${fmt(sample.tube_passes, 0)} 管程、管长 ${fmt(sample.tube_length_mm, 0)} mm;标准面积 ${fmt(sample.area_std_m2, 1)} m²(已扣除两端管板)。<br>
      标准行为厂家可制造的离散规格,按面积就近选取。</p>`
    : "";

  el.innerHTML = `
    <div class="pump-block-head"><span class="pump-block-title">L2 几何初选</span>
      <span class="muted">${esc(l2.verdict_text || "")}</span></div>
    ${banners.join("")}
    ${rows ? `<div class="table-wrap small"><table>
      <thead><tr><th scope="col">标准行</th><th scope="col" class="num">标准面积 m²</th>
      <th scope="col" class="num" title="面积裕度比 = 标准公称面积 / 需求面积;越接近 1 越合适">面积比</th>
      <th scope="col" class="num">U 计算</th>
      <th scope="col" class="num">Δp 管/壳 bar</th><th scope="col">判定</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      ${keyHelp}` : ""}
    ${estimateHtml}
    ${manualHtml}
    <p class="muted heat-note">排序规则:按面积裕度比(A_std / A_req)趋近 1 优先。<br>
      「简捷通过」仅基于 Darcy-Kern 单相粗算(未含两相分段与流致振动校核);<br>
      终版设计须导入 EDR 复核。</p>
    <div class="muted">标准系列表:${esc(std.label || std.state || "未加载")}${std.rows ? ` · ${std.rows} 条记录` : ""}
      ${std.version ? ` (${esc(std.version)})` : ""}</div>
    ${std.state === "ok" ? "" : `<p class="muted heat-std-cta"><span>标准表未导入:仅给需求面积与经验范围,无法排列标准规格。<br>
      导入入口:首页 › 标准表(换热 2 张)。</span>
      <button class="btn small" type="button" data-goto-std="hx">去首页 › 标准表</button></p>`}
    <div class="heat-l2-actions">
      <button id="heatPropsRefresh" class="btn" type="button">重新读物性</button>
    </div>
    <p id="heatL2Msg" class="muted"></p>
  `;

  el.querySelectorAll("[data-pick]").forEach((btn) => {
    btn.addEventListener("click", () => pickGeometry(btn.dataset.pick, l2));
  });
  const clear = document.getElementById("heatClearGeom");
  if (clear) clear.addEventListener("click", clearGeometry);
  document.getElementById("heatPropsRefresh").addEventListener("click", async () => {
    document.getElementById("heatL2Msg").textContent = "正在重新读物性…";
    try {
      const next = await loadDetail(currentRow.block, true);
      renderL2(next);
    } catch (err) {
      document.getElementById("heatL2Msg").textContent = `读物性失败:${err.message}`;
    }
  });
}

async function pickGeometry(key, l2) {
  const item = (l2.candidates || []).find((c) => c.std_row_key === key);
  if (!item) return;
  const values = {
    geom_source: "manual",
    std_row_key: key,
    geom_shell_id_mm: item.shell_id_mm,
    geom_shell_od_mm: item.shell_od_mm,
    geom_tube_od_mm: item.tube_od_mm,
    geom_tube_wall_mm: item.tube_wall_mm,
    geom_tube_length_mm: item.tube_length_mm,
    geom_tube_passes: item.tube_passes,
    geom_tube_pitch_mm: item.tube_pitch_mm,
    geom_tube_count: item.tube_count,
    geom_arrangement: item.arrangement,
    geom_baffle_spacing_mm: item.baffle_mm,
    area_nominal_m2: item.area_std_m2,
  };
  try {
    await apiPost(`/api/cases/${state.current?.id}/heat/${encodeURIComponent(currentRow.block)}/design`, values);
    const detail = await loadDetail(currentRow.block);
    renderL2(detail);
    cache.rows = null;
  } catch (err) {
    document.getElementById("heatL2Msg").textContent = `采用失败:${err.message}`;
  }
}

async function clearGeometry() {
  const values = {
    geom_source: null,
    std_row_key: null,
    geom_shell_id_mm: null,
    geom_shell_od_mm: null,
    geom_tube_od_mm: null,
    geom_tube_wall_mm: null,
    geom_tube_length_mm: null,
    geom_tube_passes: null,
    geom_tube_pitch_mm: null,
    geom_tube_count: null,
    geom_arrangement: null,
    geom_baffle_spacing_mm: null,
  };
  try {
    await apiPost(`/api/cases/${state.current?.id}/heat/${encodeURIComponent(currentRow.block)}/design`, values);
    renderL2(await loadDetail(currentRow.block));
  } catch (err) {
    document.getElementById("heatL2Msg").textContent = `清除失败:${err.message}`;
  }
}


/* --------------------------------------------------------- 默认参数库 */

const CONFIG_TABS = {
  fouling: {
    label: "污垢热阻",
    hint: "单位 m²·K/W;仅改数值,不改键名。",
    fields: [["value", "热阻 m²·K/W", "number"]],
  },
  "u-reference": {
    label: "经验 U 区间",
    hint: "单位 W/(m²·K);default 须落在 min–max 之间。",
    fields: [
      ["min", "U 下限", "number"],
      ["default", "U 取值", "number"],
      ["max", "U 上限", "number"],
    ],
  },
  "utility-presets": {
    label: "公用工程预设",
    hint: "单位 ℃;相态决定是否按等温相变放行(仅工艺侧拦截)。",
    fields: [
      ["label", "名称", "text"],
      ["t_in_c", "进口 ℃", "number"],
      ["t_out_c", "出口 ℃", "number"],
      ["phase", "相态", "phase"],
    ],
  },
};

const PHASES = [
  ["condensing", "冷凝(等温)"],
  ["boiling", "沸腾(等温)"],
  ["liquid", "液相升温"],
  ["gas", "气相降温"],
];

function configModal() {
  return document.getElementById("heatConfigPanel");
}

function closeConfigEditor() {
  configModal()?.classList.add("hidden");
}

function renderConfigEditor(message = "") {
  const cfg = cache.config || {};
  const tab = CONFIG_TABS[configTab];
  const block = cfg[configTab] || { data: {}, source: "builtin" };
  const rows = Object.entries(block.data || {});
  const source = block.source === "user" ? "用户覆盖" : "内置默认";
  const cells = rows.map(([key, row]) => {
    const inputs = tab.fields.map(([field, label, type]) => {
      const value = row[field];
      if (type === "phase") {
        const opts = PHASES.map(([k, v]) =>
          `<option value="${k}"${k === value ? " selected" : ""}>${v}</option>`).join("");
        return `<td><label class="heat-cfg-label">${label}</label><select data-key="${esc(key)}" data-field="${field}">${opts}</select></td>`;
      }
      return `<td><label class="heat-cfg-label">${label}</label>`
        + `<input data-key="${esc(key)}" data-field="${field}" type="${type}"`
        + `${type === "number" ? ' step="any"' : ""} value="${esc(value)}"></td>`;
    }).join("");
    return `<tr><td class="name">${esc(key)}</td>${inputs}</tr>`;
  }).join("");
  const tabs = Object.entries(CONFIG_TABS).map(([key, item]) =>
    `<button type="button" class="heat-cfg-tab${key === configTab ? " active" : ""}" data-tab="${key}">${item.label}</button>`).join("");

  configModal().innerHTML = `
    <div class="heat-config-card" role="document">
      <header class="heat-config-head">
        <div>
          <h3>换热器默认参数库</h3>
          <p class="muted">当前「${tab.label}」:${source}。保存写入 data/config,仅覆盖本页。</p>
        </div>
        <button type="button" class="close" id="heatConfigClose" title="关闭"><svg class="ico"><use href="#i-close"/></svg></button>
      </header>
      <div class="heat-cfg-tabs">${tabs}</div>
      <p class="muted heat-note">${tab.hint}</p>
      <div class="table-wrap small heat-cfg-wrap">
        <table class="heat-cfg-table"><thead><tr><th>键</th>${tab.fields.map(([, l]) => `<th>${l}</th>`).join("")}</tr></thead>
        <tbody>${cells || '<tr><td colspan="9" class="muted">当前配置无条目</td></tr>'}</tbody></table>
      </div>
      <p id="heatConfigMsg" class="muted">${esc(message)}</p>
      <div class="heat-config-actions">
        <button type="button" class="btn primary" id="heatConfigSave">保存本页</button>
        <button type="button" class="btn" id="heatConfigReset">恢复本页内置默认</button>
      </div>
    </div>`;
  configModal().classList.remove("hidden");
  document.getElementById("heatConfigClose").addEventListener("click", closeConfigEditor);
  configModal().addEventListener("click", (event) => {
    if (event.target === configModal()) closeConfigEditor();
  });
  configModal().querySelectorAll(".heat-cfg-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      configTab = btn.dataset.tab;
      renderConfigEditor();
    });
  });
  document.getElementById("heatConfigSave").addEventListener("click", saveConfigTab);
  document.getElementById("heatConfigReset").addEventListener("click", resetConfigTab);
}

function collectConfig() {
  const block = cache.config?.[configTab];
  const tab = CONFIG_TABS[configTab];
  if (!block) throw new Error("配置未载入");
  const data = {};
  for (const [key, row] of Object.entries(block.data || {})) {
    const next = { ...row };
    for (const [field, , type] of tab.fields) {
      const input = configModal().querySelector(`[data-key="${CSS.escape(key)}"][data-field="${field}"]`);
      if (!input) continue;
      if (type === "number") {
        if (input.value.trim() === "") throw new Error(`${key}.${field} 不能为空`);
        next[field] = Number(input.value);
      } else {
        next[field] = input.value;
      }
    }
    data[key] = next;
  }
  return data;
}

async function refreshConfigAfter(message) {
  cache.config = await api("/api/heat-config");
  if (currentRow) renderHeatForm(currentRow);
  renderConfigEditor(message);
}

async function saveConfigTab() {
  const msg = document.getElementById("heatConfigMsg");
  try {
    const data = collectConfig();
    await apiPost("/api/heat-config", { name: configTab, data });
    await refreshConfigAfter(`已保存 ${CONFIG_TABS[configTab].label}`);
  } catch (err) {
    msg.textContent = `保存失败:${err.message}`;
  }
}

async function resetConfigTab() {
  const msg = document.getElementById("heatConfigMsg");
  try {
    await apiDelete(`/api/heat-config?name=${encodeURIComponent(configTab)}`);
    await refreshConfigAfter(`已恢复 ${CONFIG_TABS[configTab].label} 的内置默认`);
  } catch (err) {
    msg.textContent = `恢复失败:${err.message}`;
  }
}

function openConfigEditor() {
  if (!cache.config) {
    api("/api/heat-config").then((cfg) => {
      cache.config = cfg;
      renderConfigEditor();
    }).catch((err) => {
      configModal().innerHTML = `<div class="heat-config-card"><p>读取配置失败:${esc(err.message)}</p></div>`;
      configModal().classList.remove("hidden");
    });
  } else {
    renderConfigEditor();
  }
}

export function clearHeatCache() {
  cache.caseId = null;
  cache.rows = null;
  closeDataDrawers({ restore: true });
}
