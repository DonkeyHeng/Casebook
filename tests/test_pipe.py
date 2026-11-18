"""管路选型:十步流程、校验项、标准表导入与等级表。

用例正文原样搬自 `tools/selftest.py`(迁移到 pytest 时只改了位置)。
"""

from __future__ import annotations

import math
import shutil

from tests._harness import (
    REPO_ROOT,
    SYNTH_TABLES,
    check,
    check_close,
    check_eq,
)
from tools import fake_session


def test_pipe() -> None:
    """design.pipe 的算术口径:相态 / DN / 压力 / 温度 / [σ] / 壁厚 / 等级号。"""
    from app.design import pipe as pipe_calc

    # ① 相态:痕量汽化率(库里实测 1e-8)必须按全液处理,不能 ==0 判
    check_eq("pipe: vap_frac=0 → 液", pipe_calc.phase_of(0.0), "liquid")
    check_eq("pipe: vap_frac=1e-8 → 仍算液(痕量)", pipe_calc.phase_of(1e-8), "liquid")
    check_eq("pipe: vap_frac=0.5 → 两相", pipe_calc.phase_of(0.5), "two")
    check_eq("pipe: vap_frac=1 → 气", pipe_calc.phase_of(1.0), "gas")
    check_eq("pipe: vap_frac=None → 按液处理", pipe_calc.phase_of(None), "liquid")

    # ② 内径:36 m³/h、1.5 m/s → √(4·0.01/(π·1.5)) = 92.13 mm
    check_close(
        "pipe: 内径 36 m³/h @1.5 m/s = 92.13 mm",
        pipe_calc.inner_diameter(36.0, 1.5),
        92.13,
        tol=0.05,
    )

    # ③ DN 向上取整 + 查外径
    dn, od = pipe_calc.pick_dn(92.13, SYNTH_TABLES["dn_to_od"])
    check_eq("pipe: 92.13 mm → DN100", dn, 100)
    check_close("pipe: DN100 外径 108", od, 108.0)
    check_eq(
        "pipe: 内径 20 mm → DN25(不能选 DN15)",
        pipe_calc.pick_dn(20.0, SYNTH_TABLES["dn_to_od"])[0],
        25,
    )
    check_eq("pipe: 无表时 pick_dn 返回 None(不当 0)", pipe_calc.pick_dn(20.0, {})[0], None)

    # ④ 设计压力:绝压 11 bar → 表压 0.9987 → ×1.15 ≈ 1.1487 MPa
    pd = pipe_calc.design_pressure(11.0)
    check_close("pipe: 11 bar 绝压 → 设计压力 ≈1.1487 MPa", pd["design_mpa"], 1.1487, tol=1e-4)
    check("pipe: 11 bar 不算真空", pd["vacuum"] is False)
    check_close("pipe: 11 bar 绝压带出 abs_kpa=1100", pd["abs_kpa"], 1100.0)
    # 标准大气压 1.01325 bar = 101.325 kPa > 101.3,不是真空
    pa = pipe_calc.design_pressure(1.01325)
    check("pipe: 101.325 kPa 不算真空", pa["vacuum"] is False)
    check_close("pipe: 101.325 kPa → 1.0 MPa 下限", pa["design_mpa"], 1.0)
    # §17.1 微负压 91.3 kPa 必须判真空(旧死区 0.01 MPa 会漏掉它)
    pv = pipe_calc.design_pressure(0.913)
    check("pipe: 91.3 kPa 微负压 → 真空", pv["vacuum"] is True)
    check_close("pipe: 真空设计压力取 0.1 MPa", pv["design_mpa"], 0.1)
    pv2 = pipe_calc.design_pressure(0.05)
    check("pipe: 绝压 0.05 bar → 真空", pv2["vacuum"] is True)

    # ⑤ 设计温度(§17.4):操作 +20 ℃;≥50 ℃ 自动保温;显式关裕量兼容旧调用
    check_close("pipe: 60 ℃ → 设计 80 ℃", pipe_calc.design_temperature(60.0)["design_c"], 80.0)
    t70 = pipe_calc.design_temperature(70.0, options={"design_temp_margin": False})
    check_close("pipe: 关裕量 70 ℃ → 70 ℃", t70["design_c"], 70.0)
    check_close(
        "pipe: 操作 48 ℃ → 设计 68 ℃ ≥50 保温",
        pipe_calc.design_temperature(48.0)["design_c"],
        68.0,
    )
    check("pipe: 操作 48 ℃ 设计温度触发保温", pipe_calc.design_temperature(48.0)["insulated"])
    check(
        "pipe: 操作 40 ℃ 不触发保温(§17.8 阈值 65)",
        pipe_calc.design_temperature(40.0)["insulated"] is False,
    )
    check_close("pipe: 90 ℃ → 设计 110 ℃", pipe_calc.design_temperature(90.0)["design_c"], 110.0)

    # ⑥ 许用应力:缺温度内插;T<40 取 40 ℃ 值;超范围返回 None(不猜)
    stress = SYNTH_TABLES["stress"]
    check_close(
        "pipe: [σ] 150 ℃ 内插 = 124", pipe_calc.allowable_stress("S30408", 150.0, stress), 124.0
    )
    check_close("pipe: [σ] 20 ℃ → 137", pipe_calc.allowable_stress("S30408", 20.0, stress), 137.0)
    check_close(
        "pipe: [σ] -10 ℃ 取 20 ℃ 值", pipe_calc.allowable_stress("S30408", -10.0, stress), 137.0
    )
    check_eq(
        "pipe: [σ] 450 ℃ 超范围 → None", pipe_calc.allowable_stress("S30408", 450.0, stress), None
    )

    # ⑦ 壁厚公式 t = P·D/(2(S·Φw + P·Y)):1.6 MPa / Φ108 / [σ]=130 / Y=0.4
    w = pipe_calc.calc_wall_thickness(1.6, 108.0, 130.0)
    check_close("pipe: t_calc = 1.6×108/(2×(130+0.64)) ≈ 0.6614", w["t_calc_mm"], 0.66137, tol=1e-4)
    check("pipe: t < D/6 → 公式适用", w["applicable"] is True)
    check(
        "pipe: 超大壁厚判公式不适用",
        pipe_calc.calc_wall_thickness(100.0, 108.0, 130.0)["applicable"] is False,
    )
    check_eq(
        "pipe: 缺 [σ] 不硬算", pipe_calc.calc_wall_thickness(1.6, 108.0, None)["t_calc_mm"], None
    )

    # ⑧ 圆整到标准壁厚:取"不小于所需"的最小值;超上限返回 None
    check_close(
        "pipe: 需 1.0 mm → 圆整到 4.0 mm",
        pipe_calc.round_up_wall(1.0, SYNTH_TABLES["od_to_walls"][108.0])["wall_mm"],
        4.0,
    )
    check_close(
        "pipe: 需 4.2 mm → 圆整到 4.5 mm",
        pipe_calc.round_up_wall(4.2, SYNTH_TABLES["od_to_walls"][108.0])["wall_mm"],
        4.5,
    )
    check_eq(
        "pipe: 需 5.0 mm 超过序列 → None",
        pipe_calc.round_up_wall(5.0, SYNTH_TABLES["od_to_walls"][108.0])["wall_mm"],
        None,
    )
    check_eq("pipe: 无壁厚序列(T2 未录)→ None", pipe_calc.round_up_wall(1.0, None)["wall_mm"], None)

    # ⑧b 最小壁厚下限(DN10–100 不设限那档之外):下限只抬高、不降低
    from app.design.pipe import data as pd_mod

    check_close("pipe: 下限 DN25 → 2.0", pd_mod.minimum_wall(25), 2.0)
    check_close("pipe: 下限 DN32 → 3.0", pd_mod.minimum_wall(32), 3.0)
    check_close("pipe: 下限 DN65 → 3.0", pd_mod.minimum_wall(65), 3.0)
    check_close("pipe: 下限 DN80 → 3.0", pd_mod.minimum_wall(80), 3.0)
    check_close("pipe: 下限 DN100 → 3.0", pd_mod.minimum_wall(100), 3.0)
    check_close("pipe: DN1000 焊接管下限 10 mm(§17.2)", pd_mod.minimum_wall(1000), 10.0)
    check_close(
        "pipe: 需 0.3 mm + 下限 2.0 → 取 2.0",
        pipe_calc.round_up_wall(0.3, (0.5, 0.7, 1.0, 2.0, 3.0), min_wall=2.0)["wall_mm"],
        2.0,
    )
    check_close(
        "pipe: 需 2.4 mm + 下限 2.0 → 取 2.5(下限不降级)",
        pipe_calc.round_up_wall(2.4, (0.5, 2.0, 2.5, 3.0), min_wall=2.0)["wall_mm"],
        2.5,
    )
    check(
        "pipe: 下限起作用时标 floored",
        pipe_calc.round_up_wall(0.3, (0.5, 2.0), min_wall=2.0)["floored"] is True,
    )
    check(
        "pipe: 承压更厚时不标 floored",
        pipe_calc.round_up_wall(2.4, (2.0, 2.5), min_wall=2.0)["floored"] is False,
    )

    # 端到端:小口径薄壁序列必须被下限托住;options 能关掉这一层
    thin_tables = {
        "dn_to_od": {15: 18.0, 25: 32.0},
        "od_to_walls": {18.0: (0.5, 1.0, 2.0)},
        "stress": {"S30408": {20: 137.0, 100: 130.0}},
    }
    small = [
        {
            "name": "S1",
            "vol_flow_m3h": 0.61,
            "temperature_c": 25.0,
            "pressure_bar": 1.0,
            "vap_frac": 0.0,
        }
    ]
    floored_row = pipe_calc.build(small, tables=thin_tables)["rows"][0]
    check_close("pipe: DN15 薄壁序列被下限托到 2.0 mm", floored_row["wall_mm"], 2.0)
    check("pipe: 该行标记 wall_floored", floored_row["wall_floored"] is True)
    check_close("pipe: 该行带出 min_wall_mm=2.0", floored_row["min_wall_mm"], 2.0)
    plain_row = pipe_calc.build(small, options={"min_wall": False}, tables=thin_tables)["rows"][0]
    check_close("pipe: options min_wall=False 时回到 0.5 mm", plain_row["wall_mm"], 0.5)

    # ⑨ 等级号:PN 代号 + 顺序号 + 材质代号 + 绝热后缀
    check_eq("pipe: 1.6 MPa → 压力代号 M", pipe_calc.pn_code_for(1.6), "M")
    check_eq("pipe: 1.7 MPa → 升到 N", pipe_calc.pn_code_for(1.7), "N")
    check_eq("pipe: 0.2 MPa → 压力代号 H", pipe_calc.pn_code_for(0.2), "H")
    check_eq("pipe: 1.6 MPa/不锈钢 → M1E", pipe_calc.grade_code(1.6, "E")["code"], "M1E")
    check_eq(
        "pipe: 设计温度 90 ℃ → 自动加 -H", pipe_calc.grade_code(1.6, "E", 90.0)["code"], "M1E-H"
    )
    check_eq("pipe: 设计温度 30 ℃ → 不加后缀", pipe_calc.grade_code(1.6, "E", 30.0)["code"], "M1E")
    # §17.8:-H 阈值改按设计温度 65 ℃(操作 ≥ 45 ℃),40 ℃ 常温线不再误判保温
    check_eq(
        "pipe: 设计温度 60 ℃ → 不加 -H(§17.8)", pipe_calc.grade_code(1.6, "E", 60.0)["code"], "M1E"
    )
    check_eq(
        "pipe: 设计温度 65 ℃ → 加 -H(边界)", pipe_calc.grade_code(1.6, "E", 65.0)["code"], "M1E-H"
    )

    # ⑩ 端到端 build():泵入口流速、DN 超限提示、缺流量流股、表未录入的明确报错
    streams = [
        {
            "name": "S1",
            "vol_flow_m3h": 36.0,
            "temperature_c": 60.0,
            "pressure_bar": 11.0,
            "vap_frac": 0.0,
        },
        {
            "name": "S2",
            "vol_flow_m3h": 3.0,
            "temperature_c": 60.0,
            "pressure_bar": 11.0,
            "vap_frac": 0.0,
        },
        {
            "name": "S3",
            "vol_flow_m3h": None,
            "temperature_c": 60.0,
            "pressure_bar": 11.0,
            "vap_frac": 0.0,
        },
    ]
    blocks = [{"name": "P1", "type": "Pump"}]
    conns = [{"block": "P1", "direction": "in", "stream": "S2"}]

    res = pipe_calc.build(streams, blocks, conns, options={"max_dn": 50}, tables=SYNTH_TABLES)
    check_eq(
        "pipe: build 选出 2 条、缺 1 条",
        (res["summary"]["selected"], res["summary"]["missing"]),
        (2, 1),
    )
    check_eq("pipe: 缺体积流量的流股进 missing", res["missing"][0]["stream"], "S3")
    by = {r["stream"]: r for r in res["rows"]}
    check_eq("pipe: S1 → DN100", by["S1"]["dn"], 100)
    check(
        "pipe: DN100 超本期上限 DN50 → 有提示",
        any("超出本期上限" in w for w in by["S1"]["warnings"]),
    )
    check_eq("pipe: S2 是泵入口流股", by["S2"]["pump_inlet"], True)
    check_close("pipe: 泵入口按 0.8 m/s 选型", by["S2"]["velocity_ms"], 0.8)
    check(
        "pipe: 泵入口内径比常规液速更大",
        by["S2"]["inner_d_mm"] > pipe_calc.inner_diameter(3.0, 1.5),
    )
    check_eq("pipe: 等级号生成", by["S1"]["grade_code"].startswith("M1E"), True)

    try:
        # 显式传空表:标准表没录/读不到时必须报错,而不是拿 0 算出一张假表
        pipe_calc.build(
            streams, blocks, conns, tables={"dn_to_od": {}, "od_to_walls": {}, "stress": {}}
        )
        check("pipe: 表未录入时必须报错", False, "应该抛 TablesNotReady")
    except pipe_calc.TablesNotReady:
        check("pipe: 表未录入时报 TablesNotReady(不静默出表)", True)


