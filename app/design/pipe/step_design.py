"""十步选型第 5–7 步：设计压力 / 设计温度 / 许用应力。

含高压不锈钢（4.7–6.4 MPa 区间）的强制代号、许用应力与所需壁厚特例，
以及真空判定、物料流股识别等第 5 步的前置判断。
口径来源：`docs/pipe-selection-plan.md` §14 第 5–7 步、§19。"""

from __future__ import annotations

import math
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


def dn1000_oversize_pct(
    d_calc_mm: float,
    options: Mapping[str, Any] | None = None,
    *,
    base_inner_mm: float | None = None,
) -> float:
    """计算内径相对 DN1000 实际圆整内径的超出百分比。

    `base_inner_mm` 由选型处按 `D_o − 2×S` 传入（壁厚越厚，实际内径越小）；
    不传时保留 996 mm 的标准内径口径，兼容只看 DN1000 名义档的外部调用。
    """
    opts = options or {}
    std_inner = (
        float(base_inner_mm)
        if base_inner_mm is not None
        else float(opts.get("dn1000_std_inner_mm", pipe_data.DN1000_STD_INNER_MM))
    )
    if std_inner <= 0:
        return 0.0
    return (float(d_calc_mm) - std_inner) / std_inner * 100.0


def hp_stainless_force_code(
    material: str,
    abs_kpa: float | None,
    options: Mapping[str, Any] | None = None,
    *,
    design_mpa: float | None = None,
) -> str | None:
    """高压不锈钢升档(§17.3/§17.8):绝压 4.7–6.4 MPa 的 S30408/S31603 → 强制 R。

    返回强制代号;不满足返回 None。温度**不**作硬门槛(用户 2026-11-18 确认
    "全部升 R1E,按压力带 + 材质触发"),只在 warning 里陈述实测温度。
    下限 4.7:PG-0116/PL-0125(4700 kPa)与 4800 kPa 不再在 Q1E/R1E 间跳档。
    `design_mpa` 是**只升不降**守卫:P_d > 10.0 MPa 时自然档已到 S/U,
    不能再按操作绝压把等级拉回 R(那会绕开 PN16/PN22 保护并把壁厚公式降回 R1E)。
    """
    if abs_kpa is None:
        return None
    if design_mpa is not None and float(design_mpa) > 10.0 + 1e-9:
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
    温度线性插值。**越界不外推**(与 4.3 节"不猜蠕变段"一致):

      * 设计温度 ≤ 100 ℃ → 钳位取最低档 137 MPa(低温侧偏保守);
      * 设计温度 > 350 ℃ → 返回 None。蠕变段的许用应力必须由人按标准查给,
        继续按最后一段斜率外推只会给出"看着合理"的偏薄壁厚。

    与录回的 GB/T 20801.1 表 B.1 并存 —— 本字典**只**服务高压升档族的壁厚公式(§17.8)。
    """
    if design_c is None:
        return None
    table = dict(pipe_data.HP_SS_STRESS_S30408)
    if options and options.get("hp_ss_stress_s30408"):
        table = dict(options["hp_ss_stress_s30408"])
    temps = sorted(table)
    if design_c <= temps[0]:
        return float(table[temps[0]])  # 低温侧钳位到最低档
    if design_c > temps[-1]:
        return None  # 超出录入范围(蠕变段),不录不猜
    for lo, hi in zip(temps, temps[1:], strict=False):
        if lo <= design_c <= hi:
            ratio = (design_c - lo) / (hi - lo)
            return float(table[lo] + (table[hi] - table[lo]) * ratio)
    return None


def hp_ss_required_wall(
    p_mpa: float,
    od_mm: float,
    design_c: float | None,
    options: Mapping[str, Any] | None = None,
    sigma_override: float | None = None,
    c_mm: float | None = None,
) -> float | None:
    """R1E 高压不锈钢壁厚公式（**项目专用、非标准公式**，不声称标准符合性）。

        t = P·D_o / (2[σ]^t·E_j + P) + C,  t_req = t / 0.875

    本式是项目按 §17.8 指定的专用口径，不是 GB/T 20801.1—2025
    6.6.1.2 公式(1) 的标准实现：分母压力项固定为 P（等效 Y=0.5），
    负偏差按 12.5% 一次性除入，且不参与 PN 整档保护。不得用本式
    反向声明对任何标准的符合性；需要标准符合性结论时，按标准原文
    另行计算并保留完整符号与适用条件。
    设计压力 P 按注 1(§17.1)取,E_j=1.0,负偏差按 12.5% 一次性除入;
    返回的 t_req 再向上圆整到标准壁厚序列(圆整时**不再**叠加负偏差,避免双扣)。
    `c_mm` 是腐蚀裕量 C:**传了就按它算**(管道等级表 > 全局默认),没传才回退
    `hp_ss_corrosion_mm`(1.0 mm)—— 用户按等级表给过腐蚀裕量时不能被常量顶掉。
    `sigma_override`:非 S30408 牌号(如 S31603)由调用方查录回的表 B.1 传入。
    """
    sigma = (
        sigma_override if sigma_override is not None else hp_ss_allowable_stress(design_c, options)
    )
    if sigma is None or p_mpa is None or p_mpa <= 0 or od_mm <= 0:
        return None

    opts = options or {}
    ej = float(opts.get("hp_ss_weld_factor", pipe_data.HP_SS_WELD_FACTOR))
    c = float(
        c_mm if c_mm is not None else opts.get("hp_ss_corrosion_mm", pipe_data.HP_SS_CORROSION_MM)
    )
    dev = float(opts.get("hp_ss_neg_dev_factor", pipe_data.HP_SS_NEG_DEV_FACTOR))
    if dev <= 0 or dev >= 1:
        return None
    t_c = p_mpa * float(od_mm) / (2.0 * sigma * ej + p_mpa)
    t = t_c + c
    # 与通用公式同一薄壁前提 t_c < D_o/6:只校核承压计算厚度,不含腐蚀裕量 C。
    # 小口径下 C 占比大,但 C 不是承压厚度;越界就不硬算厚壁,返回空。
    if t_c * 6.0 >= float(od_mm):
        return None
    return t / dev


def design_pressure(
    press_bar_abs: float | None, options: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """操作绝压(bar) → 设计压力(MPa)。

    口径(2026-11-18 第三轮修订,§19.1):
      * 绝压与表压分开:表压 = 绝压 − 101.3 kPa,绝压/表压都带在返回值里;
      * 真空判定:绝压 < 95.0 kPa(表压 < −6.3 kPa)→ 真空等级 V,内压按最小
        标准壁厚,外压按 0.1 MPa 失稳校核;100 kPa 的 1 bar 常压流股不再误判;
      * 非真空:`P_design = max(0, P_表压 × 1.15, P_表压 + 0.15 MPa, 1.0 MPa)`;
        95–101.3 kPa 的表压为负,0 兜底后为 0 MPa(等级 H,按最小壁厚)。
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
            "invalid": True,
            "note": "缺操作压力，无法计算设计压力、壁厚与等级号",
        }
    try:
        p_abs_bar = float(press_bar_abs)
    except (TypeError, ValueError):
        p_abs_bar = float("nan")
    if not math.isfinite(p_abs_bar):
        return {
            "design_mpa": None,
            "gauge_mpa": None,
            "abs_kpa": None,
            "vacuum": False,
            "invalid": True,
            "note": "操作压力不是有限数值，无法计算设计压力、壁厚与等级号",
        }
    if p_abs_bar <= 0:
        return {
            "design_mpa": None,
            "gauge_mpa": None,
            "abs_kpa": p_abs_bar * 100.0,
            "vacuum": False,
            "invalid": True,
            "note": "操作绝压 ≤ 0 非法，无法计算设计压力、壁厚与等级号",
        }

    p_mpa_abs = p_abs_bar / 10.0
    abs_kpa = p_abs_bar * 100.0
    gauge = p_mpa_abs - ATM_MPA
    if abs_kpa < vacuum_kpa - 1e-9:
        return {
            "design_mpa": VACUUM_DESIGN_MPA,
            "gauge_mpa": gauge,
            "abs_kpa": abs_kpa,
            "vacuum": True,
            "invalid": False,
            "note": (
                f"真空管道(绝压 < {vacuum_kpa:g} kPa):内压按最小标准壁厚,按外压 0.1 MPa 失稳校核"
            ),
        }
    return {
        "design_mpa": max(0.0, gauge * factor, gauge + add_mpa, floor_mpa),
        "gauge_mpa": gauge,
        "abs_kpa": abs_kpa,
        "vacuum": False,
        "invalid": False,
        "note": "",
    }


