"""泵的派生量:密度、自算扬程、NPSHₐ、建议电机功率。

为什么单独一个模块
------------------
这些量不是"读出来"的,是算出来的,而且要在页面 / Excel / AI 包 / 对比四个出口保持同值。
所以放纯函数:不碰 Aspen、不读库,能直接进 selftest。

口径(实测来源见 docs/aspen-field-map.md §13)
---------------------------------------------
* 泵块 `RHO_LIQ` 是**摩尔密度**(kmol/m³),质量密度 = `RHO_LIQ × MW_LIQ`;
* 扬程优先用 Aspen 的 `HEAD_CAL`;`head_from_dp()` 只做交叉校验,不当主值;
* `NPSH-AVAIL` 实测不可靠(多处为 0、量级也对不上),所以 NPSHₐ 自算:
  `NPSHₐ = (p_in − p_v) × 10⁵ ÷ (ρ·g)`,速度头忽略(< 0.5 m);
* 建议电机 = `轴功率 ÷ 传动效率(直联 1.0) × 裕量`,再向上取功率档位
  —— 电机铭牌是轴输出功率,不再除电机效率;效率只用于电功率折回轴功率。

⚠️ 功率档位表是**工程常用序列**,不是标准原文;要引用标准请另附出处。
"""

from __future__ import annotations

import math
from decimal import ROUND_CEILING, Decimal

# 这三个是从 app/values.py 转出来的**对外接口**:调用方一直写 `pump.pick_motor(...)`。
# 实现放在 app/values.py 是为了让读取层也能用(读取层不能反过来依赖派生层),
# 这里保留原名,调用方不用改。
from app.values import (
    DEFAULT_MARGIN,
    MOTOR_EFF,
    MOTOR_KW,
    MOTOR_MARGIN_TIERS,
    MOTOR_RULE,
    TRANSMISSION_EFF_DIRECT,
    TWO_PHASE_VAP_EPS,
    mass_density_kgm3,
    motor_margin,
    pick_motor,
    sanitize_pv_bar,
)

__all__ = [
    "DEFAULT_MARGIN",
    "G",
    "TWO_PHASE_VAP_LIMIT",
    "MOTOR_EFF",
    "MOTOR_KW",
    "MOTOR_MARGIN_TIERS",
    "MOTOR_RULE",
    "NPSH_RULE",
    "head_from_dp",
    "mass_density_kgm3",
    "motor_margin",
    "npsh_available_m",
    "pick_motor",
    "sanitize_pv_bar",
    "selection_head_m",
    "spec",
]

G = 9.80665  # m/s²
# 进口汽化率超过该值视为两相:ρ=m/V 已是混合密度,汽蚀公式前提不成立,四出口统一不计算。
# 与管道侧 `pipe.constants.PHASE_EPS` 同一常量(app/values.py),避免同一流股两处判成不同相态。
TWO_PHASE_VAP_LIMIT = TWO_PHASE_VAP_EPS
NPSH_RULE = "NPSHₐ = (p_进口 − p_v) × 10⁵ ÷ (ρ·g),忽略速度头;两相进口不计算"


def selection_head_m(head_m, margin=DEFAULT_MARGIN) -> int | None:
    """选型扬程:按十进制 1.1 倍后向上取整,避免二进制浮点把 55 变成 56。

    `head_m` 先按 9 位小数收敛,吸收上游加法产生的 50.00000000000001 这类噪声;
    非有限值、缺失或 ≤0 一律返回 None。
    """
    if head_m is None:
        return None
    try:
        if not math.isfinite(float(head_m)):
            return None
        head = round(float(head_m), 9)
    except (TypeError, ValueError):
        return None
    if head <= 0:
        return None
    product = Decimal(str(head)) * Decimal(str(margin))
    return int(product.to_integral_value(rounding=ROUND_CEILING))


def spec() -> dict:
    """给前端用的常量与口径说明(避免公式在 JS 里再抄一遍抄错)。"""
    return {
        "g": G,
        "motor_eff": MOTOR_EFF,
        "transmission_eff": TRANSMISSION_EFF_DIRECT,
        "margin": DEFAULT_MARGIN,
        "motor_margin_tiers": [
            {"upper_kw": upper, "margin": margin} for upper, margin in MOTOR_MARGIN_TIERS
        ],
        "motor_kw": list(MOTOR_KW),
        "motor_rule": MOTOR_RULE,
        "npsh_rule": NPSH_RULE,
    }


def head_from_dp(p_in_bar, p_out_bar, rho_kgm3) -> float | None:
    """压差反算扬程(m),只用于与 Aspen 的 HEAD_CAL 交叉校验。

    负的 Δp 会给出负扬程 —— 反向工况本来就是这样,不做绝对值处理。
    """
    if p_in_bar is None or p_out_bar is None or not rho_kgm3:
        return None
    try:
        dp = float(p_out_bar) - float(p_in_bar)
        return dp * 1e5 / (float(rho_kgm3) * G)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def npsh_available_m(p_in_bar, p_v_bar, rho_kgm3, vap_frac=None) -> float | None:
    """有效汽蚀余量(m)。

    缺 p_v 就返回 None —— **不允许**拿进口压力顶替或默认 0,那会把"没算"显示成"不汽蚀"。
    进口两相(vap_frac > 1e-4)同样不计算:两相进口已含气相,单相汽蚀余量公式
    （净正吸入压头全部由液相提供）的前提不成立,不是换一个混合密度就能修好。
    p_in 还必须有限且 > 0:0 / 负绝压是物理非法值,不能代入 `(p_in − p_v)`。
    p_v 同样必须有限且 > 0:0 / 负值按缺 p_v 处理,不能当零蒸气压算出偏大的 NPSHₐ。
    门禁放在这里而不是页面,页面 / Excel / AI 包 / 报告四条出口才会同值。
    """
    if vap_frac is not None:
        try:
            if float(vap_frac) > TWO_PHASE_VAP_LIMIT:
                return None
        except (TypeError, ValueError):
            pass
    if p_in_bar is None or p_v_bar is None or rho_kgm3 is None:
        return None
    try:
        p_in = float(p_in_bar)
        if not math.isfinite(p_in) or p_in <= 0:
            return None
        p_v = float(p_v_bar)
        if not math.isfinite(p_v) or p_v <= 0:
            return None
        if float(rho_kgm3) <= 0:
            return None
        value = (p_in - p_v) * 1e5 / (float(rho_kgm3) * G)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    # 泡点进料(p_v ≈ p_in)时差值只有浮点噪声;按显示精度(2 位小数)归零,
    # 免得表里出现「-0.00」这种看着像算错的值
    return 0.0 if abs(value) < 0.005 else value
