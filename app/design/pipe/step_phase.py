"""十步选型第 1–3 步：判相态 → 介质代号 → 选流速。

纯函数，不碰 Aspen、不读写库；标准数值全部由 `pipe_data` 或入参注入。
口径来源：`docs/pipe-selection-plan.md` §14 第 1–3 步。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .constants import DEFAULT_VELOCITY, PHASE_EPS


def phase_of(vap_frac: float | None) -> str:
    """汽化率 → `liquid` / `gas` / `two`。

    用容差而不是 `== 0`:库里实测出现过 1e-8 量级的"痕量汽化率",
    按 0 判会把全液流股误判;None 按全液处理(缺数据会在调用侧标出)。
    """
    if vap_frac is None:
        return "liquid"
    if vap_frac < PHASE_EPS:
        return "liquid"
    if vap_frac > 1.0 - PHASE_EPS:
        return "gas"
    return "two"


def medium_code(phase: str, extra: Mapping[str, str] | None = None) -> str:
    """介质代号:HG/T 20519.6 风格,P + 相态。公用工程等特殊介质走 `extra` 覆盖。"""
    if extra and phase in extra:
        return extra[phase]
    return {"liquid": "PL", "gas": "PG", "two": "PGL"}.get(phase, "PL")


def velocity_for(
    phase: str, is_pump_inlet: bool = False, options: Mapping[str, Any] | None = None
) -> float:
    """选流速(m/s)。泵入口单独一档,不能用管道的常规液速。"""
    opts = options or {}
    v = dict(DEFAULT_VELOCITY)
    v.update(opts.get("velocity") or {})
    if is_pump_inlet:
        return float(v["pump_inlet"])
    return float(v[phase])
