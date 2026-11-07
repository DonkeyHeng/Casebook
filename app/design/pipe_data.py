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

import csv
import io
import json
import re
from pathlib import Path

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

#: 牌号 → 材质类别代号(本项目只用到这五个,见 pipe-selection-plan.md §14.4)
MATERIAL_CODE_BY_GRADE = {
    "20#": "B",
    "Q345A": "C",
    "Q345B": "C",
    "S30408": "E",
    "S31603": "E",
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
# 3. DN → 公称外径(化工 Φ / B 系列)
# --------------------------------------------------------------------------
#
# 来源与核对状态(2026-11-07):
#   * DN50–DN600 的 Φ 值已逐个在 **GB/T 17395-2024 表 1 的 D 列**确认存在
#     (PDF 第 11–19、24 页):57 / 76(76.1) / 89(88.9) / 108 / 133 / 159 /
#     219(219.1) / 273(273.1) / 325(323.9) / 377 / 426 / 480 / 530 / 630;
#   * DN10–DN40 的 Φ 值是 B 系列惯例,**还没有在表 1 里逐项核对**,标 unverified;
#   * DN 与 Φ 的对应关系本身是**工程惯例**(不是 GB/T 17395 定义的),§14.2 已定稿
#     "化工 Φ 系列,DN500→Φ530",这里按同一族补齐。
DN_TO_OD: dict[int, float] = {
    10: 14.0,
    15: 18.0,
    20: 25.0,
    25: 32.0,
    32: 38.0,
    40: 45.0,
    50: 57.0,
    65: 76.0,
    80: 89.0,
    100: 108.0,
    125: 133.0,
    150: 159.0,
    200: 219.0,
    250: 273.0,
    300: 325.0,
    350: 377.0,
    400: 426.0,
    450: 480.0,
    500: 530.0,
    600: 630.0,
}

#: 外径已在标准表里核对过的 DN。2026-11-07 复核:20 档外径在 GB/T 17395 表 1
#: 与表 4 的 D 列里**全部存在**(脚本按 nominal 值比对,0 缺口)。
DN_OD_VERIFIED = set(DN_TO_OD)


# --------------------------------------------------------------------------
# 3.1 工艺管道最小壁厚下限(工程惯例,不是尺寸表)
# --------------------------------------------------------------------------
#
# 为什么需要这一层:GB/T 20801.1 的壁厚公式只管**承压**。1.0 MPa、DN15 的
# 不锈钢管按公式只要 0.07 mm,圆整到标准壁厚是 0.5 mm —— 那是换热管/仪表管的
# 规格,工艺管道焊不出来,也扛不住支承、运输与装卸(标准 4.1.6.2 明确要求把
# 腐蚀/冲蚀/螺纹深度/支承等裕量算进"最小厚度")。所以小口径工艺管道的壁厚
# 实际由**最小壁厚**控制,不是由承压控制。
#
# 取值(2026-11-07 用户拍板):DN10–25 → 2.0;DN32–65 → 3.0;DN80–100 → 3.0;
# DN125 起不设限。只按"能施工"定:小口径工艺管道的最小壁厚由焊接、刚度与支承
# 要求控制,与承压无关;故取偏下限,不追求贴近任何外部表格。
# 改之前先看 docs/pipe-selection-plan.md §15.2。
#
# 键是 **DN 上限**:`{25: 2.0}` 表示 DN ≤ 25 管子壁厚不小于 2.0 mm。
MIN_WALL_BY_DN: dict[int, float] = {25: 2.0, 65: 3.0, 100: 3.0}


def minimum_wall(dn: int | None) -> float | None:
    """DN → 最小壁厚下限 mm;`None` 表示不设限。"""
    if dn is None:
        return None
    for upper in sorted(MIN_WALL_BY_DN):
        if dn <= upper:
            return float(MIN_WALL_BY_DN[upper])
    return None

# --------------------------------------------------------------------------
# 4. 从 data/ 加载标准表(不放进公开仓库)
# --------------------------------------------------------------------------
#
# 为什么不在代码里写死:
#   * 这些数值来自国标表格,按项目约定"标准原文不入库,只把规则摘要写进 docs";
#   * 录入是外部完成的,数据在 data/preview/std/docx/*.csv(已被 .gitignore 覆盖),
#     这里只负责读进来,读不到就保持为空 —— 页面会显示"未录入",绝不拿 0 去算。
#
# 数据来源与核对(2026-11-07):
#   * 表 1 / 表 4:GB/T 17395-2024,由外部视觉录入(D≤630、S≤25);
#   * 表 B.1:GB/T 20801.1-2025,同上;原件温度首列是 40 ℃(没有 20 ℃ 列),
#     所以低于 40 ℃ 的取值按 allowable_stress() 的"取最低温度列"处理;
#   * 表 1 的 kg/m 已用 m = 0.02466·S·(D−S) 反算校验:2795 格里 2784 格误差 <0.5%,
#     11 格可疑且集中在 D≤18 与 D406 的 S24/25(本项目选型范围之外),不影响使用。

_CURATED_DIR = Path(__file__).resolve().parents[2] / "data" / "preview" / "std" / "docx"

#: 表 B.1 里每个本项目牌号**取哪一行**(同一牌号有多个标准/尺寸档时)
_STRESS_PICK = {
    "20#": ("GB/T 8163", ""),                      # 流体输送用无缝钢管
    "Q345A": ("GB/T 8163", ""),
    "Q345B": ("GB/T 6479", "*"),                   # 三个尺寸档取各温度下的最小值
    "S30408": ("GB/T 14976", "06Cr19Ni10(304)"),
    "S31603": ("GB/T 14976", "022Cr17Ni12Mo2(316L)"),
}

#: 录进来的表 B.1 每行原始数据(留着给"计算书"标注来源)
STRESS_ROWS: list[dict] = []


def _read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open("rb") as f:
        text = f.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def _parse_size(raw: str | None) -> float | None:
    """`10(10.2)` / `2.2(2.3)` → 名义值 10 / 2.2;解析不了返回 None。"""
    if raw is None or not str(raw).strip():
        return None
    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)", str(raw))
    return float(m.group(1)) if m else None


