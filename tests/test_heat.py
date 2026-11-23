"""换热器 L1 纯计算层:单位、LMTD、设计条件、边界拦截、管程分配、型号编码。

只 import `app.design.heat` 与 `app.aspen.units`(后者不拖 COM),
离线零配置即可跑,不依赖 `data/` 与 Aspen。
"""

from __future__ import annotations

import io
import shutil
import tempfile
import zipfile
from pathlib import Path

from tests._harness import check, check_close, check_eq


def _stream(name, t, p=None, m=None, vap=None):
    return {
        "name": name,
        "temperature_c": t,
        "pressure_bar": p,
        "mass_flow_kgh": m,
        "vap_frac": vap,
    }


def _heatx_block(**kw):
    base = {
        "name": "EX0101",
        "type": "HeatX",
        "duty_kw": -1000.0,
        "area_m2": 26.635,
        "inlet_temp_c": None,
        "outlet_temp_c": None,
        "cold_in_temp_c": None,
        "cold_out_temp_c": None,
        "outlet_vap_frac": None,
        "hx_u_design_wm2k": 850.0,
        "ua_wk": 22637.5,
        "hx_dtlm_c": 52.29,
        "hx_f_factor": 1.0,
        "util_id": None,
        "util_type": None,
        "util_t_in_c": None,
        "util_t_out_c": None,
    }
    base.update(kw)
    return base


def _heater_block(**kw):
    base = {
        "name": "E0101",
        "type": "Heater",
        "duty_kw": 566.0,
        "area_m2": None,
        "inlet_temp_c": 20.0,
        "outlet_temp_c": 100.0,
        "cold_in_temp_c": None,
        "cold_out_temp_c": None,
        "outlet_vap_frac": 0.0,
        "hx_u_design_wm2k": None,
        "ua_wk": None,
        "hx_dtlm_c": None,
        "hx_f_factor": None,
        "util_id": "MS",
        "util_type": "steam",
        "util_t_in_c": None,
        "util_t_out_c": None,
    }
    base.update(kw)
    return base


def test_heat_units() -> None:
    """① 单位换算:实测 V15 的 UA / UAVD 必须能换到 W/K 与 W/(m²·K)。"""
    from app.aspen import units

    check_close(
        "heat: UA 5407.44 cal/sec-K ≈ 22639.9 W/K",
        units.convert("ua", 5407.44, "cal/sec-K"),
        22639.9,
        0.5,
    )
    check_close(
        "heat: UAVD 0.0203019 cal/sec-cm²-K = 850 W/(m²·K)",
        units.convert("u_value", 0.0203019, "cal/sec-sqcm-K"),
        850.0,
        0.1,
    )
    check_eq("heat: 认不出的 U 单位返回 None", units.convert("u_value", 1.0, "furlong"), None)


def test_heat_lmtd() -> None:
    """② LMTD:正常 / 两端相等 / 温度交叉。"""
    from app.design.heat import params

    value, note = params.lmtd(100, 60, 20, 50)
    check_close("heat: LMTD(100/60, 20/50) = 44.81 ℃", value, 44.81, 0.02)
    check_eq("heat: 正常工况 note=ok", note, "ok")

    value, note = params.lmtd(100, 60, 20, 60)
    check_close("heat: 两端相等退化为算术平均 40", value, 40.0, 1e-9)
    check_eq("heat: 两端相等 note=flat", note, "flat")

    value, note = params.lmtd(100, 60, 70, 80)
    check_eq("heat: 温度交叉 LMTD=None", value, None)
    check_eq("heat: 温度交叉 note=cross", note, "cross")


def test_heat_design_conditions() -> None:
    """③ 设计压力/温度/允许压降口径。"""
    from app.design.heat import params

    check_close("heat: P_op=5 bar → P_design=6 bar", params.design_pressure(5), 6.0, 1e-9)
    check_close("heat: P_op=20 bar → 22 bar", params.design_pressure(20), 22.0, 1e-9)
    check_eq("heat: P_op=None → None", params.design_pressure(None), None)

    check_close("heat: T_max=100 → T_design=120", params.design_temperature(100), 120.0, 1e-9)
    check_close(
        "heat: 偏移 10 ℃ 越界(15~30)→ 回默认 20",
        params.design_temperature(100, 10),
        120.0,
        1e-9,
    )

    check_close("heat: 气体允许压降 10%P", params.allowable_dp(20, "gas"), 2.0, 1e-9)
    check_close("heat: 液体允许压降 20%P", params.allowable_dp(20, "liquid"), 4.0, 1e-9)
    check_close("heat: 低压下限 0.2 bar", params.allowable_dp(0.5, "liquid"), 0.2, 1e-9)


