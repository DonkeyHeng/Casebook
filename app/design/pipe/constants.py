"""管路选型的共用常量与异常。

十步流程里跨步骤都要用的阈值（相态容差、默认流速、真空绝压、DN 上限、
推荐流速区间）与「标准表没录入」异常集中放在这里，避免散在步骤间。
口径来源：`docs/pipe-selection-plan.md` §14。"""

from __future__ import annotations

from . import data as pipe_data

PHASE_EPS = 1e-4


DEFAULT_VELOCITY = {
    "liquid": 1.5,  # 1.0–2.5
    "gas": 15.0,  # 10–25
    "two": 8.0,  # 5–10
    "pump_inlet": 0.8,  # 0.5–1.0
}


DN_SEAMLESS_MAX = 600


class TablesNotReady(RuntimeError):
    """标准表还没录入时的明确报错(而不是悄悄用 0 算出一张表)。"""


ATM_MPA = 0.1013


VACUUM_DESIGN_MPA = 0.1


VACUUM_ABS_KPA = pipe_data.VACUUM_ABS_KPA


VELOCITY_RANGE = {
    "liquid": (1.0, 2.5),
    "gas": (10.0, 25.0),
    "two": (5.0, 10.0),
    "pump_inlet": (0.5, 1.0),
}
