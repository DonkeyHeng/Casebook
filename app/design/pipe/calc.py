"""管路选型十步计算的兼容门面（只做 re-export，不含实现）。

实现已按选型步骤拆进同包模块：

* `constants.py` —— 跨步骤共用常量与异常
* `step_phase.py` —— 1–3 相态 / 介质代号 / 流速
* `step_dn.py` —— 4 内径与 DN
* `step_design.py` —— 5–7 设计压力 / 温度 / 许用应力
* `step_wall.py` —— 8–9 壁厚
* `step_grade.py` —— 10 等级号
* `validate.py` —— 结果校验 V1–V14
* `selection.py` —— 批量编排

保留本文件是为了让既有 `from app.design.pipe.calc import X`（以及
`app.design.pipe.__init__` 的导出）一行都不用改。新代码可以直接按步骤模块导入。
口径来源：`docs/pipe-selection-plan.md` §14。
"""

from __future__ import annotations

from .constants import (
    ATM_MPA,
    DEFAULT_VELOCITY,
    DN_SEAMLESS_MAX,
    PHASE_EPS,
    VACUUM_ABS_KPA,
    VACUUM_DESIGN_MPA,
    VELOCITY_RANGE,
    TablesNotReady,
)
from .selection import build
from .step_design import (
    allowable_stress,
    design_pressure,
    design_temperature,
    dn1000_oversize_pct,
    hp_ss_allowable_stress,
    hp_ss_required_wall,
    hp_stainless_force_code,
    is_material_stream,
    press_to_abs_kpa,
)
from .step_dn import inner_diameter, pick_dn, select_dn, usable_dn_series
from .step_grade import grade_code, pn_code_for
from .step_phase import medium_code, phase_of, velocity_for
from .step_wall import (
    calc_wall_thickness,
    external_pressure_min_wall,
    near_boiling_liquid,
    round_up_wall,
)
from .validate import actual_velocity, validate

__all__ = [
    "PHASE_EPS",
    "DEFAULT_VELOCITY",
    "DN_SEAMLESS_MAX",
    "TablesNotReady",
    "phase_of",
    "medium_code",
    "velocity_for",
    "inner_diameter",
    "usable_dn_series",
    "pick_dn",
    "select_dn",
    "ATM_MPA",
    "VACUUM_DESIGN_MPA",
    "VACUUM_ABS_KPA",
    "press_to_abs_kpa",
    "is_material_stream",
    "dn1000_oversize_pct",
    "hp_stainless_force_code",
    "hp_ss_allowable_stress",
    "hp_ss_required_wall",
    "design_pressure",
    "design_temperature",
    "allowable_stress",
    "calc_wall_thickness",
    "external_pressure_min_wall",
    "near_boiling_liquid",
    "round_up_wall",
    "pn_code_for",
    "grade_code",
    "actual_velocity",
    "VELOCITY_RANGE",
    "validate",
    "build",
]