def test_heat_heatx_case() -> None:
    """④ HeatX 正常工况:反算 U、面积来源、判定只到 reference。"""
    from app.design.heat import params

    block = _heatx_block()
    connections = [
        {"block": "EX0101", "side": "hot", "direction": "in", "stream": "HOT_IN"},
        {"block": "EX0101", "side": "hot", "direction": "out", "stream": "HOT_OUT"},
        {"block": "EX0101", "side": "cold", "direction": "in", "stream": "COLD_IN"},
        {"block": "EX0101", "side": "cold", "direction": "out", "stream": "COLD_OUT"},
    ]
    streams = [
        _stream("HOT_IN", 100, 5, 10000),
        _stream("HOT_OUT", 60, 5, 10000),
        _stream("COLD_IN", 20, 3, 12000),
        _stream("COLD_OUT", 50, 3, 12000),
    ]

    case = params.build_design(block, connections, streams, margin=0.3)
    check_eq("heat: HeatX 侧数=2", len(case["sides"]), 2)
    check_close("heat: 自算 LMTD=44.81", case["lmtd"]["self_c"], 44.81, 0.02)
    check_eq("heat: LMTD 来源 computed", case["lmtd"]["source"], "computed")
    check_close(
        "heat: U_back = 1e6/(26.635×44.81×1) ≈ 837.9",
        case["u"]["back_calc_wm2k"],
        837.9,
        0.5,
    )
    check_eq("heat: 面积来源 aspen", case["area"]["source"], "aspen")
    check_close(
        "heat: 需求面积 = 1e6/(850×44.81)×1.3 ≈ 34.12", case["area"]["required_m2"], 34.12, 0.05
    )
    check_eq("heat: 判定 reference", case["verdict"], "reference")
    check("heat: 不得出现合格字样", "合格" not in case["verdict_note"], case["verdict_note"])
    check("heat: 假设面积有警告", "AREA_ASSUMED" in case["warnings"], str(case["warnings"]))
    check_eq("heat: 管程默认 cold(规则 7)", case["tube_fluid"], "cold")
    check_eq("heat: 管程来源 rule", case["tube_fluid_source"], "rule")


def test_heat_blockers() -> None:
    """⑤ 边界拦截:工艺侧相变 / 近等温 / 温度交叉都拦,不硬算。"""
    from app.design.heat import params

    block = _heatx_block()
    connections = [
        {"block": "EX0101", "side": "hot", "direction": "in", "stream": "HOT_IN"},
        {"block": "EX0101", "side": "hot", "direction": "out", "stream": "HOT_OUT"},
        {"block": "EX0101", "side": "cold", "direction": "in", "stream": "COLD_IN"},
        {"block": "EX0101", "side": "cold", "direction": "out", "stream": "COLD_OUT"},
    ]
    streams = [
        _stream("HOT_IN", 100, 5, 10000, vap=1.0),
        _stream("HOT_OUT", 100, 5, 10000, vap=0.0),
        _stream("COLD_IN", 20, 3, 12000),
        _stream("COLD_OUT", 90, 3, 12000),
    ]
    case = params.build_design(block, connections, streams)
    check("heat: 相变被拦截", "PHASE_CHANGE" in case["blockers"], str(case["blockers"]))
    check_eq("heat: 相变时判定 blocked", case["verdict"], "blocked")

    streams[2] = _stream("COLD_IN", 90, 3, 12000)
    streams[3] = _stream("COLD_OUT", 100, 3, 12000)
    block2 = _heatx_block(duty_kw=-10.0)
    case2 = params.build_design(block2, connections, streams)
    check("heat: 温度交叉被拦截", "TEMP_CROSS" in case2["blockers"], str(case2["blockers"]))

    # 近等温:工艺侧进出口只差 1 ℃
    streams2 = [
        _stream("HOT_IN", 100, 5, 10000),
        _stream("HOT_OUT", 99, 5, 10000),
        _stream("COLD_IN", 20, 3, 12000),
        _stream("COLD_OUT", 90, 3, 12000),
    ]
    case3 = params.build_design(_heatx_block(), connections, streams2)
    check(
        "heat: 近等温被拦截",
        "NEAR_ISOTHERMAL_PROCESS" in case3["blockers"],
        str(case3["blockers"]),
    )


