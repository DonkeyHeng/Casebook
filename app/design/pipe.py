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
9. `round_up_wall()`       加附加量后圆整到标准壁厚(含最小壁厚下限)
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
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from app.design import pipe_data

# 相态阈值:痕量汽化率(库里实测有 1e-8)不能按 ==0 判
PHASE_EPS = 1e-4

# 默认流速(m/s)。来源:§14.1 第 3 步用户定的口径。
DEFAULT_VELOCITY = {
    "liquid": 1.5,  # 1.0–2.5
    "gas": 15.0,  # 10–25
    "two": 8.0,  # 5–10
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


def velocity_for(
    phase: str, is_pump_inlet: bool = False, options: Mapping[str, Any] | None = None
) -> float:
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
    """**初选** DN:取第一个 `DN ≥ 计算内径` 的档位,返回 (DN, 公称外径)。

    注意这只是一次粗选:DN 是公称直径,同一 DN 的外径比 DN 大(如 DN250 → Φ273),
    真正的选档要用 `select_dn()` 按**实际内径**(外径 − 2×壁厚)复核。
    """
    if not dn_to_od:
        return None, None
    candidates = sorted(d for d in dn_to_od if d >= inner_d_mm - 1e-9)
    if not candidates:
        return None, None
    dn = candidates[0]
    return dn, float(dn_to_od[dn])


def select_dn(
    inner_d_mm: float,
    dn_to_od: Mapping[int, float],
    walls_by_od: Mapping[float, Sequence[float]],
    wall_for,
    max_dn: int | None = None,
) -> dict[str, Any]:
    """按**实际内径**选档:从小到大试 DN,直到 `外径 − 2×标准壁厚 ≥ 计算内径`。

    这一步是必须的:DN 只是公称直径,Φ273 的内径约 269 mm,若只按"DN ≥ 内径"选档,
    会一路选大、实际流速偏低(实测 #19–#22 有 40–57 条 V2 就是这么来的)。

    `wall_for(od)` 负责给出该外径最终选定的标准壁厚(build 里会连负偏差一起算)。
    """
    for dn in sorted(dn_to_od):
        if max_dn is not None and dn > max_dn:
            break
        od = float(dn_to_od[dn])
        wall = wall_for(od)
        if wall is None:
            # 没有壁厚数据时退化成"外径 ≥ 计算内径"的判断,别把整条流股判死
            if od >= inner_d_mm - 1e-9:
                return {"dn": dn, "od_mm": od, "wall_mm": None}
            continue
        if od - 2.0 * wall >= inner_d_mm - 1e-9:
            return {"dn": dn, "od_mm": od, "wall_mm": wall}
    return {"dn": None, "od_mm": None, "wall_mm": None}


# --------------------------------------------------------------------------
# 5–7. 设计压力 / 设计温度 / 许用应力
# --------------------------------------------------------------------------

ATM_MPA = 0.1013
VACUUM_DESIGN_MPA = 0.1
#: 常压死区:表压在 ±0.01 MPa(0.1 bar)以内按"常压"处理,不判真空。
#: 否则 1.0 bar 绝压的常压流股(库里有)会被判成真空、设计压力掉到 0.1 MPa。
ATMOSPHERIC_MARGIN_MPA = 0.01


def design_pressure(
    press_bar_abs: float | None, options: Mapping[str, Any] | None = None
) -> dict[str, Any]:
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
        return {"design_mpa": None, "gauge_mpa": None, "vacuum": False, "note": "缺操作压力"}

    p_mpa_abs = press_bar_abs / 10.0
    gauge = p_mpa_abs - ATM_MPA
    if gauge < -margin:
        return {
            "design_mpa": VACUUM_DESIGN_MPA,
            "gauge_mpa": gauge,
            "vacuum": True,
            "note": "真空管道:内压按最小标准壁厚,按外压 0.1 MPa 校核(不做外压计算)",
        }
    return {
        "design_mpa": max(gauge * factor, floor_mpa),
        "gauge_mpa": gauge,
        "vacuum": False,
        "note": "",
    }


def design_temperature(
    temp_c: float | None, insulated: bool | None = None, options: Mapping[str, Any] | None = None
) -> dict[str, Any]:
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
        return {"design_c": None, "insulated": False, "folded": False, "note": "缺介质温度"}

    if insulated is None:
        insulated = temp_c >= on_temp
    if insulated:
        return {"design_c": temp_c, "insulated": True, "folded": False, "note": ""}
    if fold_lo <= temp_c < fold_hi:
        return {
            "design_c": temp_c * fold_ratio,
            "insulated": False,
            "folded": True,
            "note": f"无保温且 {fold_lo:g}–{fold_hi:g} ℃,按 {fold_ratio:g} 折",
        }
    return {"design_c": temp_c, "insulated": False, "folded": False, "note": ""}


def allowable_stress(
    grade: str, design_c: float | None, stress_table: Mapping[str, Mapping[float, float]]
) -> float | None:
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


def calc_wall_thickness(
    p_mpa: float, od_mm: float, stress_mpa: float, y: float = pipe_data.Y_VALUE, phi_w: float = 1.0
) -> dict[str, Any]:
    """GB/T 20801.1-2025 6.2.1 直管内压:t = P·D_o / [2(S·Φw + P·Y)]。

    适用范围校验:`t < D_o/6`;超出说明公式不适用(要另按厚壁/外压算),只标记不硬算。
    """
    if stress_mpa is None or stress_mpa <= 0:
        return {"t_calc_mm": None, "applicable": False, "note": "缺许用应力"}
    denominator = 2.0 * (stress_mpa * phi_w + p_mpa * y)
    t = p_mpa * od_mm / denominator
    applicable = t < od_mm / 6.0
    return {
        "t_calc_mm": t,
        "applicable": applicable,
        "note": "" if applicable else f"t={t:.2f} ≥ D/6={od_mm / 6:.2f},公式不适用,需另行校核",
    }


def round_up_wall(
    t_required_mm: float,
    walls: Sequence[float] | None,
    od_mm: float | None = None,
    grade: str | None = None,
    min_wall: float | None = None,
) -> dict[str, Any]:
    """向上圆整到标准壁厚。

    `od_mm` + `grade` 给了的话,会**把壁厚负偏差一起算进去**:
    选中的 S 必须满足 `S − C1(S) ≥ 所需壁厚`(C1 依赖 S,所以要逐档试),
    否则就会出现"圆整完扣掉负偏差还不够"的情况(实测 #20 有 20 多条)。

    `min_wall` 是**工艺最小壁厚下限**(见 `pipe_data.MIN_WALL_BY_DN`):
    承压需要的厚度可能只有 0.07 mm,但 DN15 实际得从 2.0 mm 起,否则焊不出来。
    下限只抬高、不降低 —— 承压需要更厚时仍按承压;`floored` 说明这一档是不是下限定的。
    """
    if not walls:
        return {
            "wall_mm": None,
            "note": "缺标准壁厚序列(T2 未录入)",
            "min_wall_mm": min_wall,
            "floored": False,
        }
    usable = sorted(walls)
    floor = float(t_required_mm)
    floored = False
    if min_wall is not None and float(min_wall) > floor:
        floor = float(min_wall)
        floored = True
    for s in usable:
        if s < floor - 1e-9:
            continue
        if od_mm and grade:
            c1 = pipe_data.negative_deviation(od_mm, s, grade)
            if s - c1 < t_required_mm - 1e-9:
                continue
        return {"wall_mm": float(s), "note": "", "min_wall_mm": min_wall, "floored": floored}
    return {
        "wall_mm": None,
        "note": f"所需 {floor:.2f} mm 超过该外径最大标准壁厚 "
        f"{max(usable):g} mm(含壁厚下限),需并联或专项设计",
        "min_wall_mm": min_wall,
        "floored": floored,
    }


# --------------------------------------------------------------------------
# 10. 等级号
# --------------------------------------------------------------------------


def pn_code_for(design_mpa: float) -> str | None:
    """设计压力 → 压力等级代号(取不小于设计压力的最小 PN)。"""
    fits = [(pn, code) for code, pn in pipe_data.PN_CODES.items() if pn >= design_mpa - 1e-9]
    if not fits:
        return None
    return min(fits)[1]


def grade_code(
    design_mpa: float,
    material_code: str,
    design_c: float | None = None,
    insulation: str = "",
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
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
# 校验规则(V1–V9,见 docs/pipe-selection-plan.md §6)
# --------------------------------------------------------------------------


def actual_velocity(vol_flow_m3h: float, inner_d_mm: float) -> float | None:
    """按实际内径反算流速 m/s(内径 = 外径 − 2×壁厚)。"""
    if not inner_d_mm or inner_d_mm <= 0:
        return None
    area = math.pi * (inner_d_mm / 1000.0) ** 2 / 4.0
    return (vol_flow_m3h / 3600.0) / area


#: 各相态推荐流速区间(m/s),来自 §14.1 第 3 步
VELOCITY_RANGE = {
    "liquid": (1.0, 2.5),
    "gas": (10.0, 25.0),
    "two": (5.0, 10.0),
    "pump_inlet": (0.5, 1.0),
}


def validate(
    rows: Iterable[Mapping[str, Any]], options: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """对选型结果跑 §6 的 V1–V9,只陈述事实,不给"该怎么改模型"的建议。

    返回 `{"findings": [...], "summary": {"error": n, "warn": n}}`,
    每条 finding 是 `{stream, code, level, message, field}`。

    `field` 是**这条发现该标在哪一列**(前端据此做单元格标黄,而不是整行染黄):
    取值与 `build()` 的行字段同名(`dn` / `velocity` / `wall_mm` / `grade_code` /
    `design_pressure_mpa` / `design_temp_c`)。V2 的"实际流速"在主表里也有一列
    (前端按内径反算),所以标 `velocity` 不会落空。
    """
    opts = options or {}
    low_c = float(opts.get("material_low_c", -20.0))
    high_c = float(opts.get("material_high_c", 450.0))
    hot_c = float(opts.get("hot_insulation_c", 80.0))
    findings: list[dict[str, Any]] = []

    def add(stream: str, code: str, level: str, message: str, field: str) -> None:
        findings.append(
            {"stream": stream, "code": code, "level": level, "message": message, "field": field}
        )

    for r in rows:
        name = r.get("stream")
        dn = r.get("dn")
        od = r.get("od_mm")
        wall = r.get("wall_mm")
        v_used = r.get("velocity_ms")
        phase = r.get("phase")

        # V1 编号里的 DN ≠ 公称直径列(没有编号的工况跳过)
        pipe_no = r.get("pipe_no")
        if pipe_no:
            tail = str(pipe_no).split("-")[-1]
            if tail.isdigit() and dn is not None and int(tail) != int(dn):
                add(name, "V1", "error", f"管道编号里的口径 {tail} 与选出的 DN{dn} 不一致", "dn")

        # V5 气液共存但按单相流速选型(先判,好在 V2 里避免同一个问题报两次)
        v5_hit = False
        if phase == "two" and v_used is not None:
            lo2, hi2 = VELOCITY_RANGE["two"]
            if not (lo2 - 1e-9 <= v_used <= hi2 + 1e-9):
                v5_hit = True
                add(
                    name,
                    "V5",
                    "warn",
                    f"两相流按 {v_used:g} m/s(单相口径)选型,超出两相 {lo2:g}–{hi2:g} m/s",
                    "velocity",
                )

        # V2 实际流速超出推荐区间(两相已由 V5 覆盖,不重复报)
        id_mm = (od - 2.0 * wall) if (od and wall) else None
        v_act = None
        if id_mm:
            area = math.pi * (id_mm / 1000.0) ** 2 / 4.0
            v_act = (float(r["vol_flow_m3h"]) / 3600.0) / area if r.get("vol_flow_m3h") else None
        key = "pump_inlet" if r.get("pump_inlet") else phase
        lo, hi = VELOCITY_RANGE.get(key, (0.0, float("inf")))
        if v_act is not None and not v5_hit and not (lo - 1e-9 <= v_act <= hi + 1e-9):
            add(
                name,
                "V2",
                "warn",
                f"实际流速 {v_act:.2f} m/s 超出 {phase} 推荐区间 {lo:g}–{hi:g} m/s"
                + ("(泵入口)" if r.get("pump_inlet") else ""),
                "velocity",
            )

        # V3 计算壁厚 + 附加量 > 所选标准壁厚
        need = r.get("t_required_mm")
        if need is not None and wall is not None and need > wall + 1e-9:
            add(
                name,
                "V3",
                "error",
                f"所需壁厚 {need:.2f} mm 超过所选标准壁厚 {wall:g} mm",
                "wall_mm",
            )

        # V4 等级号的压力代号 < 设计压力
        code = r.get("grade_code") or ""
        if code and r.get("design_pressure_mpa") is not None:
            pn = pipe_data.PN_CODES.get(code[:1])
            if pn is None:
                add(name, "V4", "error", f"等级号 {code} 的压力代号不在 PN 代号表里", "grade_code")
            elif pn < float(r["design_pressure_mpa"]) - 1e-9:
                add(
                    name,
                    "V4",
                    "error",
                    f"等级号 {code} 的公称压力 {pn:g} MPa 低于设计压力 "
                    f"{float(r['design_pressure_mpa']):.2f} MPa",
                    "grade_code",
                )

        # V5 已在前面先判(与 V2 互斥,避免同一个问题报两次)

        # V6 真空流股
        if r.get("vacuum"):
            add(name, "V6", "warn", "真空流股:按外压 0.1 MPa 另行校核", "design_pressure_mpa")

        # V7 超出材料使用温度
        dt = r.get("design_temp_c")
        if dt is not None and (dt < low_c or dt > high_c):
            add(
                name,
                "V7",
                "warn",
                f"设计温度 {dt:g} ℃ 超出材料常规使用区间 {low_c:g}–{high_c:g} ℃",
                "design_temp_c",
            )

        # V8 泵出口流股用了泵入口流速
        if r.get("pump_outlet") and r.get("pump_inlet"):
            add(name, "V8", "error", "泵出口流股被按泵入口流速选型", "velocity")

        # V9 高温但没加保温代号
        if dt is not None and dt >= hot_c and not r.get("insulation"):
            add(
                name,
                "V9",
                "warn",
                f"设计温度 {dt:g} ℃ ≥ {hot_c:g} ℃,等级号却未加保温代号 -H",
                "grade_code",
            )

    return {
        "findings": findings,
        "summary": {
            "error": sum(1 for f in findings if f["level"] == "error"),
            "warn": sum(1 for f in findings if f["level"] == "warn"),
        },
    }


# --------------------------------------------------------------------------
# 批量:一张表(§14.5 主表 + 工作表 2「计算书」)
# --------------------------------------------------------------------------


def _pump_streams(
    connections: Iterable[Mapping[str, Any]], blocks: Iterable[Mapping[str, Any]]
) -> tuple[set[str], set[str]]:
    """分出"泵入口流股"与"泵出口流股"。

    `connections` 的行是 (block, direction, stream):`direction="in"` 表示
    该流股**进入**这台设备,`"out"` 表示由这台设备产出。
    泵入口按 0.8 m/s 选型,泵出口不能沿用这个口径(V8)。
    """
    pump_names = {b["name"] for b in blocks if str(b.get("type", "")).lower() == "pump"}
    inlets, outlets = set(), set()
    for c in connections:
        if c.get("block") not in pump_names:
            continue
        d = str(c.get("direction", "")).lower()
        if d == "in":
            inlets.add(c["stream"])
        elif d == "out":
            outlets.add(c["stream"])
    return inlets, outlets


def _tables_for(tables: Mapping[str, Any] | None) -> tuple[dict, dict, dict, dict, Any]:
    """取本次选型用的三张表。都来自同一次加载的快照,不会新旧混用。"""
    snap = pipe_data.tables_snapshot()
    tbl = dict(tables or {})
    dn_to_od = tbl.get("dn_to_od", pipe_data.DN_TO_OD)
    od_to_walls = tbl.get("od_to_walls", snap.od_to_walls)
    stress = tbl.get("stress", snap.stress)

    if not dn_to_od or not stress:
        empty = [
            label for label, tbl in (("DN→外径(T1)", dn_to_od), ("许用应力(T3)", stress)) if not tbl
        ]
        raise TablesNotReady(
            "标准表未录入:" + "、".join(empty) + " 为空。"
            "把 GB/T 17395-2024 表 1/表 4 与 GB/T 20801.1-2025 表 B.1 整理成 CSV,"
            "放进 data/preview/std/docx/,再重启服务(页码与列名见 docs/pipe-standards-index.md)。"
        )

    return tbl, od_to_walls, stress, dn_to_od, snap


@dataclass(frozen=True)
class _BuildContext:
    """一次选型用的常量集合:计算侧只看它,不再依赖满屏闭包变量。"""

    opts: dict[str, Any]
    tbl: dict[str, Any]
    snap: Any
    dn_to_od: Mapping
    od_to_walls: Mapping
    stress: Mapping
    max_dn: int
    default_material: str
    wall_grade: str
    global_c2: float | None
    min_wall_map: Mapping | None
    od_to_dn: dict
    pump_in: set
    pump_out: set


def _min_wall_ctx(ctx: _BuildContext, dn: int | None) -> float | None:
    """DN → 最小壁厚下限。options 里能覆盖(键可能是字符串,统一转 int)。"""
    if not ctx.min_wall_map or dn is None:
        return None
    for upper in sorted(ctx.min_wall_map, key=lambda x: int(x)):
        if dn <= int(upper):
            return float(ctx.min_wall_map[upper])
    return None


def _resolve_material(
    ctx: _BuildContext, name: str, medium: str, design_c: float, warnings: list[str]
) -> tuple[str, dict, float, Mapping, float | None]:
    """定材质、腐蚀裕量与许用应力:人工指定 > 管道等级表 > 全局默认。

    工具**不判断介质是否腐蚀**,只按用户定义的表套用(见 pipe_data 第 5 节)。
    """
    opts = ctx.opts
    tbl = ctx.tbl
    snap = ctx.snap
    stress = ctx.stress
    od_to_walls = ctx.od_to_walls
    default_material = ctx.default_material
    global_c2 = ctx.global_c2
    # 材质来源优先级:人工指定 > 管道等级表 > 全局默认
    cls = pipe_data.resolve_piping_class(
        name, medium, rules=opts.get("piping_rules"), default=opts.get("piping_default")
    )
    override = (opts.get("material_for_stream") or {}).get(name)
    material = str(override or cls["grade"] or default_material)
    if (
        not override
        and cls["source"] == "none"
        and (opts.get("piping_rules") or pipe_data.PIPING_CLASS_RULES)
    ):
        warnings.append(f"介质 {medium} 未在管道等级表里定义材质,按默认 {default_material} 暂算")
    if cls["corrosion_mm"] is not None:
        corrosion_c2 = float(cls["corrosion_mm"])
    elif global_c2 is not None:
        corrosion_c2 = float(global_c2)
    else:
        corrosion_c2 = 1.0 if material == "20#" else 0.0
    od_to_walls_used = od_to_walls
    if material.startswith("S3"):
        od_to_walls_used = tbl.get("od_to_walls_sst", snap.od_to_walls_sst) or od_to_walls
    sigma = allowable_stress(material, design_c, dict(stress))
    return material, cls, corrosion_c2, od_to_walls_used, sigma


def _size_pipe(  # noqa: PLR0913, PLR0917 —— 纯选型步骤,入参显式列出便于单测
    ctx: _BuildContext,
    q: float,
    pd: dict,
    d_i: float,
    sigma: float | None,
    corrosion_c2: float,
    od_to_walls_used: Mapping,
    is_pump_in: bool,
    phase: str,
    warnings: list[str],
) -> tuple[int, float, dict, dict, float | None, float] | None:
    """选 DN、定壁厚、核实际流速。返回 (dn, od, w, wall, min_wall_mm, t_req);
    选不出档位时返回 None(由调用方给出"需并联/专项设计"的说明)。"""
    dn_to_od = ctx.dn_to_od
    od_to_dn = ctx.od_to_dn
    wall_grade = ctx.wall_grade
    max_dn = ctx.max_dn

    def _wall_for(od: float, sigma=sigma, pd=pd) -> float | None:
        """某外径下,考虑许用应力、负偏差与最小壁厚下限后最终选定的标准壁厚。"""
        w = calc_wall_thickness(pd["design_mpa"] or 0.0, od, sigma or 0.0)
        if not w["applicable"]:
            return None
        t_req = (w["t_calc_mm"] or 0.0) + corrosion_c2
        mw = _min_wall_ctx(ctx, od_to_dn.get(float(od)))
        got = round_up_wall(t_req, od_to_walls_used.get(od), od, wall_grade, mw)
        return got["wall_mm"]

    sel = select_dn(d_i, dn_to_od, od_to_walls_used, _wall_for)
    dn, od = sel["dn"], sel["od_mm"]
    if dn is None:
        return None
    if dn > max_dn:
        warnings.append(f"DN{dn} 超出本期上限 DN{max_dn},需专项确认")
    w = calc_wall_thickness(pd["design_mpa"] or 0.0, od, sigma or 0.0)
    if not w["applicable"]:
        warnings.append(w["note"])
    t_req = (w["t_calc_mm"] or 0.0) + corrosion_c2
    if pd["vacuum"]:
        warnings.append("真空流股:按最小标准壁厚,外压另行校核(不做外压计算)")
    min_wall_mm = _min_wall_ctx(ctx, dn)
    wall = round_up_wall(t_req, od_to_walls_used.get(od), od, wall_grade, min_wall_mm)
    if wall["wall_mm"] is None:
        warnings.append(wall["note"])
    else:
        c1 = pipe_data.negative_deviation(od, wall["wall_mm"], wall_grade)
        v_act = actual_velocity(float(q), od - 2.0 * wall["wall_mm"])
        if v_act is not None:
            lo, hi = VELOCITY_RANGE.get(
                "pump_inlet" if is_pump_in else phase, (0.0, float("inf"))
            )
            if not (lo - 1e-9 <= v_act <= hi + 1e-9):
                warnings.append(
                    f"实际流速 {v_act:.2f} m/s 超出推荐 {lo:g}–{hi:g} m/s(负偏差 {c1:.2f} mm)"
                )
    return dn, od, w, wall, min_wall_mm, t_req


def _select_stream(
    ctx: _BuildContext, s: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """算一条流股。返回 (row, missing):两者只会有一个非 None。"""
    opts = ctx.opts
    dn_to_od = ctx.dn_to_od
    pump_in = ctx.pump_in
    pump_out = ctx.pump_out

    name = s.get("name")
    q = s.get("vol_flow_m3h")
    if q is None or q <= 0:
        return None, {"stream": name, "reason": "缺体积流量,未选型"}
    phase = phase_of(s.get("vap_frac"))
    is_pump_in = name in pump_in
    v = velocity_for(phase, is_pump_in, opts)
    d_i = inner_diameter(float(q), v)
    warnings: list[str] = []
    pd = design_pressure(s.get("pressure_bar"), opts)
    td = design_temperature(s.get("temperature_c"), opts.get("insulated"), opts)
    medium = medium_code(phase, opts.get("medium_codes"))
    material, cls, corrosion_c2, od_to_walls_used, sigma = _resolve_material(
        ctx, name, medium, td["design_c"], warnings
    )

    sized = _size_pipe(
        ctx, q, pd, d_i, sigma, corrosion_c2, od_to_walls_used, is_pump_in, phase, warnings
    )
    if sized is None:
        od_max = max(dn_to_od.values()) if dn_to_od else None
        return None, {
            "stream": name,
            "reason": f"计算内径 {d_i:.1f} mm 超出可选档位"
            + (f"(最大 Φ{od_max:g} = DN{max(dn_to_od)})" if od_max else "")
            + ",需并联或专项设计",
        }
    dn, od, w, wall, min_wall_mm, t_req = sized
    mcode = pipe_data.MATERIAL_CODE_BY_GRADE.get(material)
    if mcode is None:  # 判不了材质:标"待人工确认",不硬猜一个代号
        mcode = "E"
        warnings.append(f"材质 {material!r} 不在映射表里,等级号末字母按 E 暂记,待人工确认")
    gc = grade_code(pd["design_mpa"] or 0.0, mcode, td["design_c"], "", opts)
    if gc["code"] is None:
        warnings.append(gc["note"])
    if phase == "two":
        warnings.append("气液共存:按两相流速选型,需专项核算")
    return {
        "stream": name,
        "phase": phase,
        "medium_code": medium,
        "material_grade": material,
        "piping_class_note": cls["note"],
        "piping_class_source": cls["source"],
        "velocity_ms": v,
        "pump_inlet": is_pump_in,
        "pump_outlet": name in pump_out,
        "inner_d_mm": d_i,
        "dn": dn,
        "od_mm": od,
        "vol_flow_m3h": float(q),
        "design_pressure_mpa": pd["design_mpa"],
        "gauge_pressure_mpa": pd["gauge_mpa"],
        "vacuum": pd["vacuum"],
        "design_temp_c": td["design_c"],
        "insulated": td["insulated"],
        "material": material,
        "stress_mpa": sigma,
        "t_calc_mm": w["t_calc_mm"],
        "c2_mm": corrosion_c2,
        "t_required_mm": t_req,
        "min_wall_mm": min_wall_mm,
        "wall_mm": wall["wall_mm"],
        "wall_floored": bool(wall.get("floored")),
        "grade_code": gc["code"],
        "insulation": gc.get("insulation", ""),
        "warnings": warnings,
    }, None


def build(
    streams: Iterable[Mapping[str, Any]],
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
    tbl, od_to_walls, stress, dn_to_od, snap = _tables_for(tables)
    blocks = list(blocks or [])
    pump_in, pump_out = _pump_streams(list(connections or []), blocks)
    ctx = _BuildContext(
        opts=opts,
        tbl=tbl,
        snap=snap,
        dn_to_od=dn_to_od,
        od_to_walls=od_to_walls,
        stress=stress,
        max_dn=int(opts.get("max_dn", pipe_data.MAX_DN)),
        default_material=str(opts.get("material", "S30408")),
        wall_grade=str(opts.get("wall_tol_grade", pipe_data.DEFAULT_WALL_TOL_GRADE)),
        # 腐蚀裕量:显式给了就用;否则按材质给默认值(碳钢 1.0、不锈钢 0)
        global_c2=opts.get("corrosion_mm"),
        # 工艺最小壁厚下限:{DN 上限: 壁厚};传 False 表示这一层不启用
        min_wall_map=opts.get("min_wall", pipe_data.MIN_WALL_BY_DN),
        # 反查表跟着"实际用的" DN→外径 走,注入自定义表时下限也认得出来
        od_to_dn={float(od): int(dn) for dn, od in dn_to_od.items()},
        pump_in=pump_in,
        pump_out=pump_out,
    )

    rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for s in streams:
        row, miss = _select_stream(ctx, s)
        if miss is not None:
            missing.append(miss)
        else:
            rows.append(row)

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