def _load_od_to_walls() -> tuple[dict[float, tuple[float, ...]], dict[float, tuple[float, ...]]]:
    """表 1(碳钢/普通钢管)与表 4(不锈钢管)→ {外径: 标准壁厚序列}。"""
    carbon: dict[float, list[float]] = {}
    for row in _read_csv(_CURATED_DIR / "t1-17395-table1.csv"):
        d = _parse_size(row.get("D_mm"))
        s = _parse_size(row.get("S_mm"))
        if d and s:
            carbon.setdefault(d, []).append(s)
    sst: dict[float, list[float]] = {}
    for row in _read_csv(_CURATED_DIR / "t4-17395-table4.csv"):
        d = _parse_size(row.get("D_mm"))
        s = _parse_size(row.get("S_mm"))
        if d and s:
            sst.setdefault(d, []).append(s)
    return ({d: tuple(sorted(set(v))) for d, v in carbon.items()},
            {d: tuple(sorted(set(v))) for d, v in sst.items()})


def _load_stress() -> dict[str, dict[float, float]]:
    """表 B.1 → {本项目牌号: {温度: 许用应力 MPa}}。"""
    global STRESS_ROWS
    rows = _read_csv(_CURATED_DIR / "t3-20801-tableB1.csv")
    STRESS_ROWS = rows
    temps = {}
    for key in rows[0].keys() if rows else []:
        m = re.fullmatch(r"许用应力_(\d+)", key or "")
        if m:
            temps[int(m.group(1))] = key
    temps = dict(sorted(temps.items()))

    out: dict[str, dict[float, float]] = {}
    for grade, (std, mark) in _STRESS_PICK.items():
        matched = [r for r in rows
                   if r.get("标准") == std
                   and (mark in ("", "*") or str(r.get("牌号", "")).startswith(mark))]
        if not matched:
            continue
        table: dict[float, float] = {}
        for t, col in temps.items():
            vals = []
            for r in matched:
                raw = (r.get(col) or "").strip()
                if raw:
                    try:
                        vals.append(float(raw))
                    except ValueError:
                        pass
            if not vals:
                continue
            # mark="*" 表示多尺寸档并存,取保守值(最小);其余直接取第一个
            table[float(t)] = min(vals)
        if table:
            out[grade] = table
    return out


OD_TO_WALLS: dict[float, tuple[float, ...]]
OD_TO_WALLS_SST: dict[float, tuple[float, ...]]
OD_TO_WALLS, OD_TO_WALLS_SST = _load_od_to_walls()
ALLOWABLE_STRESS: dict[str, dict[float, float]] = _load_stress()

