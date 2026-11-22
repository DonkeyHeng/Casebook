"""泵选型台账的实时计算层 —— 只算,不存。

台账表 `pump_selection` 只放**人工决定**的字段(型号 / 名称 / 台数 /
材质 / 配套电机 / NPSHᵣ / 余量 / 备注)。这里算出来的设计点一律来自
当前工况:Aspen 读数 + 人填的 ΔZ / Σhf,不从任何外部 Excel 导入、
不反推、不写回库 —— 换工况后数值自动跟着变。

三态口径来自 `docs/pump-selection-plan.md` §6.2:满足 / 不满足 /
**无法判定**,「无法判定」必须写明缺什么。绝不写「验过」这类断言:
台账只记「选了什么」,不宣称「选得对不对」。

本模块是纯函数,不碰 COM、不读库,可以直接进自测。
"""

from __future__ import annotations

import math

from app.design import pump as pump_rules

# 手册惯例,与前端 PUMP_DESIGN_MARGIN 一致
DESIGN_MARGIN = 1.1
# §6.3 建议余量 0.5~1.0 m;人没填时用下限,并在返回里写明「用建议值」
NPSH_MARGIN_SUGGESTED = 0.5


def _num(v):
    """只认有限数字;缺数据一律 None,不拿 0 或常见值顶替。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def compute_view(
    block: dict,
    design: dict | None = None,
    saved: dict | None = None,
    lines: dict | None = None,
) -> dict:
    """把工况读数 + 管段记录 + 台账手填字段合成台账页展示视图。

    block  : `blocks` 表一行(dict),只用泵参数列
    design : 旧 `pump_design` 一行(None 也行);**仅在 `lines` 没给时**作为兼容路径
    saved  : `pump_selection` 一行或 None(人工决定的字段)
    lines  : `pump_line.compute()` 的结果。给了就以它为准 —— 两侧拆分、修正 NPSHₐ、
             三态都来自同一份计算,不再在这里重复推导。
    """
    b = block or {}
    s = saved or {}

    if lines is not None:
        totals = lines.get("totals") or {}
        npsh = lines.get("npsh") or {}
        head = _num(totals.get("head_m"))
        dz = _num(totals.get("dz_m"))
        hf = _num(totals.get("hf_m"))
        total = _num(totals.get("design_head_m"))
        selection_head = _num(totals.get("selection_head_m"))
        reverse = bool(totals.get("reverse"))
        npsh_a = _num(npsh.get("device_m"))
        corrected = _num(npsh.get("corrected_m"))
        state = str(npsh.get("state") or "unknown")
        reason = str(npsh.get("reason") or "")
        threshold = _num(npsh.get("threshold_m"))
        margin = _num(npsh.get("margin_used_m"))
        if margin is None:
            margin = NPSH_MARGIN_SUGGESTED
        margin_default = bool(npsh.get("margin_is_default"))
    else:
        d = design or {}
        head = _num(b.get("head_m"))  # 压升扬程 HEAD_CAL,m
        dz = _num(d.get("dz_m"))  # 位压头,m
        hf = _num(d.get("hf_m"))  # 管路阻力,m
        total = head + dz + hf if None not in (head, dz, hf) else None
        reverse = total is not None and total <= 0
        selection_head = pump_rules.selection_head_m(total)

        p_in = _num(b.get("p_in_bar"))
        p_v = _num(b.get("pv_bar"))
        rho = _num(b.get("rho_kgm3"))
        vap = _num(b.get("pump_vap_frac"))
        npsh_a = pump_rules.npsh_available_m(p_in, p_v, rho, vap)
        corrected = None  # 旧路径没有吸入段拆分,修正值不给

        npshr = _num(s.get("npshr_m"))
        margin_given = _num(s.get("margin_m"))
        margin = margin_given if margin_given is not None else NPSH_MARGIN_SUGGESTED
        margin_default = margin_given is None

        missing: list[str] = []
        if vap is not None and vap > pump_rules.TWO_PHASE_VAP_LIMIT:
            missing.append("两相进口")
        if p_v is None:
            missing.append("p_v")
        if p_in is None:
            missing.append("进口压力")
        if not rho:
            missing.append("密度")
        if npsh_a is None and not missing:
            missing.append("NPSHₐ(读数不完整)")
        if npshr is None:
            missing.append("NPSHᵣ")

        if missing:
            state = "unknown"
            reason = "缺 " + "、".join(missing) + ",无法判定"
            threshold = None
        else:
            threshold = round(npshr + margin, 4)
            state = "ok" if threshold <= npsh_a else "bad"
            reason = (
                f"NPSHᵣ {npshr:g} + 余量 {margin:g} = {threshold:g} m "
                f"{'≤' if state == 'ok' else '>'} NPSHₐ {npsh_a:g} m"
            )

    motor = ((lines or {}).get("totals") or {}).get("motor") or {}
    warnings = list(((lines or {}).get("totals") or {}).get("warnings") or [])
    if _num(b.get("pump_vap_frac")) is None:
        vap_note = "缺汽化率，暂按液相估算，请复核"
        if vap_note not in reason:
            reason = f"{reason}；{vap_note}" if reason else vap_note
        if vap_note not in warnings:
            warnings.append(vap_note)

    return {
        # 实时设计点(全部来自当前工况,不落库)
        "flow_m3h": _num(b.get("flow_m3h")),
        "head_m": head,
        "dz_m": dz,
        "hf_m": hf,
        "design_head_m": total,
        "selection_head_m": selection_head,
        "reverse": bool(reverse),
        "pump_eff": _num(b.get("pump_eff")),
        "npsh_a_m": npsh_a,
        "npsh_a_corrected_m": corrected,
        "motor_suggested_kw": (_num(motor.get("kw")) if motor else _num(b.get("motor_kw"))),
        "motor_power_source": (
            str(motor.get("power_source") or "") if motor else "Aspen轴功率（历史建议值）"
        ),
        "motor_basis_kw": _num(motor.get("basis_kw")) if motor else None,
        "motor_margin": _num(motor.get("margin")) if motor else None,
        "motor_note": str(motor.get("note") or "") if motor else "",
        "motor_basis": str(motor.get("basis") or "") if motor else "aspen",
        # 判据与三态
        "npsh_state": state,  # ok / bad / unknown
        "npsh_reason": reason,
        "npsh_threshold_m": threshold,
        "margin_used_m": margin,
        "margin_is_default": margin_default,
        "design_margin": DESIGN_MARGIN,
        "npsh_rule": pump_rules.NPSH_RULE,
        "warnings": warnings,
        # 两侧明细(只有新路径才有;旧路径为空,方便页面判断)
        "lines": lines,
        "suction": (lines or {}).get("suction"),
        "discharge": (lines or {}).get("discharge"),
        "partial_head_m": (lines or {}).get("totals", {}).get("partial_head_m"),
        "lines_rule": (lines or {}).get("rule"),
    }
