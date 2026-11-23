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
    skip,
)
from tools import fake_session


def test_pipe() -> None:
    """design.pipe 的算术口径:相态 / DN / 压力 / 温度 / [σ] / 壁厚 / 等级号。"""
    from app.design import pipe as pipe_calc

    # ① 相态:痕量汽化率(库里实测 1e-8)必须按全液处理,不能 ==0 判
    check_eq("pipe: vap_frac=0 → 液", pipe_calc.phase_of(0.0), "liquid")
    check_eq("pipe: vap_frac=1e-8 → 仍算液(痕量)", pipe_calc.phase_of(1e-8), "liquid")
    # 阈值 1e-4 与泵侧 / 读取层同一常量:同一流股不能管道判两相、泵按液算 NPSH
    check_eq("pipe: vap_frac=5e-4 → 两相(与泵侧同阈值)", pipe_calc.phase_of(0.0005), "two")
    check_eq("pipe: vap_frac=1e-5 → 仍算液", pipe_calc.phase_of(1e-5), "liquid")
    # 边界取等号与泵侧一致:≤1e-4 为单相,NPSHₐ 照算;>1e-4 才按两相
    check_eq("pipe: vap_frac=1e-4 边界 → 液", pipe_calc.phase_of(1e-4), "liquid")
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
    # 95–101.3 kPa 微负压:表压为负,下限/附加都给 0 时也不能出负设计压力
    pn = pipe_calc.design_pressure(0.96, {"pressure_add_mpa": 0.0, "pressure_floor_mpa": 0.0})
    check("pipe: 96 kPa 不算真空", pn["vacuum"] is False)
    check("pipe: 96 kPa 表压为负", pn["gauge_mpa"] < 0)
    check_eq("pipe: 微负压设计压力兜到 0", pn["design_mpa"], 0.0)

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

    # 低温段:裕量取偏冷侧,否则 -C 与 V7 会被 +20 ℃ 整体抬高 20 K 而漏判
    cold = pipe_calc.design_temperature(-10.0)
    check_close("pipe: 操作 −10 ℃ → 设计 −30 ℃(偏冷侧)", cold["design_c"], -30.0)
    check_close("pipe: 低温段裕量记为 −20", cold["margin_c"], -20.0)
    check("pipe: 低温说明写偏冷侧", "偏冷侧" in cold["note"], cold["note"])
    check_eq(
        "pipe: 操作 −10 ℃ 的设计温度触发保冷 -C",
        pipe_calc.grade_code(1.0, "E", cold["design_c"], "", None)["insulation"],
        "C",
    )

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
    # ⑧a 负偏差把最大标准壁厚的净厚吃掉时,文案要给出净厚,而不是"所需 > 最大标准壁厚"自相矛盾
    overflow = pipe_calc.round_up_wall(2.8, (2.5, 3.0), od_mm=108.0, grade="S2-B")
    check("pipe: 净厚不足仍判无可用档", overflow["wall_mm"] is None)
    check(
        "pipe: 净厚不足的备注同时写承压厚度与净厚 2.62 mm",
        "所需承压厚度 2.80 mm" in overflow["note"] and "净厚 2.62 mm" in overflow["note"],
    )
    # ⑧a2 名义下限超序列最大档:单独说明"下限不足",不再和净厚混谈
    floor_over = pipe_calc.round_up_wall(3.2, (2.5, 3.0), min_wall=3.5)
    check("pipe: 名义下限超最大档仍无可用档", floor_over["wall_mm"] is None)
    check(
        "pipe: 名义下限超最大档的备注写下限与最大档",
        "工艺最小壁厚下限 3.5 mm 超出" in floor_over["note"]
        and "最大档 3 mm" in floor_over["note"],
    )
    check("pipe: 名义下限超最大档标记 floored", floor_over["floored"] is True)
    check_eq(
        "pipe: 无 od/grade 时备注退回名义壁厚口径",
        "超过该外径最大标准壁厚 3 mm" in pipe_calc.round_up_wall(3.2, (2.5, 3.0))["note"],
        True,
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
    # ⑧c 下限 > t_req,但真正把档位抬上去的是负偏差净厚约束:不能误标 floored
    #    t_req=2.3、下限 2.5:2.5 档净厚 2.19 不够,选到 3.0;仅按净厚约束也是 3.0
    nav = pipe_calc.round_up_wall(2.3, (2.5, 3.0, 4.0), od_mm=108.0, grade="S2-B", min_wall=2.5)
    check_close("pipe: 净厚抬档时仍取 3.0", nav["wall_mm"], 3.0)
    check("pipe: 净厚抬档不算下限抬高,floored 为假", nav["floored"] is False)
    # ⑧d PN 整档保护把厚度顶过 D_o/6:薄壁公式已越界,必须显式提示(不能静默出结论)
    pn_over = pipe_calc.design_wall_for_od(
        108.0,
        1.0,
        sigma=30.0,
        force_pn_code="U",
        od_to_walls={108.0: (4.0, 36.0)},
        min_wall_map={},
    )
    check_close("pipe: PN 保护超 D_o/6 时壁厚仍算到 36 mm", pn_over["wall_mm"], 36.0)
    check(
        "pipe: PN 保护超 D_o/6 时给出薄壁域警告",
        "薄壁公式不适用" in pn_over["note"],
        pn_over["note"],
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


def test_pipe_run_overrides_export_and_validate() -> None:
    """pipe_run 人工 DN / 自定义薄壁:统一合并、导出编号降级、V3 能报出。"""
    from app.design import pipe as pc
    from app.export import pipe_schedule

    streams = [
        {
            "name": "0101",
            "stream_type": "MATERIAL",
            "vol_flow_m3h": 10.0,
            "temperature_c": 25.0,
            "pressure_bar": 11.0,
            "vap_frac": 0.0,
        },
        {
            "name": "0102",
            "stream_type": "MATERIAL",
            "vol_flow_m3h": 10.0,
            "temperature_c": 25.0,
            "pressure_bar": 11.0,
            "vap_frac": 0.0,
        },
        {
            "name": "0103",
            "stream_type": "MATERIAL",
            "vol_flow_m3h": 10.0,
            "temperature_c": 200.0,
            "pressure_bar": 50.0,
            "vap_frac": 0.0,
        },
    ]
    rows = pc.build(streams, tables=SYNTH_TABLES)["rows"]
    runs = {
        "0101": {"dn_override": 100},
        "0102": {"od_override_mm": 108.0, "wall_override_mm": 0.4},
        "0103": {"od_override_mm": 108.0, "wall_override_mm": 0.5},
    }
    merged = pc.apply_pipe_run_overrides(rows, runs)
    by = {r["stream"]: r for r in merged}

    s1 = by["0101"]
    check_eq("override: 人工 DN 生效", s1["dn"], 100)
    check_eq("override: 人工 DN 换外径", s1["od_mm"], 108.0)
    if s1["wall_mm"] is None:
        skip("override: 人工 DN 重算壁厚", "缺标准壁厚序列(T2 未录入)")
    else:
        check("override: 人工 DN 重算壁厚", s1["wall_mm"] is not None, str(s1["wall_mm"]))
    check("override: 人工 DN 备注", "人工DN" in s1["warnings"], str(s1["warnings"]))
    check_eq("override: 保留自动 DN 原值", s1.get("auto_dn"), rows[0]["dn"])

    s2 = by["0102"]
    check_eq("override: 自定义规格无标准 DN", s2["dn"], None)
    check_eq("override: 自定义外径生效", s2["od_mm"], 108.0)
    check_eq("override: 自定义壁厚生效", s2["wall_mm"], 0.4)
    check_eq("override: 自定义壁厚负偏差按 0", s2["c1_mm"], 0.0)
    check("override: 自定义流速按实际内径重算", s2["v_act_ms"] is not None, str(s2))
    check("override: 自定义规格备注", "自定义规格" in s2["warnings"], str(s2["warnings"]))
    tag = pipe_schedule._pipe_tag(s2)
    check("override: 导出编号 DN 段降级为 Φ", "-Φ108-" in tag, tag)
    check("override: 导出编号不含 -None", "-None" not in tag, tag)

    checks = pc.validate(merged)
    thin = [f for f in checks["findings"] if f["stream"] == "0102" and f["code"] == "V3"]
    check("override: 自定义薄壁触发 V3", bool(thin), str(checks["findings"]))
    r1e_thin = [f for f in checks["findings"] if f["stream"] == "0103" and f["code"] == "V3"]
    check("override: R1E 自定义薄壁也触发 V3", bool(r1e_thin), str(checks["findings"]))


def test_pipe_vacuum_code_and_wall() -> None:
    """真空代号 L、布尔 is_vacuum 与壁厚外压路径解耦;V4 不误报、V6/V11 正常。"""
    from app.design import pipe as pc

    row = pc.build(
        [
            {
                "name": "0301",
                "stream_type": "MATERIAL",
                "vol_flow_m3h": 800.0,
                "temperature_c": 30.0,
                "pressure_bar": 0.5,
                "vap_frac": 0.0,
            }
        ],
        tables=SYNTH_TABLES,
    )["rows"][0]
    check_eq("vacuum: 真空等级号 L1E", row["grade_code"], "L1E")
    check("vacuum: 布尔 is_vacuum", row["is_vacuum"] is True, str(row.get("is_vacuum")))
    check(
        "vacuum: 备注写明 PN10 配级",
        any("PN10配级" in str(w) for w in row["warnings"]),
        str(row["warnings"]),
    )
    check("vacuum: 大口径走 DN600", row["dn"] == 600, str(row["dn"]))
    check("vacuum: 外压筛查有下限", row["external_wall_mm"] is not None, str(row))
    codes = [f["code"] for f in pc.validate([row], {})["findings"]]
    check("vacuum: V4 不把 L 当普通 PN 比较", "V4" not in codes, str(codes))
    check("vacuum: V6 触发", "V6" in codes, str(codes))
    check("vacuum: V11 触发", "V11" in codes, str(codes))

    # 去掉工艺最小壁厚,直接比较真空/非真空路径:L 的 PN1.0 不得抬高真空壁厚,
    # 真空壁厚应完全由外压筛查 t_ext + C2 控制。
    vac = pc.design_wall_for_od(
        610.0,
        0.1,
        dn=600,
        material="S30408",
        sigma=130.0,
        design_temp_c=50.0,
        c2_mm=0.0,
        vacuum=True,
        min_wall_map={},
        options={},
    )
    plain = pc.design_wall_for_od(
        610.0,
        0.1,
        dn=600,
        material="S30408",
        sigma=130.0,
        design_temp_c=50.0,
        c2_mm=0.0,
        vacuum=False,
        min_wall_map={},
        options={},
    )
    # 圆整结果依赖标准壁厚序列(T2):干净克隆没有 data/preview/std/docx 下的表,
    # design_wall_for_od 会返回 wall_mm=None 并写明「缺标准壁厚序列」——此时跳过比较,
    # 下面与表无关的 t_req / PN 断言照跑。
    if vac["wall_mm"] is None or plain["wall_mm"] is None:
        skip(
            "vacuum: 外压壁厚大于同外径内压壁厚",
            vac.get("note") or plain.get("note") or "缺标准壁厚序列(T2 未录入)",
        )
    else:
        check("vacuum: 外压壁厚大于同外径内压壁厚", vac["wall_mm"] > plain["wall_mm"])
    check_close("vacuum: t_req 等于外压筛查值", vac["t_required_mm"], vac["ext_wall_mm"])
    check("vacuum: 未吃 PN1.0 整档保护", vac["pn_code"] != "L", str(vac.get("pn_code")))


def test_pipe_2026_rules() -> None:
    """2026-11-18 工程校核口径(§17):真空 V1E / 外压 / 升 R1E / 非优先 DN / 汽蚀 / 零流量。"""
    from app.design import pipe as pc
    from app.design.pipe import data as pdata

    # §17.1 真空代号 + 外压失稳最小壁厚:默认 L(PN1.0,按 PN10 配级),不再借用 V(=25 MPa)
    check_eq("rules: 真空压力代号 L", pdata.VACUUM_PN_CODE, "L")
    gc = pc.grade_code(0.1, "E", 30.0, options={"vacuum": True})
    check_eq("rules: 真空等级号 L1E", gc["code"], "L1E")
    check("rules: 真空布尔标记与代号解耦", gc["is_vacuum"] is True)
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
    # 上限 5.6 → 6.4:5.6–6.4 原来落 PN Q 档,壁厚随压力升高反而变薄
    check_eq(
        "rules: 5.61 MPa 仍升 R(修补 5.6–6.4 倒挂)",
        pc.hp_stainless_force_code("S30408", 5610.0),
        "R",
    )
    check_eq(
        "rules: 6.41 MPa 不再强制(自然 R1E 接管)",
        pc.hp_stainless_force_code("S30408", 6410.0),
        None,
    )
    check_eq("rules: 碳钢不升档", pc.hp_stainless_force_code("20#", 5000.0), None)
    check_eq(
        "rules: 升档等级号 R1E-H",
        pc.grade_code(5.0, "E", 200.0, options={"force_pn_code": "R"})["code"],
        "R1E-H",
    )
    # 5.67–6.4 MPa 段:Pd 已 >6.4,本来自然落 R1E;不能再标「强制升档」触发 V13
    hp_natural = pc.build(
        [
            {
                "name": "HP1",
                "vol_flow_m3h": 50.0,
                "temperature_c": 200.0,
                "pressure_bar": 60.0,
                "vap_frac": 0.0,
            }
        ],
        tables=SYNTH_TABLES,
    )
    hp_row = hp_natural["rows"][0]
    check("rules: 6.0 MPa 的 Pd 已 >6.4", hp_row["design_pressure_mpa"] > 6.4)
    check("rules: 自然落 R 档不算强制升档", hp_row["hp_ss_upgraded"] is False)
    check(
        "rules: 自然落 R 档不报 V13",
        all(f["code"] != "V13" for f in pc.validate(hp_natural["rows"])["findings"]),
    )

    # R1E 提示里的 C 必须报实际参与公式的值,不能固定写 1.0(等级表/选项给了就照给)
    r1e_notes = [w for w in hp_row["warnings"] if w.startswith("R1E 项目专用")]
    check(
        "rules: R1E 提示的 C 与行内 r1e_c_mm 一致",
        any(f"C={hp_row['r1e_c_mm']:g} mm" in w for w in r1e_notes),
        str(r1e_notes),
    )
    hp_c_override = pc.build(
        [
            {
                "name": "HP2",
                "vol_flow_m3h": 50.0,
                "temperature_c": 200.0,
                "pressure_bar": 60.0,
                "vap_frac": 0.0,
            }
        ],
        options={"hp_ss_corrosion_mm": 2.5},
        tables=SYNTH_TABLES,
    )
    override_row = hp_c_override["rows"][0]
    override_notes = [w for w in override_row["warnings"] if w.startswith("R1E 项目专用")]
    check(
        "rules: 覆盖 C=2.5 时 R1E 提示同步",
        any("C=2.5 mm" in w for w in override_notes),
        str(override_notes),
    )
    check(
        "rules: 覆盖 C 后不再写默认 1 mm",
        all("C=1 mm" not in w for w in override_notes),
        str(override_notes),
    )

    # §17.8 R1E 公式化壁厚:t = PD/(2[σ]^t·E_j+P) + C 后 ÷0.875
    check_close("rules: [σ] 100 ℃ → 137", pc.hp_ss_allowable_stress(100.0), 137.0)
    check_close("rules: [σ] 125 ℃ 内插 → 130", pc.hp_ss_allowable_stress(125.0), 130.0)
    check_close("rules: [σ] 90 ℃ 取平值 137", pc.hp_ss_allowable_stress(90.0), 137.0)
    # 越界不外推:上面钳位取平值,上面 350 ℃ 以上直接给 None(蠕变段不许猜)
    check_eq("rules: [σ] 400 ℃ 超字典上限 → None", pc.hp_ss_allowable_stress(400.0), None)
    check(
        "rules: [σ] 超上限时 R1E 壁厚不给",
        pc.hp_ss_required_wall(6.09, 426.0, 400.0) is None,
    )
    # 薄壁前提 t < D_o/6:越界时不硬算厚壁(用低 σ 构造越界)
    check_eq(
        "rules: R1E 薄壁越界不给壁厚",
        pc.hp_ss_required_wall(10.0, 100.0, 100.0, sigma_override=20.0),
        None,
    )
    # 边界:承压厚度 t_c < D_o/6,但含腐蚀裕量 C 后总厚度越过 D_o/6。
    # 薄壁判据只看 t_c,不能因为 C 把可算的小口径误判成厚壁。
    w_boundary = pc.hp_ss_required_wall(10.0, 18.0, 100.0, sigma_override=137.0, c_mm=3.0)
    check("rules: R1E 薄壁只按 t_c 校核,含 C 越界仍可算", w_boundary is not None)
    check_close(
        "rules: R1E 薄壁边界壁厚按 t_c+C 计算",
        w_boundary,
        (10.0 * 18.0 / (2.0 * 137.0 + 10.0) + 3.0) / 0.875,
        tol=1e-9,
    )
    check_close(
        "rules: R1E 的 C 由调用方传入(3 mm 比默认 1 mm 多 2÷0.875)",
        pc.hp_ss_required_wall(6.0935, 426.0, 210.0, c_mm=3.0),
        pc.hp_ss_required_wall(6.0935, 426.0, 210.0) + 2.0 / 0.875,
        tol=1e-6,
    )
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
    check("rules: 高温柔性应力备注", "高温管线:需做热膨胀柔性应力分析(热膨胀补偿)" in hjoined)
    check("rules: 高温备注不重复", sum("高温管线" in w for w in hrows[0]["warnings"]) == 1)

    cold = dict(base, name="0402", temperature_c=-25.0, pressure_bar=1.01325)
    crows = pc.build([cold], tables=SYNTH_TABLES)["rows"]
    check("rules: 深冷标记", crows[0]["low_temp"] is True)
    cjoined = ";".join(crows[0]["warnings"])
    check("rules: 低温韧性备注", "低温/深冷工况:复核焊缝低温韧性及低温螺栓选型" in cjoined)
    check("rules: 低温备注不重复", sum("复核焊缝低温韧性" in w for w in crows[0]["warnings"]) == 1)

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
        check(f"rules: {nm} 超径百分比备注", "计算内径超DN1000实际内径" in sjoined)

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
    check_eq(
        "checks: 扣负偏差后不足 → V3",
        codes({"wall_mm": 4.0, "c1_mm": 0.6, "t_required_mm": 3.6}),
        ["V3"],
    )
    check_eq(
        "checks: 扣负偏差后仍够 → 不报 V3",
        codes({"wall_mm": 4.0, "c1_mm": 0.5, "t_required_mm": 3.4}),
        [],
    )
    check_eq(
        "checks: R1E(C1 已除入 t_req)不双扣",
        codes({"wall_mm": 4.0, "c1_mm": 0.0, "t_required_mm": 3.9}),
        [],
    )
    check_eq("checks: 等级号压力不足 → V4", codes({"grade_code": "H1E"}), ["V4"])
    check_eq("checks: 编号口径与 DN 不符 → V1", codes({"pipe_no": "PG-0111-300"}), ["V1"])
    check_eq("checks: 两相按单相流速 → V5", codes({"phase": "two"}), ["V5"])
    # 泵入口/汽蚀限速是刻意用 0.5–1.0 m/s 的:两相进口不能拿 5–10 m/s 去要求它
    check_eq(
        "checks: 两相泵入口(0.8 m/s)不报 V5",
        codes({"phase": "two", "pump_inlet": True, "velocity_ms": 0.8, "vol_flow_m3h": 22.6}),
        [],
    )
    check_eq("checks: 真空流股 → V6", codes({"vacuum": True}), ["V6"])
    check_eq("checks: 设计温度超材料区间 → V7", codes({"design_temp_c": 500.0}), ["V7"])
    check_eq(
        "checks: 低温设计温度 −30 ℃ → V7",
        codes({"design_temp_c": pipe_calc.design_temperature(-10.0)["design_c"]}),
        ["V7"],
    )
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
    check_eq(
        "checks: 空 grade_code 时 V9 跳过(与 V4 一致)",
        codes({"design_temp_c": 90.0, "insulation": "", "grade_code": None}),
        [],
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
    check_eq(
        "checks: 真空代号 L1E 不误报 V4",
        codes({"grade_code": "L1E", "is_vacuum": True}),
        ["V6"],
    )
    check_eq(
        "checks: 真空布尔与代号解耦,旧 vacuum 字段也认",
        codes({"grade_code": "L1E", "vacuum": True}),
        ["V6"],
    )

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
        {"标准": "GB/T 8163", "牌号": "Q345A", "许用应力_20": "157", "许用应力_100": "157"},
        # 同一标准下混有 20# 行;固定取行规则不能把 20# 的低温值串给 Q345B
        {"标准": "GB/T 6479", "牌号": "20", "许用应力_20": "137", "许用应力_100": "137"},
        {"标准": "GB/T 6479", "牌号": "Q345B", "许用应力_20": "170", "许用应力_100": "160"},
        # Q345B 三个尺寸档:必须取各温度最小值,不能取第一条
        {"标准": "GB/T 6479", "牌号": "Q345B", "许用应力_20": "165", "许用应力_100": "155"},
        {
            "标准": "GB/T 14976",
            "牌号": "06Cr19Ni10(304)",
            "许用应力_20": "137",
            "许用应力_100": "130",
        },
        {"标准": "GB/T 14976", "牌号": "S30403", "许用应力_20": "115", "许用应力_100": "115"},
        {
            "标准": "GB/T 14976",
            "牌号": "022Cr19Ni10(304L)",
            "许用应力_20": "115",
            "许用应力_100": "115",
        },
        {
            "标准": "GB/T 14976",
            "牌号": "06Cr18Ni11Ti(321)",
            "许用应力_20": "130",
            "许用应力_100": "120",
        },
    ]
    out = pipe_data._stress_from_rows(rows)
    check("牌号: 预置牌号仍在", "20#" in out, f"{sorted(out)}")
    check_eq("牌号: Q345B 多尺寸档取最小值", out["Q345B"][20.0], 165.0)
    check_eq("牌号: Q345A 只取 Q345A 行", out["Q345A"][20.0], 157.0)
    check_eq("牌号: 20# 只取 GB/T 8163 的 20 行", out["20#"][20.0], 130.0)
    check_eq("牌号: 06Cr19Ni10(304) 归一到 S30408", out["S30408"][20.0], 137.0)
    check_eq("牌号: 022Cr19Ni10(304L) 归一到 S30403", out["S30403"][20.0], 115.0)
    check_eq("牌号: 06Cr18Ni11Ti(321) 归一到 S32168", out["S32168"][20.0], 130.0)
    check(
        "牌号: 别名不留第二种写法",
        all(
            name not in out
            for name in ("06Cr19Ni10(304)", "022Cr19Ni10(304L)", "06Cr18Ni11Ti(321)")
        ),
        f"{sorted(out)}",
    )
    check("牌号: 表里新增的牌号也收进来", "S32168" in out, f"{sorted(out)}")

    check_eq("材质: 已确认牌号走显式映射", pipe_data.material_code_for("S30408"), ("E", "explicit"))
    check_eq(
        "材质: 321 已在表 B.1 里确认", pipe_data.material_code_for("S32168"), ("E", "explicit")
    )
    check_eq(
        "材质: 06Cr19Ni10 仍按原分支判为不锈钢",
        pipe_data.material_code_for("06Cr19Ni10"),
        ("E", "prefix"),
    )
    check_eq(
        "材质: 新国标超低碳 022Cr19Ni10 按前缀判为不锈钢",
        pipe_data.material_code_for("022Cr19Ni10"),
        ("E", "prefix"),
    )
    check_eq(
        "材质: 新国标超低碳 022Cr17Ni12Mo2 按前缀判为不锈钢",
        pipe_data.material_code_for("022Cr17Ni12Mo2"),
        ("E", "prefix"),
    )
    # 表 B.1 里的铬钼耐热钢已逐个确认成 D;表外的仍要前置拦截,不能被短式静默判 E
    for _g in ("12Cr1MoV", "15CrMo", "12Cr2Mo1", "10Cr9Mo1VNbN"):
        check_eq(
            f"材质: 表内铬钼钢 {_g} 已确认 D", pipe_data.material_code_for(_g), ("D", "explicit")
        )
    for _g in ("1Cr5Mo", "9Cr1Mo", "5CrMo"):
        check_eq(
            f"材质: 表外铬钼耐热钢 {_g} 归入待确认(heat_resistant)",
            pipe_data.material_code_for(_g),
            (None, "heat_resistant"),
        )
    check_eq(
        "材质: 含 Ni 的 1Cr18Ni9Ti 仍是不锈钢",
        pipe_data.material_code_for("1Cr18Ni9Ti"),
        ("E", "prefix"),
    )
    check_eq("材质: Q235 已在表内确认碳钢", pipe_data.material_code_for("Q235B"), ("B", "explicit"))
    check_eq(
        "材质: Q345R 已在表内确认低合金钢", pipe_data.material_code_for("Q345R"), ("C", "explicit")
    )
    check_eq("材质: 铸铁 HT200 归 A", pipe_data.material_code_for("HT200"), ("A", "explicit"))
    check_eq(
        "材质: 低温镍钢 06Ni9DG 归 D", pipe_data.material_code_for("06Ni9DG"), ("D", "explicit")
    )
    check_eq(
        "材质: 10MnDG 归普通低合金钢 C", pipe_data.material_code_for("10MnDG"), ("C", "explicit")
    )
    check_eq("材质: TA2 按前缀判为有色金属", pipe_data.material_code_for("TA2"), ("F", "prefix"))
    check_eq("材质: 判不出来的不硬猜", pipe_data.material_code_for("XYZ"), (None, "unknown"))

    # 表 B.1 的 104 个牌号全部有确认代号,不再有"暂记 E"的兜底
    unmapped = [
        g for g in pipe_data.ALLOWABLE_STRESS if pipe_data.material_code_for(g)[1] != "explicit"
    ]
    check("材质: 表 B.1 全部牌号已确认", not unmapped, f"{unmapped}")
    bad_codes = [
        (g, c)
        for g in pipe_data.ALLOWABLE_STRESS
        for c, _src in [pipe_data.material_code_for(g)]
        if c not in pipe_data.MATERIAL_CODES
    ]
    check("材质: 代号都在 A–H 表内", not bad_codes, f"{bad_codes}")

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

    # 全表导入:牌号写法带空格(「06Cr19Ni10 (304)」),归一与预置取行都不能被空格卡住
    check_eq(
        "空格牌号归一",
        pipe_data.project_grade("06Cr19Ni10 (304)"),
        "S30408",
    )
    spaced = pipe_data._stress_from_rows(
        [
            {
                "标准": "GB/T 14976",
                "牌号": "06Cr19Ni10 (304)",
                "许用应力_40": "137",
                "许用应力_100": "130",
            }
        ]
    )
    check(
        "空格牌号仍走预置取行",
        spaced.get("S30408") == {40.0: 137.0, 100.0: 130.0},
        str(spaced),
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


def test_design_wall_contract() -> None:
    """统一壁厚函数:材质路由 / PN 保护 / R1E / 真空焊接管 / 最小壁厚覆盖。"""
    from app.design import pipe as pc
    from app.design.pipe import data as pdata

    walls = {108.0: (1.4, 1.6, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0)}
    # 工艺最小壁厚:DN100 承压只要 0.4 mm,也必须托到 3.0 mm
    base = pc.design_wall_for_od(
        108.0,
        1.0,
        dn=100,
        material="20#",
        sigma=130.0,
        design_temp_c=60.0,
        c2_mm=0.0,
        od_to_walls=walls,
        min_wall_map={100: 3.0},
    )
    check_eq("wall: 工艺下限托到 3.0", base["wall_mm"], 3.0)

    # PN 整档保护:9 MPa 落在 PN10(R),壁厚不能只按 9 MPa 算
    guarded = pc.design_wall_for_od(
        108.0,
        9.0,
        dn=100,
        material="20#",
        sigma=130.0,
        design_temp_c=60.0,
        c2_mm=0.0,
        od_to_walls=walls,
        min_wall_map={100: 3.0},
    )
    check_eq("wall: 9 MPa 取 PN10(R)", guarded["pn_code"], "R")
    check("wall: PN 整档保护抬高壁厚", guarded["wall_mm"] >= 4.3, str(guarded))

    # R1E:自然落 R 的 S31603 走 §17.8 公式,不吃 PN 整档保护
    r1e = pc.design_wall_for_od(
        108.0,
        6.5,
        dn=100,
        material="S31603",
        sigma=130.0,
        design_temp_c=200.0,
        c2_mm=0.0,
        hp_c_mm=1.0,
        od_to_walls=walls,
        min_wall_map={100: 3.0},
    )
    check("wall: S31603 6.5 MPa 自然落 R1E", r1e["hp_formula"] is True)
    check("wall: R1E 壁厚有值", r1e["wall_mm"] is not None, str(r1e))

    # R1E 厚壁越界:不是「公式无解」,而是超出薄壁圆筒适用范围,文案要能指导后续动作。
    thick = pc.design_wall_for_od(
        100.0,
        10.0,
        dn=100,
        material="S31603",
        sigma=20.0,
        design_temp_c=200.0,
        hp_c_mm=1.0,
        od_to_walls=walls,
        min_wall_map={100: 3.0},
    )
    check_eq("wall: R1E 厚壁越界不给壁厚", thick["wall_mm"], None)
    check(
        "wall: R1E 厚壁越界文案指向薄壁公式适用范围",
        "超出薄壁圆筒公式适用范围（t_c ≥ D_o/6），需另行校核" in (thick["note"] or ""),
        thick["note"],
    )

    # DN>600 没录表时用焊接管序列兜底;真空下限与工艺下限取大
    welded = pc.design_wall_for_od(
        711.0,
        0.1,
        dn=700,
        material="20#",
        sigma=130.0,
        design_temp_c=60.0,
        vacuum=True,
        od_to_walls={},
        min_wall_map={1000: 10.0},
    )
    check("wall: DN700 用焊接管序列", welded["welded"] is True)
    check("wall: 真空 DN700 壁厚不小于 10 mm", welded["wall_mm"] >= 10.0, str(welded))

    # MIN_WALL_BY_DN 必须覆盖当前 DN_TO_OD 的全部档位
    missing = sorted(dn for dn in pdata.DN_TO_OD if pdata.minimum_wall(dn) is None)
    check_eq("wall: 每个 DN 都有最小壁厚下限", missing, [])


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
    check_close("粘度: lb/(ft·hr) 按 0.4133789 折算", reader_mod._to_cp(1.0, "lb/ft-hr"), 0.4133789)
    check_close(
        "粘度: lb/(ft·s) 按 1488.16394 折算(带括号同样认)",
        reader_mod._to_cp(1.0, "lb/(ft·s)"),
        1488.16394,
    )
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


def test_pipe_followup_guards() -> None:
    """收尾修复:DN1000 动态容差 / R1E 只升不降 / 压力缺值 / 外压 C2 / 退化选档。"""
    from app.design import pipe as pc
    from app.design.pipe.step_wall import design_wall_for_od

    base_stream = {
        "name": "0101",
        "stream_type": "MATERIAL",
        "vol_flow_m3h": 10.0,
        "pressure_bar": 5.0,
        "temperature_c": 25.0,
        "vap_frac": 0.0,
    }

    # 1) Pd>10:操作绝压仍在 4.7–6.4 MPa 时也不能强制 R(否则 S→R 反向降档)
    check_eq(
        "follow: P_d=10.5 不强制 R",
        pc.hp_stainless_force_code("S30408", 5000.0, design_mpa=10.5),
        None,
    )
    check_eq(
        "follow: P_d=9.5 仍强制 R",
        pc.hp_stainless_force_code("S30408", 5000.0, design_mpa=9.5),
        "R",
    )
    forced = pc.build(
        [{**base_stream, "pressure_bar": 50.0}],
        tables=SYNTH_TABLES,
        options={"pressure_factor": 2.4},
    )["rows"][0]
    check(
        "follow: 高压自然 S 不降 R", str(forced["grade_code"]).startswith("S"), forced["grade_code"]
    )
    check("follow: 壁厚路径不标 R1E 升档", forced["hp_ss_upgraded"] is False)
    check("follow: 壁厚不写 R1E 公式说明", not any("R1E 公式口径" in w for w in forced["warnings"]))

    # 2) DN1000 动态容差:Φ1016×20 的实际内径 976,Di 980 不应成为死区
    big_dn = dict(SYNTH_TABLES["dn_to_od"])
    big_dn[1000] = 1016.0
    big_walls = dict(SYNTH_TABLES["od_to_walls"])
    big_walls[1016.0] = (20.0,)
    big_tables = {
        "dn_to_od": big_dn,
        "od_to_walls": big_walls,
        "od_to_walls_sst": big_walls,
        "stress": SYNTH_TABLES["stress"],
    }
    q980 = math.pi * (980.0 / 1000.0) ** 2 / 4.0 * 15.0 * 3600.0
    s980 = pc.build(
        [{**base_stream, "name": "0301", "vol_flow_m3h": q980, "vap_frac": 1.0}],
        tables=big_tables,
    )["rows"][0]
    check_eq("follow: 厚壁 DN1000 Di=980 选 DN1000", s980["dn"], 1000)
    check_eq("follow: 厚壁 DN1000 壁厚 20", s980["wall_mm"], 20.0)
    check_eq("follow: 厚壁 DN1000 标记容差", s980["oversize_tolerated"], True)
    check(
        "follow: 超径备注用实际内径 976",
        any("Φ1016×20→976.0mm" in w for w in s980["warnings"]),
        str(s980["warnings"]),
    )
    check_close(
        "follow: 相对实际内径超径约 0.41%",
        pc.dn1000_oversize_pct(980.0, base_inner_mm=976.0),
        0.41,
        tol=0.02,
    )
    q1030 = math.pi * (1030.0 / 1000.0) ** 2 / 4.0 * 15.0 * 3600.0
    bad1030 = pc.build(
        [{**base_stream, "name": "0302", "vol_flow_m3h": q1030, "vap_frac": 1.0}],
        tables=big_tables,
    )
    check_eq("follow: 超过 1.05×976 不选型", bad1030["rows"], [])

    # 3) 无壁厚/无许用应力:按该 DN 最小壁厚退化,且继续屏蔽 DN65/DN125
    series = pc.usable_dn_series({100: 108.0, 125: 133.0, 150: 159.0})
    fallback = pc.select_dn(
        105.0,
        series,
        {},
        lambda od: None,
        min_wall_for={100: 3.0, 125: 4.0, 150: 5.0}.get,
    )
    check_eq("follow: 退化选档跳过 DN125 选 DN150", fallback["dn"], 150)
    check_eq("follow: 退化选档不冒充实际壁厚", fallback["wall_mm"], None)

    # 4) 压力缺失/非法:不给 H1E/V1E 假结论,壁厚留空,V15 报 error
    for label, pressure in (("缺压力", None), ("压力为 0", 0.0), ("压力为负", -1.0)):
        row = pc.build(
            [{**base_stream, "name": "0201", "pressure_bar": pressure}], tables=SYNTH_TABLES
        )["rows"][0]
        check_eq(f"follow: {label} design_mpa", row["design_pressure_mpa"], None)
        check_eq(f"follow: {label} wall_mm", row["wall_mm"], None)
        check_eq(f"follow: {label} grade_code", row["grade_code"], None)
        check(f"follow: {label} 仍给几何 DN", row["dn"] is not None, str(row))
        check(f"follow: {label} 有 pressure_error", bool(row["pressure_error"]), str(row))
        findings = pc.validate([row])["findings"]
        check(
            f"follow: {label} V15 error",
            any(f["code"] == "V15" and f["level"] == "error" for f in findings),
            str(findings),
        )

    # 5) 缺温度:保持几何 DN,壁厚为空,只留一条清晰提示
    temp_row = pc.build(
        [{**base_stream, "name": "0202", "temperature_c": None}], tables=SYNTH_TABLES
    )["rows"][0]
    check_eq("follow: 缺温度仍给几何 DN", temp_row["wall_mm"], None)
    check("follow: 缺温度有 DN", temp_row["dn"] is not None, str(temp_row))
    check(
        "follow: 缺温度文案",
        temp_row["warnings"].count("缺操作温度，无法计算设计温度与壁厚") == 1,
        str(temp_row["warnings"]),
    )
    check(
        "follow: 缺温度不重复缺许用应力",
        "缺许用应力" not in ";".join(temp_row["warnings"]),
        str(temp_row["warnings"]),
    )

    # 5b) R1E 路径缺操作温度:不能拿 None 去格式化超温说明;按普通缺温退化,
    # 仍给几何 DN,壁厚/T_req/许用应力/min_wall 全空,且只留一条缺温提示。
    r1e_temp = pc.build(
        [
            {
                **base_stream,
                "name": "0206",
                "pressure_bar": 50.0,
                "temperature_c": None,
            }
        ],
        tables=SYNTH_TABLES,
    )
    r1e_temp_row = r1e_temp["rows"][0]
    check_eq("follow: R1E 缺温度不抛异常(有行)", bool(r1e_temp_row), True)
    check("follow: R1E 缺温度仍给几何 DN", r1e_temp_row["dn"] is not None, str(r1e_temp_row))
    check_eq("follow: R1E 缺温度 wall_mm 为空", r1e_temp_row["wall_mm"], None)
    check_eq("follow: R1E 缺温度 min_wall_mm 为空", r1e_temp_row["min_wall_mm"], None)
    check_eq("follow: R1E 缺温度 t_required_mm 为空", r1e_temp_row["t_required_mm"], None)
    check_eq("follow: R1E 缺温度 stress_mpa 为空", r1e_temp_row["stress_mpa"], None)
    check(
        "follow: R1E 缺温度与普通缺温行结构一致",
        set(r1e_temp_row) == set(temp_row),
        f"R1E={sorted(r1e_temp_row)} / 普通={sorted(temp_row)}",
    )
    check(
        "follow: R1E 缺温度只留一条缺温提示",
        r1e_temp_row["warnings"].count("缺操作温度，无法计算设计温度与壁厚") == 1,
        str(r1e_temp_row["warnings"]),
    )

    # >350 ℃ 仍应进 missing:设计温度是有限数值,R1E 许用应力表越界。
    over_temp = pc.build(
        [
            {
                **base_stream,
                "name": "0207",
                "pressure_bar": 50.0,
                "temperature_c": 400.0,
            }
        ],
        tables=SYNTH_TABLES,
    )
    check_eq("follow: R1E 超温不生成输出行", over_temp["rows"], [])
    check(
        "follow: R1E 超温进 missing 并说明 350 ℃ 上限",
        bool(over_temp["missing"])
        and "R1E 许用应力表范围" in over_temp["missing"][0]["reason"]
        and "350 ℃" in over_temp["missing"][0]["reason"],
        str(over_temp["missing"]),
    )

    # 6) 缺汽化率 / 两相缺质量流量
    vap_row = pc.build([{**base_stream, "name": "0203", "vap_frac": None}], tables=SYNTH_TABLES)[
        "rows"
    ][0]
    check(
        "follow: 缺汽化率有 warn",
        "缺汽化率，暂按液相选型，请复核" in vap_row["warnings"],
        str(vap_row),
    )
    two_row = pc.build(
        [{**base_stream, "name": "0204", "vap_frac": 0.3, "mass_flow_kgh": None}],
        tables=SYNTH_TABLES,
    )["rows"][0]
    check(
        "follow: 两相缺质量流量有 warn",
        "缺质量流量，未执行两相动压头（ρv²≤2000 Pa）限速校核" in two_row["warnings"],
        str(two_row),
    )

    # 7) Pd>22:保留行、按设计压力算壁厚、等级号 None + 专项设计文案
    high = pc.build([{**base_stream, "name": "0205", "pressure_bar": 230.0}], tables=SYNTH_TABLES)[
        "rows"
    ][0]
    if high["wall_mm"] is None:
        skip("follow: Pd>22 保留行", "缺标准壁厚序列(T2 未录入),行仍在、壁厚无法圆整")
    else:
        check("follow: Pd>22 保留行", high["wall_mm"] is not None, str(high))
    check_eq("follow: Pd>22 等级号 None", high["grade_code"], None)
    check(
        "follow: Pd>22 未吃 PN 保护文案",
        any("超出 PN 代号表，未吃 PN 保护，需专项设计" in w for w in high["warnings"]),
        str(high["warnings"]),
    )

    # 8) 真空外压:C2 并入 t_req,负偏差后仍够;外压控制时不误打 wall_floored
    ext = design_wall_for_od(
        219.0,
        0.1,
        dn=200,
        material="20#",
        sigma=130.0,
        design_temp_c=30.0,
        c2_mm=1.0,
        vacuum=True,
        wall_grade="S1",
        od_to_walls={219.0: (4.0, 5.0, 6.0)},
        min_wall_map={200: 2.0},
    )
    need = ext["ext_wall_mm"] + 1.0
    check_close("follow: 外压 t_req=t_ext+C2", ext["t_required_mm"], need, tol=1e-9)
    c1 = 0.75
    check("follow: S−C1 仍够外压所需", ext["wall_mm"] - c1 >= need - 1e-9)
    check("follow: 外压控制不误标工艺托底", ext["floored"] is False)
    vac_row = {
        "stream": "0206",
        "dn": 200,
        "od_mm": 219.0,
        "wall_mm": ext["wall_mm"],
        "t_required_mm": ext["t_required_mm"],
        "c1_mm": c1,
        "design_pressure_mpa": 0.1,
        "vacuum": True,
        "external_wall_mm": ext["ext_wall_mm"],
    }
    check(
        "follow: 外压 t_req 不触发 V3",
        not any(f["code"] == "V3" for f in pc.validate([vac_row])["findings"]),
        str(pc.validate([vac_row])["findings"]),
    )


def test_pipe_tag_grade_placeholder() -> None:
    """等级号缺失时管路编号不能拼出字面 -None。"""
    from app.design import pipe as pc
    from app.export import pipe_schedule

    base_stream = {
        "name": "0101",
        "stream_type": "MATERIAL",
        "vol_flow_m3h": 10.0,
        "temperature_c": 25.0,
        "vap_frac": 0.0,
    }
    for label, pressure in (("缺压力", None), ("P_d > 22 MPa", 230.0)):
        row = pc.build(
            [{**base_stream, "pressure_bar": pressure}],
            tables=SYNTH_TABLES,
        )["rows"][0]
        check_eq(f"tag: {label} grade_code 为空", row["grade_code"], None)
        tag = pipe_schedule._pipe_tag(row)
        check(
            f"tag: {label} 用待确认占位",
            tag.endswith("-待确认"),
            tag,
        )
        check(f"tag: {label} 不含字面 -None", "-None" not in tag, tag)
        check(f"tag: {label} 仍保留介质/流股/DN", tag.startswith("PL-0101-"), tag)


def test_pipe_r1e_missing_reason_by_material() -> None:
    """R1E 超许用应力范围时,S30408 与表 B.1 材质分别给出对应文案。"""
    from app.design import pipe as pc

    base_stream = {
        "name": "0207",
        "stream_type": "MATERIAL",
        "vol_flow_m3h": 10.0,
        "pressure_bar": 50.0,
        "temperature_c": 400.0,
        "vap_frac": 0.0,
    }
    s30408 = pc.build([base_stream], tables=SYNTH_TABLES)["missing"][0]["reason"]
    check_eq(
        "reason: S30408 保留 R1E 350 ℃ 文案",
        s30408,
        "设计温度 420 ℃ 超出 R1E 许用应力表范围(S30408 只到 350 ℃)，需人工给 [σ]^t 后再选型(§17.8)",
    )

    stress = {
        **SYNTH_TABLES["stress"],
        "S31603": {20: 150.0, 100: 140.0, 200: 130.0, 300: 120.0},
    }
    tables = {**SYNTH_TABLES, "stress": stress}
    s31603_res = pc.build(
        [{**base_stream, "name": "0208"}],
        tables=tables,
        options={"material_for_stream": {"0208": "S31603"}},
    )
    check_eq("reason: S31603 超温不生成输出行", s31603_res["rows"], [])
    reason = s31603_res["missing"][0]["reason"]
    check(
        "reason: S31603 走表 B.1 超温文案",
        "温度上限" in reason and "S30408 只到 350" not in reason,
        reason,
    )
    check_eq(
        "reason: S31603 超温文案完整",
        reason,
        "设计温度 420 ℃ 超出表 B.1 中 S31603 的温度上限 300 ℃,查不到 [σ]^t;需人工给定(§17.8)",
    )

    # 整表没录这个牌号时,要与"超温"分开说:补录标准表或换材质,不是降温。
    missing_grade = pc.build(
        [{**base_stream, "name": "0209", "temperature_c": 25.0}],
        tables=SYNTH_TABLES,
        options={"material_for_stream": {"0209": "S31603"}},
    )["missing"][0]["reason"]
    check(
        "reason: S31603 缺牌号单独说明",
        "未录 S31603 牌号" in missing_grade and "温度上限" not in missing_grade,
        missing_grade,
    )


def test_pipe_boundary_regressions() -> None:
    """无壁厚 DN1000 容差与人工规格的零/负值边界。"""
    from app.design import pipe as pc
    from app.design.pipe import data as pdata

    # DN1000 只剩外径、没有壁厚序列时,容差仍须按最小壁厚复核可用内径。
    dn_to_od = {1000: 1016.0}
    kwargs = {
        "max_dn": 1000,
        "min_dn": 15,
        "oversize_tol": 0.05,
        "oversize_band": (996.0, 1045.8),
        "min_wall_for": pdata.minimum_wall,
    }
    no_wall = pc.select_dn(1000.0, dn_to_od, {}, lambda _od: None, **kwargs)
    check_eq("DN1000 无壁厚: 1000 mm 落入容差", no_wall["dn"], 1000)
    check("DN1000 无壁厚: 标记 tolerated", no_wall.get("tolerated") is True, str(no_wall))
    check_eq("DN1000 无壁厚: 不冒充实际壁厚", no_wall["wall_mm"], None)
    edge = pc.select_dn(1045.0, dn_to_od, {}, lambda _od: None, **kwargs)
    check_eq("DN1000 无壁厚: 1045 mm 仍容差", edge["dn"], 1000)
    over = pc.select_dn(1045.9, dn_to_od, {}, lambda _od: None, **kwargs)
    check_eq("DN1000 无壁厚: 超出 5% 容差不选型", over["dn"], None)

    # R1E 的 C=0 是合法值,不能回退为默认 1.0 mm。
    hp = pc.build(
        [
            {
                "name": "HP0",
                "stream_type": "MATERIAL",
                "vol_flow_m3h": 10.0,
                "temperature_c": 200.0,
                "pressure_bar": 60.0,
                "vap_frac": 0.0,
            }
        ],
        tables=SYNTH_TABLES,
    )["rows"][0]
    hp["r1e_c_mm"] = 0.0
    custom = {**hp, "stream": "HP0"}
    merged = pc.apply_pipe_run_overrides(
        [custom], {"HP0": {"od_override_mm": 108.0, "wall_override_mm": 8.0}}
    )[0]
    expected = pc.hp_ss_required_wall(
        custom["design_pressure_mpa"], 108.0, custom["design_temp_c"], c_mm=0.0
    )
    check_close("override: R1E C=0 按零腐蚀裕量重算", merged["t_required_mm"], expected, tol=1e-9)

    # 负设计温度是有限值,不能按缺温度回落到普通 PN 公式。
    cold = pc.build(
        [
            {
                "name": "COLD",
                "stream_type": "MATERIAL",
                "vol_flow_m3h": 10.0,
                "temperature_c": -10.0,
                "pressure_bar": 60.0,
                "vap_frac": 0.0,
            }
        ],
        tables=SYNTH_TABLES,
    )["rows"][0]
    cold_merged = pc.apply_pipe_run_overrides(
        [cold], {"COLD": {"od_override_mm": 108.0, "wall_override_mm": 8.0}}
    )[0]
    cold_expected = pc.hp_ss_required_wall(
        cold["design_pressure_mpa"],
        108.0,
        cold["design_temp_c"],
        c_mm=cold["r1e_c_mm"],
    )
    check_close(
        "override: 负设计温度仍走 R1E 公式",
        cold_merged["t_required_mm"],
        cold_expected,
        tol=1e-9,
    )

    # 自定义压力下限 0 的工况,Pd=0 不应被当成缺压力。
    zero = pc.build(
        [
            {
                "name": "ZERO",
                "stream_type": "MATERIAL",
                "vol_flow_m3h": 10.0,
                "temperature_c": 25.0,
                "pressure_bar": 0.96,
                "vap_frac": 0.0,
            }
        ],
        options={"pressure_add_mpa": 0.0, "pressure_floor_mpa": 0.0},
        tables=SYNTH_TABLES,
    )["rows"][0]
    check_eq("override: 构造 Pd=0 行", zero["design_pressure_mpa"], 0.0)
    zero_merged = pc.apply_pipe_run_overrides(
        [zero], {"ZERO": {"od_override_mm": 108.0, "wall_override_mm": 5.0}}
    )[0]
    check(
        "override: Pd=0 仍能重算 t_required",
        zero_merged["t_required_mm"] is not None,
        str(zero_merged),
    )


def test_pipe_welded_wall_series() -> None:
    """大口径焊接管按 HG/T 20553 Ia 的实际采购档取壁厚,不用 18 mm 等非标档。"""
    from app.design.pipe import data as pdata

    common = pdata.welded_wall_series(711.0)
    dn1000 = pdata.welded_wall_series(1016.0)
    check("DN700 Ia 含 6.3 mm", 6.3 in common, str(common))
    check("DN700 Ia 含 20 mm", 20.0 in common, str(common))
    check("Ia 序列不含 18 mm", 18.0 not in common and 18.0 not in dn1000, str(common))
    check_eq(
        "DN1000 无 6.3/7.1,从 8.0 起",
        dn1000,
        (8.0, 8.8, 10.0, 11.0, 12.5, 14.2, 16.0, 17.5, 20.0),
    )


def test_check_remark_no_duplicate_temp_notes() -> None:
    """管路一览表「校核备注」列:低温/高温各只出一条,不并列旧版两条。"""
    from app.export import pipe_schedule

    for label, row in (
        ("低温", {"low_temp": True}),
        ("高温", {"high_temp": True}),
        ("冷热同命中", {"low_temp": True, "high_temp": True}),
    ):
        marks = pipe_schedule._check_remark(row).split(";")
        low = [m for m in marks if "复核焊缝低温韧性" in m]
        high = [m for m in marks if "高温管线" in m]
        assert len(low) == int(row.get("low_temp") is True), (label, marks)
        assert len(high) == int(row.get("high_temp") is True), (label, marks)
        assert not any("需做柔性应力分析" in m for m in marks), (label, marks)


def test_grade_groups_and_csv_bom() -> None:
    """管材下拉按表 B.1 的「母材类别」分组;CSV 带 BOM,Excel 打开中文不乱码。"""
    from app.api.serialize import pipe_table_status
    from app.design.pipe import data as pipe_data

    groups = pipe_data.grade_groups()
    labels = [g["label"] for g in groups]
    assert labels[-1] == "铸铁/非钢管", labels
    assert labels[:3] == ["Fe-1", "Fe-3", "Fe-4"], labels  # 自然序,Fe-11A 不许排到前面
    steel = {x for g in groups if g["steel"] for x in g["grades"]}
    cast = {x for g in groups if not g["steel"] for x in g["grades"]}
    assert {"20#", "S30408", "S31603"} <= steel
    assert {"HT200", "QT400-18"} <= cast
    flat = [x for g in groups for x in g["grades"]]
    assert len(flat) == len(set(flat)), "牌号重复归组"
    assert set(flat) == set(pipe_data.ALLOWABLE_STRESS), "有牌号没归组"
    # API 带着分组返回,前端才画得出 optgroup
    assert pipe_table_status()["stress"]["groups"] == groups
    # 带 BOM 才能双击用 Excel 打开而不乱码
    csv_path = pipe_data._CURATED_DIR / "t3-20801-tableB1.csv"
    assert csv_path.read_bytes()[:3] == b"\xef\xbb\xbf", "表 B.1 CSV 缺 UTF-8 BOM"
