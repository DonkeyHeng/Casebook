"""换热器选型的内置默认字典(只读兜底)。

口径(见 `docs/heat-exchanger-selection-plan.md` §3.7):

1. **只有这里的字典是权威默认值**。`data/config/hx-*.json` 存在且合法时
   整体覆盖(不是合并),由 `app/store` 负责读写,本模块不碰文件。
2. 内置值全部标 `builtin`;界面/导出看到 `builtin` 就按"预设假设"展示,
   不得当成实测。
3. 数值是工程常用档,不是标准原文。改数值只改这里,别在调用点写魔法数。

键名与方案 §3.4 / §5.7 / §2.2 一一对应,改动需同步文档与自测。
"""

from __future__ import annotations

# 污垢热阻(管程/壳程各选一个键),单位 m²·K/W。方案 §3.4。
FOULING_DEFAULTS: dict[str, dict] = {
    "cooling-water": {"value": 0.000172, "label": "冷却水", "fluid": "water"},
    "steam": {"value": 0.000086, "label": "水蒸气", "fluid": "steam"},
    "brine": {"value": 0.000172, "label": "盐水 / 冷冻盐水", "fluid": "brine"},
    "organic-liquid": {"value": 0.000172, "label": "有机液体", "fluid": "organic"},
    "organic-vapor": {"value": 0.000086, "label": "有机蒸气 / 塔顶蒸汽", "fluid": "organic"},
    "reboiler-fluid": {"value": 0.000516, "label": "再沸器物料", "fluid": "organic"},
    "refrigerant": {"value": 0.000176, "label": "制冷剂", "fluid": "refrigerant"},
}

# 经验总传热系数区间,单位 W/(m²·K)。方案 §5.7。
# 只在"没有面积、要反推 A_req"时取 default;min/max 只用于界面提示区间。
U_REFERENCE_DEFAULTS: dict[str, dict] = {
    "water-water": {"min": 800, "max": 1500, "default": 1000, "label": "水–水"},
    "organic-water": {"min": 500, "max": 1200, "default": 700, "label": "有机液–水"},
    "gas-liquid": {"min": 200, "max": 600, "default": 350, "label": "气体–液体"},
    "gas-gas": {"min": 50, "max": 250, "default": 120, "label": "气体–气体"},
    "condensing-steam": {
        "min": 1000,
        "max": 2000,
        "default": 1500,
        "label": "蒸汽冷凝–水",
    },
    "condensing-organic": {
        "min": 600,
        "max": 1200,
        "default": 800,
        "label": "有机蒸气冷凝",
    },
    "boiling-organic": {"min": 500, "max": 1500, "default": 800, "label": "有机液沸腾"},
}

# 公用工程预设(仅在节点读不到温度时启用),温度 ℃。方案 §2.2。
# phase: condensing 等温冷凝 / boiling 等温蒸发 / liquid 显热液体 / gas 显热气体
UTILITY_PRESET_DEFAULTS: dict[str, dict] = {
    "ls-135": {"t_in_c": 135, "t_out_c": 135, "phase": "condensing", "label": "低压饱和蒸汽"},
    "ms-175": {"t_in_c": 175, "t_out_c": 175, "phase": "condensing", "label": "中压饱和蒸汽"},
    "hs-250": {"t_in_c": 250, "t_out_c": 250, "phase": "condensing", "label": "高压饱和蒸汽"},
    "cw-25-35": {"t_in_c": 25, "t_out_c": 35, "phase": "liquid", "label": "循环冷却水(常规)"},
    "cw-30-40": {"t_in_c": 30, "t_out_c": 40, "phase": "liquid", "label": "循环冷却水(夏季)"},
    "chilled-7-12": {"t_in_c": 7, "t_out_c": 12, "phase": "liquid", "label": "冷冻水"},
    "brine-m15-m5": {"t_in_c": -15, "t_out_c": -5, "phase": "liquid", "label": "冷冻盐水"},
    "air-25-35": {"t_in_c": 25, "t_out_c": 35, "phase": "gas", "label": "空冷 / 风机"},
}

