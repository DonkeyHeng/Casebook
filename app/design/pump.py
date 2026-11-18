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
* 建议电机 = `轴功率 ÷ 电机效率 × 裕量`,再向上取功率档位。

⚠️ 功率档位表是**工程常用序列**,不是标准原文;要引用标准请另附出处。
"""

from __future__ import annotations

# 这三个是从 app/values.py 转出来的**对外接口**:调用方一直写 `pump.pick_motor(...)`。
# 实现放在 app/values.py 是为了让读取层也能用(读取层不能反过来依赖派生层),
# 这里保留原名,调用方不用改。
from app.values import (
    DEFAULT_MARGIN,
    MOTOR_EFF,
    MOTOR_KW,
    MOTOR_RULE,
    mass_density_kgm3,
    pick_motor,
    sanitize_pv_bar,
)

__all__ = [
    "DEFAULT_MARGIN",
    "G",
    "MOTOR_EFF",
    "MOTOR_KW",
    "MOTOR_RULE",
    "NPSH_RULE",
    "head_from_dp",
    "mass_density_kgm3",
    "npsh_available_m",
    "pick_motor",
    "sanitize_pv_bar",
    "spec",
]

G = 9.80665  # m/s²
NPSH_RULE = "NPSHₐ = (p_进口 − p_v) × 10⁵ ÷ (ρ·g),忽略速度头"


def spec() -> dict:
    """给前端用的常量与口径说明(避免公式在 JS 里再抄一遍抄错)。"""
    return {
        "g": G,
        "motor_eff": MOTOR_EFF,
        "margin": DEFAULT_MARGIN,
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


def npsh_available_m(p_in_bar, p_v_bar, rho_kgm3) -> float | None:
    """有效汽蚀余量(m)。

    缺 p_v 就返回 None —— **不允许**拿进口压力顶替或默认 0,那会把"没算"显示成"不汽蚀"。
    """
    if p_in_bar is None or p_v_bar is None or not rho_kgm3:
        return None
    try:
        value = (float(p_in_bar) - float(p_v_bar)) * 1e5 / (float(rho_kgm3) * G)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    # 泡点进料(p_v ≈ p_in)时差值只有浮点噪声;按显示精度(2 位小数)归零,
    # 免得表里出现「-0.00」这种看着像算错的值
    return 0.0 if abs(value) < 0.005 else value