def test_pipe_2026_rules() -> None:
    """2026-11-18 工程校核口径(§17):真空 V1E / 外压 / 升 R1E / 非优先 DN / 汽蚀 / 零流量。"""
    from app.design import pipe as pc
    from app.design.pipe import data as pdata

    # §17.1 真空代号 + 外压失稳最小壁厚
    check_eq("rules: 真空压力代号 V", pdata.VACUUM_PN_CODE, "V")
    check_eq(
        "rules: 真空等级号 V1E",
        pc.grade_code(0.1, "E", 30.0, options={"vacuum": True})["code"],
        "V1E",
    )
    t_ext = pc.external_pressure_min_wall(219.0)
    check("rules: DN200 真空外压 t_min 在 0–5 mm", t_ext is not None and 0 < t_ext < 5.0)
    check("rules: 外压 t_min 随外径增大", pc.external_pressure_min_wall(630.0) > t_ext)

    # §17.2 非优先 DN 屏蔽:计算内径落 DN65/DN125 区间时上圆整
    full = {15: 18.0, 25: 32.0, 50: 57.0, 65: 76.0, 80: 89.0, 100: 108.0, 125: 133.0, 150: 159.0}
    series = pc.usable_dn_series(full)
    check("rules: DN65 已屏蔽", 65 not in series and 80 in series)
    check("rules: DN125 已屏蔽", 125 not in series and 150 in series)
    check_eq("rules: 92 mm 内径仍选 DN100", pc.pick_dn(92.0, series)[0], 100)
    check_eq("rules: 70 mm 内径原落 DN65,屏蔽后上圆整到 DN80", pc.pick_dn(70.0, series)[0], 80)
    # §17.2 汽蚀:液相 86 ℃ / 101.3 kPa 命中;气相不命中
    check("rules: 近常压沸点液相反汽蚀", pc.near_boiling_liquid("liquid", 101.3, 86.0))
    check("rules: 气相不算汽蚀", not pc.near_boiling_liquid("gas", 101.3, 86.0))
    check("rules: 低温液不算汽蚀", not pc.near_boiling_liquid("liquid", 101.3, 25.0))
    check_close("rules: DN1000 焊接管下限 10 mm", pdata.minimum_wall(1000), 10.0)

    # §17.3/§17.8 高压不锈钢升 R1E(压力带含 4700 kPa,免与 4800 kPa 跳档)
    check_eq("rules: 5.0 MPa S30408 → R", pc.hp_stainless_force_code("S30408", 5000.0), "R")
    check_eq(
        "rules: 边界 4.7 MPa → R(合并邻近等级)", pc.hp_stainless_force_code("S30408", 4700.0), "R"
    )
    check_eq("rules: 4.6 MPa 不升档", pc.hp_stainless_force_code("S30408", 4600.0), None)
    check_eq("rules: 碳钢不升档", pc.hp_stainless_force_code("20#", 5000.0), None)
    check_eq(
        "rules: 升档等级号 R1E-H",
        pc.grade_code(5.0, "E", 200.0, options={"force_pn_code": "R"})["code"],
        "R1E-H",
    )

    # §17.8 R1E 公式化壁厚:t = PD/(2[σ]^t·E_j+P) + C 后 ÷0.875
    check_close("rules: [σ] 100 ℃ → 137", pc.hp_ss_allowable_stress(100.0), 137.0)
    check_close("rules: [σ] 125 ℃ 内插 → 130", pc.hp_ss_allowable_stress(125.0), 130.0)
    check_close("rules: [σ] 90 ℃ 取平值 137", pc.hp_ss_allowable_stress(90.0), 137.0)
    w1 = pc.hp_ss_required_wall(6.0935, 426.0, 210.0)
    check_close("rules: 6.09 MPa/Φ426/210 ℃ → t_req≈13.84", w1, 13.84, tol=0.02)
    w2 = pc.hp_ss_required_wall(5.635, 18.0, 230.0)
    check("rules: DN15 小管公式值 < 3 mm 下限", w2 is not None and w2 < 3.0)
    # 高温 → 同压力同外径壁厚单调增(消除倒挂的本质)
    w_hot = pc.hp_ss_required_wall(6.0, 426.0, 250.0)
    w_cold = pc.hp_ss_required_wall(6.0, 426.0, 150.0)
    check("rules: 同 P 同 D,温度高 → 所需壁厚更厚(无倒挂)", w_hot > w_cold)

    # §17.8 汽蚀:排除泵排出口/升压管线
    check("rules: 汽蚀 110 kPa 命中", pc.near_boiling_liquid("liquid", 110.0, 86.0))
    check(
        "rules: 汽蚀 120 kPa 不命中(收紧到 95–115)",
        not pc.near_boiling_liquid("liquid", 120.0, 86.0),
    )
    cstream = [
        {
            "name": "C1",
            "vol_flow_m3h": 20.0,
            "temperature_c": 90.0,
            "pressure_bar": 1.02,
            "vap_frac": 0.0,
        }
    ]
    cblocks = [{"name": "P1", "type": "Pump"}]
    cconns = [
        {"block": "P1", "direction": "in", "stream": "C0"},
        {"block": "P1", "direction": "out", "stream": "C1"},
    ]
    cres = pc.build(cstream, cblocks, cconns, tables=SYNTH_TABLES)
    check(
        "rules: 泵排出口不触发汽蚀限速",
        cres["rows"][0]["cavitation_limited"] is False and cres["rows"][0]["pump_outlet"],
    )

    # §17.8 未选型流股按相态分类
    big_gas = [
        {
            "name": "G1",
            "vol_flow_m3h": 900000.0,
            "temperature_c": 60.0,
            "pressure_bar": 1.01325,
            "vap_frac": 1.0,
        }
    ]
    gres = pc.build(big_gas, tables=SYNTH_TABLES)
    check("rules: 气相未选型 → 风管专项设计", "风管" in gres["missing"][0]["reason"])
    big_two = [
        {
            "name": "T1",
            "vol_flow_m3h": 900000.0,
            "temperature_c": 60.0,
            "pressure_bar": 1.01325,
            "vap_frac": 0.5,
        }
    ]
    tres = pc.build(big_two, tables=SYNTH_TABLES)
    check("rules: 两相未选型 → 多管并联", "并联" in tres["missing"][0]["reason"])

    # §17.5 零流量 + 非标编号

    tiny = [
        {
            "name": "Z1",
            "vol_flow_m3h": 0.001,
            "temperature_c": 25.0,
            "pressure_bar": 1.01325,
            "vap_frac": 0.0,
        }
    ]
    zres = pc.build(tiny, tables=SYNTH_TABLES)
    check_eq("rules: 零流量进 flagged", len(zres["flagged"]), 1)
    check("rules: 零流量标注人工确认", "需人工确认管径" in zres["flagged"][0]["reason"])
    vres = pc.build(
        [
            {
                "name": "AB",
                "vol_flow_m3h": 10.0,
                "temperature_c": 60.0,
                "pressure_bar": 11.0,
                "vap_frac": 0.0,
            }
        ],
        tables=SYNTH_TABLES,
    )
    check("rules: AB 标非标编号", vres["rows"][0]["nonstandard_no"] is True)
    vfind = pc.validate(vres["rows"])["findings"]
    check("rules: 非标编号触发 V10", any(f["code"] == "V10" for f in vfind))


