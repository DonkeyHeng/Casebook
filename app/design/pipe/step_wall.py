"""十步选型第 8–9 步：计算壁厚 → 圆整到标准壁厚。

含外压（真空）最小壁厚、近沸点液体判定；`round_up_wall()` 同时叠加
压力等级最小壁厚、负偏差与工艺最小壁厚下限。
口径来源：`docs/pipe-selection-plan.md` §14 第 8–9 步。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from . import data as pipe_data


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


def external_pressure_min_wall(
    od_mm: float | None, options: Mapping[str, Any] | None = None
) -> float | None:
    """真空管道外压失稳最小壁厚(mm,§17.1)。

    长圆筒弹性屈曲:`P_cr = 2E/(1−ν²)·(t/D_o)³`,取外压 0.1 MPa、安全系数 SF,
    反解 `t_min = D_o·(SF·P/(2E/(1−ν²)))^(1/3)`。只作筛查下限,与内压壁厚取大;
    真实支撑跨距、初始椭圆度、材料温度折减需另按 GB/T 20801.3 外压章节复核。

    这些系数都在 `pipe_data` 里,可经 options 覆盖;不返回 None 除非缺外径。
    """
    if od_mm is None or od_mm <= 0:
        return None
    opts = options or {}
    p = float(opts.get("vacuum_external_p_mpa", pipe_data.VACUUM_EXTERNAL_P_MPA))
    sf = float(opts.get("vacuum_buckling_sf", pipe_data.VACUUM_BUCKLING_SF))
    e = float(opts.get("vacuum_buckling_e_mpa", pipe_data.VACUUM_BUCKLING_E_MPA))
    nu = float(opts.get("vacuum_buckling_nu", pipe_data.VACUUM_BUCKLING_NU))
    base = 2.0 * e / (1.0 - nu * nu)
    return float(od_mm) * (sf * p / base) ** (1.0 / 3.0)


def near_boiling_liquid(
    phase: str,
    abs_kpa: float | None,
    temp_c: float | None,
    options: Mapping[str, Any] | None = None,
) -> bool:
    """近常压沸点液相 → 泵吸入易汽蚀,限速 0.8 m/s(§17.2/§17.8)。

    判据:液相 + 操作温度 80–105 ℃ + 绝压 95–115 kPa(微正压/常压吸入侧)。
    只限速,不改相态;泵排出口/明显升压管线由调用方另行排除(§17.8)。
    """

    if phase != "liquid" or abs_kpa is None or temp_c is None:
        return False
    opts = options or {}
    lo_t, hi_t = opts.get("cavitation_temp_range_c", pipe_data.CAVITATION_TEMP_RANGE_C)
    lo_p, hi_p = opts.get("cavitation_abs_kpa_range", pipe_data.CAVITATION_ABS_KPA_RANGE)
    return float(lo_t) <= temp_c <= float(hi_t) and float(lo_p) <= abs_kpa <= float(hi_p)


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