#: 本期 DN 上限(超过就标黄提示"超出 DN600,需专项确认")
MAX_DN = 600


# --------------------------------------------------------------------------
# 5. 介质 → 管道等级(材质 / 腐蚀裕量):用户定义,工具不猜
# --------------------------------------------------------------------------
#
# 工程上"这个介质该用什么材质"由设计人员按**管道等级表**定,不是拿组分算出来的:
# 腐蚀取决于浓度、温度、含水量、氯离子 ppm、流速……同一物质在不同工况下结论可以相反
# (干氯气对碳钢几乎不腐蚀,痕量水就变成剧腐蚀)。所以工具只做两件事:
#   ① 按表**一致地套用**材质与腐蚀裕量;② 把**表里没定义过**的流股明确暴露出来。
# 它不判断某种介质是否腐蚀、也不给你推荐牌号 —— 那属于材料/腐蚀专业的活儿。
#
# 配置文件:`data/config/piping-classes.json`(`data/` 已 gitignore)
#   {
#     "rules": [
#       {"stream_prefix": ["CW", "CWR"], "grade": "20#", "corrosion_mm": 1.0, "note": "循环冷却水"},
#       {"medium": ["PL", "PGL"], "grade": "S30408", "corrosion_mm": 0.0, "note": "工艺液体"}
#     ],
#     "default": {"grade": "S30408", "corrosion_mm": 0.0}
#   }
# 没配这个文件也能跑:退回全局默认材质,不提示"未定义"。

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "data" / "config"

#: 规则列表(按顺序匹配,先命中先用)
PIPING_CLASS_RULES: list[dict] = []
#: 兜底等级(可为 None,表示"没匹配就退回全局默认材质")
PIPING_CLASS_DEFAULT: dict | None = None


def _load_piping_classes() -> tuple[list[dict], dict | None]:
    path = _CONFIG_DIR / "piping-classes.json"
    if not path.is_file():
        return [], None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], None      # 配置写坏了当没配,不要因此让整个选型挂掉
    rules = [r for r in (data.get("rules") or []) if isinstance(r, dict)]
    default = data.get("default") if isinstance(data.get("default"), dict) else None
    return rules, default


PIPING_CLASS_RULES, PIPING_CLASS_DEFAULT = _load_piping_classes()


def resolve_piping_class(stream: str, medium: str,
                         rules: list[dict] | None = None,
                         default: dict | None = None) -> dict:
    """按「流股名前缀 + 介质代号」查管道等级。

    返回 `{grade, corrosion_mm, note, source}`:
    `source="rule"` 命中某条规则、`"default"` 落兜底、`"none"` 都没命中
    (由调用方决定是标"待人工指定"还是退回全局默认材质)。
    """
    use_rules = PIPING_CLASS_RULES if rules is None else rules
    use_default = PIPING_CLASS_DEFAULT if default is None else default
    name = str(stream or "").upper()
    for rule in use_rules:
        prefixes = rule.get("stream_prefix") or []
        media = rule.get("medium") or []
        if prefixes and not any(name.startswith(str(p).upper()) for p in prefixes):
            continue
        if media and medium not in media:
            continue
        return {"grade": rule.get("grade"), "corrosion_mm": rule.get("corrosion_mm"),
                "note": rule.get("note", ""), "source": "rule"}
    if use_default:
        return {"grade": use_default.get("grade"),
                "corrosion_mm": use_default.get("corrosion_mm"),
                "note": use_default.get("note", ""), "source": "default"}
    return {"grade": None, "corrosion_mm": None, "note": "", "source": "none"}


def piping_class_status() -> dict:
    """管道等级表的配置状态 —— 给页面/接口显示"现在是配了表还是全按默认"。

    默认口径:**不填表就全部按页面上的默认材质**(默认 S30408),不会因为"没配"而少算或报错。
    """
    return {
        "configured": bool(PIPING_CLASS_RULES),
        "rule_count": len(PIPING_CLASS_RULES),
        "has_default": PIPING_CLASS_DEFAULT is not None,
        "default_grade": (PIPING_CLASS_DEFAULT or {}).get("grade"),
        "note": "" if PIPING_CLASS_RULES else "未配置管道等级表:全部流股按页面上的默认材质计算",
    }


PIPING_CLASS_PATH = _CONFIG_DIR / "piping-classes.json"