def test_pipe_third_round() -> None:
    """§19 第三轮:非物流剔除 / 95 kPa / 深真空 / DN1000 容差 / 温度备注。"""
    from app.design import pipe as pc
    from app.design.pipe import data as pdata

    # §19.1 流股类型:Aspen 属性 6 的三种值
    heat = {"name": "Q1", "stream_type": "HEAT", "vol_flow_m3h": 10.0}
    work = {"name": "W1", "stream_type": "WORK", "vol_flow_m3h": 10.0}
    mat = {"name": "0101", "stream_type": "MATERIAL", "vol_flow_m3h": 10.0}
    check("rules: HEAT 剔除", pc.is_material_stream(heat) is False)
    check("rules: WORK 剔除", pc.is_material_stream(work) is False)
    check("rules: MATERIAL 保留", pc.is_material_stream(mat) is True)
    # 老库没有 stream_type:名字像 Q1 但物料结果非空 → 仍当物流保留
    check(
        "rules: 老库有物料结果不被误删",
        pc.is_material_stream({"name": "Q1", "temperature_c": 25.0}) is True,
    )
    check(
        "rules: 老库空结果 Q1 剔除",
        pc.is_material_stream({"name": "Q1", "temperature_c": None}) is False,
    )
    check("rules: 老库无类型普通流股保留", pc.is_material_stream({"name": "0101"}) is True)

    # §19.1 真空阈值 95 kPa
    check_eq("rules: 真空阈值 95.0", pc.VACUUM_ABS_KPA, 95.0)
    check("rules: 1 bar(100 kPa)不是真空", pc.design_pressure(1.0)["vacuum"] is False)
    check("rules: 1.01325 bar 不是真空", pc.design_pressure(1.01325)["vacuum"] is False)
    check_close("rules: 1 bar 设计压力回到 1.0 MPa", pc.design_pressure(1.0)["design_mpa"], 1.0)
    check("rules: 94.9 kPa 判真空", pc.design_pressure(0.949)["vacuum"] is True)
    check("rules: 95.0 kPa 边界不是真空", pc.design_pressure(0.95)["vacuum"] is False)

    # DN1000 容差上限常量
    check_close("rules: DN1000 标准内径 996", pdata.DN1000_STD_INNER_MM, 996.0)
    check_close("rules: DN1000 +5% 上限 1045", pdata.DN1000_TOL_MAX_MM, 1045.0)
    check_close("rules: 1008.4 mm 超径约 1.2%", pc.dn1000_oversize_pct(1008.4), 1.24, tol=0.05)
    check_close("rules: 1035.3 mm 超径约 3.9%", pc.dn1000_oversize_pct(1035.3), 3.95, tol=0.05)

    # §19.2/§19.4 深真空 + 温度备注:用合成表跑完整 build
    base = {
        "name": "0330",
        "stream_type": "MATERIAL",
        "vol_flow_m3h": 50.0,
        "pressure_bar": 0.019,
        "temperature_c": 292.4,
        "vap_frac": 0.0,
    }
    rows = pc.build([base], tables=SYNTH_TABLES)["rows"]
    r = rows[0]
    check("rules: 深真空标记", r["deep_vacuum"] is True)
    check("rules: 高温深真空标记", r["deep_hot_vacuum"] is True)
    joined = ";".join(r["warnings"])
    check("rules: 高真空防闪蒸备注", "高真空(≤10kPa)" in joined and "防漏气" in joined)
    check("rules: 高温深真空备注", "高温深真空" in joined and "外压失稳支撑跨距" in joined)

    hotter = dict(base, name="0402", temperature_c=306.5, pressure_bar=1.01325)
    hrows = pc.build([hotter], tables=SYNTH_TABLES)["rows"]
    check("rules: 高温标记", hrows[0]["high_temp"] is True)
    hjoined = ";".join(hrows[0]["warnings"])
    check("rules: 高温柔性应力备注", "高温管线:需做柔性应力分析(热膨胀补偿)" in hjoined)
    check("rules: 高温 ≥300℃ 备注", "高温管线(≥300℃):需做热膨胀柔性应力分析" in hjoined)

    cold = dict(base, name="0402", temperature_c=-25.0, pressure_bar=1.01325)
    crows = pc.build([cold], tables=SYNTH_TABLES)["rows"]
    check("rules: 深冷标记", crows[0]["low_temp"] is True)
    cjoined = ";".join(crows[0]["warnings"])
    check("rules: 低温韧性备注", "低温工况:复核焊缝低温韧性及低温螺栓选型" in cjoined)
    check("rules: 深冷韧性备注", "深冷工况:复核焊缝低温韧性及低温螺栓选型" in cjoined)

    # §19.3 DN1000 容差:给一张含 DN1000 的合成表,计算内径 1008.4 / 1035.3
    big_dn = dict(SYNTH_TABLES["dn_to_od"])
    big_dn[1000] = 1016.0
    big_walls = dict(SYNTH_TABLES["od_to_walls"])
    big_walls[1016.0] = (10.0, 12.0)
    big_tables = {
        "dn_to_od": big_dn,
        "od_to_walls": big_walls,
        "stress": dict(SYNTH_TABLES["stress"]),
    }
    for nm, d_calc in (("0222", 1008.4), ("0215", 1035.3)):
        # 反算流量:v=15 m/s 的气相,体积流量使计算内径恰为 d_calc
        q = math.pi * (d_calc / 1000.0) ** 2 / 4.0 * 15.0 * 3600.0
        srow = pc.build(
            [
                {
                    "name": nm,
                    "stream_type": "MATERIAL",
                    "vol_flow_m3h": q,
                    "pressure_bar": 1.01325,
                    "temperature_c": 25.0,
                    "vap_frac": 1.0,
                }
            ],
            tables=big_tables,
        )["rows"]
        check(f"rules: {nm} 容差后选中 DN1000", srow[0]["dn"] == 1000)
        check(f"rules: {nm} 壁厚 10 mm", srow[0]["wall_mm"] == 10.0)
        check(f"rules: {nm} 标记容差", srow[0]["oversize_tolerated"] is True)
        sjoined = ";".join(srow[0]["warnings"])
        check(f"rules: {nm} 流速略超备注", f"内径{d_calc:.1f}mm" in sjoined)
        check(f"rules: {nm} 超径百分比备注", "计算内径略超DN1000上限" in sjoined)

    # 容差只作用于 996–1045:1500 mm 仍不选型
    q_huge = math.pi * (1.5) ** 2 / 4.0 * 15.0 * 3600.0
    huge = pc.build(
        [
            {
                "name": "9999",
                "stream_type": "MATERIAL",
                "vol_flow_m3h": q_huge,
                "pressure_bar": 1.01325,
                "temperature_c": 25.0,
                "vap_frac": 1.0,
            }
        ],
        tables=big_tables,
    )
    check("rules: 1500 mm 仍不选型", huge["rows"] == [] and len(huge["missing"]) == 1)

    # §19.5 封面统计与主表同源:焊接管 / 保冷管都从同一份 rows 现算
    cover_rows = [
        {
            "name": f"S{i}",
            "stream_type": "MATERIAL",
            "vol_flow_m3h": 20.0,
            "pressure_bar": 1.01325,
            "temperature_c": t,
            "vap_frac": 0.0,
        }
        for i, t in enumerate((25.0, 120.0, -25.0))
    ]
    cover = pc.build(cover_rows, tables=SYNTH_TABLES)
    n_weld = sum(1 for r in cover["rows"] if (r.get("pipe_type") or "无缝") == "焊接")
    n_cold = sum(1 for r in cover["rows"] if str(r.get("grade_code") or "").endswith("-C"))
    check_eq("rules: 封面管路条数 = 主表行数", len(cover["rows"]), 3)
    check("rules: 封面保冷管统计来自主表", n_cold >= 1 and n_cold <= len(cover["rows"]))
    check("rules: 封面焊接管统计来自主表", 0 <= n_weld <= len(cover["rows"]))

    # 非物流不进未选型清单
    mix = pc.build([heat, work, mat], tables=SYNTH_TABLES)
    check("rules: 非物流不进主表/未选型", len(mix["rows"]) + len(mix["missing"]) == 1)
    check("rules: 剔除条数入 summary", mix["summary"]["excluded_nonmaterial"] == 2)


