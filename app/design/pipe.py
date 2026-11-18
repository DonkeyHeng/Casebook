# -*- coding: utf-8 -*-
"""app/design/pipe.py —— 管路选型计算(纯函数,不碰 Aspen、不读写库)。

口径来源:`docs/pipe-selection-plan.md` §14(定稿口径)与 §15(开工清单第 3 步)。
标准数据在 `app/design/pipe_data.py`;本模块只做算术,不做判断之外的猜测。

十步流程(每步一个函数,便于单测与写"计算书")
------------------------------------------------
1. `phase_of()`            判相态(液 / 气 / 气液共存)
2. `medium_code()`         介质代号(PG / PL / PGL,可扩展)
3. `velocity_for()`        选流速(液 / 气 / 两相 / 泵入口)
4. `inner_diameter()`      内径 → 两相再按 ρv² / 上游液相修正 → `select_dn()` 选档
5. `design_pressure()`     设计压力 max(表压×1.15, 表压+0.15, 1.0 MPa);真空单独处理
6. `design_temperature()`  设计温度(保温取介质温度;65–80 ℃ 无保温折 0.95)
7. `allowable_stress()`    许用应力(牌号 + 温度查表,缺温度内插,T<40 取 40 ℃)
8. `calc_wall_thickness()` t_c = P·D_o / [2(S·Φw + P·Y)]
9. `round_up_wall()`       圆整到标准壁厚(含压力等级最小壁厚 + 工艺最小壁厚下限)
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
import re
from dataclasses import dataclass, replace
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


#: 无缝钢管的上限公称直径;再大就走卷制/焊接管,「管道类型」列跟着变。
DN_SEAMLESS_MAX = 600


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


def usable_dn_series(
    dn_to_od: Mapping[int, float], options: Mapping[str, Any] | None = None
) -> dict[int, float]:
    """屏蔽非优先管径(§17.2):DN65 → DN80、DN125 → DN150。

    做法是把被禁的档从系列里拿掉,后面 `select_dn` 按"实际内径 ≥ 计算内径"逐档试,
    自然就落到上一档优先 DN 上 —— 不在选取环节写死"映射",这样改禁档表只改数据。
    """
    opts = options or {}
    banned = opts.get("disallowed_dn", pipe_data.DISALLOWED_DN)
    return {dn: od for dn, od in dn_to_od.items() if dn not in banned}


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
    min_dn: int | None = None,
    oversize_tol: float = 0.0,
    oversize_band: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """按**实际内径**选档:从小到大试 DN,直到 `外径 − 2×标准壁厚 ≥ 计算内径`。

    这一步是必须的:DN 只是公称直径,Φ273 的内径约 269 mm,若只按"DN ≥ 内径"选档,
    会一路选大、实际流速偏低(实测 #19–#22 有 40–57 条 V2 就是这么来的)。

    `wall_for(od)` 负责给出该外径最终选定的标准壁厚(build 里会连负偏差一起算)。
    `oversize_band` 限定容差只作用于指定计算内径区间(§19.3 DN1000 的 996–1045 mm),
    不给则沿用旧的"只差一点点"全区间容差。
    """
    for dn in sorted(dn_to_od):
        if max_dn is not None and dn > max_dn:
            break
        if min_dn is not None and dn < min_dn:
            continue   # DN10 只留给仪表/采样,不参与常规物流选型
        od = float(dn_to_od[dn])
        wall = wall_for(od)
        if wall is None:
            # 没有壁厚数据时退化成"外径 ≥ 计算内径"的判断,别把整条流股判死
            if od >= inner_d_mm - 1e-9:
                return {"dn": dn, "od_mm": od, "wall_mm": None}
            continue
        if od - 2.0 * wall >= inner_d_mm - 1e-9:
            return {"dn": dn, "od_mm": od, "wall_mm": wall}

    # 只差一点点就够着的,允许按容差取最大档(内径超 3–5% 换流速略超,
    # 比"选型失败、要并联或专项设计"实际得多)。容差默认关,由 options 打开。
    if oversize_tol > 0 and dn_to_od:
        lo, hi = oversize_band if oversize_band is not None else (0.0, float("inf"))
        if lo - 1e-9 <= inner_d_mm <= hi + 1e-9:
            dn = max(dn_to_od)
            od = float(dn_to_od[dn])
            wall = wall_for(od)
            if wall is not None and (od - 2.0 * wall) * (1.0 + oversize_tol) >= inner_d_mm - 1e-9:
                return {"dn": dn, "od_mm": od, "wall_mm": wall, "tolerated": True}
    return {"dn": None, "od_mm": None, "wall_mm": None}


# --------------------------------------------------------------------------
# 5–7. 设计压力 / 设计温度 / 许用应力
# --------------------------------------------------------------------------

ATM_MPA = 0.1013
VACUUM_DESIGN_MPA = 0.1
#: 真空判定**只看绝压**:操作绝压 < 95.0 kPa 即真空(表压 < −6.3 kPa,§19.1)。
#: 101.3 → 95.0:工程建模常把常压流股设成 1 bar = 100 kPa,旧阈值把它们误判成真空,
#: 设计压力从 1.0 MPa 掉到 0.1 MPa;95.0 只抓真正有负压的流股(如 91.3 / 10 / 1.9 kPa)。
#: 1.0 bar = 100 kPa > 95.0、1.01325 bar = 101.325 kPa > 95.0,都是常压。
VACUUM_ABS_KPA = pipe_data.VACUUM_ABS_KPA


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
        "temperature_c", "pressure_bar", "mass_flow_kgh", "mole_flow_kmolh",
        "vap_frac", "vol_flow_m3h",
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
        for lo, hi in zip(temps, temps[1:]):
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
    sigma = sigma_override if sigma_override is not None else hp_ss_allowable_stress(design_c, options)
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
            "note": f"真空管道(绝压 < {vacuum_kpa:g} kPa):内压按最小标准壁厚,按外压 0.1 MPa 失稳校核",
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
    phase: str, abs_kpa: float | None, temp_c: float | None, options: Mapping[str, Any] | None = None
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
    """管道等级号 = 压力代号 + 顺序号(默认 1) + 材质代号,绝热代号接在后面。

    两个代号来自 options(§17):
      * `vacuum=True` → 压力代号用 V(真空管路,与高压 H 区分);
      * `force_pn_code` → 强制升档(高压不锈钢 4.8–5.6 MPa 升 R)。
    """
    opts = options or {}
    seq = str(opts.get("sequence_no", 1))
    if opts.get("vacuum"):
        pn = str(opts.get("vacuum_pn_code", pipe_data.VACUUM_PN_CODE))
    elif opts.get("force_pn_code"):
        pn = str(opts["force_pn_code"])
    else:
        pn = pn_code_for(design_mpa)
    if pn is None:
        return {"code": None, "note": f"设计压力 {design_mpa:g} MPa 超出 PN 代号表"}
    suffix = ""
    auto = opts.get("auto_insulation", True)
    if not insulation and auto and design_c is not None:
        if design_c >= float(opts.get("insulation_on_temp_c", pipe_data.INSULATION_ON_TEMP_C)):
            insulation = "H"  # 保温
        elif design_c <= float(
            opts.get("cold_insulation_below_c", pipe_data.COLD_INSULATION_BELOW_C)
        ):
            insulation = "C"  # 保冷:低温管线不标,现场会当成普通管去包
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
        key = "pump_inlet" if (r.get("pump_inlet") or r.get("cavitation_limited")) else phase
        lo, hi = VELOCITY_RANGE.get(key, (0.0, float("inf")))
        # 两相流"慢下来"是刻意的:管径被 ρv² 或上游液相管径抬高之后,实际流速
        # 本来就会掉到 5 m/s 以下。这时再报"低于推荐区间"是自相矛盾的噪音,
        # 只说上界(太快会冲蚀/振动),下界交给 ρv² 那条判据。
        too_slow = v_act is not None and v_act < lo - 1e-9 and phase != "two"
        too_fast = v_act is not None and v_act > hi + 1e-9
        if v_act is not None and not v5_hit and (too_slow or too_fast):
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

        # V4 等级号的压力代号 < 设计压力(真空代号 V 不参与 PN 数值比较)
        code = r.get("grade_code") or ""
        if code and r.get("design_pressure_mpa") is not None:
            prefix = code[:1]
            if prefix == pipe_data.VACUUM_PN_CODE:
                pass  # V = 真空管路,按外压校核,不与 PN 表比大小
            else:
                pn = pipe_data.PN_CODES.get(prefix)
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

        # V10 非标准流水号(如 AB/BC):只高亮提示,不强行重映射(§17.5)
        if r.get("nonstandard_no"):
            add(
                name,
                "V10",
                "warn",
                "非标准流水号(非四位数字),保留原名待人工确认",
                "stream",
            )

        # V11 真空 DN≥150:外压失稳需专项复核(§17.1)
        if r.get("vacuum") and dn is not None and dn >= pipe_data.VACUUM_EXTERNAL_MIN_DN:
            add(
                name,
                "V11",
                "warn",
                f"真空 DN{dn}:已按外压失稳算最小壁厚 {r.get('external_wall_mm')} mm,"
                "仍需按实际支撑跨距复核",
                "wall_mm",
            )

        # V12 大口径两相流分层/波状流风险(§17.2)
        if r.get("stratified_risk"):
            add(
                name,
                "V12",
                "warn",
                f"DN{dn} 气液两相:均相 ρv² 判据不足,分层/波状流风险需专项核算",
                "velocity",
            )

        # V13 高压高温不锈钢应升 R1E 而非 Q1E(§17.3/§17.8)
        if r.get("hp_ss_upgraded"):
            add(
                name,
                "V13",
                "warn",
                "高压(4.7–5.6 MPa)不锈钢:等级升 R1E,按 [σ]^t 公式重算壁厚,避免高温薄壁倒挂",
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
    # 泵入口流速只对泵成立;位号 K 开头的透平不算泵(见 block_family 注释)
    from . import block_family

    pump_names = {
        b["name"] for b in blocks if block_family.family_of_block(b) == "pump"
    }
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
    min_dn: int | None = None
    #: 两相流的内径下限(由上游纯液相进料反推),键是流股名
    min_inner_d: Mapping[str, float] | None = None
    #: 屏蔽非优先 DN 后实际参与选型的系列(§17.2)
    dn_series: Mapping[int, float] | None = None
    #: 泵排出口/明显升压流股(§17.8 汽蚀排除),键是流股名
    pressurized: frozenset = frozenset()




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
    force_pn_code: str | None = None,
    design_c: float | None = None,
    material: str = "",
    hot_vacuum: bool = False,
) -> tuple[int, float, dict, dict, float | None, float, str, float | None] | None:
    """选 DN、定壁厚、核实际流速。

    返回 (dn, od, w, wall, min_wall_mm, t_req, pipe_type, ext_wall_mm);选不出档位时 None。
    `force_pn_code` 是高压不锈钢升 R1E(§17.3/§17.8)的代号。
    **R1E 不锈钢族(升档或自然)按 §17.8 公式算壁厚**,不吃整档 PN 类保护 ——
    类保护按 PN10 算会让 DN600 两相线要 25+ mm 超出标准系列,而旧的 16 mm 下限
    又让 190 ℃ 管比 173 ℃ 管薄(倒挂);公式化后同一压力族内"温度越高壁越厚"。
    `hot_vacuum`:操作 ≥250 ℃ 的真空管,壁厚下限提到 10 mm(§17.8 高温外压失稳)。
    """
    dn_to_od = ctx.dn_series if ctx.dn_series is not None else ctx.dn_to_od
    od_to_dn = ctx.od_to_dn
    wall_grade = ctx.wall_grade
    max_dn = ctx.max_dn
    min_dn = ctx.min_dn

    hp_formula = bool(
        material in pipe_data.HP_SS_GRADES
        and (force_pn_code == "R" or pn_code_for(pd["design_mpa"] or 0.0) == "R")
    )

    # 压力等级保护:管道得扛得住自己那个**等级**(PN),而不只是扛住算出来的 Pd。
    # 否则 PN16 的 DN50 按 11 MPa 算出 3 mm —— 许用应力够、试压按 PN16 走,
    # 壁厚却停在 PN10 的水平,现场无从解释。
    pn_code = force_pn_code or pn_code_for(pd["design_mpa"] or 0.0)
    class_mpa = pipe_data.PN_CODES.get(pn_code) if pn_code else None
    if pd["vacuum"]:
        class_mpa = None  # 真空按外压校核,不吃内压等级保护
    if hp_formula:
        class_mpa = None  # R1E 不锈钢族走 §17.8 公式,按设计压力算,不按整档 PN


    def _walls_of(od: float) -> tuple[Sequence[float] | None, bool]:
        """该外径的标准壁厚序列;没录到且是大口径时用焊接管常用序列兜底。"""
        walls = od_to_walls_used.get(od)
        if walls:
            return walls, False
        if od > 630.0:
            return pipe_data.WELDED_WALL_SERIES, True
        return None, False

    def _t_required(od: float) -> float | None:
        """所需壁厚。

        R1E 不锈钢族:t = P·D/(2[σ]^t·E_j+P) + C 后 ÷0.875(§17.8,负偏差已含);
        其余:max(按 Pd 算, 按等级 PN 算) + 腐蚀裕量,负偏差在圆整时另扣。
        """
        if hp_formula:
            return hp_ss_required_wall(
                pd["design_mpa"] or 0.0, od, design_c, ctx.opts,
                sigma_override=None if material == "S30408" else sigma,
            )
        w = calc_wall_thickness(pd["design_mpa"] or 0.0, od, sigma or 0.0)
        if not w["applicable"]:
            return None
        t = w["t_calc_mm"] or 0.0
        if class_mpa:
            wc = calc_wall_thickness(class_mpa, od, sigma or 0.0)
            if wc["t_calc_mm"]:
                t = max(t, wc["t_calc_mm"])
        return t + corrosion_c2

    def _process_min_wall(dn: int | None, od: float) -> float | None:
        """最小壁厚下限:工艺下限 + 真空外压下限 + R1E 小管 3 mm + 高温真空 10 mm,取大。"""
        mw = _min_wall_ctx(ctx, dn)
        if pd["vacuum"]:
            ext = external_pressure_min_wall(od, ctx.opts)
            if ext is not None:
                mw = ext if mw is None else max(mw, ext)
            if hot_vacuum and dn is not None and dn >= pipe_data.VACUUM_HOT_MIN_DN:
                floor_hot = float(ctx.opts.get(
                    "vacuum_hot_min_wall_mm", pipe_data.VACUUM_HOT_MIN_WALL_MM
                ))
                mw = floor_hot if mw is None else max(mw, floor_hot)
                warnings.append(
                    f"高温真空 DN{dn}:壁厚下限 {floor_hot:g} mm 已计入(§17.8)"
                )

        if hp_formula:
            # R1E 小口径机械强度保护:DN15 一类不小于 3 mm(§17.8)
            hp_min = float(ctx.opts.get("hp_ss_min_wall_mm", pipe_data.HP_SS_MIN_WALL_MM))
            mw = hp_min if mw is None else max(mw, hp_min)
        return mw

    def _wall_for(od: float) -> float | None:
        """某外径下,考虑许用应力、负偏差与最小壁厚下限后最终选定的标准壁厚。"""
        t_req = _t_required(od)
        if t_req is None:
            return None
        walls, _welded = _walls_of(od)
        mw = _process_min_wall(od_to_dn.get(float(od)), od)
        if hp_formula:
            # 公式里已 ÷0.875 扣过负偏差,圆整时不能再叠一次(§17.8)
            return round_up_wall(t_req, walls, None, None, mw)["wall_mm"]
        return round_up_wall(t_req, walls, od, wall_grade, mw)["wall_mm"]


    # §19.3:DN1000(Φ1016×10 → 内径 996 mm)允许 +5%,即计算内径 996–1045 mm
    # 仍选 DN1000 焊接管,不再踢进未选型。options 可关掉(tol=0)或改区间。
    tol = float(ctx.opts.get("oversize_tolerance", pipe_data.OVERSIZE_TOLERANCE) or 0.0)
    band = (
        float(ctx.opts.get("dn1000_tol_min_mm", pipe_data.DN1000_STD_INNER_MM)),
        float(ctx.opts.get("dn1000_tol_max_mm", pipe_data.DN1000_TOL_MAX_MM)),
    )
    sel = select_dn(
        d_i, dn_to_od, od_to_walls_used, _wall_for,
        min_dn=min_dn, oversize_tol=tol, oversize_band=band,
    )
    dn, od = sel["dn"], sel["od_mm"]
    if dn is None:
        return None
    if dn > max_dn:
        warnings.append(f"DN{dn} 超出本期上限 DN{max_dn},需专项确认")
    if sel.get("tolerated"):
        pct = dn1000_oversize_pct(d_i, ctx.opts)
        # 两种表述都保留:既说"流速略超上限+内径",也说"略超DN1000上限+百分比",
        # 让《未选型流股》里原来的 0222 / 0215 一类流股在备注里能直接对上号。
        warnings.append(f"流速略超上限(内径{d_i:.1f}mm),建议复核压降")
        warnings.append(f"计算内径略超DN1000上限(约{pct:.1f}%),需复核实际压降")

    w = calc_wall_thickness(pd["design_mpa"] or 0.0, od, sigma or 0.0)
    if not w["applicable"]:
        warnings.append(w["note"])

    t_req = _t_required(od) or 0.0
    walls, welded = _walls_of(od)
    if welded:
        warnings.append(
            f"DN{dn} 属大口径焊接管:壁厚按 {pipe_data.WELDED_WALL_SERIES} 常用序列圆整,"
            "需按 GB/T 3091 / GB/T 9711 复核"
        )
    ext_wall_mm = external_pressure_min_wall(od, ctx.opts) if pd["vacuum"] else None
    if pd["vacuum"]:
        note = (
            f"真空流股:内压按最小壁厚,外压失稳筛查 t_min={ext_wall_mm:.2f} mm(§17.1),"
            "真实支撑跨距需按 GB/T 20801.3 复核"
        )
        warnings.append(note)

    min_wall_mm = _process_min_wall(dn, od)
    if hp_formula:
        # 公式口径:负偏差已 ÷0.875 含入 t_req,圆整只看标准序列与下限(§17.8)
        wall = round_up_wall(t_req, walls, None, None, min_wall_mm)
    else:
        wall = round_up_wall(t_req, walls, od, wall_grade, min_wall_mm)
    if wall["wall_mm"] is None:
        warnings.append(wall["note"])
    else:
        c1 = pipe_data.negative_deviation(od, wall["wall_mm"], wall_grade) if not hp_formula else 0.0
        v_act = actual_velocity(float(q), od - 2.0 * wall["wall_mm"])
        if v_act is not None:
            lo, hi = VELOCITY_RANGE.get(
                "pump_inlet" if is_pump_in else phase, (0.0, float("inf"))
            )
            too_slow = v_act < lo - 1e-9 and phase != "two"   # 两相慢下来是刻意的
            if too_slow or v_act > hi + 1e-9:
                warnings.append(
                    f"实际流速 {v_act:.2f} m/s 超出推荐 {lo:g}–{hi:g} m/s(负偏差 {c1:.2f} mm)"
                )
    if hp_formula:
        warnings.append(
            f"R1E 公式口径:t={t_req:.2f} mm(按 [σ]^t 插值、C=1.0、÷0.875),"
            "圆整到该外径标准壁厚(§17.8)"
        )
    pipe_type = "无缝" if dn <= DN_SEAMLESS_MAX else "焊接"
    return dn, od, w, wall, min_wall_mm, t_req, pipe_type, ext_wall_mm



def _upstream_liquid_inner(
    rows: Sequence[Mapping[str, Any]], conns: Sequence[Mapping[str, Any]]
) -> dict[str, float]:
    """两相流的内径下限:产出它的设备,其纯液相进料有多粗,它就是多粗。

    闪蒸多发生在"液相进料 → 节流/加热"这一步。两相出口比进口细,就等于
    "液体闪蒸后管径骤缩"—— 现场表现为段塞、振动与冲蚀,靠流速区间拦不住。
    """
    inner = {str(r["stream"]): float(r["inner_d_mm"]) for r in rows}
    phase = {str(r["stream"]): r["phase"] for r in rows}
    producers: dict[str, set[str]] = {}
    feeds: dict[str, list[str]] = {}
    for c in conns:
        direction = str(c.get("direction", "")).lower()
        block = str(c.get("block"))
        stream = str(c.get("stream"))
        if direction == "out":
            producers.setdefault(stream, set()).add(block)
        elif direction == "in":
            feeds.setdefault(block, []).append(stream)

    out: dict[str, float] = {}
    for r in rows:
        if r["phase"] != "two":
            continue
        ups = [
            inner.get(s, 0.0)
            for b in producers.get(str(r["stream"]), set())
            for s in feeds.get(b, [])
            if phase.get(s) == "liquid"
        ]
        if ups and max(ups) > float(r["inner_d_mm"]):
            out[str(r["stream"])] = max(ups)
    return out


def _pressurized_lines(
    streams: Sequence[Mapping[str, Any]],
    conns: Sequence[Mapping[str, Any]],
    options: Mapping[str, Any] | None = None,
) -> set[str]:

    """泵排出口 / 明显升压流股(§17.8):与上游同流量同温度流股比压力。

    判据:产出它的设备里存在一条进料,流量差 ≤2%、温差 ≤2 ℃,而本流股压力比它
    高出阈值(默认 0.05 MPa)—— 那就是泵/压缩机加压后的排出口,严禁按吸入侧
    汽蚀限速放大管径(如 PL-0136/PL-0511)。真正的泵出口另有拓扑标记,两者取并集。
    """
    opts = options or {}
    rise_bar = float(opts.get("cavitation_pressure_rise_mpa",
                              pipe_data.CAVITATION_PRESSURE_RISE_MPA)) * 10.0
    by_name = {str(s.get("name")): s for s in streams}
    producers: dict[str, set[str]] = {}
    feeds: dict[str, list[str]] = {}
    for c in conns:
        direction = str(c.get("direction", "")).lower()
        block = str(c.get("block"))
        stream = str(c.get("stream"))
        if direction == "out":
            producers.setdefault(stream, set()).add(block)
        elif direction == "in":
            feeds.setdefault(block, []).append(stream)

    out: set[str] = set()
    for name, s in by_name.items():
        my_p = s.get("pressure_bar")
        my_q = s.get("vol_flow_m3h")
        my_t = s.get("temperature_c")
        if my_p is None:
            continue
        for b in producers.get(name, set()):
            for fin in feeds.get(b, []):
                f = by_name.get(fin)
                if not f or f.get("pressure_bar") is None:
                    continue
                same_q = (
                    isinstance(my_q, (int, float)) and isinstance(f.get("vol_flow_m3h"), (int, float))
                    and abs(float(my_q) - float(f["vol_flow_m3h"])) <= 0.02 * max(float(my_q), 1e-9)
                )
                same_t = (
                    isinstance(my_t, (int, float)) and isinstance(f.get("temperature_c"), (int, float))
                    and abs(float(my_t) - float(f["temperature_c"])) <= 2.0
                )
                if same_q and same_t and float(my_p) > float(f["pressure_bar"]) + rise_bar:
                    out.add(name)
    return out



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
    zero_limit = float(opts.get("zero_flow_m3h", pipe_data.ZERO_FLOW_M3H))
    if q is None or q <= 0:
        return None, {"stream": name, "reason": "缺体积流量,未选型"}
    if 0 < q <= zero_limit:
        # 零流量/备用或放空管线:不按常规物流定死管径(§17.5)
        return None, {
            "stream": name,
            "reason": "零流量/备用或放空管线,需人工确认管径",
            "zero_flow": True,
            "vol_flow_m3h": float(q),
        }
    phase = phase_of(s.get("vap_frac"))
    is_pump_in = name in pump_in
    v = velocity_for(phase, is_pump_in, opts)
    warnings: list[str] = []

    # 绝压先算出来,供真空/汽蚀/高压升档共用(§17)
    abs_kpa = press_to_abs_kpa(s.get("pressure_bar"))

    # 近常压沸点液相 → 汽蚀限速 0.8 m/s(§17.2/§17.8)。三个条件同时满足才触发:
    # 温度 80–105 ℃、绝压 95–115 kPa(微正压/常压吸入侧),且**不是**泵排出口
    # 或明显升压管线(PL-0136/PL-0511 一类加压线严禁按吸入侧放大管径)。
    cavitation = near_boiling_liquid(
        phase, abs_kpa, s.get("temperature_c"), opts
    ) and not is_pump_in and str(name) not in (ctx.pressurized or pump_out)
    if cavitation:
        v = min(v, float(opts.get("cavitation_velocity_ms", pipe_data.CAVITATION_VELOCITY_MS)))
        warnings.append(
            f"近常压沸点液相反汽蚀:流速限到 {v:g} m/s(§17.8)"
        )

    d_i = inner_diameter(float(q), v)

    # 两相流:动压头 ρv² 是把"段塞 / 振动"折成数字的常用判据。
    # 混合密度直接用 Aspen 给的质量流量 ÷ 体积流量 —— 两相混合物的体积流量就是它,
    # 不需要再把气液两相各自拆开(库里也没存分相流量)。
    if phase == "two":
        mass = s.get("mass_flow_kgh")
        limit = float(opts.get("two_phase_dp_limit_pa", 2000.0))
        if isinstance(mass, (int, float)) and mass > 0 and q > 0 and limit > 0:
            rho_m = float(mass) / float(q)
            v_limit = math.sqrt(limit / rho_m)
            if v_limit < v:
                d_i = max(d_i, inner_diameter(float(q), v_limit))
                warnings.append(
                    f"两相动压头 ρv² ≤ {limit:g} Pa(ρm={rho_m:.1f} kg/m³)→ 限速 {v_limit:.2f} m/s"
                )
    # 闪蒸后骤缩是段塞流的直接诱因:两相段的内径不得小于上游纯液相进料
    floor_d = (ctx.min_inner_d or {}).get(str(name))
    if floor_d and floor_d > d_i:
        d_i = floor_d
        warnings.append(f"两相流管径不小于上游液相进料(内径 {floor_d:.1f} mm)")
    pd = design_pressure(s.get("pressure_bar"), opts)
    td = design_temperature(s.get("temperature_c"), opts.get("insulated"), opts)
    medium = medium_code(phase, opts.get("medium_codes"))
    material, cls, corrosion_c2, od_to_walls_used, sigma = _resolve_material(
        ctx, name, medium, td["design_c"], warnings
    )

    # 高压不锈钢升 R1E(§17.3/§17.8):代号与壁厚公式都按新口径走
    force_code = hp_stainless_force_code(material, abs_kpa, opts)
    hp_upgraded = bool(force_code)
    if force_code:
        warnings.append(
            f"高压不锈钢({abs_kpa / 1000:.2f} MPa,操作 {s.get('temperature_c')} ℃):"
            f"等级升 {force_code}1E,按 [σ]^t 公式重算壁厚(§17.8)"
        )

    # 高温真空管(≥250 ℃,§17.8):DN≥400 的壁厚下限 10 mm 由 _size_pipe 落实
    hot_vacuum = bool(
        pd["vacuum"] and isinstance(s.get("temperature_c"), (int, float))
        and float(s["temperature_c"]) >= float(
            opts.get("vacuum_hot_temp_c", pipe_data.VACUUM_HOT_TEMP_C)
        )
    )
    if hot_vacuum:
        warnings.append(
            f"操作 {float(s['temperature_c']):g} ℃ 真空管:高温弹性模量下降,"
            "需按实际壁温做外压失稳专项复核(§17.8)"
        )

    # §19.2 深真空(绝压 ≤ 10 kPa)专项:防闪蒸 + 高温弹性模量折减。
    # 与 §17.8 的 hot_vacuum(≥250 ℃ 真空管壁厚下限)是两条独立口径,标记都保留。
    op_temp = s.get("temperature_c")
    deep_vacuum = bool(pd["vacuum"] and abs_kpa is not None
                       and abs_kpa <= float(opts.get("deep_vacuum_abs_kpa",
                                                     pipe_data.DEEP_VACUUM_ABS_KPA)))
    deep_hot_vacuum = bool(
        deep_vacuum and isinstance(op_temp, (int, float))
        and float(op_temp) >= float(opts.get("deep_vacuum_hot_temp_c",
                                             pipe_data.DEEP_VACUUM_HOT_TEMP_C))
    )
    if deep_vacuum:
        warnings.append("高真空(≤10kPa):严控吸入压降防闪蒸,建议全焊透/高真空法兰防漏气")
    if deep_hot_vacuum:
        warnings.append("高温深真空:弹性模量折减,严格核算外压失稳支撑跨距")

    # §19.4 温度专项:操作温度(不是设计温度)≤ −20 ℃ / ≥ 300 ℃ 时追加应力与韧性提示。
    low_temp = bool(isinstance(op_temp, (int, float))
                    and float(op_temp) <= float(opts.get("low_temp_note_c", pipe_data.LOW_TEMP_NOTE_C)))
    high_temp = bool(isinstance(op_temp, (int, float))
                     and float(op_temp) >= float(opts.get("high_temp_note_c", pipe_data.HIGH_TEMP_NOTE_C)))
    if low_temp:
        warnings.append("低温工况:复核焊缝低温韧性及低温螺栓选型")
        warnings.append("深冷工况:复核焊缝低温韧性及低温螺栓选型")
    if high_temp:
        warnings.append("高温管线:需做柔性应力分析(热膨胀补偿)")
        warnings.append("高温管线(≥300℃):需做热膨胀柔性应力分析")


    sized = _size_pipe(
        ctx, q, pd, d_i, sigma, corrosion_c2, od_to_walls_used,
        is_pump_in or cavitation, phase, warnings,  # 汽蚀限速口径同泵入口(0.5–1.0 m/s)
        force_pn_code=force_code,
        design_c=td["design_c"],
        material=material,
        hot_vacuum=hot_vacuum,
    )
    if sized is None:
        od_max = max(dn_to_od.values()) if dn_to_od else None
        # 未选型流股按相态分类给设计出路(§17.8):气相可走风管,两相必须并联
        if phase == "gas":
            tail = ",可按低压风管(矩形/圆形薄壁风道)专项设计"
        elif phase == "two":
            tail = ",需多管并联设计"
        else:
            tail = ",需并联或专项设计"
        return None, {
            "stream": name,
            "phase": phase,
            "reason": f"计算内径 {d_i:.1f} mm 超出可选档位"
            + (f"(最大 Φ{od_max:g} = DN{max(dn_to_od)})" if od_max else "")
            + tail,
        }

    dn, od, w, wall, min_wall_mm, t_req, pipe_type, ext_wall_mm = sized
    mcode = pipe_data.MATERIAL_CODE_BY_GRADE.get(material)
    if mcode is None:  # 判不了材质:标"待人工确认",不硬猜一个代号
        mcode = "E"
        warnings.append(f"材质 {material!r} 不在映射表里,等级号末字母按 E 暂记,待人工确认")

    # 等级号:真空走 V,高压不锈钢升档走 force_code,其余按设计压力(§17.1/§17.3)
    gc_opts = dict(opts)
    if pd["vacuum"]:
        gc_opts["vacuum"] = True
    elif force_code:
        gc_opts["force_pn_code"] = force_code
    gc = grade_code(pd["design_mpa"] or 0.0, mcode, td["design_c"], "", gc_opts)
    if gc["code"] is None:
        warnings.append(gc["note"])

    # 大口径两相流分层/波状流风险标记(§17.2)
    stratified = bool(
        phase == "two" and dn is not None
        and dn >= int(opts.get("stratified_flow_min_dn", pipe_data.STRATIFIED_FLOW_MIN_DN))
    )
    if stratified:
        warnings.append(
            f"DN{dn} 大口径两相流:均相 ρv² 判据不足,分层流/波状流风险需专项核算(§17.2)"
        )

    # DN≥700 真空焊接管(§17.8):光靠壁厚不经济,标注加强圈
    stiffener = bool(
        pd["vacuum"] and dn is not None
        and dn >= int(opts.get("vacuum_stiffener_min_dn", pipe_data.VACUUM_STIFFENER_MIN_DN))
    )
    if stiffener:
        warnings.append(
            f"DN{dn} 真空焊接管:需设外压加强圈,严禁全真空抽吸(§17.8)"
        )

    # 非标准流水号(§17.5):只标记,不强行重映射
    pattern = str(opts.get("stream_no_pattern", pipe_data.STREAM_NO_PATTERN))
    nonstandard = re.match(pattern, str(name)) is None

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
        "cavitation_limited": cavitation,
        "nonstandard_no": nonstandard,
        "hp_ss_upgraded": hp_upgraded,
        "stratified_risk": stratified,
        "hot_vacuum": hot_vacuum,
        "vacuum_stiffener": stiffener,
        # §19 第三轮新增标记:Word 表「校核备注」与命令行审计共用
        "deep_vacuum": deep_vacuum,
        "deep_hot_vacuum": deep_hot_vacuum,
        "low_temp": low_temp,
        "high_temp": high_temp,
        "oversize_tolerated": bool(
            wall["wall_mm"] is not None
            and d_i > od - 2.0 * wall["wall_mm"] + 1e-9
        ),
        "operating_temp_c": float(op_temp) if isinstance(op_temp, (int, float)) else None,
        "inner_d_mm": d_i,

        "dn": dn,
        "od_mm": od,
        "pipe_type": pipe_type,
        "vol_flow_m3h": float(q),
        "design_pressure_mpa": pd["design_mpa"],
        "gauge_pressure_mpa": pd["gauge_mpa"],
        "abs_pressure_kpa": abs_kpa,
        "vacuum": pd["vacuum"],
        "external_wall_mm": ext_wall_mm,
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
    tbl, od_to_walls, stress, dn_to_od_full, snap = _tables_for(tables)
    # 屏蔽非优先 DN(§17.2);od_to_dn 反查表跟着筛选后的系列走
    dn_to_od = usable_dn_series(dn_to_od_full, opts)
    blocks = list(blocks or [])
    conns = list(connections or [])
    pump_in, pump_out = _pump_streams(conns, blocks)
    all_streams = list(streams)
    stream_list = list(all_streams)
    # 泵排出口/明显升压流股(§17.8):汽蚀限速的排除名单
    pressurized = frozenset(_pressurized_lines(stream_list, conns, opts)) | frozenset(pump_out)
    # §19.1:Aspen 热量流(HEAT,如 Q1–Q4)与功流(WORK)不是物流,直接剔除;
    # 它们既不进主表也不进《未选型流股》清单(与"未选中的管线"是两回事)。
    stream_list = [s for s in stream_list if is_material_stream(s, opts)]
    ctx = _BuildContext(

        opts=opts,
        tbl=tbl,
        snap=snap,
        dn_to_od=dn_to_od_full,
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
        min_dn=int(opts["min_dn_process"]) if opts.get("min_dn_process") is not None
        else int(pipe_data.MIN_DN_PROCESS),
        dn_series=dn_to_od,
        pressurized=pressurized,
    )

    rows: list[dict[str, Any]] = []

    missing: list[dict[str, Any]] = []
    for s in stream_list:
        row, miss = _select_stream(ctx, s)
        if miss is not None:
            missing.append(miss)
        else:
            rows.append(row)

    # 第二遍:两相流的内径不得小于上游纯液相进料(第一遍算完才知道别人多粗)。
    # 只重算受影响的那几条,不动其它流股。
    min_inner = _upstream_liquid_inner(rows, conns)
    if min_inner:
        ctx2 = replace(ctx, min_inner_d=min_inner)
        rows, missing = [], []
        for s in stream_list:
            row, miss = _select_stream(ctx2, s)
            if miss is not None:
                missing.append(miss)
            else:
                rows.append(row)

    # 零流量/备用流股单列,不与"内径超限"混在一起(§17.5)
    flagged = [m for m in missing if m.get("zero_flow")]
    return {
        "rows": rows,
        "missing": missing,
        "flagged": flagged,
        "summary": {
            "streams": len(rows) + len(missing),
            "excluded_nonmaterial": len(all_streams) - len(stream_list),
            "deep_vacuum": sum(1 for r in rows if r["deep_vacuum"]),
            "low_temp": sum(1 for r in rows if r["low_temp"]),
            "high_temp": sum(1 for r in rows if r["high_temp"]),
            "oversize_tolerated": sum(1 for r in rows if r["oversize_tolerated"]),
            "selected": len(rows),
            "missing": len(missing),
            "zero_flow": len(flagged),
            "vacuum": sum(1 for r in rows if r["vacuum"]),
            "upgraded": sum(1 for r in rows if r["hp_ss_upgraded"]),
            "warned": sum(1 for r in rows if r["warnings"]),
        },
    }