#: 允许填进等级表的牌号(与许用应力表一致)
PIPING_CLASS_GRADES = tuple(MATERIAL_CODE_BY_GRADE)

#: 介质代号可以填的取值(空列表 = 不限制介质)
PIPING_CLASS_MEDIA = ("PL", "PG", "PGL")


def reload_piping_classes() -> None:
    """重新读配置文件 —— 页面保存后调用,生效不用重启服务。"""
    global PIPING_CLASS_RULES, PIPING_CLASS_DEFAULT
    PIPING_CLASS_RULES, PIPING_CLASS_DEFAULT = _load_piping_classes()


def validate_piping_classes(payload) -> tuple[dict, list[str]]:
    """清洗用户提交的等级表,返回 (清洗后, 错误列表)。

    只做**结构与取值**校验:牌号必须在许用应力表里、腐蚀裕量是非负数、
    前缀/介质至少给一个 —— 至于"这个介质该不该用这个材质"不在这里判断。
    """
    errors: list[str] = []
    data = payload if isinstance(payload, dict) else {}
    raw_rules = data.get("rules") or []
    if not isinstance(raw_rules, list):
        return {"rules": [], "default": None}, ["rules 必须是数组"]

    def _list(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return [str(v).strip().upper() for v in value if str(v).strip()]

    rules: list[dict] = []
    for i, raw in enumerate(raw_rules, 1):
        if not isinstance(raw, dict):
            errors.append(f"第 {i} 条不是对象")
            continue
        prefixes = _list(raw.get("stream_prefix"))
        media = _list(raw.get("medium"))
        grade = str(raw.get("grade") or "").strip()
        if not prefixes and not media:
            errors.append(f"第 {i} 条:前缀和介质至少填一个")
            continue
        if grade not in PIPING_CLASS_GRADES:
            errors.append(f"第 {i} 条:牌号 {grade!r} 不在 {'/'.join(PIPING_CLASS_GRADES)} 里")
            continue
        bad_media = [m for m in media if m not in PIPING_CLASS_MEDIA]
        if bad_media:
            errors.append(f"第 {i} 条:介质代号 {bad_media} 不认识(可用 {'/'.join(PIPING_CLASS_MEDIA)})")
            continue
        c2 = raw.get("corrosion_mm")
        try:
            c2 = 0.0 if c2 in (None, "") else float(c2)
        except (TypeError, ValueError):
            errors.append(f"第 {i} 条:腐蚀裕量必须是数字")
            continue
        if c2 < 0 or c2 > 20:
            errors.append(f"第 {i} 条:腐蚀裕量要在 0–20 mm 之间")
            continue
        rule = {"grade": grade, "corrosion_mm": c2}
        if prefixes:
            rule["stream_prefix"] = prefixes
        if media:
            rule["medium"] = media
        note = str(raw.get("note") or "").strip()
        if note:
            rule["note"] = note[:80]
        rules.append(rule)

    default = None
    raw_default = data.get("default")
    if isinstance(raw_default, dict) and str(raw_default.get("grade") or "").strip():
        grade = str(raw_default.get("grade")).strip()
        if grade not in PIPING_CLASS_GRADES:
            errors.append(f"兜底牌号 {grade!r} 不在 {'/'.join(PIPING_CLASS_GRADES)} 里")
        else:
            try:
                c2 = float(raw_default.get("corrosion_mm") or 0.0)
            except (TypeError, ValueError):
                c2 = 0.0
            default = {"grade": grade, "corrosion_mm": max(0.0, min(c2, 20.0))}
    return {"rules": rules, "default": default}, errors


def save_piping_classes(payload) -> tuple[bool, list[str]]:
    """校验 → 写文件 → 重新加载。返回 (是否写入, 错误列表)。"""
    cleaned, errors = validate_piping_classes(payload)
    if errors:
        return False, errors
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not cleaned["rules"] and not cleaned["default"]:
        # 空表 = 清除配置,回到"全部按默认材质"
        try:
            PIPING_CLASS_PATH.unlink(missing_ok=True)
        except OSError as exc:
            return False, [f"删除配置失败:{exc}"]
        reload_piping_classes()
        return True, []
    payload_out = {"_说明": "由管道页的「等级表」按钮生成;删掉本文件等于回到默认材质",
                   **cleaned}
    try:
        PIPING_CLASS_PATH.write_text(
            json.dumps(payload_out, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        return False, [f"写入配置失败:{exc}"]
    reload_piping_classes()
    return True, []
