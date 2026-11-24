"""换热设备的两条提取通道(纯函数,不碰 Aspen / 不读盘)。

为什么必须分开:HeatX 的冷热两侧边界都在 Aspen 节点里(两侧进出口温度/压力/相态),
Heater 只有工艺侧一侧,另一侧必须靠公用工程模板补全。混着解析会在 Heater 上读空冷侧
节点报错,只扫 HeatX 又会漏掉塔顶冷凝器、塔釜再沸器、产品冷却器这些常用 Heater 模拟
的设备。

本模块只做三件事:

1. 按 BlockType 归通道(``channel_of``);
2. 滤掉零负荷 Heater —— 进出口不变、只调压的"标签点"(``is_trivial``);
3. 按两侧相态组合挑经验 U 键(``pick_u_key``),供统一选型内核取默认值。

经验 U 只是估算,不进"合格/不合格"判定;键名与 ``data.U_REFERENCE_DEFAULTS`` 一一对应。
"""

from __future__ import annotations

import math

#: 通道代码 → 中文名(界面/导出共用)
CHANNEL_TEXT: dict[str, str] = {
    "process-process": "工艺-工艺",
    "process-utility": "工艺-公用工程",
}

#: BlockType → 通道。未登记的类型返回 None,由调用方按"非换热"处理。
CHANNEL_OF_TYPE: dict[str, str] = {
    "HeatX": "process-process",
    "MHeatX": "process-process",
    "Heater": "process-utility",
    "HeaterX": "process-utility",
}

#: 零负荷判据(kW):|Q| 低于这个值视为标签点/调压点,不进设备表。
TRIVIAL_DUTY_KW = 1e-3

#: 相态代码 → 中文名
PHASE_TEXT: dict[str, str] = {
    "liquid": "液体",
    "gas": "气体",
    "condensing": "冷凝",
    "boiling": "沸腾",
    "two-phase": "两相",
    "unknown": "相态未知",
}


def _num(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def channel_of(block_type: str | None) -> str | None:
    """按 BlockType 归通道;不认识的返回 None。"""
    return CHANNEL_OF_TYPE.get(str(block_type or "").strip())


def is_trivial(block_type: str | None, duty_kw) -> bool:
    """零负荷 Heater:只有工艺-公用工程通道才过滤,HeatX 不按此判据跳过。

    口径按工程约定取 |Q| < 1e-3 kW(约 1 W)。热负荷读不到(None)时不跳过 ——
    那是"缺数据",要让页面报出来,不能静默消失。
    """
    if channel_of(block_type) != "process-utility":
        return False
    q = _num(duty_kw)
    return q is not None and abs(q) < TRIVIAL_DUTY_KW


def side_phase(side: dict | None) -> str:
    """按进出口汽化率判一侧的相态:冷凝 / 沸腾 / 气体 / 液体 / 两相 / 未知。

    只用于挑经验 U,不替代 ``params.check_boundaries`` 的相变拦截。
    """
    if not side:
        return "unknown"
    vap_in = _num(side.get("vap_in"))
    vap_out = _num(side.get("vap_out"))
    if vap_in is None or vap_out is None:
        return "unknown"
    if vap_in >= 0.9 and vap_out <= 0.1:
        return "condensing"
    if vap_in <= 0.1 and vap_out >= 0.5:
        return "boiling"
    if min(vap_in, vap_out) >= 0.5:
        return "gas"
    if max(vap_in, vap_out) <= 0.05:
        return "liquid"
    return "two-phase"


def _pair_key(hot_phase: str, cold_phase: str) -> str | None:
    """工艺-工艺:两侧相态组合 → 经验 U 键(对称匹配)。"""
    pair = frozenset((hot_phase, cold_phase))
    # 冷凝/沸腾优先:一侧有相变,另一侧按显热侧归类
    for phase, key in (("condensing", "condensing-organic"), ("boiling", "boiling-organic")):
        other = cold_phase if hot_phase == phase else hot_phase
        if phase in pair and other in ("liquid", "gas", "condensing", "boiling"):
            return key
    if pair == frozenset(("liquid", "liquid")):
        return "organic-water"
    if pair == frozenset(("gas", "liquid")):
        return "gas-liquid"
    if pair == frozenset(("gas", "gas")):
        return "gas-gas"
    return None


def pick_u_key(channel: str | None, sides: dict, utility: dict | None = None):
    """挑经验 U 键。返回 ``(key, reason_text)``,挑不出时 key 为 None。

    * 工艺-工艺:看两侧相态(冷凝、沸腾、气-液、液-液、气-气);
    * 工艺-公用工程:看工艺侧相态 + 公用工程介质相态/种类。

    reason 写进溯源,便于在界面/导出里说清"这个 U 是按什么挑的"。
    """
    if channel == "process-process":
        hot_phase = side_phase(sides.get("hot"))
        cold_phase = side_phase(sides.get("cold"))
        key = _pair_key(hot_phase, cold_phase)
        reason = f"热侧{PHASE_TEXT[hot_phase]} + 冷侧{PHASE_TEXT[cold_phase]}"
        return key, reason

    if channel == "process-utility":
        process_phase = side_phase(sides.get("process"))
        util = dict(utility or {})
        util_phase = str(util.get("phase") or "unknown")
        util_fluid = str(util.get("fluid") or "").strip().lower()
        reason = (
            f"工艺侧{PHASE_TEXT[process_phase]} + 公用工程{PHASE_TEXT.get(util_phase, util_phase)}"
        )

        # 工艺侧相变(冷凝器 / 再沸器)优先:公用工程只做显热吸收或供热
        if process_phase == "condensing":
            return "condensing-organic", reason
        if process_phase == "boiling":
            return "boiling-organic", reason

        if util_phase == "condensing":
            # 蒸汽冷凝给热,另一侧是显热;介质是水/蒸汽时取蒸汽档
            return (
                "condensing-steam" if util_fluid in ("steam", "water") else "condensing-organic"
            ), reason
        if util_phase in ("liquid", "boiling"):
            return ("gas-liquid" if process_phase == "gas" else "organic-water"), reason
        if util_phase == "gas":
            return ("gas-gas" if process_phase == "gas" else "gas-liquid"), reason
        return None, reason
    return None, ""
