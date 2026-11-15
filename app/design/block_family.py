"""设备家族:把 Aspen 的几十种块类型收敛成 8 个页签。

为什么要这个模块
----------------
Aspen 的块类型远不止十种(balance.py 里就分了 SKIP/ZERO/POWER/DUTY 四组),
直接平铺成页签根本没法用。第二期把「设备页」改成按类型筛选,这里只做一件事:
**类型 → 家族**。

三条约定
--------
* 家族表是**唯一来源**:前端页签读 `/api/block-families`,Excel / AI 包以后也读它;
* 未识别的类型落「其他」,**绝不丢设备**(用户模型、固体单元都在这里);
* 纯函数、不依赖 Aspen、不读库 —— 可以直接进 selftest。

默认家族:有塔就落塔;没有塔就落第一个非空的;一台都没有返回「塔」,由前端显示空态。
"""

from __future__ import annotations

# (key, 显示名, 覆盖的 Aspen 类型)。顺序 = 页签顺序。
FAMILIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("column", "塔", ("RadFrac", "Absorber", "Extract")),
    ("heat", "换热", ("Heater", "HeaterX", "HeatX", "MHeatX")),
    ("flash", "分离", ("Flash2", "Flash3", "Decanter", "Sep", "Sep2")),
    ("react", "反应", ("RStoic", "REquil", "RGibbs", "RYield", "RCSTR", "RPlug", "RBatch")),
    # 泵单独一族:它有自己的一整套参数(流量/扬程/汽蚀/选型),和压缩机阀门混在一起
    # 会逼着每种列都带"对这台无意义"的留白。
    ("pump", "泵", ("Pump",)),
    # 涡轮机/透平:位号以 K 开头的块在 Aspen 里也被标成 Pump 类型(实测 case56 有 6 台),
    # 但它们是回收功的透平,不是泵 —— 混进泵页会把 NPSHₐ、选型这些"对透平无意义"的列全带上。
    ("turbine", "涡轮机", ("Turbine",)),
    ("move", "流体输送", ("Compr", "MCompr", "Valve", "Pipe", "Mult")),
    ("mix", "混合分流", ("Mixer", "FSplit", "Splitter")),
    ("other", "其他", ()),
)

FAMILY_LABELS: dict[str, str] = {key: label for key, label, _types in FAMILIES}
DEFAULT_FAMILY = "column"

_LOOKUP: dict[str, str] = {btype.lower(): key for key, _label, types in FAMILIES for btype in types}


# Aspen 把涡轮机也标成 Pump 类型,只能靠位号区分:P 开头是泵,K 开头是涡轮机。
_TURBINE_PREFIX = "K"


def family_of(btype, name=None) -> str:
    """块类型 → 家族 key。空值、未知类型一律 'other',不抛异常。

    传了 name 时会多看一位:类型是 Pump 但位号以 K 开头 → 涡轮机(见 FAMILIES 注释)。
    不传 name 就只按类型判 —— 调用方拿不到位号时的保守退化。
    """
    if not btype:
        return "other"
    key = _LOOKUP.get(str(btype).strip().lower(), "other")
    if key == "pump" and name is not None:
        if str(name).strip().upper().startswith(_TURBINE_PREFIX):
            return "turbine"
    return key


def family_of_block(block) -> str:
    """整个设备行 → 家族 key。

    同时吃 dict 与 `sqlite3.Row`(前者有 .get、后者只能下标),所以这里不直接用 .get ——
    实测把两者混用时会在 AI 包导出处炸成 `'sqlite3.Row' object has no attribute 'get'`。
    """
    if block is None:
        return "other"

    def pick(key):
        try:
            return block[key]
        except (KeyError, IndexError, TypeError):
            return None

    return family_of(pick("type"), pick("name"))


def describe() -> list[dict]:
    """给前端/导出用的家族表(顺序即页签顺序)。"""
    return [{"key": key, "label": label, "types": list(types)} for key, label, types in FAMILIES]


def counts(blocks) -> dict[str, int]:
    """{家族 key: 台数},只统计出现过的。"""
    out: dict[str, int] = {}
    for block in blocks or []:
        key = family_of_block(block)
        out[key] = out.get(key, 0) + 1
    return out


def pick_default(blocks) -> str:
    """默认页签:有塔就是塔;否则第一个非空的;全空返回「塔」。"""
    seen = counts(blocks)
    if seen.get(DEFAULT_FAMILY):
        return DEFAULT_FAMILY
    for key, _label, _types in FAMILIES:
        if seen.get(key):
            return key
    return DEFAULT_FAMILY
