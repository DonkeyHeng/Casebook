"""十步选型第 10 步：压力等级号 → 管道等级号 + 绝热代号。

口径来源：`docs/pipe-selection-plan.md` §14 第 10 步、`docs/pipe-class-codes.md`。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import data as pipe_data


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
