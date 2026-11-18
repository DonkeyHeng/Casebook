"""十步选型第 5–7 步：设计压力 / 设计温度 / 许用应力。

含高压不锈钢（4.7–5.6 MPa 区间）的强制代号、许用应力与所需壁厚特例，
以及真空判定、物料流股识别等第 5 步的前置判断。
口径来源：`docs/pipe-selection-plan.md` §14 第 5–7 步、§19。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from . import data as pipe_data
from .constants import ATM_MPA, VACUUM_ABS_KPA, VACUUM_DESIGN_MPA


def press_to_abs_kpa(press_bar_abs: float | None) -> float | None:
    """操作压力(bar,绝压) → 绝压 kPa。库里 pressure_bar 就是绝压。"""
    return float(press_bar_abs) * 100.0 if press_bar_abs is not None else None


def is_material_stream(stream: Mapping[str, Any], options: Mapping[str, Any] | None = None) -> bool:
    """Aspen 流股是否物流(Material Stream),§19.1。

    新库:`stream_type` 来自 Aspen 节点属性 6(实测 MATERIAL / HEAT / WORK),
    只放 `material_stream_types` 里的类型进选型 —— 热量流(Q1–Q4)和功流连
    《未选型流股》清单都不进,免得被当成"漏选的管线"。
    老库没有这个字段时退化成保守判据:名字形如 Q1/W2 **且**六项物料结果全空
    才剔除;只要有一条物料结果就当物流保留,不误删还没 Run 的物料流股。
    """
    opts = options or {}
    declared = str(stream.get("stream_type") or "").strip().upper()
    if declared:
        allowed = {
            str(t).strip().upper()
            for t in opts.get("material_stream_types", pipe_data.MATERIAL_STREAM_TYPES)
        }
        return declared in allowed

    name = str(stream.get("name") or "")
    pattern = str(opts.get("legacy_nonmaterial_pattern", pipe_data.LEGACY_NONMATERIAL_NAME_PATTERN))
    if not re.match(pattern, name):
        return True
    result_keys = (
        "temperature_c",
        "pressure_bar",
        "mass_flow_kgh",
        "mole_flow_kmolh",
        "vap_frac",
        "vol_flow_m3h",
    )
    return any(stream.get(k) is not None for k in result_keys)


def dn1000_oversize_pct(d_calc_mm: float, options: Mapping[str, Any] | None = None) -> float:
    """计算内径相对 DN1000 标准内径(Φ1016×10 → 996 mm)超出百分比,§19.3。"""
    opts = options or {}
    std_inner = float(opts.get("dn1000_std_inner_mm", pipe_data.DN1000_STD_INNER_MM))
    if std_inner <= 0:
        return 0.0
    return (float(d_calc_mm) - std_inner) / std_inner * 100.0


def hp_stainless_force_code(
    material: str, abs_kpa: float | None, options: Mapping[str, Any] | None = None
) -> str | None:
    """高压不锈钢升档(§17.3/§17.8):绝压 4.7–5.6 MPa 的 S30408/S31603 → 强制 R。

    返回强制代号;不满足返回 None。温度**不**作硬门槛(用户 2026-11-18 确认
    "全部升 R1E,按压力带 + 材质触发"),只在 warning 里陈述实测温度。
    下限 4.7:PG-0116/PL-0125(4700 kPa)与 4800 kPa 不再在 Q1E/R1E 间跳档。
    """
    if abs_kpa is None:
        return None
    opts = options or {}
    lo, hi = opts.get("hp_ss_pressure_band_mpa", pipe_data.HP_SS_PRESSURE_BAND_MPA)
    grades = opts.get("hp_ss_grades", pipe_data.HP_SS_GRADES)
    if material in grades and float(lo) * 1000.0 <= abs_kpa <= float(hi) * 1000.0:
        return str(opts.get("hp_ss_force_code", pipe_data.HP_SS_FORCE_CODE))
    return None


def hp_ss_allowable_stress(
    design_c: float | None, options: Mapping[str, Any] | None = None
) -> float | None:
    """R1E 公式用的 S30408 许用应力 [σ]^t(MPa)。

    用户 2026-11-18 第二轮指定字典(见 `pipe_data.HP_SS_STRESS_S30408`),按设计
    温度线性插值:≤100 ℃ 取平值,>350 ℃ 按最后一段斜率外推(结果偏保守,够用;
    超范围会另在 warning 里提示)。与录回的 GB/T 20801.1 表 B.1 并存 —— 本字典
    **只**服务高压升档族的壁厚公式(§17.8)。
    """
    if design_c is None:
        return None
    table = dict(pipe_data.HP_SS_STRESS_S30408)
    if options and options.get("hp_ss_stress_s30408"):
        table = dict(options["hp_ss_stress_s30408"])
    temps = sorted(table)
    if design_c <= temps[0]:
        return float(table[temps[0]])
    if design_c <= temps[-1]:
        for lo, hi in zip(temps, temps[1:], strict=False):
            if lo <= design_c <= hi:
                ratio = (design_c - lo) / (hi - lo)
                return float(table[lo] + (table[hi] - table[lo]) * ratio)
    lo, hi = temps[-2], temps[-1]
    slope = (table[hi] - table[lo]) / (hi - lo)
    return float(table[hi] + slope * (design_c - hi))


def hp_ss_required_wall(
    p_mpa: float,
    od_mm: float,
    design_c: float | None,
    options: Mapping[str, Any] | None = None,
    sigma_override: float | None = None,
) -> float | None:
    """R1E 高压不锈钢壁厚公式(§17.8,用户指定口径):

        t = P·D_o / (2[σ]^t·E_j + P) + C,  t_req = t / 0.875

    设计压力 P 按注 1(§17.1)取,E_j=1.0,C=1.0 mm,负偏差按 12.5% 一次性除入;
    返回的 t_req 再向上圆整到标准壁厚序列(圆整时**不再**叠加负偏差,避免双扣)。
    `sigma_override`:非 S30408 牌号(如 S31603)由调用方查录回的表 B.1 传入。
    """
    sigma = (
        sigma_override if sigma_override is not None else hp_ss_allowable_stress(design_c, options)
    )
    if sigma is None or p_mpa is None or p_mpa <= 0 or od_mm <= 0:
        return None

    opts = options or {}
    ej = float(opts.get("hp_ss_weld_factor", pipe_data.HP_SS_WELD_FACTOR))
    c = float(opts.get("hp_ss_corrosion_mm", pipe_data.HP_SS_CORROSION_MM))
    dev = float(opts.get("hp_ss_neg_dev_factor", pipe_data.HP_SS_NEG_DEV_FACTOR))
    if dev <= 0 or dev >= 1:
        return None
    t = p_mpa * float(od_mm) / (2.0 * sigma * ej + p_mpa) + c
    return t / dev


def design_pressure(
    press_bar_abs: float | None, options: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """操作绝压(bar) → 设计压力(MPa)。

    口径(2026-11-18 第三轮修订,§19.1):
      * 绝压与表压分开:表压 = 绝压 − 101.3 kPa,绝压/表压都带在返回值里;
      * 真空判定:绝压 < 95.0 kPa(表压 < −6.3 kPa)→ 真空等级 V,内压按最小
        标准壁厚,外压按 0.1 MPa 失稳校核;100 kPa 的 1 bar 常压流股不再误判;
      * 非真空:`P_design = max(P_表压 × 1.15, P_表压 + 0.15 MPa, 1.0 MPa)`。
    """
    opts = options or {}
    factor = float(opts.get("pressure_factor", 1.15))
    add_mpa = float(opts.get("pressure_add_mpa", 0.15))
    floor_mpa = float(opts.get("pressure_floor_mpa", 1.0))
    vacuum_kpa = float(opts.get("vacuum_abs_kpa", VACUUM_ABS_KPA))

    if press_bar_abs is None:
        return {
            "design_mpa": None,
            "gauge_mpa": None,
            "abs_kpa": None,
            "vacuum": False,
            "note": "缺操作压力",
        }

    p_mpa_abs = press_bar_abs / 10.0
    abs_kpa = press_bar_abs * 100.0
    gauge = p_mpa_abs - ATM_MPA
    if abs_kpa < vacuum_kpa - 1e-9:
        return {
            "design_mpa": VACUUM_DESIGN_MPA,
            "gauge_mpa": gauge,
            "abs_kpa": abs_kpa,
            "vacuum": True,
            "note": (
                f"真空管道(绝压 < {vacuum_kpa:g} kPa):内压按最小标准壁厚,按外压 0.1 MPa 失稳校核"
            ),
        }
    return {
        "design_mpa": max(gauge * factor, gauge + add_mpa, floor_mpa),
        "gauge_mpa": gauge,
        "abs_kpa": abs_kpa,
        "vacuum": False,
        "note": "",
    }


def design_temperature(
    temp_c: float | None, insulated: bool | None = None, options: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """设计温度(℃) = 操作温度 + 裕量(§17.4)。

    规范 15–30 ℃,默认取中值 20 ℃。保温判定统一按**设计温度** ≥ 50 ℃ 追加 -H:
    操作 48 ℃ 的 PG-0117/PG-0120 设计温度 68 ℃ → 加 -H,不再因"操作 <50"漏掉。
    `insulated=False` 显式传入时关掉裕量(兼容旧调用),但保温代号仍按设计温度判。
    """
    opts = options or {}
    on_temp = float(opts.get("insulation_on_temp_c", pipe_data.INSULATION_ON_TEMP_C))
    use_margin = bool(opts.get("design_temp_margin", True))
    margin = float(opts.get("design_temp_margin_c", pipe_data.DESIGN_TEMP_MARGIN_C))

    if temp_c is None:
        return {"design_c": None, "insulated": False, "folded": False, "note": "缺介质温度"}

    design_c = float(temp_c) + (margin if use_margin else 0.0)
    if insulated is None:
        insulated = design_c >= on_temp
    return {
        "design_c": design_c,
        "insulated": insulated,
        "folded": False,
        "margin_c": margin if use_margin else 0.0,
        "note": f"设计温度=操作温度+{margin:g} ℃(§17.4)" if use_margin else "",
    }


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
    for lo, hi in zip(temps, temps[1:], strict=False):
        if lo <= t <= hi:
            ratio = (t - lo) / (hi - lo)
            return float(table[lo] + (table[hi] - table[lo]) * ratio)
    return None
