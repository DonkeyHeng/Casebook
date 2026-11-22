"""十步选型第 4 步：体积流量 → 内径 → 可用 DN 系列 → 选档。

两相流股的内径修正发生在编排层（`selection.py`），本模块只负责
「给定流量与流速算内径」以及「给定内径挑一个标准 DN」。
口径来源：`docs/pipe-selection-plan.md` §14 第 4 步。"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from . import data as pipe_data


def inner_diameter(vol_flow_m3h: float, velocity_ms: float) -> float:
    """连续性方程:D_i = √(4Q/(πv)),返回 mm。Q 用 m³/h、v 用 m/s。"""
    if velocity_ms <= 0:
        raise ValueError(f"流速必须为正:{velocity_ms!r}")
    q_m3s = vol_flow_m3h / 3600.0
    d_m = math.sqrt(4.0 * q_m3s / (math.pi * velocity_ms))
    return d_m * 1000.0


def usable_dn_series(
    dn_to_od: Mapping[int, float], options: Mapping[str, Any] | None = None
) -> dict[int, float]:
    """屏蔽非优先管径(§17.2):DN65 → DN80、DN125 → DN150。

    做法是把被禁的档从系列里拿掉,后面 `select_dn` 按"实际内径 ≥ 计算内径"逐档试,
    自然就落到上一档优先 DN 上 —— 不在选取环节写死"映射",这样改禁档表只改数据。
    """
    opts = options or {}
    banned = opts.get("disallowed_dn", pipe_data.DISALLOWED_DN)
    return {dn: od for dn, od in dn_to_od.items() if dn not in banned}


def pick_dn(inner_d_mm: float, dn_to_od: Mapping[int, float]) -> tuple[int | None, float | None]:
    """**初选** DN:取第一个 `DN ≥ 计算内径` 的档位,返回 (DN, 公称外径)。

    注意这只是一次粗选:DN 是公称直径,同一 DN 的外径比 DN 大(如 DN250 → Φ273),
    真正的选档要用 `select_dn()` 按**实际内径**(外径 − 2×壁厚)复核。
    """
    if not dn_to_od:
        return None, None
    candidates = sorted(d for d in dn_to_od if d >= inner_d_mm - 1e-9)
    if not candidates:
        return None, None
    dn = candidates[0]
    return dn, float(dn_to_od[dn])


def select_dn(
    inner_d_mm: float,
    dn_to_od: Mapping[int, float],
    walls_by_od: Mapping[float, Sequence[float]],
    wall_for,
    max_dn: int | None = None,
    min_dn: int | None = None,
    oversize_tol: float = 0.0,
    oversize_band: tuple[float, float] | None = None,
    min_wall_for=None,
) -> dict[str, Any]:
    """按**实际内径**选档:从小到大试 DN,直到 `外径 − 2×标准壁厚 ≥ 计算内径`。

    这一步是必须的:DN 只是公称直径,Φ273 的内径约 269 mm,若只按"DN ≥ 内径"选档,
    会一路选大、实际流速偏低(实测 #19–#22 有 40–57 条 V2 就是这么来的)。

    `wall_for(od)` 负责给出该外径最终选定的标准壁厚(build 里会连负偏差一起算)。
    `oversize_band` 限定容差只作用于指定计算内径区间(按最大档实际内径动态给出),
    不给则沿用旧的"只差一点点"全区间容差。
    `min_wall_for(dn)` 只在**壁厚缺失**时用于退化判断:仍按该 DN 的最小壁厚
    复核实际内径,而不是拿外径直接和 Di 比,避免选到偏小一档。返回值里的
    `wall_mm` 仍是 `None` —— 不能拿最小壁厚冒充实际壁厚。
    """
    for dn in sorted(dn_to_od):
        if max_dn is not None and dn > max_dn:
            break
        if min_dn is not None and dn < min_dn:
            continue  # DN10 只留给仪表/采样,不参与常规物流选型
        od = float(dn_to_od[dn])
        wall = wall_for(od)
        if wall is None:
            # 没有壁厚数据时退化:用该 DN 的最小壁厚反算可用内径,
            # 而不是 od >= Di(那会把 DN100 的 Φ108 当成能过 105 mm 内径)。
            fallback = min_wall_for(dn) if min_wall_for is not None else None
            usable_id = od - 2.0 * float(fallback) if fallback is not None else od
            if usable_id >= inner_d_mm - 1e-9:
                return {"dn": dn, "od_mm": od, "wall_mm": None}
            continue
        if od - 2.0 * wall >= inner_d_mm - 1e-9:
            return {"dn": dn, "od_mm": od, "wall_mm": wall}

    # 只差一点点就够着的,允许按容差取最大档(内径超 3–5% 换流速略超,
    # 比"选型失败、要并联或专项设计"实际得多)。容差默认关,由 options 打开。
    if oversize_tol > 0 and dn_to_od:
        lo, hi = oversize_band if oversize_band is not None else (0.0, float("inf"))
        if lo - 1e-9 <= inner_d_mm <= hi + 1e-9:
            dn = max(dn_to_od)
            od = float(dn_to_od[dn])
            wall = wall_for(od)
            if wall is not None:
                usable_id = od - 2.0 * wall
                if usable_id * (1.0 + oversize_tol) >= inner_d_mm - 1e-9:
                    return {"dn": dn, "od_mm": od, "wall_mm": wall, "tolerated": True}
            else:
                fallback = min_wall_for(dn) if min_wall_for is not None else None
                if fallback is not None:
                    usable_id = od - 2.0 * float(fallback)
                    if usable_id * (1.0 + oversize_tol) >= inner_d_mm - 1e-9:
                        return {"dn": dn, "od_mm": od, "wall_mm": None, "tolerated": True}
    return {"dn": None, "od_mm": None, "wall_mm": None}