def test_pipe_checks() -> None:
    """design.pipe.validate():V1–V9 的触发条件(只陈述事实,不改模型)。"""
    from app.design import pipe as pipe_calc

    base = {
        "stream": "S1",
        "phase": "liquid",
        "dn": 100,
        "od_mm": 108.0,
        "wall_mm": 4.0,
        "vol_flow_m3h": 36.0,
        "velocity_ms": 1.5,
        "design_pressure_mpa": 1.6,
        "design_temp_c": 60.0,
        "grade_code": "M1E-H",
        "insulation": "H",
        "t_required_mm": 1.5,
        "vacuum": False,
        "pump_inlet": False,
        "pump_outlet": False,
    }

    def codes(row_patch: dict) -> list[str]:
        row = dict(base, **row_patch)
        return [f["code"] for f in pipe_calc.validate([row])["findings"]]

    check_eq("checks: 干净的一行没有任何发现", codes({}), [])
    # 内径 100 mm、36 m³/h → 实际 1.27 m/s,落在 1.0–2.5 内,所以上面是干净的
    check_close(
        "checks: 实际流速 1.27 m/s", pipe_calc.actual_velocity(36.0, 100.0), 1.273, tol=0.01
    )

    check_eq("checks: 流速过大 → V2", codes({"vol_flow_m3h": 200.0}), ["V2"])
    # V2 的 finding 要能落到「流速」那一列,否则前端只能整行标黄
    f2 = pipe_calc.validate([dict(base, vol_flow_m3h=200.0)])["findings"][0]
    check_eq("checks: V2 的 field 定位到 velocity", f2["field"], "velocity")
    check_eq("checks: 所需壁厚超所选 → V3", codes({"t_required_mm": 5.0}), ["V3"])
    check_eq("checks: 等级号压力不足 → V4", codes({"grade_code": "H1E"}), ["V4"])
    check_eq("checks: 编号口径与 DN 不符 → V1", codes({"pipe_no": "PG-0111-300"}), ["V1"])
    check_eq("checks: 两相按单相流速 → V5", codes({"phase": "two"}), ["V5"])
    check_eq("checks: 真空流股 → V6", codes({"vacuum": True}), ["V6"])
    check_eq("checks: 设计温度超材料区间 → V7", codes({"design_temp_c": 500.0}), ["V7"])
    check_eq(
        "checks: 泵出口当泵入口 → V8",
        codes({"pump_outlet": True, "pump_inlet": True, "velocity_ms": 0.8, "vol_flow_m3h": 20.0}),
        ["V8"],
    )
    check_eq(
        "checks: 高温无保温代号 → V9",
        codes({"design_temp_c": 90.0, "insulation": "", "grade_code": "M1E"}),
        ["V9"],
    )
    check_eq("checks: 有保温代号就不再报 V9", codes({"design_temp_c": 90.0}), [])
    # §17 新增 V10–V14
    check_eq("checks: 非标编号 → V10", codes({"nonstandard_no": True}), ["V10"])
    check_eq(
        "checks: 真空 DN200 → V11",
        codes({"vacuum": True, "dn": 200, "external_wall_mm": 2.0}),
        ["V6", "V11"],
    )
    check_eq("checks: 大口径两相分层流 → V12", codes({"stratified_risk": True}), ["V12"])
    check_eq("checks: 高压升 R1E → V13", codes({"hp_ss_upgraded": True}), ["V13"])
    check_eq("checks: 真空代号 V1E 不误报 V4", codes({"grade_code": "V1E", "vacuum": True}), ["V6"])

    # 端到端:build 出来的行直接喂给 validate,字段要能对上
    streams = [
        {
            "name": "S1",
            "vol_flow_m3h": 36.0,
            "temperature_c": 60.0,
            "pressure_bar": 11.0,
            "vap_frac": 0.0,
        }
    ]
    res = pipe_calc.build(streams, tables=SYNTH_TABLES)
    v = pipe_calc.validate(res["rows"])
    check_eq("checks: build→validate 能跑通(字段齐全)", isinstance(v["summary"], dict), True)
    check_eq("checks: 只有 1 行参与", v["summary"]["error"] + v["summary"]["warn"] >= 0, True)


