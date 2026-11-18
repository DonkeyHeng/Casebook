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


def compute_view(block: dict, design: dict | None, saved: dict | None) -> dict:
    """把工况读数 + 人填修正项 + 台账手填字段合成台账页展示视图。

    block  : `blocks` 表一行(dict),只用泵参数列
    design : `pump_design` 一行或 None(ΔZ / Σhf / 管长 / Σζ)
    saved  : `pump_selection` 一行或 None(人工决定的字段)
    """
    b = block or {}
    d = design or {}
    s = saved or {}

    head = _num(b.get("head_m"))  # 压升扬程 HEAD_CAL,m
    dz = _num(d.get("dz_m"))  # 位压头,m
    hf = _num(d.get("hf_m"))  # 管路阻力,m
    total = head + dz + hf if None not in (head, dz, hf) else None
    selection_head = math.ceil(total * DESIGN_MARGIN) if total is not None else None

    p_in = _num(b.get("p_in_bar"))
    p_v = _num(b.get("pv_bar"))
    rho = _num(b.get("rho_kgm3"))
    npsh_a = pump_rules.npsh_available_m(p_in, p_v, rho)

    motor_suggested = _num(b.get("motor_kw"))
    npshr = _num(s.get("npshr_m"))
    margin_given = _num(s.get("margin_m"))
    margin = margin_given if margin_given is not None else NPSH_MARGIN_SUGGESTED

    missing: list[str] = []
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

    return {
        # 实时设计点(全部来自当前工况,不落库)
        "flow_m3h": _num(b.get("flow_m3h")),
        "head_m": head,
        "dz_m": dz,
        "hf_m": hf,
        "design_head_m": total,
        "selection_head_m": selection_head,
        "pump_eff": _num(b.get("pump_eff")),
        "npsh_a_m": npsh_a,
        "motor_suggested_kw": motor_suggested,
        # 判据与三态
        "npsh_state": state,  # ok / bad / unknown
        "npsh_reason": reason,
        "npsh_threshold_m": threshold,
        "margin_used_m": margin,
        "margin_is_default": margin_given is None,
        "design_margin": DESIGN_MARGIN,
        "npsh_rule": pump_rules.NPSH_RULE,
    }
