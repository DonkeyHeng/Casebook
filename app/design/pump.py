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

G = 9.80665              # m/s²
MOTOR_EFF = 0.95         # 电机效率(默认值,可被调用方覆盖)
DEFAULT_MARGIN = 1.10    # 电机裕量

# 常用低压/高压电机功率档(kW)。来源是工程常用序列,不是某个标准的原文。
MOTOR_KW: tuple[float, ...] = (
    0.55, 0.75, 1.1, 1.5, 2.2, 3.0, 4.0, 5.5, 7.5, 11.0, 15.0, 18.5, 22.0,
    30.0, 37.0, 45.0, 55.0, 75.0, 90.0, 110.0, 132.0, 160.0, 200.0, 250.0,
    315.0, 355.0, 400.0,
)

MOTOR_RULE = "建议电机 = 轴功率 ÷ 电机效率(0.95) × 裕量(1.10),向上取标准档位"
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


def mass_density_kgm3(rho_molar_kmol_m3, mw_kg_kmol):
    """摩尔密度 × 分子量 = 质量密度。任一缺失返回 None(不猜)。"""
    if rho_molar_kmol_m3 is None or mw_kg_kmol is None:
        return None
    try:
        return float(rho_molar_kmol_m3) * float(mw_kg_kmol)
    except (TypeError, ValueError):
        return None


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
        return (float(p_in_bar) - float(p_v_bar)) * 1e5 / (float(rho_kgm3) * G)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def pick_motor(shaft_kw, margin: float = DEFAULT_MARGIN,
               motor_eff: float = MOTOR_EFF) -> tuple[float | None, str]:
    """轴功率 → 建议电机功率 + 一句可展示的推导。

    返回 (档位 kW 或 None, 说明)。功率 ≤ 0 视为反向工况,不选电机。
    """
    if shaft_kw is None:
        return None, "缺轴功率"
    try:
        power = float(shaft_kw)
    except (TypeError, ValueError):
        return None, "轴功率不是数字"
    if power <= 0:
        return None, f"反向工况(轴功率 {power:.2f} kW ≤ 0),不选电机"
    try:
        need = power / float(motor_eff) * float(margin)
    except (TypeError, ValueError, ZeroDivisionError):
        return None, "电机效率或裕量不合法"
    for kw in MOTOR_KW:
        if need <= kw:
            return kw, (f"{power:.2f} ÷ {motor_eff} × {margin} = {need:.2f} kW "
                        f"→ 取 {kw} kW 档")
    return None, f"需 {need:.1f} kW,超出档位表上限({MOTOR_KW[-1]} kW)"
