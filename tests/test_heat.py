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
    # 实测 V15 简捷 HeatX 的 HX_UAVD 用 kcal/hr-sqm-K(sqm 拼写 + K 温标)。
    # 这条别名漏了,设计 U 会静默变 None,需求面积随之算不出来。
    check_close(
        "heat: UAVD 730.868 kcal/hr-sqm-K = 850 W/(m²·K)",
        units.convert("u_value", 730.868444, "kcal/hr-sqm-K"),
        850.0,
        0.1,
    )
    # 实测(2026-11-24)KMX 节点单位写作 `kcal-m/hr-sqm-K`,量纲等价 kcal/(hr·m·K)。
    check_close(
        "heat: KMX 0.535 kcal-m/hr-sqm-K = 0.622 W/(m·K)",
        units.convert("conductivity", 0.534999228, "kcal-m/hr-sqm-K"),
        0.622204,
        0.001,
    )
    # CPMX 给的是摩尔基;先换成 J/(mol·K),再由 reader 用分子量折质量基。
    check_close(
        "heat: CPMX 17.5697 cal/mol-K = 73.51 J/(mol·K)",
        units.convert("molar_heat_capacity", 17.5697346, "cal/mol-K"),
        73.5118,
        0.01,
    )
    check_eq(
        "heat: 摩尔基比热不进质量基表",
        units.convert("specific_heat", 17.5697346, "cal/mol-K"),
        None,
    )


class _FakeElements:
    """最小 COM Elements 替身:只要 Count / Item(i).Name 两件事。"""

    def __init__(self, children):
        self._names = list(children)

    @property
    def Count(self) -> int:
        return len(self._names)

    def Item(self, index):
        name = self._names[index]

        class _Child:
            Name = name

        return _Child()


class _FakeNode:
    def __init__(self, value=None, unit="", children=None):
        self.Value = value
        self.UnitString = unit
        self.Elements = _FakeElements(children or {})


class _FakeSession:
    """路径 → 节点的只读替身,大小写不敏感(真实 Aspen 节点名不区分)。"""

    def __init__(self, nodes):
        self._nodes = {str(k).lower(): v for k, v in nodes.items()}

    def find(self, path):
        return self._nodes.get(str(path).lower())


def _no_value(unit="cal/mol-K"):
    return _FakeNode(None, "" if unit == "" else unit)