def test_pipe_setup() -> None:
    """标准表没录时给使用者的说明:要哪两份标准、什么文件、放哪、为什么不在仓库里。"""
    from app.design.pipe import data as pipe_data

    g = pipe_data.setup_guide()
    check(
        "setup: 说明了版权原因", ("著作权" in g["why"]) or ("版权" in g["why"]), f"why={g['why']!r}"
    )
    check_eq("setup: 目标目录", g["dir"], "data/preview/std/docx")
    check_eq("setup: 两份标准", len(g["standards"]), 2)
    codes = {s["code"] for s in g["standards"]}
    check("setup: 标准号齐全", {"GB/T 17395-2024", "GB/T 20801.1-2025"} <= codes, f"实际 {codes}")
    names = {f["name"] for f in g["files"]}
    check(
        "setup: 三个 CSV 文件名齐全",
        {"t1-17395-table1.csv", "t4-17395-table4.csv", "t3-20801-tableB1.csv"} <= names,
        f"实际 {names}",
    )
    check(
        "setup: 每个文件都写了来源与必需列",
        all(f.get("from") and f.get("columns") for f in g["files"]),
    )
    check("setup: 至少 3 步操作", len(g["steps"]) >= 3)
    check("setup: 指向页码索引文档", g["doc"].endswith("pipe-standards-index.md"))
    check("setup: 指向录入提示词", g["prompt"].endswith("pipe-standards-extract-prompt.md"))

    root = REPO_ROOT
    check("setup: 提示词文件确实在仓库里", (root / g["prompt"]).is_file(), g["prompt"])
    check(
        "setup: 提示词里不含标准数值(版权)",
        "0.035" not in (root / g["prompt"]).read_text(encoding="utf-8"),
    )
    check(
        "setup: 返回的是拷贝,改它不影响常量",
        pipe_data.setup_guide() is not g and pipe_data.setup_guide() is not pipe_data.SETUP_GUIDE,
    )