def test_heat_utility_isothermal() -> None:
    """⑥ 公用工程等温相变:必须放行,不得被当工艺侧相变拦掉。"""
    from app.design.heat import params

    block = _heater_block()
    connections = [
        {"block": "E0101", "side": "", "direction": "in", "stream": "P_IN"},
        {"block": "E0101", "side": "", "direction": "out", "stream": "P_OUT"},
    ]
    streams = [_stream("P_IN", 20, 3, 5000), _stream("P_OUT", 100, 3, 5000)]

    case = params.build_design(block, connections, streams, u_ref_key="condensing-steam")
    uid = case["utility"]
    check_eq("heat: 公用工程温度来源 preset", uid["source"], "preset")
    check_eq("heat: 预设键 ms-175", uid["key"], "ms-175")
    check_close("heat: 公用工程 175 ℃", uid["t_in_c"], 175.0, 1e-9)
    check("heat: 等温相变有标记", bool(uid["isothermal"]), str(uid))
    check("heat: 未被相变拦截", "PHASE_CHANGE" not in case["blockers"], str(case["blockers"]))
    check("heat: 未被等温拦截", "NEAR_ISOTHERMAL_PROCESS" not in case["blockers"])
    check_eq("heat: 判定 reference", case["verdict"], "reference")
    check("heat: 预设来源写进警告", "UTILITY_PRESET" in case["warnings"], str(case["warnings"]))
    check_close("heat: 自算 LMTD(175/175, 20/100) ≈ 110.20", case["lmtd"]["self_c"], 110.20, 0.05)
    check_eq("heat: 蒸汽侧走壳程 → 工艺侧走管程", case["tube_fluid"], "process")


def test_heat_tube_fluid_rules() -> None:
    """⑦ 管/壳程分配七条规则与枚举校验。"""
    from app.design.heat import data

    sug = data.tube_fluid_suggestion("HeatX", {"hot": {"corrosive": True}, "cold": {}})
    check_eq("heat: 规则 1 腐蚀侧走管程", sug["value"], "hot")
    check_eq("heat: 规则 1 编号", sug["rule"], 1)

    sug = data.tube_fluid_suggestion(
        "HeatX", {"hot": {"pressure_bar": 20}, "cold": {"pressure_bar": 5}}
    )
    check_eq("heat: 规则 2 高压侧走管程", sug["value"], "hot")

    sug = data.tube_fluid_suggestion("HeatX", {"hot": {"p_bar": 41.0}, "cold": {"p_bar": 1.0}})
    check_eq("heat: 规则 2 用 p_bar 键也生效", sug["value"], "hot")

    sug = data.tube_fluid_suggestion(
        "HeatX", {"hot": {"fouling": True}, "cold": {}}, utility_phase=None
    )
    check_eq("heat: 规则 3 结垢侧走管程", sug["value"], "hot")

    sug = data.tube_fluid_suggestion(
        "Heater",
        {"process": {}, "utility": {"fluid": "water"}},
        utility_phase="liquid",
    )
    check_eq("heat: 规则 4 冷却水走管程", sug["value"], "utility")

    sug = data.tube_fluid_suggestion(
        "Heater", {"process": {}, "utility": {}}, utility_phase="condensing"
    )
    check_eq("heat: 规则 5 等温相变公用工程走壳程", sug["value"], "process")

    sug = data.tube_fluid_suggestion(
        "HeatX",
        {"hot": {"mass_flow_kgh": 50000}, "cold": {"mass_flow_kgh": 5000}},
    )
    check_eq("heat: 规则 6 大流量走壳程 → 冷侧走管程", sug["value"], "cold")

    check_eq(
        "heat: 枚举越界 HeatX+process 报错", bool(data.check_tube_fluid("HeatX", "process")), True
    )
    check_eq("heat: 合法 Heater+utility", data.check_tube_fluid("Heater", "utility"), None)


