# -*- coding: utf-8 -*-
"""app/design/pipe.py —— 管路选型计算(纯函数,不碰 Aspen、不读写库)。

口径来源:`docs/pipe-selection-plan.md` §14(定稿口径)与 §15(开工清单第 3 步)。
标准数据在 `app/design/pipe_data.py`;本模块只做算术,不做判断之外的猜测。

十步流程(每步一个函数,便于单测与写"计算书")
------------------------------------------------
1. `phase_of()`            判相态(液 / 气 / 气液共存)
2. `medium_code()`         介质代号(PG / PL / PGL,可扩展)
3. `velocity_for()`        选流速(液 / 气 / 两相 / 泵入口)
4. `inner_diameter()`      内径 → `pick_dn()` 向上取 DN
5. `design_pressure()`     设计压力(表压 ×1.1,下限 1.0 MPa;真空单独处理)
6. `design_temperature()`  设计温度(保温取介质温度;65–80 ℃ 无保温折 0.95)
7. `allowable_stress()`    许用应力(牌号 + 温度查表,缺温度内插,T<40 取 40 ℃)
8. `calc_wall_thickness()` t_c = P·D_o / [2(S·Φw + P·Y)]
9. `round_up_wall()`       加附加量后圆整到标准壁厚
10. `grade_code()`         管道等级号 + 绝热代号

单位约定(内部一律这套,界面再换显示)
------------------------------------
温度 ℃、压力 MPa(绝对/表压明确标注)、外径与壁厚 mm、体积流量 m³/h、流速 m/s。

为什么表数据要"注入"
--------------------
GB/T 17395 表 1/表 4 与 GB/T 20801.1 表 B.1 的文字层是乱码,只能看图录入,
目前还没录回来。所以本模块**不内置任何标准数值**,全部通过 `tables` 参数传入,
这样:①现在就能写测试和验证算术;②数据回来只需填 `pipe_data.py` 的三个字典;
③不会出现"代码里偷偷写了个记不清的数"。
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

from app.design import pipe_data

# 相态阈值:痕量汽化率(库里实测有 1e-8)不能按 ==0 判
PHASE_EPS = 1e-4

# 默认流速(m/s)。来源:§14.1 第 3 步用户定的口径。
DEFAULT_VELOCITY = {
    "liquid": 1.5,   # 1.0–2.5
    "gas": 15.0,     # 10–25
    "two": 8.0,      # 5–10
    "pump_inlet": 0.8,  # 0.5–1.0
}


class TablesNotReady(RuntimeError):
    """标准表还没录入时的明确报错(而不是悄悄用 0 算出一张表)。"""


# --------------------------------------------------------------------------
# 1–3. 相态 / 介质代号 / 流速
# --------------------------------------------------------------------------

def phase_of(vap_frac: float | None) -> str:
    """汽化率 → `liquid` / `gas` / `two`。

    用容差而不是 `== 0`:库里实测出现过 1e-8 量级的"痕量汽化率",
    按 0 判会把全液流股误判;None 按全液处理(缺数据会在调用侧标出)。
    """
    if vap_frac is None:
        return "liquid"
    if vap_frac < PHASE_EPS:
        return "liquid"
    if vap_frac > 1.0 - PHASE_EPS:
        return "gas"
    return "two"


def medium_code(phase: str, extra: Mapping[str, str] | None = None) -> str:
    """介质代号:HG/T 20519.6 风格,P + 相态。公用工程等特殊介质走 `extra` 覆盖。"""
    if extra and phase in extra:
        return extra[phase]
    return {"liquid": "PL", "gas": "PG", "two": "PGL"}.get(phase, "PL")


def velocity_for(phase: str, is_pump_inlet: bool = False,
                 options: Mapping[str, Any] | None = None) -> float:
    """选流速(m/s)。泵入口单独一档,不能用管道的常规液速。"""
    opts = options or {}
    v = dict(DEFAULT_VELOCITY)
    v.update(opts.get("velocity") or {})
    if is_pump_inlet:
        return float(v["pump_inlet"])
    return float(v[phase])


# --------------------------------------------------------------------------
# 4. 内径与 DN
# --------------------------------------------------------------------------

def inner_diameter(vol_flow_m3h: float, velocity_ms: float) -> float:
    """连续性方程:D_i = √(4Q/(πv)),返回 mm。Q 用 m³/h、v 用 m/s。"""
    if velocity_ms <= 0:
        raise ValueError(f"流速必须为正:{velocity_ms!r}")
    q_m3s = vol_flow_m3h / 3600.0
    d_m = math.sqrt(4.0 * q_m3s / (math.pi * velocity_ms))
    return d_m * 1000.0


def pick_dn(inner_d_mm: float, dn_to_od: Mapping[int, float]) -> tuple[int | None, float | None]:
    """向上取 DN(公称直径按"不小于计算内径"处理),返回 (DN, 公称外径)。

    `dn_to_od` 是 DN→外径表(化工 Φ 系列)。表还没录时返回 (None, None),
    调用侧要把它记成"缺数据,未选型",不能当成 0。
    """
    if not dn_to_od:
        return None, None
    candidates = sorted(d for d in dn_to_od if d >= inner_d_mm - 1e-9)
    if not candidates:
        return None, None
    dn = candidates[0]
    return dn, float(dn_to_od[dn])


# --------------------------------------------------------------------------
# 5–7. 设计压力 / 设计温度 / 许用应力
# --------------------------------------------------------------------------

ATM_MPA = 0.1013
VACUUM_DESIGN_MPA = 0.1
#: 常压死区:表压在 ±0.01 MPa(0.1 bar)以内按"常压"处理,不判真空。
#: 否则 1.0 bar 绝压的常压流股(库里有)会被判成真空、设计压力掉到 0.1 MPa。
ATMOSPHERIC_MARGIN_MPA = 0.01


def design_pressure(press_bar_abs: float | None,
                    options: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """操作绝压(bar) → 设计压力(MPa 表压)。

    口径(§14.3):`P_design = (P_绝压 − 0.1013 MPa) × 1.1`,下限钳到 1.0 MPa;
    真空(表压为负且超出常压死区)→ 内压按最小标准壁厚,只标记"按外压 0.1 MPa 校核"。
    常压死区 ±0.01 MPa 是**实现补充**:1.0 bar 绝压(表压 −0.0013 MPa)属常压操作,
    不能判成真空,否则设计压力会从 1.0 MPa 掉到 0.1 MPa。
    """
    opts = options or {}
    factor = float(opts.get("pressure_factor", 1.1))
    floor_mpa = float(opts.get("pressure_floor_mpa", 1.0))
    margin = float(opts.get("atmospheric_margin_mpa", ATMOSPHERIC_MARGIN_MPA))

    if press_bar_abs is None:
        return {"design_mpa": None, "gauge_mpa": None, "vacuum": False,
                "note": "缺操作压力"}

    p_mpa_abs = press_bar_abs / 10.0
    gauge = p_mpa_abs - ATM_MPA
    if gauge < -margin:
        return {"design_mpa": VACUUM_DESIGN_MPA, "gauge_mpa": gauge, "vacuum": True,
                "note": "真空管道:内压按最小标准壁厚,按外压 0.1 MPa 校核(不做外压计算)"}
    return {"design_mpa": max(gauge * factor, floor_mpa), "gauge_mpa": gauge,
            "vacuum": False, "note": ""}


def design_temperature(temp_c: float | None,
                       insulated: bool | None = None,
                       options: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """设计温度(℃)。

    §14.3 口径:保温(介质 ≥50 ℃ 自动判 `-H`)→ 直接取介质温度;
    只有 `65 ≤ t < 80 ℃` 且**无保温**时才折 `t × 0.95`(摄氏直接乘)。
    """
    opts = options or {}
    on_temp = float(opts.get("insulation_on_temp_c", pipe_data.INSULATION_ON_TEMP_C))
    fold_lo = float(opts.get("fold_low_c", 65.0))
    fold_hi = float(opts.get("fold_high_c", 80.0))
    fold_ratio = float(opts.get("fold_ratio", 0.95))

    if temp_c is None:
        return {"design_c": None, "insulated": False, "folded": False,
                "note": "缺介质温度"}

    if insulated is None:
        insulated = temp_c >= on_temp
    if insulated:
        return {"design_c": temp_c, "insulated": True, "folded": False, "note": ""}
    if fold_lo <= temp_c < fold_hi:
        return {"design_c": temp_c * fold_ratio, "insulated": False, "folded": True,
                "note": f"无保温且 {fold_lo:g}–{fold_hi:g} ℃,按 {fold_ratio:g} 折"}
    return {"design_c": temp_c, "insulated": False, "folded": False, "note": ""}


def allowable_stress(grade: str, design_c: float | None,
                     stress_table: Mapping[str, Mapping[float, float]]) -> float | None:
    """许用应力 [σ] MPa:按牌号 + 设计温度查表,缺温度线性内插,T<40 ℃ 取 40 ℃ 值。"""
    if design_c is None or not stress_table:
        return None
    table = stress_table.get(grade)
    if not table:
        return None
    temps = sorted(table)
    t = max(float(design_c), temps[0])
    if t > temps[-1]:
        return None  # 超出录入范围(如不锈钢 >400 ℃ 属蠕变段,不录不猜)
    if t in table:
        return float(table[t])
    for lo, hi in zip(temps, temps[1:]):
        if lo <= t <= hi:
            ratio = (t - lo) / (hi - lo)
            return float(table[lo] + (table[hi] - table[lo]) * ratio)
    return None


# --------------------------------------------------------------------------
# 8–9. 壁厚
# --------------------------------------------------------------------------

def calc_wall_thickness(p_mpa: float, od_mm: float, stress_mpa: float,
                        y: float = pipe_data.Y_VALUE, phi_w: float = 1.0) -> dict[str, Any]:
    """GB/T 20801.1-2025 6.2.1 直管内压:t = P·D_o / [2(S·Φw + P·Y)]。

    适用范围校验:`t < D_o/6`;超出说明公式不适用(要另按厚壁/外压算),只标记不硬算。
    """
    if stress_mpa is None or stress_mpa <= 0:
        return {"t_calc_mm": None, "applicable": False, "note": "缺许用应力"}
    denominator = 2.0 * (stress_mpa * phi_w + p_mpa * y)
    t = p_mpa * od_mm / denominator
    applicable = t < od_mm / 6.0
    return {"t_calc_mm": t, "applicable": applicable,
            "note": "" if applicable else f"t={t:.2f} ≥ D/6={od_mm / 6:.2f},公式不适用,需另行校核"}


def round_up_wall(t_required_mm: float, walls: Sequence[float] | None) -> dict[str, Any]:
    """向上圆整到该外径的标准壁厚序列里的最小值。序列为空 = 表未录入。"""
    if not walls:
        return {"wall_mm": None, "note": "缺标准壁厚序列(T2 未录入)"}
    usable = sorted(w for w in walls if w >= t_required_mm - 1e-9)
    if not usable:
        return {"wall_mm": None,
                "note": f"所需 {t_required_mm:.2f} mm 超过该外径最大标准壁厚 "
                        f"{max(walls):g} mm,需并联或专项设计"}
    return {"wall_mm": float(usable[0]), "note": ""}


# --------------------------------------------------------------------------
# 10. 等级号
# --------------------------------------------------------------------------

def pn_code_for(design_mpa: float) -> str | None:
    """设计压力 → 压力等级代号(取不小于设计压力的最小 PN)。"""
    fits = [(pn, code) for code, pn in pipe_data.PN_CODES.items() if pn >= design_mpa - 1e-9]
    if not fits:
        return None
    return min(fits)[1]


def grade_code(design_mpa: float, material_code: str,
               design_c: float | None = None,
               insulation: str = "",
               options: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """管道等级号 = 压力代号 + 顺序号(默认 1) + 材质代号,绝热代号接在后面。"""
    opts = options or {}
    seq = str(opts.get("sequence_no", 1))
    pn = pn_code_for(design_mpa)
    if pn is None:
        return {"code": None, "note": f"设计压力 {design_mpa:g} MPa 超出 PN 代号表"}
    suffix = ""
    auto = opts.get("auto_insulation", True)
    if not insulation and auto and design_c is not None:
        if design_c >= float(opts.get("insulation_on_temp_c", pipe_data.INSULATION_ON_TEMP_C)):
            insulation = "H"
    if insulation:
        suffix = f"-{insulation}"
    return {"code": f"{pn}{seq}{material_code}{suffix}", "insulation": insulation, "note": ""}


# --------------------------------------------------------------------------
# 批量:一张表(§14.5 主表 + 工作表 2「计算书」)
# --------------------------------------------------------------------------

def _pump_inlets(connections: Iterable[Mapping[str, Any]],
                 blocks: Iterable[Mapping[str, Any]]) -> set[str]:
    """找出"去向是泵"的流股名 —— 这类流股要按泵入口流速选型。

    `connections` 的行是 (block, direction, stream):`direction="in"` 表示
    该流股**进入**这台设备,所以泵入口流股 = 泵的入向连接。
    """
    pump_names = {b["name"] for b in blocks if str(b.get("type", "")).lower() == "pump"}
    return {c["stream"] for c in connections
            if str(c.get("direction", "")).lower() == "in"
            and c.get("block") in pump_names}


def build(streams: Iterable[Mapping[str, Any]],
          blocks: Iterable[Mapping[str, Any]] | None = None,
          connections: Iterable[Mapping[str, Any]] | None = None,
          options: Mapping[str, Any] | None = None,
          tables: Mapping[str, Any] | None = None,
          ) -> dict[str, Any]:
    """把一批流股算成管表。

    参数
    ----
    streams : 每条要有 `name`、`vol_flow_m3h`、`temperature_c`、`pressure_bar`(绝压)、`vap_frac`
    blocks / connections : 用来识别"泵入口"(可省,省了就当都是普通管段)
    options : 流速、压力系数、保温阈值、材质等可调口径
    tables  : `{"dn_to_od": {...}, "od_to_walls": {...}, "stress": {...}}`;
              不传则用 `pipe_data` 里的(目前是空的 → 会明确报 TablesNotReady)
    """
    opts = dict(options or {})
    tbl = dict(tables or {})
    dn_to_od = tbl.get("dn_to_od", pipe_data.DN_TO_OD)
    od_to_walls = tbl.get("od_to_walls", pipe_data.OD_TO_WALLS)
    stress = tbl.get("stress", pipe_data.ALLOWABLE_STRESS)

    if not dn_to_od or not stress:
        raise TablesNotReady(
            "标准表未录入:DN→外径(T1)或许用应力(T3)为空。"
            "先把 GB/T 17395 表 1/表 4 与 GB/T 20801.1 表 B.1 录进 pipe_data.py"
            "(任务单见 docs/_internal/prompt-vision-extract.md)"
        )

    blocks = list(blocks or [])
    pump_in = _pump_inlets(list(connections or []), blocks)
    max_dn = int(opts.get("max_dn", pipe_data.MAX_DN))
    default_material = str(opts.get("material", "S30408"))
    wall_grade = str(opts.get("wall_tol_grade", pipe_data.DEFAULT_WALL_TOL_GRADE))
    corrosion_c2 = float(opts.get("corrosion_mm", 1.0 if default_material == "20#" else 0.0))

    rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    for s in streams:
        name = s.get("name")
        q = s.get("vol_flow_m3h")
        if q is None or q <= 0:
            missing.append({"stream": name, "reason": "缺体积流量,未选型"})
            continue

        phase = phase_of(s.get("vap_frac"))
        is_pump_in = name in pump_in
        v = velocity_for(phase, is_pump_in, opts)
        d_i = inner_diameter(float(q), v)
        dn, od = pick_dn(d_i, dn_to_od)
        warnings: list[str] = []
        if dn is None:
            missing.append({"stream": name, "reason": f"计算内径 {d_i:.1f} mm 超出 DN 表"})
            continue
        if dn > max_dn:
            warnings.append(f"DN{dn} 超出本期上限 DN{max_dn},需专项确认")

        pd = design_pressure(s.get("pressure_bar"), opts)
        td = design_temperature(s.get("temperature_c"), opts.get("insulated"), opts)
        sigma = allowable_stress(default_material, td["design_c"], stress)
        w = calc_wall_thickness(pd["design_mpa"] or 0.0, od, sigma or 0.0)
        if not w["applicable"]:
            warnings.append(w["note"])

        t_req = (w["t_calc_mm"] or 0.0) + corrosion_c2
        if pd["vacuum"]:
            warnings.append("真空流股:按最小标准壁厚,外压另行校核(不做外压计算)")
        wall = round_up_wall(t_req, od_to_walls.get(od))
        if wall["wall_mm"] is None:
            warnings.append(wall["note"])
        else:
            c1 = pipe_data.negative_deviation(od, wall["wall_mm"], wall_grade)
            if wall["wall_mm"] - c1 < (w["t_calc_mm"] or 0.0) + corrosion_c2 - 1e-9:
                warnings.append(
                    f"圆整后 {wall['wall_mm']:g} mm 扣负偏差 {c1:.2f} mm 后不足所需,建议升一档"
                )

        material = str(opts.get("material_for_stream", {}).get(name, default_material))
        mcode = pipe_data.MATERIAL_CODE_BY_GRADE.get(material)
        if mcode is None:  # 判不了材质:标"待人工确认",不硬猜一个代号
            mcode = "E"
            warnings.append(f"材质 {material!r} 不在映射表里,等级号末字母按 E 暂记,待人工确认")
        gc = grade_code(pd["design_mpa"] or 0.0, mcode, td["design_c"], "", opts)
        if gc["code"] is None:
            warnings.append(gc["note"])
        if phase == "two":
            warnings.append("气液共存:按两相流速选型,需专项核算")

        rows.append({
            "stream": name,
            "phase": phase,
            "medium_code": medium_code(phase, opts.get("medium_codes")),
            "velocity_ms": v,
            "pump_inlet": is_pump_in,
            "inner_d_mm": d_i,
            "dn": dn,
            "od_mm": od,
            "design_pressure_mpa": pd["design_mpa"],
            "gauge_pressure_mpa": pd["gauge_mpa"],
            "design_temp_c": td["design_c"],
            "insulated": td["insulated"],
            "material": material,
            "stress_mpa": sigma,
            "t_calc_mm": w["t_calc_mm"],
            "c2_mm": corrosion_c2,
            "t_required_mm": t_req,
            "wall_mm": wall["wall_mm"],
            "grade_code": gc["code"],
            "warnings": warnings,
        })

    return {
        "rows": rows,
        "missing": missing,
        "summary": {
            "streams": len(rows) + len(missing),
            "selected": len(rows),
            "missing": len(missing),
            "warned": sum(1 for r in rows if r["warnings"]),
        },
    }