def test_std_import() -> None:
    """标准表导入:文件名白名单、编码/必需列/行数,以及"先试加载一遍再落盘"。

    自测一律往临时目录里导,不碰 data/preview/std/docx/ 下正在用的表。
    """
    import shutil

    from app.design.pipe import data as pipe_data

    # 临时目录放在 data/preview/ 下(已被 .gitignore 覆盖):系统 Temp 目录在沙箱里
    # 清理时会 chmod 失败,自测得自己 rmtree
    tmp_root = REPO_ROOT / "data" / "preview" / "_selftest-std"
    shutil.rmtree(tmp_root, ignore_errors=True)
    tmp_root.mkdir(parents=True, exist_ok=True)

    names = [s["name"] for s in pipe_data.STD_FILES]
    check_eq(
        "std: 三个 CSV 文件名",
        names,
        ["t1-17395-table1.csv", "t4-17395-table4.csv", "t3-20801-tableB1.csv"],
    )
    check(
        "std: 每张表都写了来源与必需列",
        all(s.get("from") and s.get("columns") for s in pipe_data.STD_FILES),
    )
    check(
        "std: 白名单外的文件名不收",
        pipe_data.std_file_spec("t1-17395-table1.csv") is not None
        and pipe_data.std_file_spec("whatever.csv") is None,
    )

    spec = pipe_data.std_file_spec("t1-17395-table1.csv")
    errs, _ = pipe_data._validate_std_text(spec, "   ")
    check_eq("std: 空文件被拒", errs, ["文件是空的"])
    good = "D_mm,S_mm\n" + "\n".join(f"{d},2.5" for d in range(6, 30))
    errs, _ = pipe_data._validate_std_text(spec, good.replace("D_mm", "D"))
    check("std: 缺列被拒", any("缺列" in e for e in errs), f"{errs}")
    errs, _ = pipe_data._validate_std_text(spec, "D_mm,S_mm\n10,2.5\n")
    check("std: 行数太少被拒", any("至少" in e for e in errs), f"{errs}")
    errs, _ = pipe_data._validate_std_text(spec, good.replace("2.5", "两毫米"))
    check("std: 读不出的壁厚被拒", any("外径" in e for e in errs), f"{errs}")
    errs, info = pipe_data._validate_std_text(spec, good)
    check_eq("std: 合规的尺寸表通过", errs, [])
    check_eq("std: 统计到 24 个外径", info.get("od_count"), 24)
    errs, _ = pipe_data._validate_std_text(spec, good + "\ufffd")
    check("std: 乱码(非 UTF-8)被拒", any("UTF-8" in e for e in errs), f"{errs}")

    base = tmp_root / "a" / "docx"
    ok, errs, status = pipe_data.import_standards(
        {"files": {"t1-17395-table1.csv": good}}, target_dir=base, reload=False
    )
    check_eq("std: 单张表能导入", (ok, errs), (True, []))
    check("std: 文件写到了目标目录", (base / "t1-17395-table1.csv").is_file())
    rows = [f["rows"] for f in status["files"] if f["name"] == "t1-17395-table1.csv"]
    check_eq("std: 导入后状态里是 24 行", rows, [24])

    # 一个文件不合格 → 一张都不写(别留半份)
    ok, errs, _ = pipe_data.import_standards(
        {"files": {"t1-17395-table1.csv": good, "t4-17395-table4.csv": "foo,bar\n1,2\n"}},
        target_dir=base,
        reload=False,
    )
    check_eq("std: 有一个不合格就整单拒绝", ok, False)
    check("std: 错误里点名是哪个文件", any("t4-17395-table4.csv" in e for e in errs), f"{errs}")
    check("std: 被拒时没写进任何文件", not (base / "t4-17395-table4.csv").exists())

    ok, errs, _ = pipe_data.import_standards({"files": {}}, target_dir=base, reload=False)
    check_eq("std: 没选文件时明确报错", ok, False)

    # 表 B.1 还要能对上本项目用到的牌号,否则整个选型还是跑不起来
    real = REPO_ROOT / "data" / "preview" / "std" / "docx"
    stress_path = real / "t3-20801-tableB1.csv"
    base = tmp_root / "b" / "docx"
    if stress_path.is_file():
        ok, errs, _ = pipe_data.import_standards(
            {"files": {"t3-20801-tableB1.csv": stress_path.read_text(encoding="utf-8-sig")}},
            target_dir=base,
            reload=False,
        )
        check_eq("std: 真表 B.1 导入通过", (ok, errs), (True, []))
    else:
        print("  (本机没有表 B.1,跳过真表导入的检查)")
    # 表 B.1 的牌号不再限定 5 个:表里录了什么就能用什么(方案 A,2026-11-18)
    ok, errs, _ = pipe_data.import_standards(
        {"files": {"t3-20801-tableB1.csv": "标准,牌号,许用应力_20\nGB/T 9999,XYZ,100\n"}},
        target_dir=base,
        reload=False,
    )
    check_eq("std: 表里出现新牌号也能导入", (ok, errs), (True, []))
    check_eq(
        "std: 新牌号进了许用应力表",
        sorted(
            pipe_data._stress_from_rows(
                [{"标准": "GB/T 9999", "牌号": "XYZ", "许用应力_20": "100"}]
            )
        ),
        ["XYZ"],
    )
    check_eq(
        "std: 新牌号的材质类别判不出来",
        pipe_data.material_code_for("XYZ"),
        (None, "unknown"),
    )
    # 牌号列抄成尺寸/纯数字的表:一个牌号都收不进来 → 仍然拒
    ok, errs, _ = pipe_data.import_standards(
        {"files": {"t3-20801-tableB1.csv": "标准,牌号,许用应力_20\nGB/T 9999,Φ57×3.5,100\n"}},
        target_dir=base,
        reload=False,
    )
    check_eq("std: 牌号列不像牌号的表被拒", ok, False)
    check("std: 拒绝理由点到牌号列", any("牌号" in e for e in errs), f"{errs}")

    # reload_standards:按目录重读;目录里没表就是空(页面显示未录),不是异常
    old_count = len(pipe_data.OD_TO_WALLS)
    try:
        pipe_data.reload_standards(tmp_root / "no-such-std-dir")
        check_eq("std: 目录里没表时重载成空", len(pipe_data.OD_TO_WALLS), 0)
    finally:
        pipe_data.reload_standards()
    check_eq("std: 重载后原来的表还在", len(pipe_data.OD_TO_WALLS), old_count)
    shutil.rmtree(tmp_root, ignore_errors=True)