def test_heat_model_code() -> None:
    """⑧ GB/T 151 编码:完整 / 缺项占位 / 非法枚举 / MPa 换算。"""
    from app.design.heat import model_code

    check_eq("heat: 5.5 bar → 0.55 MPa", model_code.format_mpa(5.5), "0.55")
    check_eq("heat: 0.6 bar → 0.06 MPa", model_code.format_mpa(0.6), "0.06")
    check_eq("heat: 16 bar → 1.6 MPa", model_code.format_mpa(16), "1.6")

    full = model_code.build_code(
        front="B",
        shell="E",
        rear="M",
        dn_mm=800,
        p_tube_bar=5.5,
        p_shell_bar=6.0,
        area_nominal_m2=100,
        tube_length_m=6,
        tube_od_mm=25,
        passes=2,
    )
    check_eq("heat: 完整编码", full["code"], "BEM800-0.55/0.6-100-6/25-2I")
    check_eq("heat: 完整编码无缺项", full["missing"], [])
    check_eq("heat: 完整编码无错误", full["errors"], [])

    missing = model_code.build_code(
        front="B",
        shell="E",
        rear="M",
        dn_mm=800,
        p_tube_bar=5.5,
        p_shell_bar=6.0,
        area_nominal_m2=100,
        tube_length_m=6,
        tube_od_mm=25,
        passes=None,
    )
    check_eq("heat: 缺管程数不输出完整编码", missing["code"], None)
    check("heat: 缺项列出管程数", "管程数 N" in missing["missing"], str(missing["missing"]))
    check("heat: 占位串含 ?", "?" in missing["code_partial"], missing["code_partial"])

    illegal = model_code.build_code(front="B", shell="E", rear="Z", dn_mm=800)
    check("heat: 非法后端报错", bool(illegal["errors"]), str(illegal["errors"]))
    check_eq("heat: 非法后端不输出编码", illegal["code"], None)

    check(
        "heat: 编码器不脑补缺项",
        "?" in missing["code_partial"] and missing["code"] is None,
        missing["code_partial"],
    )


