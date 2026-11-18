"""管路选型(纯计算,不碰 Aspen、不读写库)。

三块内容,放在一个包里是因为它们改一处就要一起看:

* `calc` —— 十步选型(相态 → 流速 → 内径 → DN → 设计压力/温度 → [σ] → 壁厚 →
  圆整 → 等级号)与校验 V1–V14;
* `data` —— 标准数据表(壁厚偏差、Y 值、PN/材质代号、DN→外径)、三张国标 CSV 的
  导入与版本化、管道等级表;
* `prompt` —— 「复制填写提示词」的正文(文件名/必需列取自 `data.STD_FILES`,
  示例行取自 `samples/pipe-standards-template/`)。

口径来源:`docs/pipe-selection-plan.md`(定稿口径)与 `docs/pipe-class-codes.md`。

本包的公开名字直接取自 `calc`,调用方既可以 `from app.design.pipe import build`,
也可以 `from app.design import pipe` 之后用 `pipe.build` —— 两种写法等价。
"""

from __future__ import annotations

from . import data
from . import data as pipe_data  # 旧名兼容:有些调用方写 pipe.pipe_data
from .calc import (
    PHASE_EPS,
    DEFAULT_VELOCITY,
    DN_SEAMLESS_MAX,
    TablesNotReady,
    phase_of,
    medium_code,
    velocity_for,
    inner_diameter,
    usable_dn_series,
    pick_dn,
    select_dn,
    ATM_MPA,
    VACUUM_DESIGN_MPA,
    VACUUM_ABS_KPA,
    press_to_abs_kpa,
    is_material_stream,
    dn1000_oversize_pct,
    hp_stainless_force_code,
    hp_ss_allowable_stress,
    hp_ss_required_wall,
    design_pressure,
    design_temperature,
    allowable_stress,
    calc_wall_thickness,
    external_pressure_min_wall,
    near_boiling_liquid,
    round_up_wall,
    pn_code_for,
    grade_code,
    actual_velocity,
    VELOCITY_RANGE,
    validate,
    build,
)

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
