"""AI 分析包:把一次工况导出成"给 AI 读"的一组文件,打成一个 zip。

为什么不复用 Excel
----------------
Excel 是给人看的(合并单元格、格式、冻结窗格),AI 读不到那些;
分析包用 Markdown + CSV + JSON:纯文本、体积小、任何模型都能直接吃。

包内文件
--------
README.md            说明书 + 数据字典 + 这次运行的状态(**先读它**)
metrics.csv          精简指标:概览 / 状态码 / 异常清单,一张表看结论
streams.csv          流股完整数据(已归一单位)+ 原始值与原始单位
blocks.csv           设备完整数据 + 模块状态 + Aspen 原文提示
column_profiles.csv  塔逐板剖面(没有塔就没有这个文件)
composition.csv      已读过组成的流股(按需读,可能没有这个文件)
case.json            机器可读的同一份元数据(给程序解析用)

三条硬约定
----------
1. **空值就是空字符串**:FindNode 找不到节点时读出来是 None,
   必须和"数值真的等于 0"区分开,不能让 AI 把缺失当成 0。
2. **换算可追溯**:数值已归一到固定单位,同时保留原始值与原始单位。
3. **只陈述事实**:本包不写任何"该怎么改模型"的建议。
"""
from __future__ import annotations

import csv
import io
import json
import time
import zipfile

from app.store import db

SEVERITY_TEXT = {"ok": "收敛", "warning": "有告警", "error": "有错误", "empty": "没有结果"}
STATUS_TEXT = {0: "正常", 1: "错误", 2: "告警"}

# 状态码含义来自实测(docs/aspen-field-map.md 第 6 节),不是查文档猜的。
# 2026-11-01 修正:PER_ERROR 在"全干净"和"有报错"的模型里都是 1,没有区分度。
CODE_NOTES = {
    "RSTAT": "运行状态。实测一直是 0,对报错不敏感",
    "CVSTAT": "收敛块状态。0=正常,**非 0 才是真出错**",
    "CSSTAT": "计算状态。实测恒为 2,**不代表失败**",
    "UOSSTAT": "单元操作状态。0=正常;1=有模块状态非零(**含告警**)",
    "PPSTAT": "实测恒为 2,**不代表失败**",
    "SENSSTAT": "灵敏度分析状态。实测多为 2 或 0,不代表失败",
    "ITSTAT": "迭代次数(实测多为 4)",
    "PER_ERROR": "**不能当出错计数**:全干净的模型和真有报错的模型都出现 1,仅记录",
}

UNITS = {
    "temperature": "℃",
    "pressure": "bar",
    "mass_flow": "kg/h",
    "mole_flow": "kmol/h",
    "volume_flow": "m³/h",
    "power": "kW",
    "area": "m²",
    "vap_frac": "无量纲 0–1",
}

PACK_VERSION = 2   # 2:新增 pumps.csv(泵参数);读包的脚本按 pack_version 判文件是否存在


# ---------------------------------------------------------------- 小工具

def _csv_bytes(header: list[str], rows: list[list]) -> bytes:
    """UTF-8 带 BOM:Excel 双击打开不乱码,AI 工具也能正常读。"""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def _md_bytes(text: str) -> bytes:
    return text.encode("utf-8")


def _num(value):
    """CSV 里的数字:None 一律留空,不要把"读不到"写成 0。

    不做四舍五入 —— 撕裂流误差常在 1e-14 量级,`round(1.1e-14, 6)` 会变成 0.0,
    把"数值上已经收敛"写成"误差为零",这是丢信息。
    """
    if value is None:
        return ""
    return value


BALANCE_TEXT = {
    "ok": "通过",
    "bad": "超差",
    "no_data": "无法校验",
    "skip": "不校验",
}


