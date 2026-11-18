"""与层无关的数值口径:读到的数算不算数、怎么换算、查哪一档。

为什么要单独一个模块
------------------
同样三条规则,读取层(`app/aspen/reader.py`)与派生层(`app/design/pump.py`)都要用:

* 泡点压力算不算有效读数(0 / 负数 / NaN 一律不算);
* 摩尔密度 × 分子量 = 质量密度;
* 轴功率该配哪一档电机。

规则放在 `design/` 里,读取层就得反过来依赖派生层;放在 `aspen/` 里,派生层又
要去依赖读取层 —— 两条路都会把层级的方向搞乱。这三条规则本来就与层无关,
放进这个中间层,两边都只依赖它,谁也不欠谁。

口径出处:`docs/pump-selection-plan.md`(电机档与裕量)与
`docs/aspen-field-map.md` §13(泵块字段)。改这里之前先读。
"""

from __future__ import annotations

import math

# 电机效率(默认值,可被调用方覆盖)与裕量
MOTOR_EFF = 0.95
DEFAULT_MARGIN = 1.10

# 常用低压/高压电机功率档(kW)。来源是工程常用序列,不是某个标准的原文。
MOTOR_KW: tuple[float, ...] = (
    0.55,
    0.75,
    1.1,
    1.5,
    2.2,
    3.0,
    4.0,
    5.5,
    7.5,
    11.0,
    15.0,
    18.5,
    22.0,
    30.0,
    37.0,
    45.0,
    55.0,
    75.0,
    90.0,
    110.0,
    132.0,
    160.0,
    200.0,
    250.0,
    315.0,
    355.0,
    400.0,
)

MOTOR_RULE = "建议电机 = 轴功率 ÷ 电机效率(0.95) × 裕量(1.10),向上取标准档位"


def mass_density_kgm3(rho_molar_kmol_m3, mw_kg_kmol):
    """摩尔密度 × 分子量 = 质量密度。任一缺失返回 None(不猜)。"""
    if rho_molar_kmol_m3 is None or mw_kg_kmol is None:
        return None
    try:
        return float(rho_molar_kmol_m3) * float(mw_kg_kmol)
    except (TypeError, ValueError):
        return None


def sanitize_pv_bar(p_v_bar) -> float | None:
    """p_v 只认「有限、> 0」的读数,其余一律 None。

    为什么要有这一层(2026-11-14 实测):
      Aspen 结果树里 `Output\\PL` 是**目录节点**,读出来是标志位 `1`(还带 bar 单位);
      分压在各组分叶子上,混合物总压只在 `STRM_UPP\\PBUB\\MIXED\\TOTAL`。
      不设这道闸,PL 的 1 bar 会被当成真实蒸气压,把 NPSHₐ 算成负数。
    """
    if p_v_bar is None:
        return None
    try:
        v = float(p_v_bar)
    except (TypeError, ValueError):
        return None
    # NaN / ±Inf / ≤0 都不是有效蒸气压:以前只挡了 NaN,正无穷会漏过去
    if not math.isfinite(v) or v <= 0:
        return None
    return v


def pick_motor(
    shaft_kw, margin: float = DEFAULT_MARGIN, motor_eff: float = MOTOR_EFF
) -> tuple[float | None, str]:
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
            return kw, (f"{power:.2f} ÷ {motor_eff} × {margin} = {need:.2f} kW → 取 {kw} kW 档")
    return None, f"需 {need:.1f} kW,超出档位表上限({MOTOR_KW[-1]} kW)"
