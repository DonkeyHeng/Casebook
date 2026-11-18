"""选型结果校验 V1–V14 与实际流速反算。

`validate()` 只读一张已生成的表，不做任何选型计算，也不改行；
每条 finding 带 `field`，前端据此只标单元格而不是整行。
口径来源：`docs/pipe-selection-plan.md` §6。"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from . import data as pipe_data
from .constants import VELOCITY_RANGE


def actual_velocity(vol_flow_m3h: float, inner_d_mm: float) -> float | None:
    """按实际内径反算流速 m/s(内径 = 外径 − 2×壁厚)。"""
    if not inner_d_mm or inner_d_mm <= 0:
        return None
    area = math.pi * (inner_d_mm / 1000.0) ** 2 / 4.0
    return (vol_flow_m3h / 3600.0) / area


def validate(
    rows: Iterable[Mapping[str, Any]], options: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """对选型结果跑 §6 的 V1–V9,只陈述事实,不给"该怎么改模型"的建议。

    返回 `{"findings": [...], "summary": {"error": n, "warn": n}}`,
    每条 finding 是 `{stream, code, level, message, field}`。

    `field` 是**这条发现该标在哪一列**(前端据此做单元格标黄,而不是整行染黄):
    取值与 `build()` 的行字段同名(`dn` / `velocity` / `wall_mm` / `grade_code` /
    `design_pressure_mpa` / `design_temp_c`)。V2 的"实际流速"在主表里也有一列
    (前端按内径反算),所以标 `velocity` 不会落空。
    """
    opts = options or {}
    low_c = float(opts.get("material_low_c", -20.0))
    high_c = float(opts.get("material_high_c", 450.0))
    hot_c = float(opts.get("hot_insulation_c", 80.0))
    findings: list[dict[str, Any]] = []

    def add(stream: str, code: str, level: str, message: str, field: str) -> None:
        findings.append(
            {"stream": stream, "code": code, "level": level, "message": message, "field": field}
        )

    for r in rows:
        name = r.get("stream")
        dn = r.get("dn")
        od = r.get("od_mm")
        wall = r.get("wall_mm")
        v_used = r.get("velocity_ms")
        phase = r.get("phase")

        # V1 编号里的 DN ≠ 公称直径列(没有编号的工况跳过)
        pipe_no = r.get("pipe_no")
        if pipe_no:
            tail = str(pipe_no).split("-")[-1]
            if tail.isdigit() and dn is not None and int(tail) != int(dn):
                add(name, "V1", "error", f"管道编号里的口径 {tail} 与选出的 DN{dn} 不一致", "dn")

        # V5 气液共存但按单相流速选型(先判,好在 V2 里避免同一个问题报两次)
        v5_hit = False
        if phase == "two" and v_used is not None:
            lo2, hi2 = VELOCITY_RANGE["two"]
            if not (lo2 - 1e-9 <= v_used <= hi2 + 1e-9):
                v5_hit = True
                add(
                    name,
                    "V5",
                    "warn",
                    f"两相流按 {v_used:g} m/s(单相口径)选型,超出两相 {lo2:g}–{hi2:g} m/s",
                    "velocity",
                )

        # V2 实际流速超出推荐区间(两相已由 V5 覆盖,不重复报)
        id_mm = (od - 2.0 * wall) if (od and wall) else None
        v_act = None
        if id_mm:
            area = math.pi * (id_mm / 1000.0) ** 2 / 4.0
            v_act = (float(r["vol_flow_m3h"]) / 3600.0) / area if r.get("vol_flow_m3h") else None
        key = "pump_inlet" if (r.get("pump_inlet") or r.get("cavitation_limited")) else phase
        lo, hi = VELOCITY_RANGE.get(key, (0.0, float("inf")))
        # 两相流"慢下来"是刻意的:管径被 ρv² 或上游液相管径抬高之后,实际流速
        # 本来就会掉到 5 m/s 以下。这时再报"低于推荐区间"是自相矛盾的噪音,
        # 只说上界(太快会冲蚀/振动),下界交给 ρv² 那条判据。
        too_slow = v_act is not None and v_act < lo - 1e-9 and phase != "two"
        too_fast = v_act is not None and v_act > hi + 1e-9
        if v_act is not None and not v5_hit and (too_slow or too_fast):
            add(
                name,
                "V2",
                "warn",
                f"实际流速 {v_act:.2f} m/s 超出 {phase} 推荐区间 {lo:g}–{hi:g} m/s"
                + ("(泵入口)" if r.get("pump_inlet") else ""),
                "velocity",
            )

        # V3 计算壁厚 + 附加量 > 所选标准壁厚
        need = r.get("t_required_mm")
        if need is not None and wall is not None and need > wall + 1e-9:
            add(
                name,
                "V3",
                "error",
                f"所需壁厚 {need:.2f} mm 超过所选标准壁厚 {wall:g} mm",
                "wall_mm",
            )

        # V4 等级号的压力代号 < 设计压力(真空代号 V 不参与 PN 数值比较)
        code = r.get("grade_code") or ""
        if code and r.get("design_pressure_mpa") is not None:
            prefix = code[:1]
            if prefix == pipe_data.VACUUM_PN_CODE:
                pass  # V = 真空管路,按外压校核,不与 PN 表比大小
            else:
                pn = pipe_data.PN_CODES.get(prefix)
                if pn is None:
                    add(
                        name,
                        "V4",
                        "error",
                        f"等级号 {code} 的压力代号不在 PN 代号表里",
                        "grade_code",
                    )
                elif pn < float(r["design_pressure_mpa"]) - 1e-9:
                    add(
                        name,
                        "V4",
                        "error",
                        f"等级号 {code} 的公称压力 {pn:g} MPa 低于设计压力 "
                        f"{float(r['design_pressure_mpa']):.2f} MPa",
                        "grade_code",
                    )

        # V5 已在前面先判(与 V2 互斥,避免同一个问题报两次)

        # V6 真空流股
        if r.get("vacuum"):
            add(name, "V6", "warn", "真空流股:按外压 0.1 MPa 另行校核", "design_pressure_mpa")

        # V7 超出材料使用温度
        dt = r.get("design_temp_c")
        if dt is not None and (dt < low_c or dt > high_c):
            add(
                name,
                "V7",
                "warn",
                f"设计温度 {dt:g} ℃ 超出材料常规使用区间 {low_c:g}–{high_c:g} ℃",
                "design_temp_c",
            )

        # V8 泵出口流股用了泵入口流速
        if r.get("pump_outlet") and r.get("pump_inlet"):
            add(name, "V8", "error", "泵出口流股被按泵入口流速选型", "velocity")

        # V9 高温但没加保温代号
        if dt is not None and dt >= hot_c and not r.get("insulation"):
            add(
                name,
                "V9",
                "warn",
                f"设计温度 {dt:g} ℃ ≥ {hot_c:g} ℃,等级号却未加保温代号 -H",
                "grade_code",
            )

        # V10 非标准流水号(如 AB/BC):只高亮提示,不强行重映射(§17.5)
        if r.get("nonstandard_no"):
            add(
                name,
                "V10",
                "warn",
                "非标准流水号(非四位数字),保留原名待人工确认",
                "stream",
            )

        # V11 真空 DN≥150:外压失稳需专项复核(§17.1)
        if r.get("vacuum") and dn is not None and dn >= pipe_data.VACUUM_EXTERNAL_MIN_DN:
            add(
                name,
                "V11",
                "warn",
                f"真空 DN{dn}:已按外压失稳算最小壁厚 {r.get('external_wall_mm')} mm,"
                "仍需按实际支撑跨距复核",
                "wall_mm",
            )

        # V12 大口径两相流分层/波状流风险(§17.2)
        if r.get("stratified_risk"):
            add(
                name,
                "V12",
                "warn",
                f"DN{dn} 气液两相:均相 ρv² 判据不足,分层/波状流风险需专项核算",
                "velocity",
            )

        # V13 高压高温不锈钢应升 R1E 而非 Q1E(§17.3/§17.8)
        if r.get("hp_ss_upgraded"):
            add(
                name,
                "V13",
                "warn",
                "高压(4.7–5.6 MPa)不锈钢:等级升 R1E,按 [σ]^t 公式重算壁厚,避免高温薄壁倒挂",
                "grade_code",
            )

    return {
        "findings": findings,
        "summary": {
            "error": sum(1 for f in findings if f["level"] == "error"),
            "warn": sum(1 for f in findings if f["level"] == "warn"),
        },
    }