def _json(value, fallback):
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _compact(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _cell(text) -> str:
    """Markdown 表格里的单元格:竖线会破表,换行会破表。"""
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


# ---------------------------------------------------------------- 各文件

def _metrics_rows(case, columns, stage_counts, reasons, codes, tears, blocks,
                  composition) -> list[list]:
    rows: list[list] = []

    def add(category, item, value="", unit="", note=""):
        rows.append([category, item, value, unit, note])

    severity = case["severity"] or ""
    add("概览", "模型文件", case["model_name"] or "", "", case["model_path"] or "")
    add("概览", "工况编号", case["id"], "", "本机工况库里的 id")
    add("概览", "采集时间", case["read_at"] or "", "", "从 Aspen 读数的时刻")
    add("概览", "Aspen 版本", case["aspen_version"] or "", "", "运行状态页的 VERNO")
    add("概览", "运行 ID", case["run_id"] or "", "", "Aspen 本次运行的编号")
    add("概览", "运行时间", case["run_date"] or "", "", "Aspen 记录的上次运行时刻")
    add("概览", "运行用户", case["run_user"] or "", "", "")
    add("概览", "流股数", _num(case["stream_count"]), "条", "")
    add("概览", "设备数", _num(case["block_count"]), "个", "")
    add("概览", "塔数", len(columns), "座", "有逐板剖面的 RadFrac/Absorber")
    add("概览", "读取耗时", _num(case["elapsed_sec"]), "s", "本程序从 Aspen 读一遍的耗时")
    add("概览", "撕裂流条数", len(tears), "条", "闭环回路的撕裂流,误差见下")
    add("概览", "已读组成的流股数", len(composition["streams"]), "条",
        "组成按需读,不是全量;见 composition.csv")
    add("概览", "收敛判定", SEVERITY_TEXT.get(severity, severity), "",
        "ok=收敛 / warning=有告警 / error=有错误")
    add("概览", "能否用于统计对比", "是" if case["converged"] else "否", "",
        "只有无报错的工况才建议做定量对比或优化判断")

    for key, note in CODE_NOTES.items():
        if key in codes:
            add("状态码", key, _num(codes.get(key)), "", note)

    for n, reason in enumerate(reasons, 1):
        add("判定理由", f"第 {n} 条", reason, "", "")

    # 焓平衡自检:ΔH = Σ出 − Σ入(流股能量流),对设备热负荷 / 功率
    counts = {"ok": 0, "bad": 0, "no_data": 0, "skip": 0}
    for b in blocks:
        state = b["balance_state"]
        if state in counts:
            counts[state] += 1
    checked = counts["ok"] + counts["bad"]
    add("焓平衡", "参与校验台数", checked, "台",
        "塔 / 反应器按约定不校验;缺数据 / 缺参照值的记「无法校验」")
    add("焓平衡", "通过", counts["ok"], "台", "|ΔH − 参照| 在容差内(热负荷 1% 或 0.5 kW 取大)")
    add("焓平衡", "超差", counts["bad"], "台", "超出容差,明细见 blocks.csv 的「平衡说明」列")
    add("焓平衡", "无法校验", counts["no_data"], "台", "有流股没有焓值,或缺热负荷 / 轴功率字段")
    add("焓平衡", "不校验", counts["skip"], "台", "RadFrac / 反应器:进出口焓差不等于设备热负荷")
    worst = sorted((b for b in blocks if b["balance_state"] == "bad"),
                   key=lambda b: -abs((b["dh_kw"] or 0) - (b["ref_kw"] or 0)))[:5]
    for b in worst:
        add("焓平衡-超差", b["name"], f"ΔH {b['dh_kw']:.2f} kW", "kW",
            b["balance_note"] or "")

    # 报错/告警模块逐条列原文(Aspen 的 BLKMSG 是唯一可得的准确原因)
    for b in blocks:
        status = b["status"]
        if status in (1, 2):
            add("异常模块", b["name"], STATUS_TEXT.get(status, status), "",
                f"{b['type'] or '-'} · BLKMSG = {b['message'] or '(空)'}")

    for tear in tears:
        add("撕裂流", tear.get("stream") or "", _num(tear.get("error")), "",
            f"{tear.get('variable') or ''} · 容差 {tear.get('tolerance')}")

    gaps = _json(case["unit_gaps_json"], {}) if "unit_gaps_json" in case.keys() else {}
    add("数据质量", "单位不认识而留空的字段数", gaps.get("count") or 0, "个",
        "这些字段是换算表没覆盖,不是模型没结果:"
        + ("、".join(gaps.get("units") or []) or "无"))

    add("数据规模", "塔剖面级数合计", sum(stage_counts), "级",
        "、".join(f"{c['block']}({c['stage_count']})" for c in columns) or "无")
    return rows


def _streams_csv(streams) -> bytes:
    rows = []
    for s in streams:
        raw = _json(s["raw_json"], {})
        rows.append([
            s["name"],
            _num(s["temperature_c"]),
            _num(s["pressure_bar"]),
            _num(s["mass_flow_kgh"]),
            _num(s["mole_flow_kmolh"]),
            _num(s["vap_frac"]),
            _num(s["vol_flow_m3h"]),
            _num(s["enthalpy_kjkg"]),
            _num(s["enthalpy_kjkmol"]),
            _num(s["enthalpy_flow_kw"]),
            _compact(raw),
        ])
    return _csv_bytes(
        ["流股", "温度℃", "压力bar", "质量流量kg/h", "摩尔流量kmol/h",
         "汽化率", "体积流量m³/h", "质量焓kJ/kg", "摩尔焓kJ/kmol", "能量流kW",
         "原始数据(JSON:字段→值+单位)"],
        rows,
    )


def _connections_csv(connections) -> bytes:
    rows = []
    for c in connections:
        rows.append([
            c["block"],
            "进" if c["direction"] == "in" else "出",
            c["stream"],
            c["side"] or "",
            c["port"] or "",
            _num(c["stage"]),
            c["source"] or "",
        ])
    return _csv_bytes(
        ["设备", "方向", "流股", "分侧", "端口", "进料板", "来源"],
        rows,
    )


def _blocks_csv(blocks) -> bytes:
    rows = []
    for b in blocks:
        raw = _json(b["raw_json"], {})
        rows.append([
            b["name"],
            b["type"] or "",
            _num(b["status"]),
            STATUS_TEXT.get(b["status"], ""),
            _num(b["duty_kw"]),
            _num(b["power_kw"]),
            _num(b["area_m2"]),
            _num(b["nstage"]),
            _num(b["top_temp_c"]),
            _num(b["bottom_temp_c"]),
            _num(b["dh_kw"]),
            _num(b["ref_kw"]),
            BALANCE_TEXT.get(b["balance_state"], ""),
            b["balance_note"] or "",
            b["message"] or "",
            _compact(raw),
        ])
    return _csv_bytes(
        ["设备", "类型", "状态码", "状态", "热负荷kW", "功率kW", "换热面积m²",
         "理论级数", "顶温℃", "底温℃", "ΔH kW", "参照kW", "焓平衡", "平衡说明",
         "提示原文(BLKMSG)", "原始数据(JSON)"],
        rows,
    )


def _profiles_csv(stages_by_block) -> bytes:
    rows = []
    for block, stages in stages_by_block.items():
        for st in stages:
            rows.append([
                block,
                st["stage"],
                _num(st["temperature_c"]),
                _num(st["pressure_bar"]),
                _num(st["liq_flow"]),
                _num(st["vap_flow"]),
                _num(st["duty_kw"]),
            ])
    return _csv_bytes(
        ["塔", "级", "温度℃", "压力bar", "液流量(模型单位)", "气流量(模型单位)", "热负荷kW"],
        rows,
    )


def _composition_csv(rows) -> bytes:
    out = []
    for r in rows:
        out.append([r["stream"], r["component"], _num(r["x_liq"]), _num(r["y_vap"])])
    return _csv_bytes(
        ["流股", "组分", "液相摩尔分数x", "气相摩尔分数y"],
        out,
    )


def _pump_blocks(blocks) -> list:
    """只挑 Pump 块。类型名大小写不保证,统一转小写比。"""
    return [b for b in blocks if str(b["type"] or "").strip().lower() == "pump"]


def has_pumps(blocks) -> bool:
    return bool(_pump_blocks(blocks))


PUMP_HEADERS = ["设备", "状态", "流量m³/h", "扬程m", "效率%", "轴功率kW", "建议电机kW",
                "进口压力bar", "出口压力bar", "压升bar", "密度kg/m³", "进口汽化率",
                "p_v bar", "NPSHa m", "提示原文"]


def _pumps_csv(pumps) -> bytes:
    """泵参数(只有 Pump 块)。

    两个值必须标清楚是**算的**,不能让读包的 AI 当成 Aspen 实测:
      * 建议电机kW = 轴功率 ÷ 0.95 × 1.10 后取标准档位;
      * NPSHa m  = (进口压力 − p_v)×10⁵ ÷ (ρ·g),**只在有 p_v 时才有值**。
    空 = 没算,不是"不会汽蚀"。
    """
    from app.design import pump as pump_rules

    rows = []
    for b in pumps:
        npsh = pump_rules.npsh_available_m(b["p_in_bar"], b["pv_bar"], b["rho_kgm3"])
        rows.append([
            b["name"],
            STATUS_TEXT.get(b["status"], ""),
            _num(b["flow_m3h"]),
            _num(b["head_m"]),
            _num(None if b["pump_eff"] is None else round(b["pump_eff"] * 100, 1)),
            _num(b["power_kw"]),
            _num(b["motor_kw"]),
            _num(b["p_in_bar"]),
            _num(b["p_out_bar"]),
            _num(b["dp_bar"]),
            _num(b["rho_kgm3"]),
            _num(b["pump_vap_frac"]),
            _num(b["pv_bar"]),
            _num(npsh),
            b["message"] or "",
        ])
    return _csv_bytes(PUMP_HEADERS, rows)


def _case_json(case, columns, stage_counts, reasons, codes, tears, blocks,
               composition, connections, has_pipe: bool = False) -> bytes:
    files = {
        "README.md": "说明书 + 数据字典(先读这个)",
        "metrics.csv": "精简指标:概览 / 状态码 / 异常清单",
        "streams.csv": "流股完整数据(已归一单位)",
        "blocks.csv": "设备完整数据 + 模块状态",
        "case.json": "本文件:机器可读元数据",
    }
    if columns:
        files["column_profiles.csv"] = "塔逐板剖面"
    if has_pumps(blocks):
        files["pumps.csv"] = "泵参数(流量/扬程/效率/电机/p_v/NPSHa;标注哪几个是算的)"
    if connections:
        files["connections.csv"] = "设备 ↔ 流股连接关系(拓扑)"
    if composition["rows"]:
        files["composition.csv"] = "已读流股的逐组分摩尔分数"
    if has_pipe:
        files["pipe_schedule.csv"] = "管路选型(实时算,不写库;校验码见「校验码」列)"

    payload = {
        "generator": "Casebook",
        "pack_version": PACK_VERSION,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "units": UNITS,
        "files": files,
        "case": {
            "id": case["id"],
            "model_name": case["model_name"],
            "model_path": case["model_path"],
            "read_at": case["read_at"],
            "aspen_version": case["aspen_version"],
            "run_id": case["run_id"],
            "run_date": case["run_date"],
            "run_user": case["run_user"],
            "stream_count": case["stream_count"],
            "block_count": case["block_count"],
            "connection_count": len(connections),
            "balance": {state: sum(1 for b in blocks if b["balance_state"] == state)
                        for state in ("ok", "bad", "no_data", "skip")},
            "elapsed_sec": case["elapsed_sec"],
        },
        "convergence": {
            "severity": case["severity"],
            "severity_text": SEVERITY_TEXT.get(case["severity"] or "", ""),
            "converged": bool(case["converged"]),
            "usable_for_comparison": bool(case["converged"]),
            "reasons": reasons,
            "codes": codes,
            "code_notes": CODE_NOTES,
            "tears": tears,
            "block_errors": [b["name"] for b in blocks if b["status"] == 1],
            "block_warnings": [b["name"] for b in blocks if b["status"] == 2],
            "errtxt": case["errtxt"] or "",
        },
        "columns": [
            {"block": c["block"], "stage_count": c["stage_count"]}
            for c in columns
        ],
        "composition": {
            "streams": composition["streams"],
            "components": composition["components"],
            "file": "composition.csv" if composition["rows"] else None,
            "note": "组成按需读取:只包含在网页上点开过的流股,不是全量",
        },
        "notes": [
            "空值表示该模型里没有这个字段/读不到,不等于 0",
            "数值已换算到固定单位,原始值与原始单位保留在「原始数据」列",
            "本包只陈述事实,不含任何修复或调参建议",
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _readme(case, columns, stage_counts, reasons, codes, tears, blocks,
            composition, connections, metrics_count: int) -> str:
    severity = case["severity"] or ""
    severity_text = SEVERITY_TEXT.get(severity, severity or "未知")
    converged = bool(case["converged"])
    generated = time.strftime("%Y-%m-%d %H:%M:%S")

    problem_blocks = [b for b in blocks if b["status"] in (1, 2)]
    tear_bad = [t for t in tears
                if isinstance(t.get("error"), (int, float)) and t.get("tolerance")
                and t["error"] > t["tolerance"]]

    out: list[str] = []
    w = out.append

    w("# Casebook · AI 分析包")
    w("")
    w("> 本文件是整个数据包的使用说明,给 AI 和人读都行。")
    w("> 数据来自一次 **Aspen Plus 模拟运行**的读数,只做**分析**,不含任何修改模型的建议。")
    w("")
    w("| 项 | 值 |")
    w("| --- | --- |")
    w(f"| 模型 | `{_cell(case['model_name'] or case['model_path'])}` |")
    w(f"| 工况编号 | #{case['id']}(本机工况库) |")
    w(f"| 采集时间 | {_cell(case['read_at'])} |")
    w(f"| 规模 | {case['stream_count']} 条流股 / {case['block_count']} 个设备 / {len(columns)} 座塔 |")
    w(f"| 收敛判定 | **{severity_text}**(converged = {'true' if converged else 'false'}) |")
    w(f"| 分析包生成 | {generated} |")
    w("")

    # --- 1 ---
    w("## 1. 先读这一节:这份数据能不能用")
    w("")
    if severity == "empty":
        w("**这份工况没有任何结果** —— 读取时 Aspen 里的模型还没有运行过(或结果为空)。")
        w("")
        w("> 数据包里的数值列基本全是空的,先别拿它做分析。")
        w("> 请先在 Aspen 里 Run 一次,再重新读取一次工况。")
        w("")
    elif converged:
        w("这次运行**没有报错**,流股与设备结果可以用于定量分析。")
    elif severity == "warning":
        w("这次运行**有告警**(模块本身没有报错)。")
        if problem_blocks:
            w("")
            w("涉及的模块:`" + "`、`".join(b["name"] for b in problem_blocks) + "`")
        w("")
        w("> 数值可用,但引用时请说明告警项;本工具仍把它标为不可用于统计对比")
        w("> (converged = false),因为告警往往意味着该模块的结果需要更谨慎地对待。")
    else:
        w(f"这次运行**{severity_text}**。")
        if problem_blocks:
            w("")
            w("Aspen 报告了下列问题:`" + "`、`".join(b["name"] for b in problem_blocks) + "`")
        w("")
        w("> **给 AI 的结论:不要用这份数据做定量结论(比值、收率、能耗对比、优化判断)。**")
        w("> 有问题的塔/模块读到的数值是迭代中途值,不是收敛解;")
        w("> 其余流股可作定性参考,但引用时要说明工况未收敛。")
    w("")

    # --- 2 ---
    w("## 2. 文件清单")
    w("")
    w("| 文件 | 内容 | 规模 |")
    w("| --- | --- | --- |")
    w(f"| `README.md` | 本文件:说明 + 数据字典 + 运行状态 | — |")
    w(f"| `metrics.csv` | 精简指标(概览 / 状态码 / 异常清单) | {metrics_count} 行 |")
    w(f"| `streams.csv` | 流股完整数据 | {case['stream_count']} 行 |")
    w(f"| `blocks.csv` | 设备完整数据 | {case['block_count']} 行 |")
    if connections:
        w(f"| `connections.csv` | 设备 ↔ 流股连接关系(拓扑) | {len(connections)} 行 |")
    if columns:
        w(f"| `column_profiles.csv` | 塔逐板剖面 | {sum(stage_counts)} 行 |")
    if composition["rows"]:
        w(f"| `composition.csv` | 已读流股的逐组分摩尔分数 | {len(composition['rows'])} 行 |")
    w(f"| `case.json` | 机器可读元数据(同样的信息,便于程序解析) | — |")
    w("")
    w("CSV 一律 **UTF-8 带 BOM、逗号分隔、首行是列名**。")
    w("")

    # --- 3 ---
    w("## 3. 单位约定")
    w("")
    w("所有数值已经换算到固定单位,不要按原始单位理解:")
    w("")
    w("| 量 | 单位 | 列名 |")
    w("| --- | --- | --- |")
    w("| 温度 | ℃ | `温度℃` / `顶温℃` / `底温℃` |")
    w("| 压力 | bar | `压力bar` |")
    w("| 质量流量 | kg/h | `质量流量kg/h` |")
    w("| 摩尔流量 | kmol/h | `摩尔流量kmol/h` |")
    w("| 体积流量 | m³/h | `体积流量m³/h` |")
    w("| 热负荷 / 功率 | kW | `热负荷kW` / `功率kW` |")
    w("| 换热面积 | m² | `换热面积m²` |")
    w("| 汽化率 | 无量纲 0–1 | `汽化率` |")
    w("| 质量焓 | kJ/kg | `质量焓kJ/kg`(`HMX_MASS` 的 `MIXED`) |")
    w("| 摩尔焓 | kJ/kmol | `摩尔焓kJ/kmol`(`HMX` 的 `MIXED`) |")
    w("| 能量流(m·h) | kW | `能量流kW`(`HMX_FLOW` 的 `MIXED`),**可直接相加** |")
    w("")
    w("为什么必须换算:Aspen 的单位跟着模型的单位集走(本机 `METCBAR`),")
    w("同一个「热负荷」在不同模块可能给 `cal/sec`,泵却给 `kW`,直接并排会误判。")
    w("原始值与原始单位同时保留在 `streams.csv` / `blocks.csv` 的最后一列(JSON),可逐条核对。")
    w("单位不认识时该数值**留空**,程序不猜。")
    w("")
    w("> 塔剖面里的 `液流量(模型单位)` / `气流量(模型单位)` 是 Aspen 的原始数值,")
    w("> 没有统一换算(不同单位集下含义不同),使用时以相对变化为主。")
    w("")

    # --- 4 ---
    w("## 4. 字段含义")
    w("")
    w("### 4.1 `streams.csv` —— 流股")
    w("")
    w("| 列 | 含义 |")
    w("| --- | --- |")
    w("| 流股 | Aspen 里的流股 ID(用户自己起的名字) |")
    w("| 温度℃ | 出口温度(`RES_TEMP`) |")
    w("| 压力bar | 出口压力(`RES_PRES`) |")
    w("| 质量流量kg/h | `RES_MASSFLOW` |")
    w("| 摩尔流量kmol/h | `RES_MOLEFLOW` |")
    w("| 汽化率 | `RES_VFRAC`,0=全液相,1=全气相 |")
    w("| 体积流量m³/h | `RES_VOLFLOW` |")
    w("| 质量焓kJ/kg | 焓按质量算(`HMX_MASS` 的子节点 `MIXED`),含 Aspen 的任意参考态 |")
    w("| 摩尔焓kJ/kmol | 焓按摩尔算(`HMX` 的子节点 `MIXED`) |")
    w("| 能量流kW | **m·h**,`HMX_FLOW` 的子节点 `MIXED`;做焓平衡就用它:ΔH = Σ出 − Σ入 |")
    w("| 原始数据 | 原始值与单位,JSON |")
    w("")
    w("### 4.2 `blocks.csv` —— 单元设备")
    w("")
    w("| 列 | 含义 |")
    w("| --- | --- |")
    w("| 设备 | 模块名 |")
    w("| 类型 | `Heater` / `Pump` / `RadFrac` / `HeatX` / `Mixer` … |")
    w("| 状态码 / 状态 | 模块自身状态:`0`=正常、`1`=错误、`2`=告警 |")
    w("| 热负荷kW | 按 `QCALC` → `QNET` → `HX_DUTY` 取第一个有值的字段 |")
    w("| 功率kW | 泵/压缩机轴功率(`BRAKE_POWER` / `ELEC_POWER`) |")
    w("| 换热面积m² | `HX_AREAC` |")
    w("| 理论级数 | 塔的 `NSTAGE`(输入) |")
    w("| 顶温℃ / 底温℃ | 塔的第一级 / 最后一级温度 |")
    w("| ΔH kW | 焓平衡:ΔH = Σ出 − Σ入(流股 `能量流kW`)。正=放热/失焓,负=吸热/得焓 |")
    w("| 参照kW | 对照值:`热负荷kW`(Heater/Flash2)、`功率kW`(泵/压缩机)、`0`(绝热设备)、"
      "换热器按冷侧 `HX_DUTY` |")
    w("| 焓平衡 | `通过` / `超差` / `无法校验`(缺流股焓值或参照值)/ `不校验`(塔、反应器) |")
    w("| 平衡说明 | 差值、容差、比值等人话;超差时先看这一列 |")
    w("| 提示原文 | Aspen 的 `BLKMSG` 原文(英文),空=没报错 |")
    w("")
    w("### 4.2.1 焓平衡判定的口径")
    w("")
    w("| 设备 | 参照 | 判据 |")
    w("| --- | --- | --- |")
    w("| Heater / Flash2 | `热负荷kW` | \\|ΔH − 热负荷\\| ≤ max(1% × 热负荷, 0.5 kW) |")
    w("| HeatX | `HX_DUTY` | 只校验**冷侧**:ΔH_cold = HX_DUTY(整体 ΔH 恒为 0) |")
    w("| Pump / Compr | `功率kW` | 按 ΔH / 功率 ∈ [0.85, 1.05](差的是效率,实测压缩机 0.95) |")
    w("| Mixer / FSplit / Valve | 0 | \\|ΔH\\| ≤ max(0.5% × 最大焓流, 0.5 kW) |")
    w("| RadFrac / 反应器 | — | 不校验:进出口焓差不等于设备热负荷 |")
    w("")
    w("> 流股焓用的是 `HMX_FLOW`(已经是 m·h,单位 MW)。Aspen 焓有任意参考态,")
    w("> 绝对值不能跨设备相加;`ΔH = Σ出 − Σ入` 才是可比的量。")
    w("")
    if has_pumps(blocks):
        w("### 4.2.2 `pumps.csv` —— 泵参数")
        w("")
        w("只有 `Pump` 块有这张表。**哪几列是算的,表里写清楚了**:")
        w("")
        w("| 列 | 含义 |")
        w("| --- | --- |")
        w("| 设备 / 状态 | 模块名与自身状态(`0`=正常、`1`=错误、`2`=告警) |")
        w("| 流量m³/h | 泵块 `VFLOW`,Aspen 原值 |")
        w("| 扬程m | 泵块 `HEAD_CAL`,Aspen 原值;**反向工况为负** |")
        w("| 效率% | 泵块 `CEFF` × 100 |")
        w("| 轴功率kW | `BRAKE_POWER`,Aspen 原值(反向工况为负) |")
        w("| 建议电机kW | **算的**:轴功率 ÷ 0.95 × 1.10 后向上取标准档位;功率 ≤ 0 时留空 |")
        w("| 进口压力bar / 出口压力bar | 取自泵的**进口 / 出口流股**,不是泵块的 `IN_PRES` |")
        w("| 压升bar | 泵块 `IN_PRES`。⚠️ 名字叫 IN_PRES,实测是**压升**:`压升 ÷ (ρ·g)` 与 `扬程m` 吻合 |")
        w("| 密度kg/m³ | `RHO_LIQ`(摩尔密度)× `MW_LIQ` |")
        w("| 进口汽化率 | 进口流股的汽化率;> 0 视为两相,不给 NPSHa |")
        w("| p_v bar | 饱和蒸气压,**用户逐台手填**,程序不估算 |")
        w("| NPSHa m | **算的**:`(进口压力 − p_v)×10⁵ ÷ (ρ·g)`。**空 = 没算,不等于「不会汽蚀」** |")
        w("")
    w("### 4.3 `column_profiles.csv` —— 塔逐板剖面")
    w("")
    w("每行是一块理论板:`级` 从 1 开始,**级 1 是塔顶**。")
    w("覆盖所有 `RadFrac` / `Absorber` 模块;其它设备没有逐板数据。")
    w("")
    w("### 4.4 `metrics.csv` —— 精简指标")
    w("")
    w("单张表,`类别` 列区分五类:概览 / 状态码 / 判定理由 / 异常模块 / 撕裂流。")
    w("想快速看结论就只读这张表。")
    w("")
    if connections:
        w("### 4.5 `connections.csv` —— 设备与流股的连接关系")
        w("")
        w("| 列 | 含义 |")
        w("| --- | --- |")
        w("| 设备 | 模块名 |")
        w("| 方向 | `进` = 该流股流入这个模块,`出` = 从它流出 |")
        w("| 流股 | 流股 ID |")
        w("| 分侧 | `cold` / `hot`(换热器冷/热侧),其余为空 |")
        w("| 端口 | 塔的进料端口号(来自 `IN_STRMID` 的元素名),其余为空 |")
        w("| 进料板 | 塔的进料位置(来自 `Input` 的 `FEED_STAGE`) |")
        w("| 来源 | `stream`=流股侧 SOURCE/DESTINATION;`strmid`=设备 IN/OUT_STRMID;"
          "`hx`=换热器分侧字段;`feed_stage`=进料板映射 |")
        w("")
        w("> 主流股的连接来自流股侧的 `SOURCE` / `DESTINATION`(值是模块名):")
        w("> 实测设备侧 `IN_STRMID` 只有塔上有,其余设备没有这个字段。")
        w("> 边界流股(没有上游 = 进料,没有下游 = 产品)不会出现在 `connections.csv` 里。")
        w("")
    if composition["rows"]:
        w("### 4.6 `composition.csv` —— 流股组成")
        w("")
        w("| 列 | 含义 |")
        w("| --- | --- |")
        w("| 流股 | 流股 ID |")
        w("| 组分 | 组分 ID(Aspen 里定义的名称) |")
        w("| 液相摩尔分数x | 该组分在液相中的摩尔分数,同一流股内合计 ≈ 1 |")
        w("| 气相摩尔分数y | 该组分在气相中的摩尔分数,同一流股内合计 ≈ 1 |")
        w("")
        w(f"本包覆盖 **{len(composition['streams'])} 条流股**:"
          + "、".join(f"`{s}`" for s in composition["streams"]) + "。")
        w("")
        w("> 组成是**按需读**的:只有在网页上点开过的流股才在这里,")
        w("> 不是全部流股。纯液相流股的 y 列可能整列为 0 或空,不代表缺数据。")
        w("")

    # --- 5 ---
    w("## 5. 这次运行的状态(读自 Aspen 运行状态页)")
    w("")
    w("| 状态码 | 值 | 实测含义 |")
    w("| --- | --- | --- |")
    for key, note in CODE_NOTES.items():
        if key in codes:
            w(f"| `{key}` | {_cell(codes.get(key))} | {note} |")
    w("")
    w("> `CSSTAT` / `PPSTAT` / `SENSSTAT` 实测恒为 2,**不代表失败**,不要据此判错。")
    w("> 判定收敛要看 `CVSTAT` / `UOSSTAT` / `PER_ERROR` **再加上**逐模块的 `BLKSTAT`。")
    w("")

    w("### 5.1 有问题的模块")
    w("")
    if problem_blocks:
        w("| 模块 | 类型 | 状态 | Aspen 原文(BLKMSG) |")
        w("| --- | --- | --- | --- |")
        for b in problem_blocks:
            w(f"| {_cell(b['name'])} | {_cell(b['type'] or '-')} | "
              f"{STATUS_TEXT.get(b['status'], '')} | {_cell(b['message'] or '')} |")
    else:
        w("无(所有模块 `BLKSTAT` 都是 0)。")
    w("")
    if case["errtxt"]:
        w("Aspen 运行状态页里的原始提示文本:")
        w("")
        w("```")
        w(str(case["errtxt"]).strip())
        w("```")
        w("")

    w("### 5.2 撕裂流误差")
    w("")
    if tears:
        w("| 撕裂流 | 变量 | 误差 | 容差 | 是否超标 |")
        w("| --- | --- | --- | --- | --- |")
        for t in tears:
            err, tol = t.get("error"), t.get("tolerance")
            over = isinstance(err, (int, float)) and tol and err > tol
            w(f"| {_cell(t.get('stream'))} | {_cell(t.get('variable'))} | "
              f"{_cell(err)} | {_cell(tol)} | {'**是**' if over else '否'} |")
        w("")
        w("> 注意:**撕裂流误差小 ≠ 流程可用**。实测见过撕裂流全在 1e-14~1e-6,")
        w("> 但塔仍然报「干涸」「组分平衡方程不收敛」。判定必须两条腿走路。")
    else:
        w("本次运行没有撕裂流(模型里没有闭环回路或没有撕裂流定义)。")
    w("")

    # --- 6 ---
    w("## 6. 数据约定与已知限制")
    w("")
    w("- **空值 = 读不到**,不是 0。Aspen 树里找不到节点时,程序读出来是空,")
    w("  表示「这个模型没有这个字段」或「该字段不可读」,不要当 0 参与计算。")
    unit_gaps = _json(case["unit_gaps_json"], {}) if "unit_gaps_json" in case.keys() else {}
    if unit_gaps.get("count"):
        w(f"- **本次有 {unit_gaps['count']} 个字段因为单位不认识被留空**"
          f"(原始单位:{'、'.join(f'`{u}`' for u in unit_gaps.get('units') or []) or '空'})。")
        w("  这类空值不是模型的问题,是工具的换算表没覆盖 —— 用的时候请换回原始数据列自己算。")
    w("- **组成(各组分摩尔分数)是按需读的**。18 组分 × 2 相 × 每条流股的调用量是")
    w("  基础字段的 6 倍(121 条流股从 9 s 变成 41 s),所以默认不读。")
    if composition["rows"]:
        w(f"- 本工况已经读过 {len(composition['streams'])} 条流股的组成,见 `composition.csv`;")
        w("  其余流股没有组成数据(缺失 ≠ 为零)。")
    else:
        w("- 本工况**没有读过任何流股的组成**。需要时在网页上点流股名(单条约 0.3 秒),")
        w("  或在命令行用 `python -m app.cli --read --composition` 全量重读一次(约 41 s)。")
    w("- **流程拓扑不在包内**:只有流股和设备列表,没有「谁连到谁」的连接关系。")
    w("- **能量流(热流/功流)未覆盖**:两个测试模型全是物流,字段形态还没实测,不臆造。")
    w("- 设备状态码来自 `Output\\BLKSTAT`,错误原文来自 `Output\\BLKMSG`;")
    w("  Aspen 的 `.sum` 文件里那句完整结论(如「The following blocks were completed with errors」)")
    w("  在数据树里**没有**对应节点,所以本包只有逐模块原文。")
    w("")

    # --- 7 ---
    w("## 7. 给 AI 的说明")
    w("")
    w("1. 先看第 1 节,确认数据能不能支持定量分析;不确定时把限定条件写进结论里。")
    w("2. 不要臆造本包没有的字段(例如组分、连接关系、公用工程消耗)。缺失就是缺失。")
    w("3. 引用数值时请带上列名与流股/设备名,便于人工回查 CSV。")
    w("4. 本包**不含**修复、调参、改模型一类的建议 —— 那是建模者自己的判断,不是本工具的职责。")
    w("")
    w("---")
    w("")
    w(f"由 Casebook 生成 · 数据包格式版本 {PACK_VERSION} · {generated}")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- 总入口

def _pipe_result(streams, blocks, connections):
    """管路选型结果;标准表没录全时返回 (None, None),不硬造数据。"""
    from app.design import pipe as pipe_calc

    try:
        res = pipe_calc.build([dict(s) for s in streams], [dict(b) for b in blocks],
                              [dict(c) for c in connections])
    except pipe_calc.TablesNotReady:
        return None, None
    except Exception:            # 选型出问题不该拖垮整个分析包
        return None, None
    return res, pipe_calc.validate(res["rows"])


def _pipe_csv(rows, checks) -> bytes:
    """管路选型表:一行一条流股,校验码写在一列里。"""
    hit: dict[str, list[str]] = {}
    for f in checks["findings"]:
        hit.setdefault(f["stream"], []).append(f["code"])
    header = ["流股", "相态", "介质代号", "体积流量_m3h", "公称直径", "外径_mm", "壁厚_mm",
              "管材", "等级号", "设计压力_MPa", "设计温度_C", "许用应力_MPa",
              "计算壁厚_mm", "所需壁厚_mm", "最小壁厚_mm", "壁厚按下限",
              "实际流速_ms", "校验码", "备注"]
    body = []
    for r in rows:
        body.append([
            r["stream"], r["phase"], r["medium_code"], _num(r["vol_flow_m3h"]),
            r["dn"], _num(r["od_mm"]), _num(r["wall_mm"]), r["material"], r["grade_code"],
            _num(r["design_pressure_mpa"]), _num(r["design_temp_c"]), _num(r["stress_mpa"]),
            _num(r["t_calc_mm"]), _num(r["t_required_mm"]), _num(r.get("min_wall_mm")),
            "是" if r.get("wall_floored") else "",
            _num(r["velocity_ms"]),
            " ".join(hit.get(r["stream"], [])), "; ".join(r["warnings"]),
        ])
    return _csv_bytes(header, body)


def build_zip(conn, case_id: int) -> tuple[bytes, str]:
    """生成整个分析包。返回 (zip 字节, 建议文件名)。

    Raises:
        LookupError: 没有这个工况 id。
    """
    case = db.get_case(conn, case_id)
    if case is None:
        raise LookupError(case_id)

    streams = db.read_streams(conn, case_id)
    blocks = db.read_blocks(conn, case_id)
    columns = db.list_columns(conn, case_id)
    stage_counts = [c["stage_count"] for c in columns]
    stages_by_block = {
        c["block"]: db.read_column_stages(conn, case_id, c["block"]) for c in columns
    }

    connections = db.read_connections(conn, case_id)
    comp_rows = db.read_all_composition(conn, case_id)
    composition = {
        "rows": comp_rows,
        "streams": db.list_composed_streams(conn, case_id),
        "components": sorted({r["component"] for r in comp_rows}),
    }

    reasons = _json(case["reasons_json"], [])
    codes = _json(case["status_json"], {})
    tears = _json(case["tear_json"], [])

    metrics_rows = _metrics_rows(case, columns, stage_counts, reasons, codes,
                                 tears, blocks, composition)
    readme = _readme(case, columns, stage_counts, reasons, codes, tears,
                     blocks, composition, connections, len(metrics_rows))
    files: list[tuple[str, bytes]] = [
        ("README.md", _md_bytes(readme)),
        ("metrics.csv", _csv_bytes(["类别", "项目", "值", "单位", "说明"], metrics_rows)),
        ("streams.csv", _streams_csv(streams)),
        ("blocks.csv", _blocks_csv(blocks)),
    ]
    if connections:
        files.append(("connections.csv", _connections_csv(connections)))
    pumps = _pump_blocks(blocks)
    if pumps:
        files.append(("pumps.csv", _pumps_csv(pumps)))
    if columns:
        files.append(("column_profiles.csv", _profiles_csv(stages_by_block)))
    if comp_rows:
        files.append(("composition.csv", _composition_csv(comp_rows)))
    pipe_res, pipe_checks = _pipe_result(streams, blocks, connections)
    if pipe_res:
        files.append(("pipe_schedule.csv", _pipe_csv(pipe_res["rows"], pipe_checks)))
    files.append(("case.json", _case_json(case, columns, stage_counts, reasons,
                                          codes, tears, blocks, composition,
                                          connections, has_pipe=bool(pipe_res))))

    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            zf.writestr(name, data)

    stamp = time.strftime("%Y%m%d_%H%M")
    # 文件名用 ASCII:HTTP 头的 Content-Disposition 对非 ASCII 支持不一致,避免踩坑
    filename = f"Casebook_case{case_id}_ai-pack_{stamp}.zip"
    return bio.getvalue(), filename