def test_heat_config_and_store() -> None:
    """⑨ 配置兜底 / 覆盖 / 坏 JSON 回退,以及 hx_design / hx_selection / hx_props 落库。"""
    from app.design.heat import data
    from app.store import db, heat_config

    with tempfile.TemporaryDirectory() as tmp:
        # 冷启动:没有文件 → 内置,零警告
        cfg, source, warnings = heat_config.load_config("fouling", data.FOULING_DEFAULTS, tmp)
        check_eq("heat: 无配置文件走内置", source, "builtin")
        check_eq("heat: 内置零警告", warnings, [])
        check_eq("heat: 内置 7 档污垢", len(cfg), 7)

        # 坏 JSON → 回退内置 + 警告,不抛异常
        Path(tmp, "hx-fouling.json").write_text("{not json", encoding="utf-8")
        cfg, source, warnings = heat_config.load_config("fouling", data.FOULING_DEFAULTS, tmp)
        check_eq("heat: 坏 JSON 回退内置", source, "builtin")
        check("heat: 坏 JSON 有警告", bool(warnings), str(warnings))

        # 合法覆盖 → user
        good = {"cooling-water": {"value": 0.0002, "label": "冷却水(厂区)"}}
        ok, msg = heat_config.save_config("fouling", good, tmp)
        check_eq("heat: 合法配置写入成功", ok, True)
        check_eq("heat: 写入提示", "已保存 hx-fouling.json" in msg, True)
        cfg, source, warnings = heat_config.load_config("fouling", data.FOULING_DEFAULTS, tmp)
        check_eq("heat: 覆盖后来源 user", source, "user")
        check_eq("heat: 覆盖是整体替换(只剩 1 档)", len(cfg), 1)
        check_close("heat: 用户值生效", cfg["cooling-water"]["value"], 0.0002, 1e-12)

        # 非法值(缺 value)→ 拒绝写盘
        ok, msg = heat_config.save_config("fouling", {"x": {"label": "无值"}}, tmp)
        check_eq("heat: 非法配置拒绝写盘", ok, False)
        check("heat: 拒绝原因提到 value", "value" in msg, msg)

        # 经验 U 区间校验:default 必须落在 min~max
        bad = {"water-water": {"min": 800, "max": 1500, "default": 2000}}
        ok, _ = heat_config.save_config("u-reference", bad, tmp)
        check_eq("heat: U 区间越界拒绝写盘", ok, False)

    # 落库:hx_design upsert 两次,version 自增。
    # Windows 上 sqlite 连接未完全释放时 TemporaryDirectory 删不掉,改用 mkdtemp 留痕。
    tmp = tempfile.mkdtemp(prefix="casebook-hx-")
    if True:
        conn = db.open_db(str(Path(tmp, "t.db")))
        check_eq(
            "heat: 首次写 hx_design",
            db.save_hx_design(conn, "k1", "EX0101", {"tube_fluid": "cold"}),
            True,
        )
        row = db.get_hx_design(conn, "k1", "EX0101")
        check_eq("heat: tube_fluid 落库", row["tube_fluid"], "cold")
        check_eq("heat: 首版 version=0", row["version"], 0)

        check_eq(
            "heat: 二次写 hx_design",
            db.save_hx_design(conn, "k1", "EX0101", {"tube_fluid": "hot", "margin": 0.4}),
            True,
        )
        row = db.get_hx_design(conn, "k1", "EX0101")
        check_eq("heat: 覆盖 tube_fluid", row["tube_fluid"], "hot")
        check_close("heat: 覆盖 margin", row["margin"], 0.4, 1e-9)
        check_eq("heat: version 自增到 1", row["version"], 1)

        check_eq(
            "heat: 未知列被忽略",
            db.save_hx_design(conn, "k1", "EX0102", {"drop_table": 1, "note": "ok"}),
            True,
        )
        row = db.get_hx_design(conn, "k1", "EX0102")
        check_eq("heat: 未知列未落库", "drop_table" in row, False)

        check_eq(
            "heat: 写 hx_selection",
            db.save_hx_selection(conn, "k1", "EX0101", {"model_no": "BEM800-0.55/0.6-100-6/25-2I"}),
            True,
        )
        check_eq(
            "heat: 读回型号",
            db.read_hx_selection(conn, "k1")[0]["model_no"],
            "BEM800-0.55/0.6-100-6/25-2I",
        )

        # hx_props 有 cases 外键:先建一个空工况再写
        cid = db.save_case(
            conn,
            {
                "model": {"name": "x", "path": "x", "server": "", "read_at": "2026-11-23"},
                "convergence": {"state": "ok", "note": ""},
                "streams": [],
                "blocks": [],
                "columns": [],
                "connections": [],
                "balance": {},
                "components": [],
                "component_meta": {},
                "unit_gaps": {},
                "stats": {},
            },
        )
        check_eq(
            "heat: 写 hx_props",
            db.save_hx_props(conn, cid, "S1", {"mu_cp": 1.2, "source": "manual"}),
            True,
        )
        check_close("heat: 读回 mu_cp", db.read_hx_props(conn, cid)[0]["mu_cp"], 1.2, 1e-9)
        conn.close()


def test_heat_assemble_and_edr() -> None:
    """⑩ 拼行与 EDR 清单:API/导出共用一份口径,空值不补 0。"""
    from app.design.heat import assemble, data

    block = _heatx_block()
    connections = [
        {"block": "EX0101", "side": "hot", "direction": "in", "stream": "HOT_IN"},
        {"block": "EX0101", "side": "hot", "direction": "out", "stream": "HOT_OUT"},
        {"block": "EX0101", "side": "cold", "direction": "in", "stream": "COLD_IN"},
        {"block": "EX0101", "side": "cold", "direction": "out", "stream": "COLD_OUT"},
    ]
    streams = [
        _stream("HOT_IN", 100, 5, 10000),
        _stream("HOT_OUT", 60, 5, 10000),
        _stream("COLD_IN", 20, 3, 12000),
        _stream("COLD_OUT", 50, 3, 12000),
    ]
    configs = {
        "fouling": data.FOULING_DEFAULTS,
        "u-reference": data.U_REFERENCE_DEFAULTS,
        "utility-presets": data.UTILITY_PRESET_DEFAULTS,
    }
    row = assemble.build_row(block, connections, streams, {}, {}, configs)
    check_eq("heat: 拼行位号", row["block"], "EX0101")
    check_eq("heat: 拼行判定 reference", row["case"]["verdict"], "reference")
    check("heat: 缺几何时输出占位编码", row["model_code"]["code"] is None, str(row["model_code"]))
    check(
        "heat: 占位编码带 DN?",
        "BEM?" in row["model_code"]["code_partial"],
        row["model_code"]["code_partial"],
    )

    checklist = assemble.edr_checklist([row])
    check_eq("heat: EDR 清单一行", len(checklist), 1)
    item = checklist[0]
    for head in (
        "设备位号",
        "热负荷 kW",
        "ΔTm(自算) ℃",
        "面积 m²",
        "面积来源",
        "管程污垢 m²·K/W",
        "判定",
    ):
        check(f"heat: EDR 列 {head}", head in item, str(list(item)))
    check_close("heat: EDR 热负荷", item["热负荷 kW"], -1000.0, 1e-9)
    check_eq("heat: EDR 面积来源 aspen", item["面积来源"], "aspen")
    check_eq("heat: EDR 未选污垢留空", item["管程污垢 m²·K/W"], None)