def test_heat_prop_nodes() -> None:
    r"""③ 换热物性节点:实测气相/液相树结构下 cp/λ/μ 都要读得到。

    树结构照抄 2026-11-24 V15 全流程实测(工况 77,E0104 四端):
    `STRM_UPP\{CPMX|KMX|MUMX}\MIXED\{VAPOR|LIQUID}`,相态在 MIXED 后面,
    另一相为空节点。老代码相态顺序写反 + 不认 VAPOR,导致气相三项全空。
    """
    from app.aspen import reader

    nodes = {
        r"\Data\Streams\0114\Output\MW": _FakeNode(24.9938141),
        # 气相流股:VAPOR 有值,LIQUID 是空叶子
        r"\Data\Streams\0114\Output\STRM_UPP\CPMX\MIXED": _FakeNode(
            1,
            "cal/mol-K",
            {"VAPOR": _FakeNode(9.40335839, "cal/mol-K"), "LIQUID": _no_value()},
        ),
        r"\Data\Streams\0114\Output\STRM_UPP\CPMX\MIXED\VAPOR": _FakeNode(9.40335839, "cal/mol-K"),
        r"\Data\Streams\0114\Output\STRM_UPP\KMX\MIXED": _FakeNode(
            1,
            "kcal-m/hr-sqm-K",
            {
                "VAPOR": _FakeNode(0.0951764667, "kcal-m/hr-sqm-K"),
                "LIQUID": _no_value("kcal-m/hr-sqm-K"),
            },
        ),
        r"\Data\Streams\0114\Output\STRM_UPP\KMX\MIXED\VAPOR": _FakeNode(
            0.0951764667, "kcal-m/hr-sqm-K"
        ),
        r"\Data\Streams\0114\Output\STRM_UPP\MUMX\MIXED": _FakeNode(
            1, "cP", {"VAPOR": _FakeNode(0.0561478853, "cP"), "LIQUID": _no_value("cP")}
        ),
        r"\Data\Streams\0114\Output\STRM_UPP\MUMX\MIXED\VAPOR": _FakeNode(0.0561478853, "cP"),
        # 液相流股:反过来,VAPOR 空、LIQUID 有值
        r"\Data\Streams\0117\Output\MW": _FakeNode(18.01528),
        r"\Data\Streams\0117\Output\STRM_UPP\CPMX\MIXED": _FakeNode(
            1,
            "cal/mol-K",
            {"VAPOR": _no_value(), "LIQUID": _FakeNode(17.5697346, "cal/mol-K")},
        ),
        r"\Data\Streams\0117\Output\STRM_UPP\CPMX\MIXED\LIQUID": _FakeNode(17.5697346, "cal/mol-K"),
        r"\Data\Streams\0117\Output\STRM_UPP\KMX\MIXED": _FakeNode(
            1,
            "kcal-m/hr-sqm-K",
            {
                "VAPOR": _no_value("kcal-m/hr-sqm-K"),
                "LIQUID": _FakeNode(0.534999228, "kcal-m/hr-sqm-K"),
            },
        ),
        r"\Data\Streams\0117\Output\STRM_UPP\KMX\MIXED\LIQUID": _FakeNode(
            0.534999228, "kcal-m/hr-sqm-K"
        ),
        r"\Data\Streams\0117\Output\STRM_UPP\MUMX\MIXED": _FakeNode(
            1, "cP", {"VAPOR": _no_value("cP"), "LIQUID": _FakeNode(0.734702749, "cP")}
        ),
        r"\Data\Streams\0117\Output\STRM_UPP\MUMX\MIXED\LIQUID": _FakeNode(0.734702749, "cP"),
    }
    session = _FakeSession(nodes)

    gas = reader.read_stream_hx_props(session, "0114")
    check_close("换热物性: 气相 cp = 1574.1 J/(kg·K)", gas["cp_jkgk"], 1574.13, 0.5)
    check_close("换热物性: 气相 λ = 0.1107 W/(m·K)", gas["k_wmk"], 0.110690, 0.001)
    check_close("换热物性: 气相 μ = 0.05615 cP", gas["mu_cp"], 0.056148, 1e-4)
    check_eq("换热物性: 气相路径命中 VAPOR", gas["paths"]["cp"].endswith("MIXED\\VAPOR"), True)
    check_eq("换热物性: 气相无单位缺口", gas["unit_gaps"], [])

    liquid = reader.read_stream_hx_props(session, "0117")
    check_close("换热物性: 液相 cp = 4080.5 J/(kg·K)", liquid["cp_jkgk"], 4080.52, 0.5)
    check_close("换热物性: 液相 λ = 0.6222 W/(m·K)", liquid["k_wmk"], 0.622204, 0.001)
    check_close("换热物性: 液相 μ = 0.7347 cP", liquid["mu_cp"], 0.734703, 1e-4)

    # 目录节点值恒为 1,不能当成读数(老坑:1 cal/mol-K 会被当 cp)
    check_eq("换热物性: 目录节点 1 不算读数", gas["cp_jkgk"] > 1000, True)


def test_heat_heater_side_streams() -> None:
    """④ Heater 连接的 side 是空串,必须归到 process 侧,不能整片漏掉。

    实测(2026-11-24,工况 77):HeatX 连接写 hot/cold,Heater 连接 side 为空。
    老 `heat_side_streams` 只认 hot/cold,导致 Heater 物性一条都读不到。
    """
    from app.aspen import reader

    blocks = [
        {"name": "E0102", "type": "Heater"},
        {"name": "E0104", "type": "HeatX"},
    ]
    connections = [
        {"block": "E0102", "side": "", "direction": "in", "stream": "0108"},
        {"block": "E0102", "side": "", "direction": "out", "stream": "0109"},
        {"block": "E0104", "side": "hot", "direction": "in", "stream": "0114"},
        {"block": "E0104", "side": "cold", "direction": "in", "stream": "0117"},
    ]

    heater = reader.heat_side_streams(blocks, connections, "E0102")
    check_eq("Heater 空侧归到 process", heater, {"process": ["0108", "0109"]})

    heatx = reader.heat_side_streams(blocks, connections, "E0104")
    check_eq("HeatX 仍按 hot/cold", heatx, {"hot": ["0114"], "cold": ["0117"]})

    everything = reader.heat_side_streams(blocks, connections)
    check_eq("不指定 block 时两侧都在", set(everything), {"process", "hot", "cold"})


