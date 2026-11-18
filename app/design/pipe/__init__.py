"""管路选型(纯计算,不碰 Aspen、不读写库)。

按选型步骤分模块,放在一个包里是因为它们改一处就要一起看:

* `constants` —— 跨步骤共用的阈值与异常(相态容差、默认/推荐流速、真空绝压等);
* `step_phase` —— 第 1–3 步:相态 / 介质代号 / 流速;
* `step_dn` —— 第 4 步:内径与 DN 选档;
* `step_design` —— 第 5–7 步:设计压力 / 设计温度 / 许用应力;
* `step_wall` —— 第 8–9 步:壁厚计算与圆整;
* `step_grade` —— 第 10 步:管道等级号;
* `validate` —— 选型结果校验 V1–V14(只读结果,不参与计算);
* `selection` —— 批量编排:一组流股逐条走完十步,产出主表与计算书行;
* `calc` —— 兼容门面,re-export 全部公开名字,既有 import 路径不变;
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
    ATM_MPA,
    DEFAULT_VELOCITY,
    DN_SEAMLESS_MAX,
    PHASE_EPS,
    VACUUM_ABS_KPA,
    VACUUM_DESIGN_MPA,
    VELOCITY_RANGE,
    TablesNotReady,
    actual_velocity,
    allowable_stress,
    build,
    calc_wall_thickness,
    design_pressure,
    design_temperature,
    dn1000_oversize_pct,
    external_pressure_min_wall,
    grade_code,
    hp_ss_allowable_stress,
    hp_ss_required_wall,
    hp_stainless_force_code,
    inner_diameter,
    is_material_stream,
    medium_code,
    near_boiling_liquid,
    phase_of,
    pick_dn,
    pn_code_for,
    press_to_abs_kpa,
    round_up_wall,
    select_dn,
    usable_dn_series,
    validate,
    velocity_for,
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