def test_heat_exports_and_config_reset() -> None:
    """导出三件套与配置恢复:口径同源、列数冻结、空值 `—`、CSV 有溯源字段。"""
    import openpyxl
    from docx import Document

    from app.design.heat import data as hx_data
    from app.export import aipack, hx_schedule, workbook
    from app.store import db, heat_config

    root = Path(__file__).resolve().parents[1]
    scratch = Path(tempfile.mkdtemp(prefix="casebook-hx-export-"))
    sample = scratch / "sample.db"
    shutil.copyfile(root / "samples" / "case-23-error.db", sample)
    conn = db.open_db(sample)
    try:
        cases = [dict(row) for row in db.list_cases(conn)]
        case_id = cases[0]["id"]

        data, fname = hx_schedule.build_hx_docx(conn, case_id)
        check_eq("Word 文件名", fname.endswith(".docx"), True)
        doc = Document(io.BytesIO(data))
        tables = [t for t in doc.tables if len(t.columns) == 15]
        check_eq("Word 有且仅有一张 15 列表", len(tables), 1)
        headers = [c.text.replace("\n", " ") for c in tables[0].rows[0].cells]
        check_eq("首列是位号", headers[0], "位号")
        check_eq("第四列是公称面积", headers[3], "公称面积 m²")
        check_eq("末列是备注", headers[-1], "备注")
        first = [c.text for c in tables[0].rows[1].cells]
        check_eq("数据行 15 列", len(first), 15)
        check(any(v == "—" for v in first), "缺值写 — 而不是 0")

        book = openpyxl.load_workbook(io.BytesIO(workbook.build_workbook(conn, case_id)))
        check("换热器" in book.sheetnames, "整本 Excel 有换热器页")
        ws = book["换热器"]
        head = [c.value for c in next(ws.iter_rows(max_row=1))]
        check_eq("Excel 换热页 15 + 判定 + 缺失项", len(head), 17)
        check_eq("倒数第二列是判定", head[-2], "判定")

        _zdata, _zname = aipack.build_zip(conn, case_id)
        zf = zipfile.ZipFile(io.BytesIO(_zdata))
        names = zf.namelist()
        check("heat_exchangers.csv" in names, "AI 包含 heat_exchangers.csv")
        check("hx_prompt.md" in names, "AI 包含 hx_prompt.md")
        header = zf.read("heat_exchangers.csv").decode("utf-8-sig").splitlines()[0]
        for field in ("area_source", "utility_source", "verdict"):
            check(field in header, f"CSV 含 {field}")
    finally:
        conn.close()

    cfg_dir = Path(tempfile.mkdtemp(prefix="casebook-hx-cfg-"))
    ok, _msg = heat_config.save_config("fouling", hx_data.FOULING_DEFAULTS, base=cfg_dir)
    check_eq("配置写入成功", ok, True)
    _cfg, source, _warn = heat_config.load_config("fouling", hx_data.FOULING_DEFAULTS, base=cfg_dir)
    check_eq("写入后来源是 user", source, "user")
    ok, _msg = heat_config.reset_config("fouling", base=cfg_dir)
    check_eq("恢复内置成功", ok, True)
    _cfg, source, _warn = heat_config.load_config("fouling", hx_data.FOULING_DEFAULTS, base=cfg_dir)
    check_eq("恢复后来源回 builtin", source, "builtin")