def test_heat_streamless_side_not_missing() -> None:
    """⑤ Heater 公用工程侧没有流股,顶层不能算"缺物性"。"""
    from app.api import hx_props

    block = {"name": "E0102", "duty_kw": 56.86}
    sides = {
        "process": {
            "stream": "0108",
            "t_in_c": 20.0,
            "t_out_c": 35.0,
            "mass_flow_kgh": 1000.0,
            "vap_in": 0.0,
            "vap_out": 0.0,
        },
        "utility": {"stream": None},
    }
    cached = {"0108": {"mu_cp": 0.0109, "cp_jkgk": 2198.9, "k_wmk": 0.0334}}
    payload = hx_props._payload(block, sides, cached, "case", None)
    check_eq("无流股侧不进顶层缺失", payload["missing"], [])
    check_eq("顶层判定完整", payload["complete"], True)

    missing_payload = hx_props._payload(block, sides, {}, "case", None)
    # cp 由 Q/m/ΔT 反推,λ/μ 仍缺
    check_eq("cp 缺失时走反推", missing_payload["sides"]["process"]["source"]["cp"], "derived")
    check_eq("有流股但读不到值仍要报缺失", missing_payload["missing"], ["λ", "μ"])


def test_heat_heat_stream_filter() -> None:
    """⑥ HEAT 热流股(如 Q1~Q4)不能当工艺侧:它没有 cp/λ/μ 节点。

    实测(2026-11-24,工况 77):E0202 同时挂物料 0210→0211 和一条 HEAT 进 Q1,
    老逻辑按连接顺序把工艺侧流股名覆盖成 Q1,然后永远报缺三项。
    """
    from app.aspen import reader
    from app.design.heat import params

    block = {"name": "E0202", "type": "Heater", "inlet_temp_c": None, "outlet_temp_c": None}
    connections = [
        {"block": "E0202", "side": "", "direction": "in", "stream": "0210"},
        {"block": "E0202", "side": "", "direction": "out", "stream": "0211"},
        {"block": "E0202", "side": "", "direction": "in", "stream": "Q1"},
    ]
    streams = [
        {"name": "0210", "stream_type": "MATERIAL", "temperature_c": 50.0, "vap_frac": 0.0},
        {"name": "0211", "stream_type": "MATERIAL", "temperature_c": 80.0, "vap_frac": 0.0},
        {"name": "Q1", "stream_type": "HEAT"},
    ]

    sides = params.build_sides(block, connections, streams)
    check_eq("build_sides 工艺侧取物料流股", sides["process"]["stream"], "0210")

    material_names = {"0210", "0211"}
    wanted = reader.heat_side_streams([block], connections, "E0202", material_names=material_names)
    check_eq("heat_side_streams 滤掉 HEAT 流股", wanted, {"process": ["0210", "0211"]})

    # 不传 material_names 时保持旧行为(向后兼容)
    wanted_all = reader.heat_side_streams([block], connections, "E0202")
    check_eq("不过滤时三条都在", wanted_all, {"process": ["0210", "0211", "Q1"]})


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