# Aspen 公用工程名 → 预设键的**建议映射**(只做建议,命中后仍标 preset)。
# 名字是用户自己起的,不能当成类型判据;读得到温度时一律优先 aspen 值。
UTILITY_NAME_SUGGESTIONS: dict[str, str] = {
    "LWAT": "chilled-7-12",
    "WAT": "cw-30-40",
    "MS": "ms-175",
    "LS": "ls-135",
    "HS": "hs-250",
    "AIR": "air-25-35",
}

# 管程/壳程分配规则(方案 §5.12)。顺序即优先级,`rule` 写进溯源。
TUBE_FLUID_RULES: tuple[dict, ...] = (
    {"rule": 1, "key": "corrosive", "text": "腐蚀性介质走管程,只换管束"},
    {"rule": 2, "key": "high-pressure", "text": "高压侧走管程,壳体不承高压"},
    {"rule": 3, "key": "fouling", "text": "易结垢侧走管程,可机械清洗"},
    {"rule": 4, "key": "cooling-water", "text": "冷却水走管程"},
    {"rule": 5, "key": "utility-isothermal", "text": "蒸汽冷凝/制冷剂蒸发走壳程"},
    {"rule": 6, "key": "large-flow", "text": "大流量低粘度侧走壳程,压降更低"},
    {"rule": 7, "key": "fallback", "text": "无明确判据,按课堂惯例"},
)

# 计算说明书与 EDR 清单里对来源的中文标签。**界面、台账、导出共用这一张表**,
# 免得同一来源在三处写法不一致。
SOURCE_LABELS: dict[str, str] = {
    "aspen": "Aspen 实测/简捷",
    "manual": "人工录入",
    "edr": "EDR 校核",
    "preset": "预设假设",
    "assumed": "经验假设",
    "builtin": "内置默认",
    "rule": "规则建议",
    "user": "用户指定",
    "missing": "未取得",
    "none": "无",
}


def source_label(source: str | None) -> str:
    """把来源代码翻成中文;未知代码原样返回,不猜。"""
    if not source:
        return SOURCE_LABELS["missing"]
    return SOURCE_LABELS.get(source, source)


def fouling_value(config: dict | None, key: str | None) -> float | None:
    """按查表键取污垢热阻;键为空或查不到返回 None(不静默取默认档)。"""
    if not key:
        return None
    table = config or FOULING_DEFAULTS
    row = table.get(key)
    if not isinstance(row, dict):
        return None
    try:
        return float(row["value"])
    except (KeyError, TypeError, ValueError):
        return None


def u_reference(config: dict | None, key: str | None) -> dict | None:
    """取经验 U 区间;返回 {'min','max','default','label'} 或 None。"""
    if not key:
        return None
    table = config or U_REFERENCE_DEFAULTS
    row = table.get(key)
    if not isinstance(row, dict):
        return None
    try:
        return {
            "min": float(row["min"]),
            "max": float(row["max"]),
            "default": float(row["default"]),
            "label": str(row.get("label") or key),
        }
    except (KeyError, TypeError, ValueError):
        return None


def utility_preset(config: dict | None, key: str | None) -> dict | None:
    """取公用工程预设;返回 {'t_in_c','t_out_c','phase','label'} 或 None。"""
    if not key:
        return None
    table = config or UTILITY_PRESET_DEFAULTS
    row = table.get(key)
    if not isinstance(row, dict):
        return None
    try:
        return {
            "t_in_c": float(row["t_in_c"]),
            "t_out_c": float(row["t_out_c"]),
            "phase": str(row.get("phase") or "unknown"),
            "label": str(row.get("label") or key),
        }
    except (KeyError, TypeError, ValueError):
        return None


def utility_preset_suggestion(utility_id: str | None, utility_type: str | None) -> str | None:
    """按 Aspen 公用工程名/类型给一个预设键建议;仅建议,调用方标 preset。"""
    if utility_id:
        hit = UTILITY_NAME_SUGGESTIONS.get(utility_id.upper())
        if hit:
            return hit
    if utility_type == "steam":
        return "ms-175"
    if utility_type == "water":
        return "cw-30-40"
    if utility_type == "air":
        return "air-25-35"
    return None


