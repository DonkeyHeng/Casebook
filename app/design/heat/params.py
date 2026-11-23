"""L1 换热器工况整理与计算(纯函数,不碰 Aspen / 不读盘)。

输入都是普通 dict / sqlite3.Row(调用方从库里取好、从 Aspen 读好再传进来),
输出是一份可直接映射到 `hx_design` 的字典,外加拦截原因与溯源标签。

公式与口径见 `docs/heat-exchanger-selection-plan.md` §5、§6、§7:

* ΔTm 默认按**逆流**算,`F` 未修正时 `F=1` 并标 `f_uncorrected`;
* 数值上 ℃ 温差 = K,不做 +273.15;
* 热负荷取绝对值算 U,符号单独保留;
* 任一输入是假设 → 判定只能是 `reference`,Phase 1 永不输出"合格/不合格"。
"""

from __future__ import annotations

import math

from . import data

# 相变判据:进出口汽化率变化超过这个值就当有相变(体积分数,0~1)
PHASE_EPS = 1e-4
# 近等温判据:单侧进出口温差 <= 这个值(℃)时,纯显热 LMTD 退化,必须提示
NEAR_ISOTHERMAL_DT = 5.0
# 温度交叉判据:逆流端差 <= 这个值(℃)视为交叉/夹点,不给 LMTD
TEMP_CROSS_DT = 1.0
# 同一设备两侧端差相对差小于这个比例时,用算术平均代替 LMTD(避免 0/0)
LMTD_FLAT_RATIO = 1e-3
# 设计温度默认附加值(℃),方案 §5.5 允许 15~30
DESIGN_T_DEFAULT_OFFSET = 20.0
# 设计压力:取操作压力 ×1.1 与 操作压力 +1 bar 的较大者(方案 §5.4)
DESIGN_P_FACTOR = 1.1
DESIGN_P_MARGIN_BAR = 1.0
# 允许压降默认:气体 0.1×P,液体 0.2×P,但不低于 0.2 bar(方案 §5.6)
DP_ALLOW_GAS_RATIO = 0.10
DP_ALLOW_LIQUID_RATIO = 0.20
DP_ALLOW_FLOOR_BAR = 0.2

# 拦截代码 → 中文提示(界面、导出共用)
BLOCKER_TEXT = {
    "PHASE_CHANGE": "工艺侧进出口汽化率变化,属相变工况;纯显热 LMTD 不适用,请转 EDR 分段计算",
    "NEAR_ISOTHERMAL_PROCESS": "工艺侧近等温,端差退化;纯显热 LMTD 不可靠,请转 EDR",
    "TEMP_CROSS": "逆流端差为负或接近零(温度交叉),LMTD 无定义,请转 EDR",
    "MISSING_PROCESS_TEMPS": "工艺侧进出口温度不全,无法算 LMTD",
    "MISSING_DUTY": "设备热负荷缺失,无法算 U 与面积需求",
    "MULTI_STREAM": "多股/多侧换热(MHeatX 或多端口),L1 不处理",
    "UTILITY_NO_TEMP": "公用工程既无实测温度也未选预设/手填,无法算 LMTD",
    "UTILITY_ELECTRIC": "电加热/燃料公用工程没有流体温度,LMTD 无意义",
    "NO_SIDES": "未取到冷热两侧流股,无法建立工况",
    "TUBE_FLUID_ILLEGAL": (
        "管程侧分配与设备类型不符(HeatX 只能 hot/cold,Heater 只能 process/utility)"
    ),
}
# 警告(不拦截,只降级)
WARNING_TEXT = {
    "UTILITY_PRESET": "公用工程温度来自预设假设,不是实测",
    "UTILITY_MANUAL": "公用工程温度为人工录入",
    "UTILITY_ISOTHERMAL": "公用工程为等温相变,已按等温端差处理",
    "U_ASSUMED": "总传热系数取经验值,面积需求仅供参考",
    "AREA_ASSUMED": "面积来自 Aspen 简捷假设(非 rating 结果)",
    "F_UNCOVERED": "F 修正系数未取得,按纯逆流(F=1)计算",
    "TUBE_FLUID_RULE": "管/壳程分配按规则建议,未经用户确认",
    "DTLM_FROM_ASPEN": "平均温差直接取 Aspen HX_DTLM,未独立复算",
}