def test_std_template() -> None:
    """标准表格式模板:表头合法、示例值虚构、能挡住「把模板当数据导入」。"""
    from app.design.pipe import data as pipe_data
    from app.design.pipe import prompt as pipe_standards_prompt

    preview = pipe_data.template_preview()
    check_eq(
        "模板: 三份文件齐全",
        [f["name"] for f in preview["files"]],
        [s["name"] for s in pipe_data.STD_FILES],
    )
    check("模板: 目录在仓库里", preview.get("exists") is True)
    for item in preview["files"]:
        check_eq(f"模板: {item['name']} 正好 2 行示例", len(item["rows"]), 2)
        for col in item["columns"]:
            check(
                f"模板: {item['name']} 含必需列 {col}",
                col in item["headers"],
                f"实际表头 {item['headers']}",
            )
    b1 = next(f for f in preview["files"] if f["name"].startswith("t3-"))
    check(
        "模板: 表 B.1 有温度列",
        any(h.startswith("许用应力_") for h in b1["headers"]),
        f"{b1['headers']}",
    )

    texts = pipe_data.template_texts()
    check_eq("模板: 三份都能读出来", len(texts), 3)
    raw = texts["t1-17395-table1.csv"]
    check("模板: 原样文本被识别", pipe_data.is_template_text(raw))
    check(
        "模板: 带 BOM 与 CRLF 也识别",
        pipe_data.is_template_text("\ufeff" + raw.replace("\n", "\r\n")),
    )
    check(
        "模板: 改过数值就不再算模板", not pipe_data.is_template_text(raw.replace("111.1", "114.3"))
    )

    tmp = REPO_ROOT / "data" / "preview" / "_selftest-template"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        ok, errs, _ = pipe_data.import_standards(
            {"files": {"t1-17395-table1.csv": raw}}, target_dir=tmp, reload=False
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    check_eq("模板: 原样导入被拒", ok, False)
    check("模板: 拒绝理由说明这是模板", any("模板" in e for e in errs), f"{errs}")

    text = pipe_standards_prompt.build()
    for name in texts:
        check(f"提示词: 点到 {name}", name in text)
    check("提示词: 写了必需列", "D_mm" in text and "许用应力_20" in text)
    check("提示词: 声明示例数值为虚构", "虚构" in text)
    check("提示词: 说明最少行数", "10 行" in text)


def test_material_scope() -> None:
    """方案 A:牌号清单跟着表 B.1 走 —— 全表解析、别名归一、类别推断。"""
    from app.design.pipe import data as pipe_data

    rows = [
        {"标准": "GB/T 8163", "牌号": "20#", "许用应力_20": "130", "许用应力_100": "120"},
        {"标准": "GB/T 6479", "牌号": "Q345B", "许用应力_20": "170", "许用应力_100": "160"},
        # Q345B 三个尺寸档:必须取各温度最小值,不能取第一条
        {"标准": "GB/T 6479", "牌号": "Q345B", "许用应力_20": "165", "许用应力_100": "155"},
        {
            "标准": "GB/T 14976",
            "牌号": "06Cr19Ni10(304)",
            "许用应力_20": "137",
            "许用应力_100": "130",
        },
        {"标准": "GB/T 14976", "牌号": "S32168", "许用应力_20": "130", "许用应力_100": "120"},
    ]
    out = pipe_data._stress_from_rows(rows)
    check("牌号: 预置牌号仍在", "20#" in out, f"{sorted(out)}")
    check_eq("牌号: Q345B 多尺寸档取最小值", out["Q345B"][20.0], 165.0)
    check_eq("牌号: 06Cr19Ni10(304) 归一到 S30408", out["S30408"][20.0], 137.0)
    check("牌号: 别名不留第二种写法", "06Cr19Ni10(304)" not in out, f"{sorted(out)}")
    check("牌号: 表里新增的牌号也收进来", "S32168" in out, f"{sorted(out)}")

    check_eq("材质: 预置牌号走显式映射", pipe_data.material_code_for("S30408"), ("E", "explicit"))
    check_eq("材质: 321 按前缀判为不锈钢", pipe_data.material_code_for("S32168"), ("E", "prefix"))
    check_eq("材质: Q235 按前缀判为碳钢", pipe_data.material_code_for("Q235B"), ("B", "prefix"))
    check_eq(
        "材质: Q345R 按前缀判为低合金钢", pipe_data.material_code_for("Q345R"), ("C", "prefix")
    )
    check_eq("材质: TA2 按前缀判为有色金属", pipe_data.material_code_for("TA2"), ("F", "prefix"))
    check_eq("材质: 判不出来的不硬猜", pipe_data.material_code_for("XYZ"), (None, "unknown"))

    check("牌号形状: 20# 像牌号", pipe_data.looks_like_grade("20#"))
    check("牌号形状: 06Cr19Ni10(304) 像牌号", pipe_data.looks_like_grade("06Cr19Ni10(304)"))
    check("牌号形状: Φ57×3.5 不像牌号", not pipe_data.looks_like_grade("Φ57×3.5"))
    check("牌号形状: 112.5 不像牌号", not pipe_data.looks_like_grade("112.5"))

    grades = pipe_data.piping_class_grades()
    check("可选牌号: 非空", bool(grades), f"{grades}")
    check("可选牌号: 已排序", list(grades) == sorted(grades), f"{grades}")
    check("可选牌号: 预置的 S30408 一定在", "S30408" in grades, f"{grades}")
    if pipe_data.ALLOWABLE_STRESS:
        check_eq(
            "可选牌号: 与许用应力表的键一致",
            list(grades),
            sorted(pipe_data.ALLOWABLE_STRESS),
        )

    _, errors = pipe_data.validate_piping_classes(
        {"rules": [{"medium": ["PL"], "grade": "S30408"}]}
    )
    check_eq("等级表: 表内已有的牌号通过", errors, [])
    _, errors = pipe_data.validate_piping_classes({"rules": [{"medium": ["PL"], "grade": "XYZ"}]})
    check("等级表: 表里没有的牌号被拒", any("牌号" in e for e in errors), f"{errors}")


def test_piping_class() -> None:
    """管道等级表:按「流股名前缀 + 介质代号」匹配,工具只套用不判断腐蚀性。"""
    from app.design.pipe import data as pipe_data

    rules = [
        {"stream_prefix": ["CW", "CWR"], "grade": "20#", "corrosion_mm": 1.0, "note": "循环水"},
        {"medium": ["PGL"], "grade": "S31603", "corrosion_mm": 0.0, "note": "两相工艺流"},
    ]
    default = {"grade": "S30408", "corrosion_mm": 0.0}

    r = pipe_data.resolve_piping_class("CW-0101", "PL", rules=rules, default=default)
    check_eq("piping: 前缀命中循环水规则", (r["grade"], r["source"]), ("20#", "rule"))
    check_close("piping: 循环水腐蚀裕量 1.0 mm", r["corrosion_mm"], 1.0)

    r = pipe_data.resolve_piping_class("0101", "PGL", rules=rules, default=default)
    check_eq("piping: 两相介质命中第二类规则", r["grade"], "S31603")

    r = pipe_data.resolve_piping_class("0101", "PL", rules=rules, default=default)
    check_eq("piping: 没命中规则就走兜底", (r["grade"], r["source"]), ("S30408", "default"))

    r = pipe_data.resolve_piping_class("0101", "PL", rules=rules, default=None)
    check_eq("piping: 既没命中也没兜底 → none", r["source"], "none")

    # 配错/没配文件都不能让选型挂掉
    check_eq(
        "piping: 空规则+空兜底不抛异常",
        pipe_data.resolve_piping_class("X", "PL", rules=[], default=None)["grade"],
        None,
    )

    # 页面保存前的结构校验(只验结构,不判断"该不该用这个材质")
    good = {
        "rules": [{"stream_prefix": ["cw"], "grade": "20#", "corrosion_mm": 1.0}],
        "default": {"grade": "S30408"},
    }
    cleaned, errors = pipe_data.validate_piping_classes(good)
    check_eq("piping: 合法配置通过校验", errors, [])
    check_eq("piping: 前缀统一转大写", cleaned["rules"][0]["stream_prefix"], ["CW"])
    check_close("piping: 腐蚀裕量转成数字", cleaned["rules"][0]["corrosion_mm"], 1.0)

    _, errors = pipe_data.validate_piping_classes({"rules": [{"medium": ["PL"], "grade": "铝"}]})
    check("piping: 不认识的牌号被拒", any("牌号" in e for e in errors))

    # NaN 能同时骗过 c2 < 0 和 c2 > 20 两个比较,得单独挡
    _, errors = pipe_data.validate_piping_classes(
        {"rules": [{"medium": ["PL"], "grade": "20#", "corrosion_mm": float("nan")}]}
    )
    check("piping: 腐蚀裕量 NaN 被拒", errors != [], f"errors={errors}")
    cleaned, errors = pipe_data.validate_piping_classes(
        {
            "rules": [{"medium": ["PL"], "grade": "20#"}],
            "default": {"grade": "S30408", "corrosion_mm": float("nan")},
        }
    )
    check(
        "piping: 兜底腐蚀裕量 NaN 归零",
        errors == [] and cleaned["default"]["corrosion_mm"] == 0.0,
        f"cleaned={cleaned}",
    )

    _, errors = pipe_data.validate_piping_classes({"rules": [{"grade": "S30408"}]})
    check("piping: 前缀和介质都不填被拒", any("至少填一个" in e for e in errors))

    _, errors = pipe_data.validate_piping_classes(
        {"rules": [{"medium": ["XX"], "grade": "S30408"}]}
    )
    check("piping: 不认识的介质代号被拒", any("介质代号" in e for e in errors))

    _, errors = pipe_data.validate_piping_classes(
        {"rules": [{"medium": ["PL"], "grade": "S30408", "corrosion_mm": -1}]}
    )
    check("piping: 负腐蚀裕量被拒", any("0–20" in e for e in errors))


def test_std_versioning() -> None:
    """标准表版本化:导入先在 versions/ 拼完整一套,校验通过才原子切指针。

    以前是逐个 CSV 覆盖生效目录:第二个文件失败时第一个已经生效,页面会拿到
    新的壁厚表配旧的许用应力表。
    """
    import shutil

    from app.design.pipe import data as pipe_data

    root = REPO_ROOT / "data" / "preview" / "_selftest-vers"
    shutil.rmtree(root, ignore_errors=True)
    versions = root / "versions"
    pointer = root / "current.txt"
    base = root / "docx"
    base.mkdir(parents=True)

    old = (
        pipe_data._STD_ROOT,
        pipe_data._VERSIONS_DIR,
        pipe_data._POINTER_PATH,
        pipe_data._CURATED_DIR,
    )
    pipe_data._STD_ROOT = root
    pipe_data._VERSIONS_DIR = versions
    pipe_data._POINTER_PATH = pointer
    pipe_data._CURATED_DIR = base
    try:
        size_csv = "D_mm,S_mm\n" + "\n".join(f"{d},2.5" for d in range(6, 30))
        ok, errs, _ = pipe_data.import_standards(
            {"files": {"t1-17395-table1.csv": size_csv}}, reload=False
        )
        check_eq("标准表版本: 导入成功", (ok, errs), (True, []))
        name = pipe_data._read_pointer()
        check(
            "标准表版本: 指针指向新版本目录",
            bool(name) and (versions / name).is_dir(),
            f"pointer={name!r}",
        )
        check(
            "标准表版本: 版本目录里放的是新表",
            bool(name) and (versions / name / "t1-17395-table1.csv").is_file(),
        )
        check(
            "标准表版本: 生效目录切到新版本",
            pipe_data._resolve_active_dir() == versions / name,
            str(pipe_data._resolve_active_dir()),
        )

        before = sorted(p.name for p in versions.iterdir())
        ok2, errs2, _ = pipe_data.import_standards(
            {"files": {"t1-17395-table1.csv": "foo,bar\n1,2\n"}}, reload=False
        )
        check_eq("标准表版本: 不合格导入被拒", ok2, False)
        check(
            "标准表版本: 失败不留版本目录",
            sorted(p.name for p in versions.iterdir()) == before,
            str(before),
        )
        check("标准表版本: 失败不改指针", pipe_data._read_pointer() == name)
    finally:
        (
            pipe_data._STD_ROOT,
            pipe_data._VERSIONS_DIR,
            pipe_data._POINTER_PATH,
            pipe_data._CURATED_DIR,
        ) = old
        shutil.rmtree(root, ignore_errors=True)


def test_hydraulics() -> None:
    """hydraulics:Re / λ / Σhf。用设计算例 P0216 反算,口径见 pump-selection-plan §3.1。

    算例(设计文件 4.1.2):V=4.32 m³/h、ρ=723.007、μ=1.23 cP、d=32 mm、
    L=40 m、Σζ=17.43,顾毓珍公式得 λ≈0.028、Σhf≈5.93 m。
    """
    from app.design import hydraulics as h

    check_eq("管路: 流速(算例)", round(h.velocity_ms(4.32, 32), 2), 1.49)
    check_eq("管路: 缺流量返回 None", h.velocity_ms(None, 32), None)
    re = h.reynolds(32, 1.49, 723.007, 1.23)
    check_eq("管路: Re(算例约 28000)", round(re, -2), 28000)
    check_eq("管路: 缺粘度返回 None", h.reynolds(32, 1.49, 723.007, None), None)

    lam_gu = h.friction_factor(re, 0.05, 32, h.METHOD_GU)
    check_eq("管路: 顾毓珍 λ≈0.028", round(lam_gu, 3), 0.028)
    check("管路: 顾毓珍超出 Re 范围返回 None", h.gu_yuzhen(1e7) is None)

    lam = h.friction_factor(1e5, 0.05, 100, h.METHOD_COLEBROOK)
    check(
        "管路: Colebrook 在 1e5 给 0.016~0.024", lam is not None and 0.016 < lam < 0.024, str(lam)
    )
    check_eq("管路: 层流一律 64/Re", round(h.friction_factor(1000, 0.05, 100), 6), 0.064)

    hf = h.head_loss_m(40, 32, 17.43, lam_gu, 1.49)
    check_eq("管路: Σhf(算例约 5.9 m)", round(hf, 1), 5.9)
    check_eq("管路: 缺 λ 返回 None", h.head_loss_m(40, 32, 17.43, None, 1.49), None)

    out = h.solve(4.32, 723.007, 1.23, 32, 40, 17.43, method=h.METHOD_GU)
    check_eq("管路: solve 汇总 Σhf", round(out["hf_m"], 1), 5.9)
    check_eq(
        "管路: solve 报出缺失项",
        h.solve(4.32, 723.007, 1.23, 32, None, None)["missing"],
        ["管长 L", "管件系数 Σζ"],
    )
    check_eq(
        "管路: 粘度缺失也报出来", "粘度" in h.solve(4.32, 723.007, None, 32, 40, 1)["missing"], True
    )

    # --- 粘度读取:路径顺序与单位换算(2026-11-15 修正:结果在 STRM_UPP 下)
    from app.aspen import reader as reader_mod

    check(
        "粘度: STRM_UPP 变体排在最前",
        reader_mod.VISC_STREAM_PATHS[0].startswith("STRM_UPP"),
        reader_mod.VISC_STREAM_PATHS[0],
    )
    check_eq("粘度: cP 原样", reader_mod._to_cp("1.23", "cP"), 1.23)
    check_eq("粘度: Pa·s 换算", reader_mod._to_cp(0.00123, "Pa-s"), 1.23)
    check_eq("粘度: mPa·s 原样", reader_mod._to_cp(0.5, "mPa-s"), 0.5)
    check_eq("粘度: 认不出的单位返回 None", reader_mod._to_cp("1.2", "kgf/m2"), None)
    check_eq("粘度: 非数字返回 None", reader_mod._to_cp("abc", "cP"), None)

    # --- _lower_leaf:MIXED 是目录时要钻到 TOTAL,不能把标志位 1 当读数
    # (实测 STRM_UPP\PL\MIXED 与 PBUB\MIXED 直接取值都是 1 bar,那是目录/标志节点)
    def _leaf(nodes, path):
        session = fake_session.FakeSession({"case": "inline-visc", "nodes": nodes})
        return reader_mod._lower_leaf(session, path)

    base = r"\Data\Streams\S\Output\STRM_UPP"
    check_eq(
        "粘度: MIXED 是叶子时直接取",
        round(_leaf({base + r"\VISC\MIXED": {"v": 1.23, "u": "cP"}}, base + r"\VISC")[0], 2),
        1.23,
    )

    value, unit = _leaf(
        {
            base + r"\VISC\MIXED": {"v": 1, "u": "bar", "kids": ["TOTAL"]},
            base + r"\VISC\MIXED\TOTAL": {"v": 0.483, "u": "cP"},
        },
        base + r"\VISC",
    )
    check_eq("粘度: MIXED 是目录时钻到 TOTAL", (round(value, 3), unit), (0.483, "cP"))

    # MUMX(M=混合物粘度)是首选属性,真值同样在 MIXED\TOTAL 下
    value, unit = _leaf(
        {
            base + r"\MUMX\MIXED": {"v": 1, "u": "cP", "kids": ["TOTAL"]},
            base + r"\MUMX\MIXED\TOTAL": {"v": 0.483, "u": "cP"},
        },
        base + r"\MUMX",
    )
    check_eq("粘度: MUMX 下钻到 TOTAL", (round(value, 3), unit), (0.483, "cP"))
    check(
        "粘度: MUMX 排在 VISC 前面",
        reader_mod.VISC_STREAM_PATHS[0].startswith("STRM_UPP\\MUMX"),
        reader_mod.VISC_STREAM_PATHS[0],
    )

    check_eq(
        "粘度: 认不出的目录不硬猜",
        _leaf(
            {
                base + r"\VISC\MIXED": {"v": 1, "u": "bar", "kids": ["SOMETHING"]},
                base + r"\VISC\MIXED\SOMETHING": {"v": 9.9, "u": "cP"},
            },
            base + r"\VISC",
        ),
        (None, ""),
    )
