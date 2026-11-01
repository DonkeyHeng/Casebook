"""单位归一:把 Aspen 里的原始单位和值,换算成项目固定单位。

项目固定单位:℃ / bar / kg·h⁻¹ / kmol·h⁻¹ / m³·h⁻¹ / kW / m²

为什么要做这一步
---------------
Aspen 的单位跟着模型的单位集走(本机是 `METCBAR`,但别的 bkp 可能是 `ENG`)。
实测同一个"热负荷":加热器给 `cal/sec`,泵给 `kW`。直接并排会误判,
所以**一律按节点自带的 UnitString 换算**,不假设单位集固定。
"""
from __future__ import annotations

TARGET = {
    "temperature": "C",
    "pressure": "bar",
    "mass_flow": "kg/hr",
    "mole_flow": "kmol/hr",
    "volume_flow": "m3/hr",
    "power": "kW",
    "area": "m2",
    "enthalpy": "kJ/kg",
    "enthalpy_molar": "kJ/kmol",
    "energy_flow": "kW",
}

# 每个量纲:{单位: (系数, 偏移)}   目标值 = 原值 * 系数 + 偏移
_TABLES: dict[str, dict[str, tuple[float, float]]] = {
    "temperature": {
        "c": (1.0, 0.0),
        "degc": (1.0, 0.0),
        "k": (1.0, -273.15),
        "f": (5 / 9, -32 * 5 / 9),
        "degf": (5 / 9, -32 * 5 / 9),
        "r": (5 / 9, -491.67 * 5 / 9),
        "rankine": (5 / 9, -491.67 * 5 / 9),
    },
    "pressure": {
        "bar": (1.0, 0.0),
        "atm": (1.01325, 0.0),
        "pa": (1e-5, 0.0),
        "kpa": (1e-2, 0.0),
        "mpa": (10.0, 0.0),
        "psi": (0.0689475729, 0.0),
        "psia": (0.0689475729, 0.0),
        "mmhg": (0.001333224, 0.0),
        "torr": (0.001333224, 0.0),
        "kgf/cm2": (0.980665, 0.0),
    },
    "mass_flow": {
        "kg/hr": (1.0, 0.0),
        "kg/h": (1.0, 0.0),
        "kg/s": (3600.0, 0.0),
        "g/s": (3.6, 0.0),
        "lb/hr": (0.45359237, 0.0),
        "lb/s": (1632.932532, 0.0),
        "t/hr": (1000.0, 0.0),      # 公吨
        "ton/hr": (1000.0, 0.0),    # Aspen 里按公吨处理
    },
    "mole_flow": {
        "kmol/hr": (1.0, 0.0),
        "kmol/h": (1.0, 0.0),
        "kmol/s": (3600.0, 0.0),
        "kmol/min": (60.0, 0.0),
        "mol/s": (3.6, 0.0),
        "lbmol/hr": (0.45359237, 0.0),
    },
    "volume_flow": {
        "m3/hr": (1.0, 0.0),
        "m3/h": (1.0, 0.0),
        "cum/hr": (1.0, 0.0),      # Aspen 里"立方米/小时"的写法(读样本模型时实测到)
        "cum/h": (1.0, 0.0),
        "m3/s": (3600.0, 0.0),
        "l/min": (0.06, 0.0),
        "l/hr": (0.001, 0.0),
        "l/s": (3.6, 0.0),
        "gal/min": (0.2271247, 0.0),   # 美制加仑
        "ft3/hr": (0.02831685, 0.0),
        "cuft/hr": (0.02831685, 0.0),
    },
    "power": {
        "kw": (1.0, 0.0),
        "w": (0.001, 0.0),
        "mw": (1000.0, 0.0),
        # ⚠️ Aspen 的 cal 是 IT 卡(4.1868 J),不是热化学卡(4.184 J)。
        # 2026-11-01 实测:同一模型 MET 读 28.0 Gcal/hr、改 ENG 后读 111,112,980 Btu/hr,
        # 两者是同一物理量;按 Btu 换算得 32564.0 kW ⇒ 1 Gcal/hr = 1163.0 kW ⇒ 1 cal = 4.1868 J。
        # 用 1162.2222(即 4.184)会让所有热负荷偏低 0.067%。
        "cal/sec": (0.0041868, 0.0),
        "cal/s": (0.0041868, 0.0),
        "gcal/hr": (1163.0, 0.0),
        "gcal/s": (4186800.0, 0.0),
        "kcal/hr": (0.001163, 0.0),    # 与 IT 卡一致
        "kcal/s": (4.1868, 0.0),
        "btu/hr": (0.0002930711, 0.0),
        "hp": (0.745699872, 0.0),
        "j/s": (0.001, 0.0),
    },
    "area": {
        "m2": (1.0, 0.0),
        "sqm": (1.0, 0.0),
        "sqft": (0.09290304, 0.0),
        "ft2": (0.09290304, 0.0),
        "cm2": (0.0001, 0.0),
    },
    # 质量焓 → kJ/kg。同样是 IT 卡(1 cal = 4.1868 J)。
    # 实测:流股 Output\HMX_MASS\MIXED 给的是 J/kg(数值很大,含生成焓基准)。
    "enthalpy": {
        "kj/kg": (1.0, 0.0),
        "j/kg": (0.001, 0.0),
        "j/g": (1.0, 0.0),
        "j/gm": (1.0, 0.0),
        "cal/g": (4.1868, 0.0),
        "cal/gm": (4.1868, 0.0),
        "kcal/kg": (4.1868, 0.0),
        "btu/lb": (2.3260, 0.0),
        "btu/lbm": (2.3260, 0.0),
        "cal/lb": (4.1868 / 0.45359237, 0.0),
    },
    # 摩尔焓 → kJ/kmol。实测:流股 Output\HMX\MIXED 给的是 J/kmol。
    "enthalpy_molar": {
        "kj/kmol": (1.0, 0.0),
        "j/kmol": (0.001, 0.0),
        "kj/mol": (1000.0, 0.0),
        "j/mol": (1.0, 0.0),
        "kcal/kmol": (4.1868, 0.0),
        "kcal/mol": (4186.8, 0.0),
        "cal/mol": (4186.8, 0.0),
        "btu/lbmol": (2.3260, 0.0),
        "btu/mol": (2326.0, 0.0),
    },
    # 能量流(已经是 m·h)→ kW。实测:流股 Output\HMX_FLOW\MIXED 给的是 MW。
    # 这个量可以直接跨设备求和做 ΔH,不用再乘流量。
    "energy_flow": {
        "kw": (1.0, 0.0),
        "mw": (1000.0, 0.0),
        "w": (0.001, 0.0),
        "j/s": (0.001, 0.0),
        "hp": (745.699872, 0.0),
        "gcal/hr": (1163.0, 0.0),
        "gcal/s": (4186800.0, 0.0),
        "kcal/hr": (1.163, 0.0),
        "kcal/s": (4186.8, 0.0),
        "cal/sec": (0.0041868, 0.0),
        "cal/s": (0.0041868, 0.0),
        "btu/hr": (0.0002930711, 0.0),
    },
}


def normalize_unit(unit: str) -> str:
    """把 'Cal/Sec'、'KW'、'm³/h' 这类写法统一成查表 key。"""
    if unit is None:
        return ""
    u = str(unit).strip().lower().replace("³", "3").replace("²", "2")
    u = u.replace("·", "").replace(" ", "")
    return u


def convert(quantity: str, value, unit: str):
    """把 (value, unit) 换算到该量纲的目标单位。

    Returns:
        换算后的 float;值为 None 或单位不认识时返回 None(调用方据此标"单位未知")。
    """
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None

    if quantity == "fraction":  # 汽化率/分数:无量纲,原样返回
        return num

    table = _TABLES.get(quantity)
    if table is None:
        raise KeyError(f"未知量纲:{quantity}")

    key = normalize_unit(unit)
    if not key:
        # 无量纲量(汽化率、分数)本来就该是原值
        return num if quantity in ("fraction",) else None

    factor = table.get(key)
    if factor is None:
        return None

    k, offset = factor
    return num * k + offset


def target_unit(quantity: str) -> str:
    """该量纲的目标单位(用于展示)。"""
    return TARGET.get(quantity, "")