def _get(row, key, default=None):
    """同时兼容 dict 与 sqlite3.Row 的取值;取不到返回 default。"""
    if row is None:
        return default
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _num(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def lmtd(th_in: float, th_out: float, tc_in: float, tc_out: float) -> tuple[float | None, str]:
    """逆流对数平均温差。返回 (值, 说明);不能算时值为 None。

    说明取值: ok / flat(两端相等,用算术平均) / cross(端差 <= 0)。
    """
    d1 = th_in - tc_out
    d2 = th_out - tc_in
    if d1 <= 0 or d2 <= 0:
        return None, "cross"
    if abs(d1 - d2) <= LMTD_FLAT_RATIO * min(d1, d2):
        return (d1 + d2) / 2, "flat"
    return (d1 - d2) / math.log(d1 / d2), "ok"


def design_pressure(p_op_bar: float | None) -> float | None:
    """设计压力 = max(1.1×P_op, P_op+1 bar);P_op 缺失返回 None。"""
    p = _num(p_op_bar)
    if p is None:
        return None
    return round(max(p * DESIGN_P_FACTOR, p + DESIGN_P_MARGIN_BAR), 2)


def design_temperature(t_max_c: float | None, offset: float | None = None) -> float | None:
    """设计温度 = 最高操作温度 + 附加值(默认 20 ℃,允许 15~30)。"""
    t = _num(t_max_c)
    if t is None:
        return None
    off = _num(offset)
    if off is None:
        off = DESIGN_T_DEFAULT_OFFSET
    if not 15.0 <= off <= 30.0:
        off = DESIGN_T_DEFAULT_OFFSET
    return round(t + off, 1)


def allowable_dp(p_op_bar: float | None, phase: str | None) -> float | None:
    """允许压降估算:气体 10%P、液体 20%P,下限 0.2 bar。"""
    p = _num(p_op_bar)
    if p is None:
        return None
    ratio = DP_ALLOW_GAS_RATIO if phase == "gas" else DP_ALLOW_LIQUID_RATIO
    return round(max(p * ratio, DP_ALLOW_FLOOR_BAR), 3)


# ---------------------------------------------------------------- 物性(Phase 2)
#
# L2 算 h / Re / Pr 要 cp、λ、μ 三项。Aspen 未必挂物性集(实测本机 STRM_UPP
# 只有 PL / MUMX / PBUB),所以:
#   ① 读到的就用,标 aspen;
#   ② cp 读不到时,允许用 |Q|/(m·|ΔT|) 反推一份,标 derived —— 它把热损、潜热
#      都算进显热,只能在"单相 + 有实测 Q/m/ΔT"时当校核值;
#   ③ λ 与 μ 没有反推途径,读不到就留空(L1 不受影响)。

#: 反推 cp 的门槛(低于这个就当没有工况信息,不反推)
CP_DERIVE_MIN_DUTY_KW = 0.5
CP_DERIVE_MIN_DT_K = 2.0
#: 单相判据:汽化率低于这个值就当全是液相;None 按"未知但允许"处理(相变另有拦截)
CP_DERIVE_MAX_VAP = 0.02

PROP_TEXT = {
    "aspen": "Aspen 实测",
    "derived": "由 Q/(m·ΔT) 反推",
    "manual": "人工录入",
    "missing": "未取得",
}


def cp_from_duty(
    duty_kw: float | None, mass_flow_kgh: float | None, dt_c: float | None
) -> float | None:
    """单相由能量平衡反推 cp(J/(kg·K));条件不足返回 None,不猜。

    口径:cp = |Q| / (m · |ΔT|);Q 取设备热负荷绝对值,m 为该侧质量流量。
    相变工况不要用它(潜热会被折成显热,结果偏大一个量级)—— 相变已被 §6 拦截。
    """
    q_kw, m, dt = _num(duty_kw), _num(mass_flow_kgh), _num(dt_c)
    if q_kw is None or m is None or dt is None:
        return None
    if abs(q_kw) < CP_DERIVE_MIN_DUTY_KW or m <= 0 or abs(dt) < CP_DERIVE_MIN_DT_K:
        return None
    cp = abs(q_kw) * 1000.0 / ((m / 3600.0) * abs(dt))
    return round(cp, 1)


def side_props(side: dict | None, stored: dict | None = None, duty_kw: float | None = None) -> dict:
    """把"库里缓存的物性 + 可反推的 cp"整理成一条侧物性。

    Args:
        side: `build_sides()` 的一条侧记录(要 t_in_c / t_out_c / mass_flow_kgh / vap_*)
        stored: `hx_props` 的一行(dict),可缺键
        duty_kw: 设备热负荷(kW),反推 cp 用

    返回:
        {mu_cp, cp_jkgk, k_wmk, source:{mu,cp,k}, missing:[...], notes:[...]}
    """
    stored = stored or {}
    out = {
        "mu_cp": _num(stored.get("mu_cp")),
        "cp_jkgk": _num(stored.get("cp_jkgk")),
        "k_wmk": _num(stored.get("k_wmk")),
        "source": {
            "mu": "aspen" if _num(stored.get("mu_cp")) is not None else "missing",
            "cp": "aspen" if _num(stored.get("cp_jkgk")) is not None else "missing",
            "k": "aspen" if _num(stored.get("k_wmk")) is not None else "missing",
        },
        "missing": [],
        "notes": [],
    }

    if out["cp_jkgk"] is None and side:
        vap_in = _num(side.get("vap_in"))
        vap_out = _num(side.get("vap_out"))
        single_phase = all(v is None or v <= CP_DERIVE_MAX_VAP for v in (vap_in, vap_out))
        dt = None
        if side.get("t_in_c") is not None and side.get("t_out_c") is not None:
            dt = float(side["t_in_c"]) - float(side["t_out_c"])
        if single_phase:
            derived = cp_from_duty(duty_kw, side.get("mass_flow_kgh"), dt)
            if derived is not None:
                out["cp_jkgk"] = derived
                out["source"]["cp"] = "derived"
                out["notes"].append("cp 由 Q/(m·ΔT) 反推,仅作校核值")

    for key, name in (("cp_jkgk", "cp"), ("k_wmk", "λ"), ("mu_cp", "μ")):
        if out[key] is None:
            out["missing"].append(name)
    return out


def _side_from_stream(stream) -> dict:
    return {
        "stream": _get(stream, "name"),
        "t_in_c": _num(_get(stream, "temperature_c")),
        "t_out_c": None,
        "p_bar": _num(_get(stream, "pressure_bar")),
        "mass_flow_kgh": _num(_get(stream, "mass_flow_kgh")),
        # 体积流量:管程流速与接管口径要用;质量流量算不出流速(缺密度)
        "vol_flow_m3h": _num(_get(stream, "vol_flow_m3h")),
        "vap_in": _num(_get(stream, "vap_frac")),
        "vap_out": None,
        "corrosive": False,
        "fouling": False,
        "fluid": "unknown",
    }


def _merge_side(side: dict, stream) -> None:
    """把同一侧的进出口流股合成一条侧记录(温度/汽化率按方向取)。"""
    if side.get("stream") is None:
        side["stream"] = _get(stream, "name")
    if side.get("t_in_c") is None:
        side["t_in_c"] = _num(_get(stream, "temperature_c"))
        side["vap_in"] = _num(_get(stream, "vap_frac"))
    else:
        side["t_out_c"] = _num(_get(stream, "temperature_c"))
        side["vap_out"] = _num(_get(stream, "vap_frac"))
    if side.get("p_bar") is None:
        side["p_bar"] = _num(_get(stream, "pressure_bar"))
    if side.get("mass_flow_kgh") is None:
        side["mass_flow_kgh"] = _num(_get(stream, "mass_flow_kgh"))
    if side.get("vol_flow_m3h") is None:
        side["vol_flow_m3h"] = _num(_get(stream, "vol_flow_m3h"))


def build_sides(block, connections, streams) -> dict:
    """按 block 与连接关系整理冷/热/工艺/公用工程侧。返回 {'hot','cold'} 或 {'process','utility'}。

    只做"取值与配对",不做任何假设补全 —— 缺失就是 None,由边界拦截报出来。
    """
    btype = _get(block, "type") or ""
    smap = {(_get(s, "name")): s for s in streams}

    if btype in ("HeatX", "MHeatX"):
        sides: dict[str, dict] = {}
        for conn in connections:
            if _get(conn, "block") != _get(block, "name"):
                continue
            side = _get(conn, "side") or ""
            if side not in ("hot", "cold"):
                continue
            stream = smap.get(_get(conn, "stream"))
            if stream is None:
                continue
            rec = sides.setdefault(side, _side_from_stream(stream))
            direction = _get(conn, "direction")
            if direction == "in":
                rec.setdefault("inlets", [])
                if _get(conn, "stream") not in rec["inlets"]:
                    rec["inlets"].append(_get(conn, "stream"))
                rec["t_in_c"] = _num(_get(stream, "temperature_c"))
                rec["vap_in"] = _num(_get(stream, "vap_frac"))
                if rec.get("p_bar") is None:
                    rec["p_bar"] = _num(_get(stream, "pressure_bar"))
                if rec.get("mass_flow_kgh") is None:
                    rec["mass_flow_kgh"] = _num(_get(stream, "mass_flow_kgh"))
                if rec.get("vol_flow_m3h") is None:
                    rec["vol_flow_m3h"] = _num(_get(stream, "vol_flow_m3h"))
            elif direction == "out":
                rec["t_out_c"] = _num(_get(stream, "temperature_c"))
                rec["vap_out"] = _num(_get(stream, "vap_frac"))
            else:
                _merge_side(rec, stream)
        return sides

    if btype in ("Heater", "HeaterX"):
        process = {
            "stream": None,
            "t_in_c": _num(_get(block, "inlet_temp_c")),
            "t_out_c": _num(_get(block, "outlet_temp_c")),
            "p_bar": None,
            "mass_flow_kgh": None,
            "vol_flow_m3h": None,
            "vap_in": None,
            "vap_out": _num(_get(block, "outlet_vap_frac")),
            "corrosive": False,
            "fouling": False,
            "fluid": "unknown",
        }
        for conn in connections:
            if _get(conn, "block") != _get(block, "name"):
                continue
            if (_get(conn, "side") or "") not in ("", None):
                continue
            stream = smap.get(_get(conn, "stream"))
            if stream is None:
                continue
            if _get(conn, "direction") == "in":
                process["stream"] = _get(stream, "name")
                if process["t_in_c"] is None:
                    process["t_in_c"] = _num(_get(stream, "temperature_c"))
                    process["vap_in"] = _num(_get(stream, "vap_frac"))
                if process["p_bar"] is None:
                    process["p_bar"] = _num(_get(stream, "pressure_bar"))
                if process["mass_flow_kgh"] is None:
                    process["mass_flow_kgh"] = _num(_get(stream, "mass_flow_kgh"))
        if process["p_bar"] is None:
            # 没有连接流股时退回块上的泵式压力字段(有的模型 HEATER 也带 P_IN/P_OUT)
            process["p_bar"] = _num(_get(block, "p_in_bar"))

        utility = {
            "stream": None,
            "t_in_c": _num(_get(block, "util_t_in_c")),
            "t_out_c": _num(_get(block, "util_t_out_c")),
            "p_bar": None,
            "mass_flow_kgh": None,
            "vol_flow_m3h": None,
            "vap_in": None,
            "vap_out": None,
            "corrosive": False,
            "fouling": True,  # 公用工程侧默认按易结垢/需可清洗考虑,仅作规则输入
            "fluid": _get(block, "util_type") or "unknown",
            "utility_id": _get(block, "util_id"),
        }
        return {"process": process, "utility": utility}
    return {}


def resolve_utility(block, presets, preset_key=None, manual=None) -> dict:
    """决定公用工程侧的温度与来源。

    优先级:aspen(节点读到) > manual(人工) > preset(预设键) > missing。
    manual 只在 aspen 缺失时生效;传了 manual 就封顶为 manual 来源。

    返回 {'t_in_c','t_out_c','phase','source','key','isothermal'}。
    source ∈ aspen / manual / preset / missing / undefined。
    """
    t_in = _num(_get(block, "util_t_in_c"))
    t_out = _num(_get(block, "util_t_out_c"))
    if t_in is not None and t_out is not None:
        fluid = str(_get(block, "util_type") or "")
        isothermal = abs(t_in - t_out) <= 1.0
        if isothermal:
            phase = "condensing" if fluid == "steam" else "liquid"
        else:
            phase = "gas" if fluid in ("steam", "air") else "liquid"
        return {
            "t_in_c": t_in,
            "t_out_c": t_out,
            "phase": phase,
            "source": "aspen",
            "key": None,
            "isothermal": isothermal,
        }

    if (
        manual
        and _num(manual.get("t_in_c")) is not None
        and _num(manual.get("t_out_c")) is not None
    ):
        mt_in, mt_out = _num(manual["t_in_c"]), _num(manual["t_out_c"])
        return {
            "t_in_c": mt_in,
            "t_out_c": mt_out,
            "phase": manual.get("phase")
            or ("condensing" if abs(mt_in - mt_out) <= 1.0 else "liquid"),
            "source": "manual",
            "key": None,
            "isothermal": abs(mt_in - mt_out) <= 1.0,
        }

    key = preset_key or data.utility_preset_suggestion(
        _get(block, "util_id"), _get(block, "util_type")
    )
    preset = data.utility_preset(presets, key)
    if preset:
        return {
            "t_in_c": preset["t_in_c"],
            "t_out_c": preset["t_out_c"],
            "phase": preset["phase"],
            "source": "preset",
            "key": key,
            "isothermal": preset["phase"] in ("condensing", "boiling")
            or abs(preset["t_in_c"] - preset["t_out_c"]) <= 1.0,
        }

    return {
        "t_in_c": None,
        "t_out_c": None,
        "phase": "unknown",
        "source": "undefined" if not _get(block, "util_id") else "missing",
        "key": None,
        "isothermal": None,
    }


def _lmtl_sides(sides: dict, utility: dict | None, duty_kw: float | None):
    """挑出算 LMTD 的热侧与冷侧。

    返回 (hot, cold, swap_note)。Heater 按热负荷符号判断:
    duty > 0 表示工艺侧被加热 → 工艺侧是冷侧、公用工程是热侧。
    """
    if "hot" in sides and "cold" in sides:
        return sides.get("hot"), sides.get("cold"), None
    process = sides.get("process")
    if process is None or utility is None:
        return None, None, None
    duty = _num(duty_kw)
    if duty is not None and duty < 0:
        return process, utility, "process-hot"
    return utility, process, "utility-hot"


def check_boundaries(sides: dict, hot: dict | None, cold: dict | None, utility: dict | None):
    """五态边界拦截。只拦工艺侧相变/近等温;等温相变公用工程放行。

    返回 (blockers, warnings);blockers 是拦截代码列表,warnings 是警告代码列表。
    """
    blockers: list[str] = []
    warnings: list[str] = []
    if not sides:
        return ["NO_SIDES"], warnings

    # 多股/多侧:同一侧进了多股(MHeatX 常见),L1 不处理
    for key in ("hot", "cold"):
        if len(sides.get(key, {}).get("inlets") or []) > 1:
            blockers.append("MULTI_STREAM")

    process_sides = [k for k in ("hot", "cold", "process") if k in sides]
    for key in process_sides:
        side = sides[key]
        if side.get("t_in_c") is None or side.get("t_out_c") is None:
            blockers.append("MISSING_PROCESS_TEMPS")
            continue
        vap_in, vap_out = side.get("vap_in"), side.get("vap_out")
        if vap_in is not None and vap_out is not None and abs(vap_out - vap_in) > PHASE_EPS:
            blockers.append("PHASE_CHANGE")
        if abs(side["t_in_c"] - side["t_out_c"]) <= NEAR_ISOTHERMAL_DT:
            blockers.append("NEAR_ISOTHERMAL_PROCESS")

    if utility is not None:
        uid = _get_utility_id(sides)
        if uid and _is_electric(uid):
            blockers.append("UTILITY_ELECTRIC")
        if utility.get("t_in_c") is None or utility.get("t_out_c") is None:
            if not blockers:
                blockers.append("UTILITY_NO_TEMP")
        elif utility.get("isothermal"):
            warnings.append("UTILITY_ISOTHERMAL")

    if hot is not None and cold is not None:
        th_in, th_out = hot.get("t_in_c"), hot.get("t_out_c")
        tc_in, tc_out = cold.get("t_in_c"), cold.get("t_out_c")
        if None not in (th_in, th_out, tc_in, tc_out) and (
            min(th_in - tc_out, th_out - tc_in) <= TEMP_CROSS_DT
        ):
            blockers.append("TEMP_CROSS")

    # 去重但保序
    blockers = list(dict.fromkeys(blockers))
    return blockers, warnings


def _get_utility_id(sides: dict) -> str | None:
    util = sides.get("utility") or {}
    return util.get("utility_id")


def _is_electric(uid: str | None) -> bool:
    return str(uid or "").upper().startswith("ELE")


def check_tube_fluid_boundaries(kind: str, value: str | None) -> list[str]:
    """管程分配越界检查;返回拦截代码(空列表 = 合法)。"""
    message = data.check_tube_fluid(kind, value)
    return ["TUBE_FLUID_ILLEGAL"] if message else []


def build_design(
    block,
    connections,
    streams,
    fouling_cfg=None,
    u_cfg=None,
    preset_cfg=None,
    fouling_tube_key=None,
    fouling_shell_key=None,
    u_ref_key=None,
    tube_fluid=None,
    tube_fluid_source=None,
    utility_preset_key=None,
    utility_manual=None,
    margin=0.30,
    f_correction=None,
    area_manual_m2=None,
) -> dict:
    """一台设备 → 一份 hx_design 草案(含计算、拦截、溯源)。

    这是 L1 的主入口:纯函数,只依赖入参。返回结构见函数末尾 `return`。
    """
    name = _get(block, "name")
    btype = _get(block, "type") or ""
    duty_kw = _num(_get(block, "duty_kw"))
    sides = build_sides(block, connections, streams)
    kind = "HeatX" if btype in ("HeatX", "MHeatX") else btype

    utility = None
    if "utility" in sides:
        utility = resolve_utility(block, preset_cfg, utility_preset_key, utility_manual)
        sides["utility"] = {**sides["utility"], **utility}

    hot, cold, swap = _lmtl_sides(sides, utility, duty_kw)
    blockers, warnings = check_boundaries(sides, hot, cold, utility)
    blockers.extend(check_tube_fluid_boundaries(kind, tube_fluid))

    if duty_kw is None:
        blockers.append("MISSING_DUTY")

    # 管/壳程分配:未指定时按规则建议,并标 rule
    if tube_fluid is None and sides:
        suggestion = data.tube_fluid_suggestion(
            kind,
            {k: dict(v) for k, v in sides.items() if k in ("hot", "cold", "process", "utility")},
            utility_phase=(utility or {}).get("phase"),
        )
        tube_fluid = suggestion["value"]
        tube_fluid_source = "rule"
        warnings.append("TUBE_FLUID_RULE")

    # 设计条件(按管/壳程映射)
    tube_side = sides.get(tube_fluid) if tube_fluid else None
    shell_side = None
    if tube_fluid:
        others = [
            k for k in ("hot", "cold", "process", "utility") if k in sides and k != tube_fluid
        ]
        shell_side = sides.get(others[0]) if others else None

    def _conditions(side):
        if not side:
            return {
                "p_op_bar": None,
                "p_design_bar": None,
                "t_design_c": None,
                "dp_allow_bar": None,
            }
        temps = [t for t in (side.get("t_in_c"), side.get("t_out_c")) if t is not None]
        t_max = max(temps) if temps else None
        phase = "gas" if side.get("vap_in", 0) and side.get("vap_in") >= 0.5 else "liquid"
        return {
            "p_op_bar": side.get("p_bar"),
            "p_design_bar": design_pressure(side.get("p_bar")),
            "t_design_c": design_temperature(t_max),
            "dp_allow_bar": allowable_dp(side.get("p_bar"), phase),
        }

    tube_cond = _conditions(tube_side)
    shell_cond = _conditions(shell_side)

    # 污垢热阻(按管/壳程查表;查不到就是 None,不猜档)
    fouling_tube = data.fouling_value(fouling_cfg, fouling_tube_key)
    fouling_shell = data.fouling_value(fouling_cfg, fouling_shell_key)

    # 面积与 U
    area_aspen = _num(_get(block, "area_m2"))
    area_manual = _num(area_manual_m2)
    u_aspen = _num(_get(block, "hx_u_design_wm2k"))
    ua_aspen = _num(_get(block, "ua_wk"))
    dtlm_aspen = _num(_get(block, "hx_dtlm_c"))
    f_aspen = _num(_get(block, "hx_f_factor"))
    f = _num(f_correction) if _num(f_correction) is not None else (f_aspen or 1.0)
    if f_aspen is None and f_correction is None:
        warnings.append("F_UNCOVERED")

    # LMTD:优先自算(可复算),Aspen 值只做交叉对照
    dtlm_self = None
    dtlm_note = None
    if hot is not None and cold is not None:
        th_in, th_out = hot.get("t_in_c"), hot.get("t_out_c")
        tc_in, tc_out = cold.get("t_in_c"), cold.get("t_out_c")
        if None not in (th_in, th_out, tc_in, tc_out):
            dtlm_self, dtlm_note = lmtd(th_in, th_out, tc_in, tc_out)
            if dtlm_self is None:
                blockers.append("TEMP_CROSS")
    dtlm = dtlm_self if dtlm_self is not None else dtlm_aspen
    dtlm_source = "computed" if dtlm_self is not None else ("aspen" if dtlm_aspen else "none")
    if dtlm_source == "aspen":
        warnings.append("DTLM_FROM_ASPEN")

    # 反算 U 与面积需求
    u_back = None
    area_req = None
    q_w = abs(duty_kw) * 1000 if duty_kw is not None else None
    if q_w and area_aspen and dtlm and f:
        u_back = q_w / (area_aspen * dtlm * f)
    u_assumed = None
    if u_aspen:
        u_source = "aspen_design"
    else:
        ref = data.u_reference(u_cfg, u_ref_key)
        if ref:
            u_assumed = ref["default"]
            u_source = "assumed"
            warnings.append("U_ASSUMED")
        else:
            u_source = "none"
    u_for_req = u_aspen or u_assumed or u_back
    if q_w and u_for_req and dtlm and f:
        area_req = q_w / (u_for_req * dtlm * f) * (1 + (_num(margin) or 0.0))

    area = area_aspen or area_manual
    if area_aspen:
        area_source = "aspen"
        warnings.append("AREA_ASSUMED")  # Aspen 简捷面积 = 假设 U 算出来的,不是 rating
    elif area_manual:
        area_source = "manual"
    else:
        area_source = "none"

    # 判定:Phase 1 只到 reference,永不给合格
    if blockers:
        verdict, verdict_note = "blocked", "存在拦截项,未进入 L1 计算"
    elif area is None and area_req is None:
        verdict, verdict_note = "unknown", "面积与需求面积都缺失"
    else:
        verdict, verdict_note = "reference", "L1 参考结果(未做 EDR rating)"

    if utility is not None:
        if utility["source"] == "preset":
            warnings.append("UTILITY_PRESET")
        elif utility["source"] == "manual":
            warnings.append("UTILITY_MANUAL")

    return {
        "block": name,
        "kind": kind,
        "duty_kw": duty_kw,
        "sides": sides,
        "hot": hot,
        "cold": cold,
        "swap_note": swap,
        "utility": utility,
        "tube_fluid": tube_fluid,
        "tube_fluid_source": tube_fluid_source,
        "design": {
            "tube": tube_cond,
            "shell": shell_cond,
            "t_design_offset_c": DESIGN_T_DEFAULT_OFFSET,
        },
        "fouling": {
            "tube": fouling_tube,
            "shell": fouling_shell,
            "tube_key": fouling_tube_key,
            "shell_key": fouling_shell_key,
        },
        "area": {
            "aspen_m2": area_aspen,
            "manual_m2": area_manual,
            "used_m2": area,
            "required_m2": area_req,
            "source": area_source,
            "margin": _num(margin),
        },
        "u": {
            "aspen_wm2k": u_aspen,
            "back_calc_wm2k": round(u_back, 1) if u_back else None,
            "assumed_wm2k": u_assumed,
            "source": u_source,
            "ref_key": u_ref_key,
            "ua_wk": ua_aspen,
        },
        "lmtd": {
            "self_c": round(dtlm_self, 2) if dtlm_self is not None else None,
            "aspen_c": dtlm_aspen,
            "used_c": round(dtlm, 2) if dtlm is not None else None,
            "source": dtlm_source,
            "note": dtlm_note,
            "f": f,
        },
        "verdict": verdict,
        "verdict_note": verdict_note,
        "blockers": blockers,
        "blocker_text": [BLOCKER_TEXT.get(b, b) for b in blockers],
        "warnings": list(dict.fromkeys(warnings)),
        "warning_text": [WARNING_TEXT.get(w, w) for w in dict.fromkeys(warnings)],
        "provenance": {
            "area": area_source,
            "utility": (utility or {}).get("source") if utility else "none",
            "u": u_source,
            "lmtd": dtlm_source,
        },
    }
