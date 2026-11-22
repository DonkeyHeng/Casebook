"""pipe_run 人工覆盖的统一合并层。

页面、Excel / Word 导出与 `validate()` 都先过这里,避免「页面显示人工 DN、导出还是
自动 DN、校验也查自动壁厚」三套口径。只替换生效行字段,自动选档原值保留在
`auto_dn` / `auto_od_mm` / `auto_wall_mm`,前端「恢复自动」与对比仍可用。

口径:
* `dn_override` → 按 DN→外径表取 OD,复用 `design_wall_for_od` 重算壁厚;
* `od_override_mm + wall_override_mm` → 自定义规格,壁厚按人工值,负偏差按 0;
* 两者都有时自定义规格优先(与泵面板 `_dn_from_overrides` 一致);
* 人工 DN 缺标准外径时只加提示,不猜壁厚。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from . import data as pipe_data
from .step_wall import design_wall_for_od
from .validate import actual_velocity


def _finite(value) -> float | None:
    """接受任意有限数；设计与工艺字段中的 0、负温度不能当成缺值。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _positive(value) -> float | None:
    value = _finite(value)
    return value if value is not None and value > 0 else None


def _nonnegative(value) -> float | None:
    value = _finite(value)
    return value if value is not None and value >= 0 else None


def _dn_num(value) -> int | None:
    value = _positive(value)
    return int(value) if value is not None else None


def _append_note(warnings: list[str], note: str) -> None:
    if note and note not in warnings:
        warnings.append(note)


def _auto_fields(row: dict, dn, od, wall) -> None:
    row.setdefault("auto_dn", dn)
    row.setdefault("auto_od_mm", od)
    row.setdefault("auto_wall_mm", wall)


def _recompute_wall(
    row: dict,
    *,
    dn: int | None,
    od_mm: float,
    options: Mapping[str, Any],
    custom: bool = False,
) -> dict | None:
    """按生效外径重算标准壁厚;自定义规格时只算 `t_required` 供 V3 用。"""
    pressure = _nonnegative(row.get("design_pressure_mpa"))
    sigma = _positive(row.get("stress_mpa"))
    if pressure is None or sigma is None:
        return None
    return design_wall_for_od(
        od_mm,
        pressure,
        dn=dn,
        material=str(row.get("material") or ""),
        sigma=sigma,
        design_temp_c=_finite(row.get("design_temp_c")),
        c2_mm=_nonnegative(row.get("c2_mm")) or 0.0,
        vacuum=bool(row.get("is_vacuum", row.get("vacuum"))),
        hot_vacuum=bool(row.get("hot_vacuum")),
        hp_c_mm=_nonnegative(row.get("r1e_c_mm")),
        force_pn_code="R" if row.get("hp_ss_upgraded") else None,
        options=options,
    )


def apply_pipe_run_overrides(
    rows: Iterable[Mapping[str, Any]],
    runs: Mapping[str, Mapping[str, Any]] | None = None,
    options: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """返回合并人工覆盖后的行副本;无覆盖的行保持原样。"""
    runs = runs or {}
    opts = options or {}
    out: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        run = runs.get(str(row.get("stream"))) or {}
        dn_override = _dn_num(run.get("dn_override"))
        od_override = _positive(run.get("od_override_mm"))
        wall_override = _positive(run.get("wall_override_mm"))
        warnings = list(row.get("warnings") or [])

        if od_override and wall_override:
            auto_dn, auto_od, auto_wall = (
                row.get("auto_dn", row.get("dn")),
                row.get("auto_od_mm", row.get("od_mm")),
                row.get("auto_wall_mm", row.get("wall_mm")),
            )
            _auto_fields(row, auto_dn, auto_od, auto_wall)
            row["dn"] = None
            row["od_mm"] = od_override
            row["wall_mm"] = wall_override
            row["c1_mm"] = 0.0
            row["dn_source"] = "custom"
            row["spec_source"] = "custom"
            result = _recompute_wall(
                row, dn=dn_override, od_mm=od_override, options=opts, custom=True
            )
            if result:
                row["t_calc_mm"] = result.get("t_calc_mm")
                row["t_required_mm"] = result.get("t_required_mm")
                row["min_wall_mm"] = result.get("min_wall_mm")
                row["wall_floored"] = False
                row["external_wall_mm"] = result.get("ext_wall_mm")
            else:
                row["t_calc_mm"] = None
                row["t_required_mm"] = None
                row["min_wall_mm"] = None
                _append_note(warnings, "缺设计压力/许用应力,自定义规格壁厚无法校核")
            _append_note(warnings, "自定义规格")
        elif dn_override:
            od = pipe_data.DN_TO_OD.get(dn_override)
            if od is None:
                _append_note(warnings, f"DN{dn_override} 不在标准外径表,保留自动规格待人工确认")
            else:
                auto_dn, auto_od, auto_wall = (
                    row.get("auto_dn", row.get("dn")),
                    row.get("auto_od_mm", row.get("od_mm")),
                    row.get("auto_wall_mm", row.get("wall_mm")),
                )
                _auto_fields(row, auto_dn, auto_od, auto_wall)
                result = _recompute_wall(row, dn=dn_override, od_mm=float(od), options=opts)
                row["dn"] = dn_override
                row["od_mm"] = float(od)
                row["dn_source"] = "manual"
                row["spec_source"] = "manual"
                if result and result.get("wall_mm") is not None:
                    wall = float(result["wall_mm"])
                    row["wall_mm"] = wall
                    row["t_calc_mm"] = result.get("t_calc_mm")
                    row["t_required_mm"] = result.get("t_required_mm")
                    row["min_wall_mm"] = result.get("min_wall_mm")
                    row["wall_floored"] = bool(result.get("floored"))
                    row["external_wall_mm"] = result.get("ext_wall_mm")
                    hp = bool(result.get("hp_formula"))
                    row["c1_mm"] = (
                        0.0
                        if hp
                        else pipe_data.negative_deviation(
                            float(od),
                            wall,
                            str(opts.get("wall_grade") or pipe_data.DEFAULT_WALL_TOL_GRADE),
                        )
                    )
                else:
                    row["wall_mm"] = None
                    row["c1_mm"] = None
                    row["t_required_mm"] = None
                    row["min_wall_mm"] = None
                    row["wall_floored"] = False
                    _append_note(
                        warnings,
                        str((result or {}).get("note") or "人工 DN 壁厚算不出,待人工填自定义规格"),
                    )
                _append_note(warnings, "人工DN")

        if row.get("od_mm") is not None and row.get("wall_mm") is not None:
            inner = float(row["od_mm"]) - 2.0 * float(row["wall_mm"])
            vol = row.get("vol_flow_m3h")
            if inner > 0:
                row["effective_inner_mm"] = inner
                if isinstance(vol, (int, float)) and vol > 0:
                    row["v_act_ms"] = actual_velocity(float(vol), inner)
        row["warnings"] = warnings
        out.append(row)
    return out


def pipe_dn_text(row: Mapping[str, Any]) -> str:
    """公称直径文本:标准 DN 用数字;自定义规格无标准 DN 时用 Φ{od};缺失留空。"""
    dn = row.get("dn")
    if dn is not None and str(dn).strip():
        return str(int(dn))
    od = _positive(row.get("od_mm"))
    if od is not None and str(row.get("spec_source") or "") == "custom":
        return f"Φ{od:g}"
    return ""