def design_temperature(
    temp_c: float | None, insulated: bool | None = None, options: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """设计温度(℃):高温段 = 操作温度 + 裕量,低温段 = 操作温度 − 裕量(§17.4)。

    规范 15–30 ℃,默认取 20 ℃。裕量要往**更危险的一侧**取:

      * 操作温度 ≥ 0 ℃ → `T_d = t + 20`,保温判定按设计温度 ≥ 50 ℃ 追加 -H
        (操作 48 ℃ 的 PG-0117/PG-0120 设计温度 68 ℃ → 加 -H,不再漏);
      * 操作温度 < 0 ℃ → `T_d = t − 20`,即比操作温度更冷。否则 +20 ℃ 会把整条
        低温管抬高 20 K:保冷 `-C`(按设计温度 ≤ 0 ℃ 判)与低温告警 V7
        (按设计温度 < −20 ℃ 判)一起漏判 —— 操作 −30 ℃ 曾被算成 −10 ℃。

    `insulated=False` 显式传入时关掉裕量(兼容旧调用),但保温代号仍按设计温度判。
    """
    opts = options or {}
    on_temp = float(opts.get("insulation_on_temp_c", pipe_data.INSULATION_ON_TEMP_C))
    use_margin = bool(opts.get("design_temp_margin", True))
    margin = float(opts.get("design_temp_margin_c", pipe_data.DESIGN_TEMP_MARGIN_C))

    if temp_c is None:
        return {"design_c": None, "insulated": False, "folded": False, "note": "缺介质温度"}

    signed = margin if float(temp_c) >= 0.0 else -margin
    design_c = float(temp_c) + (signed if use_margin else 0.0)
    if insulated is None:
        insulated = design_c >= on_temp
    if not use_margin:
        note = ""
    elif signed < 0:
        note = f"设计温度=操作温度−{margin:g} ℃(低温段取偏冷侧,§17.4)"
    else:
        note = f"设计温度=操作温度+{margin:g} ℃(§17.4)"
    return {
        "design_c": design_c,
        "insulated": insulated,
        "folded": False,
        "margin_c": signed if use_margin else 0.0,
        "note": note,
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
