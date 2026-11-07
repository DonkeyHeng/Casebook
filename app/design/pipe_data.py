# -*- coding: utf-8 -*-
"""app/design/pipe_data.py —— 管路选型用标准数据表(纯数据 + 纯函数)。

数据来源与核对状态(2026-11-07):

| 数据 | 来源 | 状态 |
| --- | --- | --- |
| `WALL_TOLERANCE_T8` / `WALL_TOLERANCE_T9` / `negative_deviation()` | GB/T 17395-2024 表 8 / 表 9(PDF 第 49 页) | ✅ 300 dpi 渲染逐格录入,已一次人工核对 |
| `Y_VALUE` / `Y_MAX_TEMP_C` | GB/T 20801.1-2025 表 27 | ✅ 条文已确认(≤482 ℃ 取 0.4) |
| `PN_CODES` / `MATERIAL_CODES` / `INSULATION_CODES` | HG/T 20519.6 | ✅ 见 `docs/pipe-class-codes.md` |
| T1 DN→公称外径、T2 外径×标准壁厚、T3 许用应力 | GB/T 17395 表 1/表 4、GB/T 20801.1 表 B.1 | ⬜ **未落地**,等外部录入(见文件末尾 TODO) |

页码与分页结构见 `docs/pipe-standards-index.md`。
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# 1. 系数与代号表
# --------------------------------------------------------------------------

#: 壁厚公式里的 Y 值(GB/T 20801.1-2025 表 27:铁素体钢 / 奥氏体钢在 ≤482 ℃ 均取 0.4)
Y_VALUE = 0.4
Y_MAX_TEMP_C = 482.0

#: 压力等级代号 → 公称压力 MPa(HG/T 20519.6,国内常用系列)
PN_CODES = {
    "H": 0.25,
    "K": 0.6,
    "L": 1.0,
    "M": 1.6,
    "N": 2.5,
    "P": 4.0,
    "Q": 6.4,
    "R": 10.0,
    "S": 16.0,
    "T": 20.0,
    "U": 22.0,
}

#: 材质类别代号 → 名称(HG/T 20519.6)
MATERIAL_CODES = {
    "A": "铸铁",
    "B": "碳钢",
    "C": "普通低合金钢",
    "D": "合金钢",
    "E": "不锈钢",
    "F": "有色金属",
    "G": "非金属",
    "H": "衬里/内防腐",
}

#: 绝热代号(HG/T 20519.6;C/P/T 需人工勾选)
INSULATION_CODES = {
    "H": "保温",
    "C": "保冷",
    "P": "人身防护防烫",
    "T": "伴热",
}

#: 本项目口径:介质温度 ≥ 50 ℃ 自动加 `-H`(见 pipe-class-codes.md §5)
INSULATION_ON_TEMP_C = 50.0


# --------------------------------------------------------------------------
# 2. 壁厚允许偏差(GB/T 17395-2024 表 8 / 表 9,C1 的来源)
# --------------------------------------------------------------------------
#
# 规则表示法:
#   ("sym", pct, abs_mm)  →  ±pct·S 或 ±abs_mm,取其中的较大值
#   ("lower", pct)        →  上偏差不做要求,下偏差 −pct·S
#
# 表 8 按 S/D 分档:((S/D 上限, 规则), ...);上限 None 表示"全区间适用"。
# 注意:表 8 存在**跨列合并格**——S3-B 行的第 1 格横跨 S/D≤0.025 与 0.025~0.05 两列,
#      所以它的第一条是 (0.05, ...) 而不是 (0.025, ...)。核对方法见
#      docs/pipe-standards-index.md §4.1.1(按单元格竖线的墨迹占比数格子)。

_INF = float("inf")

WALL_TOLERANCE_T8 = {
    "S1": ((None, ("sym", 0.15, 0.60)),),
    "S2-A": ((None, ("sym", 0.125, 0.40)),),
    "S2-B": ((None, ("lower", 0.125)),),
    "S3-A": ((None, ("sym", 0.10, 0.20)),),
    "S3-B": (
        (0.05, ("sym", 0.15, 0.40)),  # 该格在原表横跨 S/D≤0.025 与 0.025~0.05 两列
        (0.1, ("sym", 0.125, 0.40)),
        (_INF, ("sym", 0.10, 0.40)),
    ),
    "S3-C": ((None, ("lower", 0.10)),),
    "S4-A": ((None, ("sym", 0.075, 0.15)),),
    "S4-B": (
        (0.025, ("sym", 0.15, 0.20)),
        (0.05, ("sym", 0.125, 0.20)),
        (0.1, ("sym", 0.10, 0.20)),
        (_INF, ("sym", 0.075, 0.20)),
    ),
    "S5": ((None, ("sym", 0.05, 0.10)),),
}

#: 表 9 非标准化壁厚允许偏差:(上偏差百分比, 下偏差百分比),都是相对 S
WALL_TOLERANCE_T9 = {
    "NS1": (0.15, -0.125),
    "NS2": (0.15, -0.10),
    "NS3": (0.125, -0.10),
    "NS4": (0.125, -0.075),
}

#: 无缝钢管的常用负偏差等级。GB/T 8163 一类输送流体用无缝钢管按 −12.5% 计,
#: 对应表 8 的 S2-B / 表 9 的 NS1;要改口径只改这一处。
DEFAULT_WALL_TOL_GRADE = "S2-B"


def _ratio(d_mm: float, s_mm: float) -> float:
    """S/D。d_mm 为公称外径,不允许为 0。"""
    if d_mm <= 0:
        raise ValueError(f"公称外径必须为正数: {d_mm!r}")
    return s_mm / d_mm


def _pick_rule(bands, ratio: float):
    """从 ((上限, 规则), ...) 里挑出命中的规则。"""
    for upper, rule in bands:
        if upper is None or ratio <= upper:
            return rule
    return None


def negative_deviation(
    d_mm: float,
    s_mm: float,
    grade: str = DEFAULT_WALL_TOL_GRADE,
    table: str = "T8",
) -> float:
    """返回**壁厚负偏差的绝对值**(mm),也就是公式里的 C1。

    参数
    ----
    d_mm : 公称外径,mm
    s_mm : 公称壁厚,mm(圆整**之后**的标准壁厚)
    grade : 表 8 的偏差等级(`S2-B` 等)或表 9 的(`NS1` 等)
    table : `"T8"` 标准化壁厚偏差、`"T9"` 非标准化壁厚偏差

    说明
    ----
    * 表 8 的 ±X 是"±pct·S 或 ±abs_mm 取较大值",所以负偏差 = max(pct·S, abs);
    * 表 8 的 S2-B / S3-C 只规定下偏差,负偏差 = pct·S;
    * 表 8 没有空白格;某档为 None 只可能是抄漏,函数会返回 0.0 并暴露出来,不要当成"无要求";
    * 非标准化表(表 9)直接给上下偏差百分比,负偏差 = |下偏差| · S。
    """
    if table.upper() == "T9":
        if grade not in WALL_TOLERANCE_T9:
            raise KeyError(f"表 9 没有偏差等级 {grade!r};可选:{sorted(WALL_TOLERANCE_T9)}")
        _up, down = WALL_TOLERANCE_T9[grade]
        return abs(down) * s_mm

    if grade not in WALL_TOLERANCE_T8:
        raise KeyError(f"表 8 没有偏差等级 {grade!r};可选:{sorted(WALL_TOLERANCE_T8)}")
    rule = _pick_rule(WALL_TOLERANCE_T8[grade], _ratio(d_mm, s_mm))
    if rule is None:
        return 0.0
    if rule[0] == "sym":
        _kind, pct, abs_mm = rule
        return max(pct * s_mm, abs_mm)
    if rule[0] == "lower":
        return rule[1] * s_mm
    raise ValueError(f"未知的偏差规则:{rule!r}")


def min_wall_for_negative_deviation(t_required_mm: float, grade: str = DEFAULT_WALL_TOL_GRADE) -> float:
    """负偏差只往下做时,反解"至少要选多厚",使 t_required ≤ S·(1 − pct)。

    只处理表 9 / 表 8 里"百分比负偏差"的情形(表 8 的 S2-B、S3-C、表 9 全部);
    表 8 里带 `或 ±abs` 的等级不能这样反解(要按 S 迭代),那种情况直接调
    `negative_deviation()` 逐档试算。
    """
    if grade in WALL_TOLERANCE_T9:
        pct = abs(WALL_TOLERANCE_T9[grade][1])
    else:
        rules = WALL_TOLERANCE_T8.get(grade)
        if not rules or len(rules) != 1 or rules[0][1] is None or rules[0][1][0] != "lower":
            raise ValueError(
                f"{grade!r} 不是纯百分比负偏差等级(带 或 ±abs 的要按 S 逐档试算,不能反解)"
            )
        pct = rules[0][1][1]
    return t_required_mm / (1.0 - pct)


# --------------------------------------------------------------------------
# 3. 待落地:等外部录入的标准表(不要凭空补数)
# --------------------------------------------------------------------------
#
# TODO(T1) DN → 公称外径:化工 Φ 系列(DN500→Φ530),要拿 GB/T 17395 表 1/表 4 的 D 列核对;
# TODO(T2) 公称外径 → 可选标准壁厚序列:GB/T 17395 表 1(D≤630、S≤25)与表 4(不锈钢);
# TODO(T3) 许用应力 [σ]:GB/T 20801.1 表 B.1 的 20# / Q345A(B) / S30408 / S31603,20–400 ℃。
#
# 三张表都靠"看图录入"(文字层是乱码),交付物见 docs/_internal/prompt-vision-extract.md。
# 表落地后在这里补:
#   DN_TO_OD: dict[int, float]
#   OD_TO_WALLS: dict[float, tuple[float, ...]]
#   ALLOWABLE_STRESS: dict[str, dict[float, float]]   # 牌号 → {温度: MPa}

DN_TO_OD: dict[int, float] = {}
OD_TO_WALLS: dict[float, tuple[float, ...]] = {}
ALLOWABLE_STRESS: dict[str, dict[float, float]] = {}

#: 本期 DN 上限(超过就标黄提示"超出 DN600,需专项确认")
MAX_DN = 600