def test_heat_channels() -> None:
    """⑫ 双通道归类:HeatX 走工艺-工艺,Heater 走工艺-公用工程,零负荷 Heater 过滤。"""
    from app.design.heat import channels

    check_eq("channel: HeatX 归工艺-工艺", channels.channel_of("HeatX"), "process-process")
    check_eq("channel: MHeatX 归工艺-工艺", channels.channel_of("MHeatX"), "process-process")
    check_eq("channel: Heater 归工艺-公用工程", channels.channel_of("Heater"), "process-utility")
    check_eq("channel: HeaterX 归工艺-公用工程", channels.channel_of("HeaterX"), "process-utility")
    check_eq("channel: 非换热类型不归通道", channels.channel_of("RadFrac"), None)

    check_eq("channel: 零负荷 Heater 被过滤", channels.is_trivial("Heater", 0.0004), True)
    check_eq("channel: 正常 Heater 不过滤", channels.is_trivial("Heater", 4.75), False)
    check_eq("channel: HeatX 不按零负荷跳过", channels.is_trivial("HeatX", 0.0), False)
    check_eq("channel: 热负荷缺失不跳过(要报缺数据)", channels.is_trivial("Heater", None), False)

    check_eq(
        "channel: 汽化率 1→0 判冷凝",
        channels.side_phase({"vap_in": 1.0, "vap_out": 0.0}),
        "condensing",
    )
    check_eq(
        "channel: 汽化率 0→1 判沸腾",
        channels.side_phase({"vap_in": 0.0, "vap_out": 1.0}),
        "boiling",
    )
    check_eq(
        "channel: 两侧都是气判气体",
        channels.side_phase({"vap_in": 1.0, "vap_out": 1.0}),
        "gas",
    )
    check_eq(
        "channel: 进出口液相判液体",
        channels.side_phase({"vap_in": 0.0, "vap_out": 0.0}),
        "liquid",
    )

    key, reason = channels.pick_u_key(
        "process-process",
        {"hot": {"vap_in": 1.0, "vap_out": 0.0}, "cold": {"vap_in": 0.0, "vap_out": 0.0}},
    )
    check_eq("channel: 冷凝+液体 → condensing-organic", key, "condensing-organic")
    check("channel: 选档理由写清两侧相态", "冷凝" in reason and "液体" in reason, reason)

    key, _reason = channels.pick_u_key(
        "process-utility",
        {"process": {"vap_in": 0.0, "vap_out": 0.0}},
        {"phase": "condensing", "fluid": "steam"},
    )
    check_eq("channel: 液体工艺侧 + 蒸汽冷凝 → condensing-steam", key, "condensing-steam")

    key, _reason = channels.pick_u_key(
        "process-utility",
        {"process": {"vap_in": 1.0, "vap_out": 1.0}},
        {"phase": "liquid", "fluid": "water"},
    )
    check_eq("channel: 气体工艺侧 + 冷却水 → gas-liquid", key, "gas-liquid")


def test_heat_auto_u_heater() -> None:
    """⑬ Heater 自动补 U:工艺侧相态 + 公用工程介质 → 经验档,能算出需求面积。"""
    from app.design.heat import params

    block = _heater_block(
        duty_kw=411.05,
        inlet_temp_c=31.5,
        outlet_temp_c=90.0,
        outlet_vap_frac=0.0,
        util_id="MS",
        util_type="steam",
        util_t_in_c=175.0,
        util_t_out_c=175.0,
    )
    connections = [
        {"block": "E0101", "side": "", "direction": "in", "stream": "P_IN"},
        {"block": "E0101", "side": "", "direction": "out", "stream": "P_OUT"},
    ]
    streams = [
        _stream("P_IN", 31.5, 3, 5000, vap=0.0),
        _stream("P_OUT", 90.0, 3, 5000, vap=0.0),
    ]

    case = params.build_design(block, connections, streams)
    check_eq("auto-u: Heater 归工艺-公用工程通道", case["channel"], "process-utility")
    check_eq("auto-u: 来源标 empirical", case["u"]["source"], "empirical")
    check_eq("auto-u: 选中蒸汽冷凝档", case["u"]["auto_key"], "condensing-steam")
    check_close("auto-u: 经验 U 取 1500", case["u"]["assumed_wm2k"], 1500.0, 1e-9)
    check("auto-u: 需求面积算出来了", case["area"]["required_m2"] is not None, str(case["area"]))
    check("auto-u: 有 U_AUTO 警告", "U_AUTO" in case["warnings"], str(case["warnings"]))
    check_eq("auto-u: 判定 reference", case["verdict"], "reference")

    # 人工指定档优先于自动挑档
    case2 = params.build_design(block, connections, streams, u_ref_key="organic-water")
    check_eq("auto-u: 人工档优先", case2["u"]["source"], "assumed")
    check_close("auto-u: 人工档取 700", case2["u"]["assumed_wm2k"], 700.0, 1e-9)


def test_heat_trivial_filter() -> None:
    """⑭ 零负荷 Heater 不进设备表;正常 Heater 与 HeatX 保留。"""
    from app.design.heat import assemble

    blocks = [
        _heater_block(name="E0000", duty_kw=0.0),
        _heater_block(name="E0101"),
        _heatx_block(name="EX0101"),
    ]
    rows = assemble.assemble(blocks, [], [], {}, {}, {})
    check_eq("filter: 只留 2 台", len(rows), 2)
    check_eq("filter: 零负荷 Heater 被跳过", [r["block"] for r in rows], ["E0101", "EX0101"])
