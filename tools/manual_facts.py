"""从源码抽取计算说明书的"事实基线"。

说明书的每条行为描述都应能回指到这里的某一条事实：节点路径、单位换算
系数、真实存在的符号名，以及由默认参数推出的边界值。只读源码与常量，
不启动 Aspen、不连 COM。

为什么单独一层：`check_manual_refs` 只能证明"编号存在"，证明不了"这句
话描述的是代码真实行为"。把事实先抽成集合，检查器才能做"逐条比对"，
而不是再让模型读一遍文档猜对错。
"""

from __future__ import annotations

import ast
import math
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
ASPEN = APP / "aspen"
READER = ASPEN / "reader.py"

PLACEHOLDER = "{}"

# 路径里必然出现、但名字不是全大写常量的节点。其余节点名从源码字面量里抽。
_BASE_NODES = {
    "data",
    "streams",
    "blocks",
    "output",
    "input",
    "strm_upp",
    "mixed",
    "liquid",
    "liq",
    "vapor",
    "total",
    "res_pres",
    "res_massflow",
    "res_volflow",
    "res_vfrac",
    "res_temp",
    "source",
    "destination",
}


def _strip_placeholders(text: str) -> str:
    """把 `{name}`、`<流股>` 这类占位统一成 `{}`。"""
    text = re.sub(r"\{[^{}]*\}", PLACEHOLDER, text)
    return re.sub(r"<[^<>]*>", PLACEHOLDER, text)


def normalize_path(text: str) -> str:
    """统一大小写、占位符与多余反斜杠，供比对用。"""
    path = text.strip().strip("`").strip()
    path = path.replace("/", "\\")
    path = _strip_placeholders(path)
    path = re.sub(r"\\{2,}", r"\\", path)
    path = path.rstrip("\\")
    return path.lower()


@lru_cache(maxsize=1)
def _module_constants() -> dict[str, object]:
    """读 reader.py 的模块级常量（PV/VISC 候选路径就在那里）。"""
    tree = ast.parse(READER.read_text(encoding="utf-8"))
    found: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            found[target.id] = ast.literal_eval(node.value)
        except (ValueError, TypeError, SyntaxError):
            continue
    return found


@lru_cache(maxsize=1)
def stream_rel_paths() -> tuple[str, ...]:
    """流股侧物性候选（p_v / 粘度），相对 `\\Output` 的写法。"""
    consts = _module_constants()
    rels: list[str] = []
    for key in ("PV_STREAM_PATHS", "VISC_STREAM_PATHS"):
        value = consts.get(key)
        if isinstance(value, (tuple, list)):
            rels.extend(item for item in value if isinstance(item, str))
    return tuple(rels)


@lru_cache(maxsize=1)
def node_names() -> frozenset[str]:
    """合法的节点段名：基础节点 + 候选路径里的段 + 源码里的全大写字面量。"""
    names = set(_BASE_NODES)
    for rel in stream_rel_paths():
        names.update(part.lower() for part in rel.split("\\") if part)
    text = READER.read_text(encoding="utf-8")
    names.update(match.group(1).lower() for match in re.finditer(r'"([A-Z][A-Z0-9_]{1,})"', text))
    return frozenset(names)


_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"


@lru_cache(maxsize=1)
def symbols() -> frozenset[str]:
    """app/ 下真实出现过的标识符：函数名、类名、字段名、常量名都算。

    故意取"出现过即可"而不是"有定义"，因为说明书里的字段名大量来自字典
    键、SQL 列和前端属性；判据是"仓库里有没有这个东西"，不是"是不是 def"。
    """
    found: set[str] = set()
    for path in APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        token: list[str] = []
        for char in text:
            if char in _CHARS:
                token.append(char)
            elif token:
                found.add("".join(token))
                token = []
        if token:
            found.add("".join(token))
    return frozenset(found)


@lru_cache(maxsize=1)
def unit_factors() -> dict[str, set[float]]:
    """units 表与粘度换算的"1 单位 → 目标值"系数，键已归一。

    同一个键可能出现在不同量纲表里（`hp` 在 power 是 0.7457、在 energy_flow
    是 745.7），因此按集合收，命中任一即算对——检查器不该替产品选量纲。
    """
    from app.aspen import units

    factors: dict[str, set[float]] = {}
    for table in units._TABLES.values():  # noqa: SLF001 - 说明书检查需要整张表
        for unit, spec in table.items():
            factors.setdefault(unit, set()).add(float(spec[0]))
    # 粘度不走 _TABLES，系数写死在 reader._to_cp；逐条实测一遍拿真值。
    from app.aspen import reader

    for unit in ("cP", "mPa·s", "Pa·s", "kg/m·s", "poise", "lb/(ft·hr)"):
        value = reader._to_cp(1.0, unit)  # noqa: SLF001
        if value:
            factors.setdefault(units.normalize_unit(unit), set()).add(float(value))
    return factors


def unit_alias(text: str) -> list[str]:
    """把 "psi/psia"、"kgf/cm²"、"kg/(m·s)" 拆成可查表的键。"""
    from app.aspen import units

    cleaned = text.replace("(", "").replace(")", "")
    keys = [units.normalize_unit(cleaned)]
    if "/" in cleaned and cleaned not in units._TABLES.get("pressure", {}):  # noqa: SLF001
        keys.extend(units.normalize_unit(part) for part in cleaned.split("/") if part)
    return [key for key in keys if key]


@lru_cache(maxsize=1)
def r1e_boundaries() -> tuple[float, float]:
    """由默认参数反解 R1E 的两个操作绝压边界（P_d = 6.4 / 10.0 MPa）。

    默认口径 `P_d = max(0, k·p_g, p_g + add, floor)`，其中 k·p_g 在两个
    边界上都是控制项，所以 p_g = min(P_d/k, P_d-add)；再换回绝压。
    """
    from app.design.pipe.constants import ATM_MPA
    from app.design.pump_line_context import default_pipe_options

    opts = default_pipe_options()
    factor = float(opts["pressure_factor"])
    add = float(opts["pressure_add_mpa"])

    values = []
    for design in (6.4, 10.0):
        gauge = min(design / factor, design - add)
        values.append(gauge + ATM_MPA)
    return values[0], values[1]


def pressure_band() -> tuple[float, float]:
    """R1E 强制压力带（操作绝压，MPa）。"""
    from app.design.pipe.data import HP_SS_PRESSURE_BAND_MPA

    return float(HP_SS_PRESSURE_BAND_MPA[0]), float(HP_SS_PRESSURE_BAND_MPA[1])


def same_factor(declared: float, actual: float) -> bool:
    """系数比对：允许文档只写到有效位数。"""
    return math.isclose(declared, actual, rel_tol=1e-4, abs_tol=1e-9)