def tube_fluid_suggestion(
    kind: str,
    sides: dict,
    utility_phase: str | None = None,
    default: str | None = None,
) -> dict:
    """按 §5.12 的 7 条规则给"哪一侧走管程"的建议。

    参数
    ----
    kind : 'HeatX' / 'Heater'
        决定合法枚举:HeatX 只能 hot/cold,Heater 只能 process/utility。
    sides : dict
        {侧名: {'pressure_bar': float|None, 'corrosive': bool, 'fouling': bool,
                'mass_flow_kgh': float|None, 'viscosity_cp': float|None}}
        侧名须是 hot / cold / process / utility。
    utility_phase : 'condensing' / 'boiling' / 'liquid' / 'gas' / None
        公用工程相态;等温相变命中规则 5。
    default : str|None
        兜底值;不传则 HeatX→'cold',Heater→'process'(规则 7)。

    返回 {'value', 'reason', 'matched_rule'}。**只建议,不写库**。
    """
    allowed = ("hot", "cold") if kind == "HeatX" else ("process", "utility")
    candidates = {k: v for k, v in sides.items() if k in allowed and isinstance(v, dict)}

    # 规则 1:腐蚀侧走管程(只看工艺侧;公用工程一般不做合金管束)
    for side in ("hot", "cold", "process"):
        if side in candidates and candidates[side].get("corrosive"):
            return _suggest(side, 1, f"{side} 侧有腐蚀性")

    # 规则 2:高压侧走管程(压差 >= 5 bar 才算"一侧明显高压")
    press = {
        s: (
            candidates[s].get("p_bar")
            if candidates[s].get("p_bar") is not None
            else candidates[s].get("pressure_bar")
        )
        for s in candidates
    }
    vals = [v for v in press.values() if isinstance(v, (int, float))]
    if len(vals) == 2 and max(vals) - min(vals) >= 5.0:
        high = max(press, key=lambda s: press[s])
        return _suggest(high, 2, f"{high} 侧压力高({press[high]:.1f} bar)")

    # 规则 3:易结垢侧走管程
    for side in ("hot", "cold", "process"):
        if side in candidates and candidates[side].get("fouling"):
            return _suggest(side, 3, f"{side} 侧易结垢")

    # 规则 4:冷却水公用工程走管程
    if utility_phase == "liquid" and "utility" in candidates:
        fluid = str(candidates["utility"].get("fluid") or "")
        if fluid in ("water", "brine", ""):
            return _suggest("utility", 4, "公用工程为冷却水/冷冻水")

    # 规则 5:等温相变公用工程走壳程 → 工艺侧走管程
    if utility_phase in ("condensing", "boiling"):
        process_side = "process" if "process" in candidates else None
        if process_side:
            return _suggest(process_side, 5, "公用工程等温相变,走壳程")

    # 规则 6:大流量低粘度侧走壳程 → 管程给另一侧(只在两侧都有流量时判)
    flows = {s: candidates[s].get("mass_flow_kgh") for s in candidates}
    vv = [v for v in flows.values() if isinstance(v, (int, float)) and v > 0]
    if len(vv) == 2 and max(vv) / min(vv) >= 3.0:
        big = max(flows, key=lambda s: flows[s])
        other = next(s for s in candidates if s != big)
        return _suggest(other, 6, f"{big} 侧流量大,走壳程")

    # 规则 7:惯例兜底
    fallback = default or ("cold" if kind == "HeatX" else "process")
    if fallback not in allowed:
        fallback = allowed[0]
    return _suggest(fallback, 7, "无明确判据,按惯例")


def _suggest(value: str, rule: int, reason: str) -> dict:
    return {"value": value, "rule": rule, "reason": reason}


def check_tube_fluid(kind: str, value: str | None) -> str | None:
    """校验枚举越界;合法返回 None,非法返回错误文案(中文,可直接上界面)。"""
    if value is None:
        return None
    if kind == "HeatX" and value not in ("hot", "cold"):
        return f"HeatX 的 tube_fluid 只能取 hot/cold,收到 {value!r}"
    if kind in ("Heater", "HeaterX") and value not in ("process", "utility"):
        return f"{kind} 的 tube_fluid 只能取 process/utility,收到 {value!r}"
    return None
