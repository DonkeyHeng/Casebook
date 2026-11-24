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

# 「两相」的统一定义:进口汽化率超过它,管道侧按两相选型、泵侧不计算 NPSHₐ、
# 读取层不再拿 m/V 当液相密度。一个常量管三处,免得同一流股两处判成不同相态。
TWO_PHASE_VAP_EPS = 1e-4

# 电机效率:只用于「电功率 → 轴功率」的折算(ELEC_POWER 回退时),不是选档时的除数。
# 电机铭牌标的是轴输出功率,所以轴功率 → 电机功率只再乘裕量。
MOTOR_EFF = 0.95
#: 设计扬程余量(泵选型扬程 ×1.10),与电机裕量不是一回事,不要混用。
DEFAULT_MARGIN = 1.10
#: 电机裕量分段(参照 API 610 / GB/T 3215):小功率档留更大余量,
#: 大功率档按 1.10。显式传 margin 时仍以调用方为准,兼容旧单测与项目口径。
MOTOR_MARGIN_TIERS: tuple[tuple[float | None, float], ...] = (
    (22.0, 1.25),  # power_kw < 22 kW
    (55.0, 1.15),  # 22 ≤ power_kw ≤ 55 kW
    (None, 1.10),  # power_kw > 55 kW
)
# 传动效率:直联(联轴器)取 1.0;皮带 ≈0.95、齿轮箱按厂家样本。默认按直联算。
TRANSMISSION_EFF_DIRECT = 1.0

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

MOTOR_RULE = (
    "建议电机 = 基准功率 ÷ 传动效率(直联 1.0) × 分段裕量"
    "(<22 kW 1.25;22–55 kW 1.15;>55 kW 1.10),向上取标准档位"
)
#: GB/T 3215-2025 7.1.4 规定的最小可接受电动机额定功率;低于此值且项目执行该标准时,需买方批准。
#: 这里只用于展示提示,不强行抬高选档,避免把历史工况的工程口径静默改掉。
MOTOR_STANDARD_MIN_KW = 4.0


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
    # NaN / ±Inf / ≤0 均非有效蒸气压:此前仅拦截 NaN,正无穷会漏过
    if not math.isfinite(v) or v <= 0:
        return None
    return v


def motor_margin(power_kw) -> float:
    """按基准功率取电机裕量(API 610 / GB/T 3215 分段口径)。

    分段点按**基准功率**(设计轴功率或 Aspen 折回的轴功率)判断,而不是乘完
    裕量后的需求功率;功率非法时给小功率档余量,由 `pick_motor` 另行拦截。
    """
    try:
        power = float(power_kw)
    except (TypeError, ValueError):
        return 1.25
    if power < MOTOR_MARGIN_TIERS[0][0]:
        return MOTOR_MARGIN_TIERS[0][1]
    if power <= MOTOR_MARGIN_TIERS[1][0]:
        return MOTOR_MARGIN_TIERS[1][1]
    return MOTOR_MARGIN_TIERS[2][1]


def pick_motor(
    shaft_kw,
    margin: float | None = None,
    transmission_eff: float = TRANSMISSION_EFF_DIRECT,
) -> tuple[float | None, str]:
    """轴功率 → 建议电机功率 + 一句可展示的推导。

    电机铭牌的功率是**轴输出功率**,所以这里只除传动效率(直联 1.0)再乘裕量;
    电机效率 0.95 只在「电功率折回轴功率」时用(见 app/aspen/reader.py),
    两者不是同一个折算,混用会把档位系统性放大 1/0.95。

    `margin` 不传时走 `motor_margin()` 的分段裕量;显式传入(如 1.10)时按旧口径,
    便于老单测与特殊项目覆写。返回 (档位 kW 或 None, 说明)。
    功率 ≤ 0 视为反向工况,不选电机。
    """
    if shaft_kw is None:
        return None, "缺轴功率"
    try:
        power = float(shaft_kw)
    except (TypeError, ValueError):
        return None, "轴功率不是数字"
    if power <= 0:
        return None, f"反向工况(轴功率 {power:.2f} kW ≤ 0),不选电机"
    used_margin = motor_margin(power) if margin is None else margin
    try:
        need = power / float(transmission_eff) * float(used_margin)
    except (TypeError, ValueError, ZeroDivisionError):
        return None, "传动效率或裕量不合法"
    for kw in MOTOR_KW:
        if need <= kw:
            note = (
                f"{power:.2f} ÷ {transmission_eff:g} × {used_margin:g} = "
                f"{need:.2f} kW → 取 {kw} kW 档"
            )
            if kw < MOTOR_STANDARD_MIN_KW:
                note += (
                    f"；如项目执行 GB/T 3215-2025 需买方批准"
                    f"（7.1.4 最小可接受值 {MOTOR_STANDARD_MIN_KW:g} kW）"
                )
            return kw, note
    return None, f"需 {need:.1f} kW,超出档位表上限({MOTOR_KW[-1]} kW)"
