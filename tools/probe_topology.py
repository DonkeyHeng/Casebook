"""tools/probe_topology.py —— 0.2 第一步:拓扑 / 焓字段探测(**只读**)

要回答的三个问题(答案决定第 1 步怎么写)
-----------------------------------------
1. 流股 `Output` 下**有没有焓字段**?节点叫什么?单位是 `Kcal/kg` 还是 `Btu/lb`?
2. 设备的 `IN_STRMID` / `OUT_STRMID` **能不能读全**?读不全时连接关系还能从哪读?
   HeatX / RadFrac 的 `Input` 里有没有"热侧 / 冷侧"这类端口字段?
3. `\\Data\\Utilities` 的成员长什么样?设备有没有引用它们?

怎么用
------
    probe_topology.bat                  # 双击(推荐):必须在真实桌面会话里跑,COM 才看得见
    python -m tools.probe_topology
    python -m tools.probe_topology --model <关键字>    # 只探某个已打开的模型(路径子串)
    python -m tools.probe_topology --out data/x.txt    # 换个输出路径

产出:`data/probe_topology.txt` —— 一份自带结论的完整证据,直接发回来即可。

实测要点(2026-11-01,某参考全流程模型,98 流股 / 67 设备)
-----------------------------------------------------------------
* 焓**不是单个标量节点**:`\\Data\\Streams\\<流>\\Output\\HMX` 是目录,值在 `HMX\\MIXED`(J/kmol);
  另有 `HMX_MASS\\MIXED`(J/kg)和 `HMX_FLOW\\MIXED`(MW,现成的 m·h,可直接相加)。
  直接读 `HMX` 会拿到 `1` 这种标志位,所以本脚本会**把目录解析到叶子**再取单位。
* `ENTH_CYCLE` / `ENTH_TIME` / `L1_ENTH` 这类名字**不是**主流股焓(自定义 / 分相 / 统计量,单位常为空),
  报告里会标成"辅助量",不用于焓平衡。
* 非塔设备的 `Output\\IN_STRMID` **常常不存在**(67 台里只有 8 台塔有);
  通用来源是流股侧的 `Output\\SOURCE` / `DESTINATION`(值是块名),
  换热器另有分侧字段 `Input\\FCOLD`/`FHOT` 与 `Output\\COLDIN`/`COLDOUT`/`HOTIN`/`HOTOUT`。
* 塔的 `Output\\IN_STRMID`:元素 Name 是端口号(1/2/3),**Value 才是流股名**;
  `Input\\FEED_STAGE` 反过来(Name=流股名,Value=进料板号),两者可互校。
* `\\Data\\Utilities\\<U>\\Output` 有 `UTL_TCOST`($/hr)、`UTL_TRATE`(kg/hr)、`UTL_PPRICE`($/kg)、
  `UTL_ENTH_IN/OUT`(J/kg)、`UTL_DUTY`/`UTL_USAGE`/`BLK_ID`(数组,元素名 = 用到它的设备)。
* 热负荷单位跟着单位集走:该模型 `QCALC`/`HX_DUTY` 是 MW,泵是 kW —— 一律按 UnitString 换算。

前提与约束
----------
* 模型已在 Aspen Plus 里打开,**已经 Run 过**(没 Run 过也能跑,但焓 / 连接大概率读不到)。
* **只读**:不调用 Run2 / Reinit,不写任何值,不改模型。
* 沙箱里 ROT 可能为空(见 docs/aspen-field-map.md 第 1 节),所以建议在桌面双击跑。
* 任一环节失败都不中断:异常写进报告,继续探下一段。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
import traceback
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:  # pywin32 缺失 / 不在项目根目录时,不在导入期炸掉,留给 main 报错
    from app.aspen import connect, reader, units
except Exception as _exc:  # noqa: BLE001
    connect = reader = units = None
    _IMPORT_ERROR = _exc
else:
    _IMPORT_ERROR = None

# ---------------------------------------------------------------- 数据树常量
DATA_ROOT = r"\Data"
STREAM_ROOT = r"\Data\Streams"
BLOCK_ROOT = r"\Data\Blocks"
UTIL_ROOT = r"\Data\Utilities"

# 焓候选名:先按名字挑,再用"把 Output 子节点全列出来"兜底。
ENTHALPY_CANDIDATES = (
    "HMX_FLOW", "HMX", "HMX_MASS", "H",
    "H_OUT", "H_IN", "HMASS", "HMOLE", "MHX",
    "RES_ENTHALPY", "ENTHALPY", "ENTH", "ENTH_OUT", "ENTHALPY_MASS",
)

# 端口类字段:热侧 / 冷侧 / 壳程 / 管程 / 进料 ……
PORT_HINT = re.compile(r"hot|cold|side|shell|tube|port|feed|prod|inlet|outlet|strm|conn", re.I)
UTIL_HINT = re.compile(r"util|utl", re.I)

# 焓 → 项目固定单位 kJ/kg(或 kJ/kmol)。Aspen 的 cal 是 IT 卡 4.1868 J(字段地图 8.1)。
MASS_H_TO_KJKG = {
    "kj/kg": 1.0,
    "j/kg": 0.001,   # 有的模型 HMX_MASS 直接用 J/kg(实测出现过)
    "j/g": 1.0,
    "j/gm": 1.0,
    "cal/g": 4.1868,
    "cal/gm": 4.1868,
    "kcal/kg": 4.1868,
    "btu/lb": 2.3260,
    "btu/lbm": 2.3260,
    "cal/lb": 4.1868 / 0.45359237,
}
MOLAR_H_TO_KJKMOL = {
    "kj/kmol": 1.0,
    "j/kmol": 0.001,   # 实测模型 HMX 就是 J/kmol
    "kj/mol": 1000.0,
    "j/mol": 1.0,
    "kcal/kmol": 4.1868,
    "kcal/mol": 4186.8,
    "cal/mol": 4186.8,
    "btu/lbmol": 2.3260,
    "btu/mol": 2326.0,
}
# 单位 → kW 的系数(能量流字段,如 HMX_FLOW 的 MW)
ENERGY_FLOW_KW = {
    "mw": 1000.0,
    "kw": 1.0,
    "w": 0.001,
    "j/s": 0.001,
    "hp": 745.699872,
    "gcal/hr": 1163.0,
    "gcal/s": 4186800.0,
    "kcal/hr": 1.163,
    "kcal/s": 4186.8,
    "cal/sec": 0.0041868,
    "cal/s": 0.0041868,
    "btu/hr": 0.0002930711,
}

SAMPLE_STREAMS = 6   # 枚举多少条样流的 Output 全字段(枚举一次约 0.4 s)
SAMPLE_DUMPS = 3     # 报告里完整打印几条样流
FORMAT_WIDTH = 78
CHECK_TYPES = {"Heater", "Pump", "Mixer", "Splitter", "Valve", "Flash2", "Compr", "MCompr"}


# ---------------------------------------------------------------- 控制台 / 报告


class Report:
    """同时打印到控制台、并攒一份 txt。"""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._session = None  # 按类型转储要现场取值,见 _dump_rows

    def say(self, text: str = "") -> None:
        print(text)
        self.lines.append(text)

    def head(self, title: str, ch: str = "=", width: int = FORMAT_WIDTH) -> None:
        self.say("")
        self.say(ch * width)
        self.say(title)
        self.say(ch * width)

    def table(self, headers: list[str], rows: list[list], indent: int = 2) -> None:
        if not rows:
            return
        cells = [[fmt(c) for c in row] for row in rows]
        widths = [_dw(h) for h in headers]
        for row in cells:
            for i, cell in enumerate(row):
                if i < len(widths):
                    widths[i] = max(widths[i], _dw(cell))
        pad = " " * indent
        self.say(pad + "  ".join(_pad(h, widths[i]) for i, h in enumerate(headers)))
        self.say(pad + "  ".join("-" * widths[i] for i in range(len(headers))))
        for row in cells:
            self.say(pad + "  ".join(_pad(c, widths[i]) for i, c in enumerate(row)))

    def save(self, path: str) -> None:
        folder = os.path.dirname(os.path.abspath(path))
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(self.lines) + "\n")


def _dw(text) -> int:
    """显示宽度:中文按 2 格算,让 txt 在记事本里也能对齐。"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(text))


def _pad(text, width: int) -> str:
    return str(text) + " " * max(0, width - _dw(text))


def _wrap(text: str, width: int = 150, indent: str = "    ") -> str:
    """按显示宽度硬折行,保证 txt 里不出现超长行。"""
    lines, current = [], ""
    for token in str(text).split(", "):
        piece = (", " if current else "") + token
        if _dw(current + piece) > width:
            lines.append(current)
            current = token
        else:
            current += piece
    if current:
        lines.append(current)
    return ("\n" + indent).join(lines) if lines else ""


def progress(i: int, total: int, label: str) -> None:
    if not sys.stdout.isatty():
        return
    pct = (i + 1) / max(total, 1)
    bar = "#" * int(pct * 20)
    shown = ""
    for ch in str(label):
        if _dw(shown + ch) > 26:
            break
        shown += ch
    sys.stdout.write(f"\r  [{bar}{' ' * (20 - len(bar))}] {i + 1:>4}/{total:<4} {shown:<28}")
    sys.stdout.flush()


def progress_done() -> None:
    if sys.stdout.isatty():
        sys.stdout.write("\r" + " " * 76 + "\r")
        sys.stdout.flush()


# ---------------------------------------------------------------- 取值小工具


def unit_of(node) -> str:
    """UnitString 有时是属性、有时是方法,两种都兼容。"""
    try:
        unit = node.UnitString
        unit = unit() if callable(unit) else unit
        return str(unit or "").strip()
    except Exception:
        return ""


def as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_text(value, limit: int = 40) -> str:
    """把值压成一行短文本(用于判断"值是不是流股名"和显示)。"""
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def fmt(value, limit: int = 60) -> str:
    if value is None:
        return "None"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float):
            if value != value:  # nan
                return "nan"
            mag = abs(value)
            if mag and (mag >= 1e7 or mag < 1e-4):
                return f"{value:.4e}"
            return f"{value:.6g}"
        return str(value)
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def elem_name(elem) -> str:
    try:
        return str(elem.Name)
    except Exception:
        return "?"


def elem_value(elem):
    try:
        return elem.Value
    except Exception:
        return None


def describe(session, path: str, elems_cap: int = 8):
    """取节点快照:值 / 单位 / 子项数与子项名、子项值。节点不存在返回 None。"""
    node = session.find(path)
    if node is None:
        return None
    info = {"path": path, "value": None, "unit": "", "n": 0, "elems": [], "elem_values": []}
    try:
        info["value"] = node.Value
    except Exception:
        info["value"] = None
    info["unit"] = unit_of(node)
    try:
        elems = node.Elements
        count = int(elems.Count)
        info["n"] = count
        for i in range(min(count, elems_cap)):
            elem = elems.Item(i)
            info["elems"].append(elem_name(elem))
            info["elem_values"].append(elem_value(elem))
    except Exception:
        pass
    return info


def describe_line(name: str, info) -> str:
    """渲染成一行(name + 值/单位,或 <数组 n 项: …>)。"""
    if info is None:
        return f"{name}  <节点不存在>"
    if info["n"]:
        head = f"<数组 {info['n']} 项"
        if info["elems"]:
            more = " …" if info["n"] > len(info["elems"]) else ""
            head += ":" + ",".join(info["elems"]) + more
        return f"{name}  {head}>"
    value = info["value"]
    if value is None:
        return f"{name}  <空节点/读不到>"
    unit = info["unit"]
    text = f"{name}  {fmt(value)}"
    return f"{text}  [{unit}]" if unit else text


def dump_children(session, path: str, max_items: int = 60):
    """列出一个节点的子节点:返回 ([(名字, describe 结果)], 子节点总数)。"""
    node = session.find(path)
    if node is None:
        return None, 0
    names = reader.child_names(node)
    rows = []
    for name in names[:max_items]:
        rows.append((name, describe(session, path + "\\" + name)))
    return rows, len(names)


def read_pairs(session, path: str):
    """读数组:返回 [(元素名, 元素值)];节点不存在返回 None,存在但空返回 []。"""
    node = session.find(path)
    if node is None:
        return None
    out: list[tuple[str, object]] = []
    elems = None
    count = 0
    try:
        elems = node.Elements
        count = int(elems.Count)
    except Exception:
        count = 0
    for i in range(count):
        try:
            elem = elems.Item(i)
        except Exception:
            continue
        out.append((elem_name(elem), elem_value(elem)))
    if not out:  # 有的字段是标量,不是数组
        try:
            value = node.Value
            if value not in (None, ""):
                out.append(("", value))
        except Exception:
            pass
    return out


def _is_index(text: str) -> bool:
    return bool(re.fullmatch(r"\d+(\.\d+)?", text or ""))


def str_array(session, path: str, stream_lower: dict | None = None):
    """读流股名数组。两种布局都存在,按"哪一边是流股名"自动判定:

      * Name=端口号、Value=流股名(IN_STRMID / OUT_STRMID)
      * Name=流股名、Value=数字  (FEED_STAGE)

    有 `stream_lower`(全部流股名,小写→原名)时**优先用真实流股名比对** ——
    否则像 "0134" 这种纯数字流股名会被误当成端口号(实测踩过)。

    Returns:
        (None, 0)      —— 节点不存在
        ([流股名…], n) —— n = 条目存在但两边都认不出流股名的个数
    """
    pairs = read_pairs(session, path)
    if pairs is None:
        return None, 0
    out: list[str] = []
    skipped = 0
    for name, value in pairs:
        text = as_text(value)
        picked = None
        if stream_lower:
            if text and text.lower() in stream_lower:
                picked = stream_lower[text.lower()]
            elif name and name.lower() in stream_lower:
                picked = stream_lower[name.lower()]
        if picked is None:
            if text and not _is_index(text):
                picked = text            # Value 是流股名
            elif name and not _is_index(name):
                picked = name            # Name 是流股名
        if picked is None:
            skipped += 1
        elif picked not in out:
            out.append(picked)
    return out, skipped


def read_stage_map(session, path: str, stream_lower: dict | None = None):
    """读 FEED_STAGE:Name=流股名、Value=进料板号 → [(流股, 板号)];不存在返回 None。

    同样要防"纯数字流股名":有 `stream_lower` 时以真实流股名优先。
    """
    pairs = read_pairs(session, path)
    if pairs is None:
        return None
    rows = []
    for name, value in pairs:
        picked = None
        if stream_lower:
            if name and name.lower() in stream_lower:
                picked = stream_lower[name.lower()]
            else:
                text = as_text(value)
                if text and text.lower() in stream_lower:
                    picked = stream_lower[text.lower()]
        if picked is None and name and not _is_index(name):
            picked = name
        if picked:
            rows.append((picked, fmt(value)))
    return rows


def state_of(items) -> str:
    if items is None:
        return "missing"
    return "ok" if items else "empty"


def looks_like_enthalpy(name: str) -> bool:
    n = str(name).upper()
    if "ENTH" in n:
        return True
    return n in {"H", "HMX", "HMX_FLOW", "HMX_MASS", "HMXV", "HMXL", "HMASS", "HMOLE", "MHX"}


def enthalpy_kind(unit: str) -> str:
    key = units.normalize_unit(unit)
    if key in ENERGY_FLOW_KW:
        return "energy_flow"
    if key in MASS_H_TO_KJKG:
        return "mass"
    if key in MOLAR_H_TO_KJKMOL:
        return "molar"
    return "unknown"


def enthalpy_kind_text(kind: str) -> str:
    return {
        "energy_flow": "能量流(可直接相加)",
        "mass": "质量焓(需乘质量流量)",
        "molar": "摩尔焓(需乘摩尔流量)",
    }.get(kind, "未识别")


def enthalpy_score(name: str, unit: str) -> int:
    """给"看起来像焓"的字段打分:越大越可信。"""
    n = str(name).upper()
    score = {"HMX_FLOW": 100, "HMX": 95, "HMX_MASS": 90, "H": 85}.get(n, 0)
    if not score:
        if n.startswith("HMX") or n in ("HMASS", "HMOLE", "MHX"):
            score = 60
        elif "ENTH" in n:
            score = 50
            if re.search(r"CYCLE|TIME", n):
                score = 5  # Aspen 的循环 / 时间统计量,不是焓
        else:
            score = 30
    if not unit:
        score -= 25  # 单位空的字段多半是标志位
    kind = enthalpy_kind(unit)
    if kind == "energy_flow":
        score += 5
    elif kind in ("mass", "molar"):
        score += 2
    return score


def resolve_enthalpy_leaf(session, path: str, depth: int = 2):
    """把"看起来像焓"的节点解析到真正带值的叶子。

    实测:`Output\\HMX` 是目录(只有一个子节点 `MIXED`),值在 `HMX\\MIXED`;
    直接读 `HMX` 会拿到 `1` 这种标志位。返回 {path, value, unit} 或 None。
    """
    info = describe(session, path)
    if info is None:
        return None
    if info["n"] == 0:
        return {"path": path, "value": info["value"], "unit": info["unit"]}
    if depth <= 0:
        return None
    children = reader.child_names(session.find(path))
    for cand in ("MIXED", "TOTAL", "LIQ", "VAP", "MASS", "MOLE"):
        if cand in children:
            return resolve_enthalpy_leaf(session, path + "\\" + cand, depth - 1)
    if len(children) == 1:
        return resolve_enthalpy_leaf(session, path + "\\" + children[0], depth - 1)
    return None


def enthalpy_flow_kw(value, unit: str, mass_flow, mole_flow):
    """用焓 × 流量粗算"焓流"(kW),纯做单位换算校验。

    Aspen 的焓有任意参考态,绝对值**不能**跨设备相加;可加的是 ΔH(同一股流进出之差)。
    """
    if value is None:
        return None, "焓值为空"
    key = units.normalize_unit(unit)
    if key in ENERGY_FLOW_KW:
        return value * ENERGY_FLOW_KW[key], f"本身就是能量流({key})"
    if key in MASS_H_TO_KJKG:
        if mass_flow is None:
            return None, "质量流量为空"
        return value * MASS_H_TO_KJKG[key] * mass_flow / 3600.0, f"{key} → kJ/kg,再乘质量流量"
    if key in MOLAR_H_TO_KJKMOL:
        if mole_flow is None:
            return None, "摩尔流量为空"
        return value * MOLAR_H_TO_KJKMOL[key] * mole_flow / 3600.0, f"{key} → kJ/kmol,再乘摩尔流量"
    return None, f"单位 {unit or '空'} 未识别"


def refs_in(info, stream_lower: dict) -> list[str]:
    """这个节点的值 / 元素名 / 元素值里,哪些是已存在的流股名。"""
    if info is None:
        return []
    found: list[str] = []

    def add(value):
        text = as_text(value)
        if text:
            original = stream_lower.get(text.lower())
            if original and original not in found:
                found.append(original)

    add(info.get("value"))
    for name in info.get("elems") or []:
        add(name)
    for value in info.get("elem_values") or []:
        add(value)
    return found


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 探测:流股


def probe_streams(session) -> dict:
    """流股:每条一次的廉价检查 + 少量样流的全字段枚举 + 焓字段逐流股取值。

    为什么要采样:实测 `Output` 有 120 个子节点,枚举一次约 0.4 s(Item(i).Name 是
    2 次 COM 调用/项);98 条全枚举要 55 s。字段集在所有物料流上是同一套,
    取 6 条样流就够看清"有没有焓字段";确定字段后再逐流股只读那几个叶子。
    """
    data = {
        "names": [], "sampled": [], "samples": [], "union": {},
        "no_result": [], "massflow": {}, "moleflow": {},
        "source": {}, "dest": {}, "candidates": {},
        "enthalpy": {"fields": [], "primary": None}, "errors": [],
    }
    root = session.find(STREAM_ROOT)
    if root is None:
        data["errors"].append(f"找不到 {STREAM_ROOT}")
        return data
    data["names"] = reader.child_names(root)

    # ---- 1) 每条流股:有没有结果 / 流量 / 上下游(约 5 次 COM 调用)
    has_result = {}
    for i, name in enumerate(data["names"]):
        progress(i, len(data["names"]), name)
        opath = f"{STREAM_ROOT}\\{name}\\Output"
        try:
            has_result[name] = session.find(opath + "\\RES_TEMP") is not None
        except Exception as exc:
            has_result[name] = False
            data["errors"].append(f"{name}: {exc}")
        data["massflow"][name] = as_float(session.value(opath + "\\RES_MASSFLOW"))
        data["moleflow"][name] = as_float(session.value(opath + "\\RES_MOLEFLOW"))
        data["source"][name] = as_text(session.value(opath + "\\SOURCE"))
        data["dest"][name] = as_text(session.value(opath + "\\DESTINATION"))
    progress_done()
    data["no_result"] = [n for n in data["names"] if not has_result[n]]

    # ---- 2) 挑样流:等距 6 条 + 流量最大 3 条 + 没有结果的特异流股 3 条
    picks: list[str] = []
    step = max(1, len(data["names"]) // 6)
    picks.extend(data["names"][::step][:6])
    by_flow = sorted(
        (s for s, v in data["massflow"].items() if v is not None),
        key=lambda s: -(data["massflow"][s] or 0.0),
    )
    picks.extend(by_flow[:3])
    picks.extend(data["no_result"][:3])
    picks = list(dict.fromkeys(picks))[:SAMPLE_STREAMS]

    # ---- 3) 样流:枚举 Output 全部子节点,汇总字段联合分布
    for name in picks:
        rows, total = dump_children(session, f"{STREAM_ROOT}\\{name}\\Output", 200)
        if not rows:
            continue
        data["sampled"].append(name)
        data["samples"].append({"name": name, "rows": rows, "total": total})
        for child, _info in rows:
            data["union"][child] = data["union"].get(child, 0) + 1

    # ---- 4) 焓字段:先解析到叶子,再对所有流股逐条取值(评分最高的 3 个)
    base_sample = data["sampled"][0] if data["sampled"] else (data["names"][0] if data["names"] else "")
    resolved = []
    for name in sorted(data["union"], key=lambda n: -data["union"][n]):
        if not looks_like_enthalpy(name):
            continue
        leaf = resolve_enthalpy_leaf(session, f"{STREAM_ROOT}\\{base_sample}\\Output\\{name}")
        if leaf is None:
            continue
        resolved.append({
            "name": name,
            "leaf": leaf["path"],
            "unit": leaf["unit"],
            "probe": leaf["value"],
            "kind": enthalpy_kind(leaf["unit"]),
            "score": enthalpy_score(name, leaf["unit"]),
            "values": {},
        })
    resolved.sort(key=lambda item: (-item["score"], item["name"]))
    base_prefix = f"{STREAM_ROOT}\\{base_sample}\\Output\\"
    for field in resolved[:3]:
        # 叶子相对 Output 的后缀(如 "\\MIXED"),逐流股直接拼路径,省掉重复解析
        suffix = field["leaf"][len(base_prefix):] if field["leaf"].startswith(base_prefix) else field["name"]
        field["suffix"] = suffix
        for i, name in enumerate(data["names"]):
            progress(i, len(data["names"]), "H " + name)
            path = f"{STREAM_ROOT}\\{name}\\Output\\{suffix}"
            node = session.find(path)
            if node is None:
                continue
            try:
                value = node.Value
            except Exception:
                value = None
            if value is None:
                continue
            unit = unit_of(node)
            field["values"][name] = value
            if unit and unit != field["unit"]:
                field["unit"] = " | ".join(sorted({field["unit"], unit})) if field["unit"] else unit
        progress_done()
    data["enthalpy"]["fields"] = resolved
    primary = next((f for f in resolved if f["kind"] in ("energy_flow", "mass", "molar")), None)
    data["enthalpy"]["primary"] = primary["name"] if primary else None

    # ---- 5) 候选名兜底:联合字段里没有的名字,在 2 条样流上各试一次
    known = {n.upper() for n in data["union"]}
    unknown = [c for c in ENTHALPY_CANDIDATES if c.upper() not in known]
    for name in picks[:2]:
        for cand in unknown:
            info = describe(session, f"{STREAM_ROOT}\\{name}\\Output\\{cand}")
            if info is not None:
                leaf = resolve_enthalpy_leaf(session, info["path"])
                data["candidates"].setdefault(cand, []).append({
                    "stream": name, "leaf": leaf or {}, "info": info,
                })
    return data


def probe_locations(session) -> dict:
    """备选位置:\\Data 顶层 + Stream-Sum 的结构(有的模型没有 Stream-Sum)。"""
    data = {"data_children": [], "stream_sum": {}}
    data["data_children"] = reader.child_names(session.find(DATA_ROOT))
    for path in (
        r"\Data\Stream-Sum",
        r"\Data\Stream-Sum\Input",
        r"\Data\Stream-Sum\Output",
    ):
        node = session.find(path)
        data["stream_sum"][path] = None if node is None else reader.child_names(node)
    return data


# ---------------------------------------------------------------- 探测:设备


DISCOVERY_PER_TYPE = 2   # 每种类型枚举几台设备的 Input/Output 子节点(枚举大设备很贵:T0101 有 1266 个)
KNOWN_CONN_FIELDS = (
    "FCOLD", "FHOT", "COLDIN", "COLDOUT", "HOTIN", "HOTOUT",
    "FEED_STAGE", "PROD_STAGE",
    "UTILITY_ID", "UTILID", "UTIL", "COND_UTIL", "REB_UTIL", "COLD_UTIL", "HOT_UTIL",
)


def _child_count(session, path: str):
    """只取子节点个数(2 次 COM 调用),不枚举名字 —— 给设备表用。"""
    node = session.find(path)
    if node is None:
        return None
    try:
        return int(node.Elements.Count)
    except Exception:
        return 0


def probe_refs(session, path: str, stream_lower: dict, elems_cap: int = 3) -> list[str]:
    """便宜版"这个字段里有没有流股名":只读 Value,必要时再看前几个元素名/值。

    不用 describe()(它会取 UnitString + 全部子项),实测能省掉一半以上的 COM 调用。
    """
    node = session.find(path)
    if node is None:
        return []
    refs: list[str] = []

    def add(value):
        text = as_text(value)
        if text:
            original = stream_lower.get(text.lower())
            if original and original not in refs:
                refs.append(original)

    try:
        add(node.Value)
    except Exception:
        pass
    if not refs:
        try:
            elems = node.Elements
            for i in range(min(int(elems.Count), elems_cap)):
                elem = elems.Item(i)
                add(elem_name(elem))
                add(elem_value(elem))
        except Exception:
            pass
    return refs


def probe_blocks(session, stream_names: list[str]) -> dict:
    """设备连接:分三步走,避免"逐台枚举上千个子节点"的爆炸成本。

    1. 读全部设备的类型(每台 2 次调用);
    2. 每种类型挑 2 台枚举 Input/Output 子节点 → 该类型的字段清单,并**学习**
       "哪些字段里真的装着流股名"(按类型记一次,不用逐台试);
    3. 全部设备:廉价事实(IN/OUT_STRMID、FEED_STAGE、NSTAGE、子节点个数)
       + 用第 2 步学到的字段名取值。

    实测:逐台全枚举 67 台要 ~165 s;这样 ~15 s,信息量几乎一样。
    """
    data = {
        "names": [], "rows": [], "errors": [], "in_union": {}, "out_union": {},
        "dangling": [], "unreferenced": [], "port_hits": [], "connected": [],
        "util_hits": [], "type_samples": {}, "type_counts": {}, "feeds": {},
        "fields_by_type": {}, "discovery": {}, "learned": {},
    }
    root = session.find(BLOCK_ROOT)
    if root is None:
        data["errors"].append(f"找不到 {BLOCK_ROOT}")
        return data
    data["names"] = reader.child_names(root)
    # 名字比对一律大小写不敏感:Aspen 偶尔会把流股名改成大写,直接按原串比会误报悬空引用。
    stream_lower = {name.lower(): name for name in stream_names}
    referenced_lower: dict[str, str] = {}

    # ---- 1) 类型
    types: dict[str, str] = {}
    for block in data["names"]:
        try:
            types[block] = str(session.find(f"{BLOCK_ROOT}\\{block}").AttributeValue(6) or "")
        except Exception:
            types[block] = ""
        if types[block]:
            data["type_counts"][types[block]] = data["type_counts"].get(types[block], 0) + 1

    # ---- 2) 每种类型挑 2 台枚举字段 + 学习端口字段
    for block in data["names"]:
        btype = types[block] or "(未知)"
        picked = data["discovery"].setdefault(btype, [])
        if len(picked) < DISCOVERY_PER_TYPE:
            picked.append(block)
    for btype, picked_blocks in data["discovery"].items():
        sides = {"Input": set(), "Output": set()}
        for block in picked_blocks:
            bpath = f"{BLOCK_ROOT}\\{block}"
            for side in ("Input", "Output"):
                try:
                    names = reader.child_names(session.find(bpath + "\\" + side))
                except Exception as exc:
                    data["errors"].append(f"{block}\\{side}: {exc}")
                    continue
                sides[side].update(names)
                bucket = data["in_union"] if side == "Input" else data["out_union"]
                for name in names:
                    bucket[name] = bucket.get(name, 0) + 1
            if btype not in data["type_samples"]:
                data["type_samples"][btype] = {
                    "block": block, "inputs": [], "outputs": [], "bpath": bpath,
                }
        data["fields_by_type"][btype] = {side: sorted(names) for side, names in sides.items()}
        sample = data["type_samples"].get(btype)
        if sample:
            sample["inputs"] = data["fields_by_type"][btype]["Input"]
            sample["outputs"] = data["fields_by_type"][btype]["Output"]

        # 学习:该类型里哪些字段名"装着流股名"(只看第一台样例,避免重复付代价)
        candidates = set(KNOWN_CONN_FIELDS)
        for side in ("Input", "Output"):
            candidates |= {
                n for n in data["fields_by_type"][btype][side]
                if PORT_HINT.search(n) or UTIL_HINT.search(n)
            }
        learned = set()
        for name in sorted(candidates):
            for side in ("Input", "Output"):
                if probe_refs(session, sample["bpath"] + "\\" + side + "\\" + name, stream_lower):
                    learned.add(name)
        data["learned"][btype] = sorted(learned)

    # ---- 3) 全部设备:廉价事实 + 用学到的字段取值
    for i, block in enumerate(data["names"]):
        progress(i, len(data["names"]), block)
        btype = types[block] or ""
        key_type = btype or "(未知)"
        bpath = f"{BLOCK_ROOT}\\{block}"
        ipath, opath = bpath + "\\Input", bpath + "\\Output"
        try:
            ich_count = _child_count(session, ipath)
            och_count = _child_count(session, opath)
        except Exception:
            ich_count = och_count = None
        try:
            ins, in_skipped = str_array(session, opath + "\\IN_STRMID", stream_lower)
        except Exception as exc:
            ins, in_skipped = None, 0
            data["errors"].append(f"{block}\\IN_STRMID: {exc}")
        try:
            outs, out_skipped = str_array(session, opath + "\\OUT_STRMID", stream_lower)
        except Exception as exc:
            outs, out_skipped = None, 0
            data["errors"].append(f"{block}\\OUT_STRMID: {exc}")
        for stream in (ins or []) + (outs or []):
            referenced_lower.setdefault(stream.lower(), stream)

        try:
            feed_map = read_stage_map(session, ipath + "\\FEED_STAGE", stream_lower)
        except Exception:
            feed_map = None
        if feed_map:
            data["feeds"][block] = feed_map
        try:
            n_stages = _int_or_none(session.value(ipath + "\\NSTAGE"))
        except Exception:
            n_stages = None

        data["rows"].append({
            "name": block, "type": btype,
            "ins": ins or [], "outs": outs or [],
            "in_state": state_of(ins), "out_state": state_of(outs),
            "in_skipped": in_skipped, "out_skipped": out_skipped,
            "n_inputs": ich_count, "n_outputs": och_count,
            "n_stages": n_stages,
            "sampled": block in data["discovery"].get(key_type, []),
        })

        conn_fields = data["learned"].get(key_type, [])
        util_fields = sorted({n for n in (list(conn_fields) + list(KNOWN_CONN_FIELDS))
                              if UTIL_HINT.search(n)})
        for name in conn_fields:
            for side in ("Input", "Output"):
                refs = probe_refs(session, bpath + "\\" + side + "\\" + name, stream_lower)
                if refs:
                    hit = {"block": block, "type": btype, "side": side,
                           "name": name, "refs": refs}
                    data["port_hits"].append(hit)
                    data["connected"].append(hit)
        for name in util_fields:
            for side in ("Input", "Output"):
                path = bpath + "\\" + side + "\\" + name
                node = session.find(path)
                if node is None:
                    continue
                try:
                    value = node.Value
                except Exception:
                    continue
                if value in (None, ""):
                    continue
                data["util_hits"].append({"block": block, "type": btype, "side": side,
                                          "name": name, "value": as_text(value)})
    progress_done()

    data["dangling"] = sorted(
        referenced_lower[key] for key in referenced_lower.keys() - stream_lower.keys()
    )
    data["unreferenced"] = sorted(
        stream_lower[key] for key in stream_lower.keys() - referenced_lower.keys()
    )
    return data


# ---------------------------------------------------------------- 探测:公用工程


UTIL_KEY_FIELDS = (
    "UTIL_TYPE", "UTL_TCOST", "UTL_TRATE", "UTL_PPRICE",
    "UTL_IN_TEMP", "UTL_OUT_TEMP", "UTL_ENTH_IN", "UTL_ENTH_OUT",
    "UTL_B_TEMP", "UTL_EPOWER", "UTL_HCOOL",
)
UTIL_ARRAY_FIELDS = ("BLK_ID", "BLK_TYPE", "UTL_DUTY", "UTL_USAGE")


def probe_utilities(session) -> dict:
    """公用工程:成员、关键数值、以及"哪些设备在用它"。

    只读关键字段,不逐字段 describe(成员有 60+43 个子节点,全量读要十几秒)。
    """
    data = {"names": [], "members": {}, "in_union": {}, "out_union": {}, "errors": []}
    root = session.find(UTIL_ROOT)
    if root is None:
        data["errors"].append(f"找不到 {UTIL_ROOT}")
        return data
    data["names"] = reader.child_names(root)
    for util in data["names"]:
        upath = f"{UTIL_ROOT}\\{util}"
        entry = {"n_inputs": 0, "n_outputs": 0, "key": {}, "arrays": {}}
        try:
            in_names = reader.child_names(session.find(upath + "\\Input"))
            out_names = reader.child_names(session.find(upath + "\\Output"))
        except Exception as exc:
            data["errors"].append(f"{util}: {exc}")
            data["members"][util] = entry
            continue
        entry["n_inputs"], entry["n_outputs"] = len(in_names), len(out_names)
        for name in in_names:
            data["in_union"][name] = data["in_union"].get(name, 0) + 1
        for name in out_names:
            data["out_union"][name] = data["out_union"].get(name, 0) + 1
        for field in UTIL_KEY_FIELDS:
            leaf = resolve_enthalpy_leaf(session, upath + "\\Output\\" + field)
            if leaf is None or leaf["value"] is None:
                continue
            entry["key"][field] = (leaf["value"], leaf["unit"])
        for field in UTIL_ARRAY_FIELDS:
            pairs = read_pairs(session, upath + "\\Output\\" + field)
            if pairs:
                entry["arrays"][field] = pairs
        data["members"][util] = entry
    return data


# ---------------------------------------------------------------- 渲染:结论


def render_streams_summary(rep: Report, streams: dict) -> None:
    rep.say("")
    rep.say("【问题 1】流股 Output 下有没有焓字段?单位是什么?")
    names = streams["names"]
    if not names:
        rep.say("  结论:【读不到】\\Data\\Streams 下没有流股,跳过。")
        return
    fields = streams["enthalpy"]["fields"]
    usable = [f for f in fields if f["kind"] != "unknown" and f["values"]]
    if not usable:
        if not streams["sampled"]:
            rep.say("  结论:【没有(整块为空)】样流的 Output 全是空的 —— 这份模型多半没 Run 过。")
        else:
            rep.say("  结论:【样流里没找到可用的焓字段】Output 联合字段清单见下面明细。")
        if streams["no_result"]:
            shown = ", ".join(streams["no_result"][:10])
            rep.say(f"    另有 {len(streams['no_result'])} 条流股没有 RES_TEMP(能量流或没 Run):{shown}")
        return

    rep.say(f"  结论:【有】认出 {len(fields)} 个焓相关名字,其中 {len(usable)} 个可用:")
    for field in usable:
        unit = field["unit"] or "(空)"
        kind = field["kind"]
        factor = ""
        if kind == "energy_flow":
            factor = "已经是 m·h,单位 MW/kW,可跨设备求和"
        else:
            key = units.normalize_unit(unit)
            if kind == "mass" and key in MASS_H_TO_KJKG:
                factor = f"换 kJ/kg ×{MASS_H_TO_KJKG[key]:g}"
            elif kind == "molar" and key in MOLAR_H_TO_KJKMOL:
                factor = f"换 kJ/kmol ×{MOLAR_H_TO_KJKMOL[key]:g}"
        rep.say(
            f"    - {field['name']}(叶子 {field['leaf']})"
            f" · 单位 {unit} · {enthalpy_kind_text(kind)} · {len(field['values'])}/{len(names)} 条有值"
            + (f" · {factor}" if factor else "")
        )
    aux = [f for f in fields if f["kind"] == "unknown" or not f["values"]]
    if aux:
        rep.say("    这些名字像焓、但不是主流股焓(自定义 / 分相 / 统计量),别用来做平衡:")
        for field in aux[:8]:
            rep.say(f"      {field['name']}:单位 {field['unit'] or '(空)'},样例值 {fmt(field['probe'])}")

    primary = next((f for f in usable if f["kind"] == "energy_flow"), usable[0])
    rep.say(f"    样例(主字段 {primary['name']},只用于校验单位):")
    for name, value in list(primary["values"].items())[:6]:
        kw, why = enthalpy_flow_kw(
            value, primary["unit"], streams["massflow"].get(name), streams["moleflow"].get(name)
        )
        if kw is not None:
            kw = 0.0 if abs(kw) < 0.05 else kw
            tail = f"→ {kw:,.1f} kW"
        else:
            tail = f"({why})"
        rep.say(f"      {name}:{fmt(value)} {primary['unit']} {tail}")
    rep.say("    注:Aspen 焓带任意参考态,只能做同一股流进出之差(ΔH),不能跨设备直接相加。")


def render_blocks_summary(rep: Report, blocks: dict, streams: dict) -> None:
    rep.say("")
    rep.say("【问题 2】IN_STRMID / OUT_STRMID 能不能读全?读不全时靠什么建拓扑?")
    rows = blocks["rows"]
    if not rows:
        rep.say("  结论:【读不到】\\Data\\Blocks 下没有设备,跳过。")
        return

    def tally(key: str) -> tuple[int, int, int]:
        ok = sum(1 for r in rows if r[key] == "ok")
        missing = sum(1 for r in rows if r[key] == "missing")
        empty = sum(1 for r in rows if r[key] == "empty")
        return ok, missing, empty

    in_ok, in_miss, in_empty = tally("in_state")
    out_ok, out_miss, out_empty = tally("out_state")
    total = len(rows)
    rep.say(
        f"  结论:设备 {total} 台 —— Output\\IN_STRMID 可读 {in_ok} 台"
        f"(缺节点 {in_miss} / 空 {in_empty});OUT_STRMID 可读 {out_ok} 台"
        f"(缺节点 {out_miss} / 空 {out_empty})"
    )
    if in_ok < total:
        have = [r["name"] for r in rows if r["in_state"] == "ok"]
        kinds = Counter(r["type"] for r in rows if r["in_state"] == "ok")
        rep.say(
            "    → 有 IN_STRMID 的只有:"
            + ", ".join(have[:12]) + (" …" if len(have) > 12 else "")
            + ";按类型:" + ", ".join(f"{k}×{v}" for k, v in kinds.items())
        )
    skipped = sum(r.get("in_skipped", 0) + r.get("out_skipped", 0) for r in rows)
    if skipped:
        rep.say(f"    ⚠ 数组里有 {skipped} 个条目读不出来(已跳过,没用序号顶替)")

    # 流股侧 SOURCE / DESTINATION —— 通用拓扑来源
    src = streams.get("source") or {}
    dst = streams.get("dest") or {}
    has_src = [s for s, v in src.items() if v]
    has_dst = [s for s, v in dst.items() if v]
    rep.say(
        f"    ★ 通用来源:流股 Output\\SOURCE / DESTINATION —— {len(has_src)}/{len(src)} 条有 SOURCE,"
        f"{len(has_dst)}/{len(dst)} 条有 DESTINATION"
    )
    if has_src or has_dst:
        rep.say(
            f"      可反推 {len(has_dst)} 条边;无上游(进料){len(src) - len(has_src)} 条,"
            f"无下游(产品){len(dst) - len(has_dst)} 条"
        )
        sample = [(s, src.get(s) or "-", dst.get(s) or "-") for s in (has_dst or has_src)[:5]]
        rep.say("      样例:流股      上游 → 下游")
        for stream, up, down in sample:
            rep.say(f"        {stream:<10}{up} → {down}")

    if blocks["feeds"]:
        rep.say("    进料与进料板号(Input\\FEED_STAGE:Name=流股名、Value=板号;塔的 IN_STRMID 与它互校):")
        for block, feeds in list(blocks["feeds"].items())[:8]:
            text = ", ".join(f"{s}→第{st}板" for s, st in feeds)
            rep.say(f"      {block}:{text}")

    # 值等于流股名的字段 = 真正的端口字段
    connected = blocks["connected"]
    if connected:
        rep.say("    ★ 值或元素名等于真实流股名的字段(真正的端口字段,已滤掉 NO / REPORT 这类噪声):")
        by_type: dict[str, list[dict]] = {}
        for hit in connected:
            by_type.setdefault(hit["type"] or "(未知)", []).append(hit)
        for btype, hits in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
            fields_by_side: dict[str, list[str]] = {}
            for hit in hits:
                buckets = fields_by_side.setdefault(hit["side"], [])
                if hit["name"] not in buckets:
                    buckets.append(hit["name"])
            parts = [f"{side}:{', '.join(names[:8])}" for side, names in fields_by_side.items()]
            rep.say(f"      {btype}({len(hits)} 处)" + " | ".join(parts))
    else:
        rep.say("    ★ 值等于流股名的字段:没找到(只能靠流股侧 SOURCE/DESTINATION 建拓扑)")

    bad_blocks = [r for r in rows if r["in_state"] != "ok" or r["out_state"] != "ok"]
    if bad_blocks:
        rep.say(
            f"    ⚠ 没有 IN/OUT_STRMID 的设备 {len(bad_blocks)} 台:"
            + ", ".join(r["name"] for r in bad_blocks[:20])
            + (" …" if len(bad_blocks) > 20 else "")
        )


def render_utilities_summary(rep: Report, util: dict, blocks: dict) -> None:
    rep.say("")
    rep.say("【问题 3】\\Data\\Utilities 的成员长什么样?设备引用它们吗?")
    if not util["names"]:
        rep.say("  结论:【读不到】\\Data\\Utilities 下没有成员,跳过。")
        return
    rep.say(f"  成员 {len(util['names'])} 个:{', '.join(util['names'])}")
    for name in util["names"]:
        entry = util["members"].get(name) or {}
        keys = entry.get("key") or {}
        parts = []
        for field in ("UTL_TCOST", "UTL_TRATE", "UTL_PPRICE", "UTL_IN_TEMP", "UTL_OUT_TEMP",
                      "UTL_ENTH_IN", "UTL_ENTH_OUT"):
            if field in keys:
                value, unit = keys[field]
                parts.append(f"{field}={fmt(value)} {unit}".strip())
        rep.say(f"    {name}:{'  '.join(parts) if parts else '(关键数值读不到)'}")
        users: list[str] = []
        for field in ("UTL_DUTY", "UTL_USAGE", "BLK_ID"):
            pairs = (entry.get("arrays") or {}).get(field) or []
            names = [p[0] for p in pairs if p[0]]
            if names:
                users = names
                break
        if users:
            rep.say(f"      使用设备({len(users)}):{', '.join(users[:12])}"
                    + (" …" if len(users) > 12 else ""))
        else:
            rep.say("      使用设备:该成员的数组字段为空(可能没被引用)")
    users_total = sum(
        len((entry.get("arrays") or {}).get("UTL_DUTY") or [])
        for entry in util["members"].values()
    )
    rep.say(f"  设备引用:Utilities 侧 UTL_DUTY 数组合计 {users_total} 条引用;")
    hits = blocks.get("util_hits") or []
    if hits:
        names = sorted({hit["name"] for hit in hits})
        rep.say(f"    设备侧字段:{len(hits)} 处,字段名 {', '.join(names[:10])}")
        for hit in hits[:8]:
            rep.say(f"      {hit['block']:<12}{hit['side']}\\{hit['name']:<14}{hit.get('value', '')}")
    else:
        rep.say("    设备侧字段:没找到(以 Utilities 侧 UTL_DUTY 数组为准)")


# ---------------------------------------------------------------- 渲染:明细


def render_stream_details(rep: Report, streams: dict) -> None:
    rep.head("一、流股明细", ch="-")
    names = streams["names"]
    union = sorted(streams["union"].items(), key=lambda kv: (-kv[1], kv[0]))
    rep.say(
        f"  流股 {len(names)} 条;样流 {len(streams['sampled'])} 条(枚举 Output 全字段);"
        f"Output 联合字段 {len(union)} 个(数字=出现在多少条样流上):"
    )
    for start in range(0, len(union), 3):
        chunk = union[start:start + 3]
        rep.say("    " + "  ".join(f"{_pad(name, 24)}{count:>4}" for name, count in chunk))
    if not union:
        rep.say("    (样流 Output 全部为空)")
    if streams["no_result"]:
        shown = ", ".join(streams["no_result"][:20])
        rep.say(f"  没有 RES_TEMP 的流股 {len(streams['no_result'])} 条:{shown}"
                + (" …" if len(streams["no_result"]) > 20 else ""))

    for sample in streams["samples"][:SAMPLE_DUMPS]:
        rep.say("")
        rep.say(f"  ── {sample['name']} 的 Output 全字段(单位在 [] 里)")
        for name, info in sample["rows"]:
            rep.say("    " + describe_line(name, info))
        if sample["total"] > len(sample["rows"]):
            rep.say(f"    … 还有 {sample['total'] - len(sample['rows'])} 个子节点未显示")

    fields = streams["enthalpy"]["fields"]
    if fields:
        rep.say("")
        rep.say("  焓字段逐个解析(HMX 是目录,值在 HMX\\MIXED;解析到叶子才有单位和值):")
        for field in fields:
            rep.say(
                f"    {field['name']:<12}→ {field['leaf']}  [{field['unit'] or '空'}]"
                f"  样例={fmt(field['probe'])}  评分 {field['score']}  {enthalpy_kind_text(field['kind'])}"
            )
        for field in fields:
            if not field["values"]:
                continue
            rep.say("")
            rep.say(f"  `{field['name']}` 的逐流股覆盖({len(field['values'])}/{len(names)} 条):")
            rows = []
            for stream, value in field["values"].items():
                kw, _why = enthalpy_flow_kw(
                    value, field["unit"], streams["massflow"].get(stream),
                    streams["moleflow"].get(stream),
                )
                rows.append((stream, fmt(value), field["unit"], kw))
            ranked = sorted((r for r in rows if r[3] is not None), key=lambda r: -abs(r[3]))
            picks = list(dict.fromkeys([r[0] for r in rows[:8]] + [r[0] for r in ranked[:4]]))
            table_rows = [
                [r[0], r[1], r[2], "-" if r[3] is None else f"{r[3]:,.1f}"]
                for r in rows if r[0] in picks
            ]
            rep.table(["流股", "值", "单位", "换算后 kW"], table_rows, indent=4)
            if len(rows) > len(picks):
                rep.say(f"    … 其余 {len(rows) - len(picks)} 条略")

    if streams["candidates"]:
        rep.say("")
        rep.say("  额外试过的候选字段(Output 联合字段里没有的名字):")
        for cand, items in sorted(streams["candidates"].items()):
            for item in items[:1]:
                rep.say(f"    {cand} @ {item['stream']}  ->  " + describe_line(cand, item["info"]))
    elif streams["union"]:
        rep.say("")
        rep.say("  额外试过的候选字段:全部不存在(焓只能从上面列出的字段里找)")


def render_location_details(rep: Report, loc: dict) -> None:
    rep.say("")
    rep.say("  备选位置(\\Data 顶层 + Stream-Sum):")
    rep.say("    \\Data 子节点:" + (", ".join(loc["data_children"]) or "无"))
    for path, names in loc["stream_sum"].items():
        rep.say(f"    {path}:" + ("(不存在)" if names is None else (", ".join(names) or "(空)")))


def render_block_details(rep: Report, blocks: dict) -> None:
    rep.head("二、设备连接明细", ch="-")
    rep.say("  1) 按类型统计:")
    rep.table(
        ["类型", "台数", "IN 可读", "OUT 可读", "有进料板映射"],
        [
            [
                btype,
                count,
                sum(1 for r in blocks["rows"]
                    if (r["type"] or "(未知)") == btype and r["in_state"] == "ok"),
                sum(1 for r in blocks["rows"]
                    if (r["type"] or "(未知)") == btype and r["out_state"] == "ok"),
                sum(1 for r in blocks["rows"]
                    if (r["type"] or "(未知)") == btype and r["name"] in blocks["feeds"]),
            ]
            for btype, count in sorted(blocks["type_counts"].items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    )
    rep.say("")
    rep.say("  2) 逐台设备(入/出 = IN_STRMID / OUT_STRMID 读到的流股数;- = 节点缺失,空 = 有节点无内容):")
    rep.table(
        ["设备", "类型", "入", "出", "Input 字段", "Output 字段", "级数", "抽样"],
        [
            [
                r["name"], r["type"] or "(未知)",
                _count_cell(r["in_state"], len(r["ins"])),
                _count_cell(r["out_state"], len(r["outs"])),
                "-" if r.get("n_inputs") is None else r["n_inputs"],
                "-" if r.get("n_outputs") is None else r["n_outputs"],
                "-" if r.get("n_stages") is None else r["n_stages"],
                "是" if r.get("sampled") else "",
            ]
            for r in blocks["rows"]
        ],
    )
    if blocks["connected"]:
        rep.say("")
        rep.say("  3) 值或元素名等于流股名的字段(= 设备端口,可直接建边):")
        for hit in blocks["connected"][:60]:
            refs = ", ".join(hit["refs"][:6]) + (" …" if len(hit["refs"]) > 6 else "")
            rep.say(f"    {hit['block']:<12}{hit['type'] or '?':<10}{hit['side']}\\{hit['name']:<14}{refs}")
    if blocks["dangling"]:
        shown = ", ".join(blocks["dangling"][:12]) + (" …" if len(blocks["dangling"]) > 12 else "")
        rep.say("")
        rep.say(f"  ⚠ 悬空引用 {len(blocks['dangling'])} 条(被设备引用但 \\Data\\Streams 里没有):{shown}")


ENERGY_HINT = re.compile(r"^(Q|DUTY|HX_|BRAKE|ELEC|WORK|NET|NSTAGE|ENTH|HMX)", re.I)


def _field_hint(name: str) -> bool:
    """这个字段值不值得现场取值打印(端口 / 能量 / 公用工程)。"""
    if name.upper().startswith("BAL_"):  # BAL_* 是平衡 / CO2 记账,不是端口,别刷屏
        return False
    return bool(PORT_HINT.search(name) or UTIL_HINT.search(name) or ENERGY_HINT.match(name))


def _dump_rows(rep: Report, sample: dict, key: str, cap: int = 40):
    """按类型样例的字段名,只对"值得看的字段"现场取值(其余返回 None,不花 COM 调用)。"""
    names = sample.get(key) or []
    session = rep._session
    side = "Input" if key == "inputs" else "Output"
    rows = []
    used = 0
    for name in names:
        if _field_hint(name) and used < cap and session is not None:
            rows.append((name, describe(session, sample["bpath"] + "\\" + side + "\\" + name, 3)))
            used += 1
        else:
            rows.append((name, None))
    return rows, len(names)


def render_type_samples(rep: Report, blocks: dict) -> None:
    rep.say("")
    rep.say("  4) 每种类型抽样 2 台的字段清单(前 40 个)+ 端口/能量/公用工程相关字段的值:")
    for btype, sample in sorted(blocks["type_samples"].items()):
        rep.say(f"    ── {btype}(样例 {sample['block']})")
        for sides_key, side in (("inputs", "Input"), ("outputs", "Output")):
            names = sample.get(sides_key) or []
            rep.say(f"      {side}({len(names)} 个):"
                    + (_wrap(", ".join(names[:40]), 130, "        ") if names else "(空)"))
            rows, _ = _dump_rows(rep, sample, sides_key)
            for name, info in rows:
                if info is None:
                    continue
                rep.say("        " + describe_line(name, info))


def render_utility_details(rep: Report, util: dict) -> None:
    rep.head("三、公用工程明细", ch="-")
    if not util["names"]:
        rep.say("  \\Data\\Utilities 下没有成员。")
        return
    rep.say(f"  Input 字段(全成员去重,{len(util['in_union'])} 个):")
    rep.say("    " + _wrap(", ".join(sorted(util["in_union"])), 150, "    "))
    rep.say(f"  Output 字段(全成员去重,{len(util['out_union'])} 个):")
    rep.say("    " + _wrap(", ".join(sorted(util["out_union"])), 150, "    "))
    for name in util["names"]:
        entry = util["members"].get(name) or {}
        rep.say("")
        rep.say(f"  ── {name}(Input {entry.get('n_inputs', 0)} 个 / Output {entry.get('n_outputs', 0)} 个)")
        for field, (value, unit) in sorted((entry.get("key") or {}).items()):
            rep.say(f"      {field} = {fmt(value)}  [{unit}]")
        for field, pairs in sorted((entry.get("arrays") or {}).items()):
            names = [p[0] for p in pairs if p[0]]
            if names:
                rep.say(f"      {field} -> {', '.join(names[:10])}" + (" …" if len(names) > 10 else ""))


def _count_cell(state: str, n: int) -> str:
    if state == "missing":
        return "-"
    if state == "empty":
        return "空"
    return str(n)


# ---------------------------------------------------------------- 渲染:一份模型


def render_model(rep: Report, model_path: str, data: dict) -> None:
    rep.head(f"模型:{model_path}")
    streams, blocks, util = data["streams"], data["blocks"], data["utilities"]
    edges = sum(len(r["ins"]) + len(r["outs"]) for r in blocks["rows"])
    src_ok = sum(1 for v in (streams.get("source") or {}).values() if v)
    dst_ok = sum(1 for v in (streams.get("dest") or {}).values() if v)
    rep.say(
        f"  概况:流股 {len(streams['names'])} 条 / 设备 {len(blocks['names'])} 台 / "
        f"公用工程 {len(util['names'])} 个"
    )
    rep.say(
        f"    连接来源:设备 IN/OUT_STRMID 共 {edges} 条边;"
        f"流股 SOURCE {src_ok} 条、DESTINATION {dst_ok} 条"
    )
    errors = streams["errors"] + blocks["errors"] + util["errors"] + data.get("errors", [])
    if errors:
        rep.say(f"  ⚠ 读取过程中的异常 {len(errors)} 条(不影响其它段):")
        for err in errors[:15]:
            rep.say("    - " + str(err))

    render_streams_summary(rep, streams)
    render_blocks_summary(rep, blocks, streams)
    render_utilities_summary(rep, util, blocks)

    rep.head("四、结论之后:下一步怎么用这份数据", ch="-")
    rep.say("  1) 流股焓 → `streams` 表新列;按报告的 UnitString 换算,别假设单位集。")
    rep.say("  2) 连接关系 → 新表 `connections`(block, side, stream, seq):")
    rep.say("     优先用流股侧 SOURCE/DESTINATION 生成;设备 IN/OUT_STRMID 与换热器分侧字段做交叉校验。")
    rep.say("  3) 公用工程只做结构记录(成员 / 单价 / 用量 / 使用设备),不做成本判断(已拍板)。")
    rep.say("  4) 焓平衡用流股 HMX_FLOW(能量流)算 ΔH,与设备 duty 对照;RadFrac / 反应器不校验。")


def render_balance_check(rep: Report, blocks: dict, streams: dict) -> None:
    """用流股侧 HMX_FLOW(能量流)和设备热负荷现场对一次账,验证第 2 步的口径。"""
    rep.head("五、焓平衡可行性现场验证", ch="-")
    energy = next(
        (f for f in streams["enthalpy"]["fields"] if f["kind"] == "energy_flow" and f["values"]),
        None,
    )
    if energy is None:
        rep.say("  流股上没有「能量流」型焓字段,跳过;第 2 步改用焓×流量,口径需另定。")
        return
    values = energy["values"]
    src, dst = streams["source"], streams["dest"]
    ins: dict[str, list[str]] = {}
    outs: dict[str, list[str]] = {}
    for stream in streams["names"]:
        up, down = src.get(stream) or "", dst.get(stream) or ""
        if down:
            ins.setdefault(down, []).append(stream)
        if up:
            outs.setdefault(up, []).append(stream)

    rows = []
    skipped = 0
    for row in blocks["rows"]:
        if row["type"] not in CHECK_TYPES:
            continue
        block = row["name"]
        in_streams = ins.get(block, [])
        out_streams = outs.get(block, [])
        if not in_streams or not out_streams:
            skipped += 1
            continue
        delta = sum(values.get(s, 0.0) for s in out_streams) - sum(
            values.get(s, 0.0) for s in in_streams
        )
        duty, duty_note = _read_duty(rep._session, block, row["type"])
        if duty is not None and abs(duty * 1000.0 - 1.0) < 1e-6 and abs(delta) > 0.005:
            duty_note += "(值≈1 kW,疑似标志位)"
        if duty is None:
            rows.append([block, row["type"], f"{len(in_streams)}/{len(out_streams)}",
                         f"{delta:,.3f}", "-", "-", duty_note])
            continue
        diff = delta - duty
        pct = (diff / duty * 100.0) if duty else float("nan")
        rows.append([
            block, row["type"], f"{len(in_streams)}/{len(out_streams)}",
            f"{delta:,.3f}", f"{duty:,.3f}", f"{pct:+.2f}%",
            f"{duty_note} diff {diff:+,.3f} MW",
        ])
        if len(rows) >= 12:
            break
    rep.say(f"  ΔH = Σ出 − Σ入(流股 {energy['name']},MW);duty 取设备自己的热负荷/功率字段(已换算成 MW)")
    rep.table(["设备", "类型", "入/出", "ΔH MW", "duty MW", "相对差", "备注"], rows)
    if skipped:
        rep.say(f"  另有 {skipped} 台同类设备因流股侧边不全没对照(闸阀/混合器常见)。")
    rep.say("  说明:符号以 Aspen 为准(加热为正);Pump 的 ΔH 只含流体得到的那部分,和 BRAKE_POWER 天然不等。")


def _duty_fields(btype: str) -> tuple[str, ...]:
    """按设备类型挑"热负荷 / 功率"字段的优先级。

    压缩机 / 泵的 QCALC 有时只是标志位(实测 MCompr 的 QCALC=1),不能当负荷用;
    它们真正的功率在 BRAKE_POWER / ELEC_POWER。
    """
    t = (btype or "").lower()
    if t in ("compr", "mcompr", "pump", "turbine", "valve", "pipe"):
        return ("BRAKE_POWER", "ELEC_POWER", "QCALC", "QNET", "HX_DUTY")
    if t == "heatx":
        return ("HX_DUTY", "QCALC", "QNET")
    return ("QCALC", "QNET", "HX_DUTY", "BRAKE_POWER", "ELEC_POWER")


def _read_duty(session, block: str, btype: str = ""):
    """读设备热负荷 / 功率,统一成 MW。返回 (MW 或 None, 说明)。"""
    if session is None:
        return None, "无会话"
    base = BLOCK_ROOT + "\\" + block + "\\Output"
    for field in _duty_fields(btype):
        node = session.find(base + "\\" + field)
        if node is None:
            continue
        try:
            value = node.Value
        except Exception:
            continue
        if value is None:
            continue
        kw = units.convert("power", value, unit_of(node))
        if kw is None:
            return None, f"{field}={fmt(value)} 单位未识别"
        return kw / 1000.0, field
    return None, "没有热负荷字段"


# ---------------------------------------------------------------- 主流程


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="拓扑 / 焓字段探测(只读,附着到已打开的 Aspen 模型)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--model", default=None,
        help="只探这个模型(路径子串匹配,如 <关键字>);默认探 ROT 里所有已打开的模型",
    )
    parser.add_argument(
        "--out", default=None,
        help="报告输出路径,默认 <项目>/data/probe_topology.txt",
    )
    return parser.parse_args(argv)


def _defaults() -> dict:
    """各段探测的兜底结构:某一段失败时,渲染仍然跑得完并说明情况。"""
    return {
        "streams": {
            "names": [], "sampled": [], "samples": [], "union": {},
            "no_result": [], "massflow": {}, "moleflow": {},
            "source": {}, "dest": {}, "candidates": {},
            "enthalpy": {"fields": [], "primary": None}, "errors": [],
        },
        "locations": {"data_children": [], "stream_sum": {}},
        "blocks": {
            "names": [], "rows": [], "errors": [], "in_union": {}, "out_union": {},
            "dangling": [], "unreferenced": [], "port_hits": [], "connected": [],
            "util_hits": [], "type_samples": {}, "type_counts": {}, "feeds": {},
        },
        "utilities": {"names": [], "members": {}, "in_union": {}, "out_union": {}, "errors": []},
        "errors": [],
    }


def run_phase(label: str, fn):
    """跑一段探测;把「现在在干什么、花了多久」打到控制台(文件里不出现)。"""
    print(f"\n[阶段] {label} …", end="", flush=True)
    started = time.time()
    try:
        result = fn()
    except Exception as exc:
        print(f" 失败({exc!r})")
        traceback.print_exc()
        return None, exc
    print(f" 完成,{time.time() - started:.1f} s")
    return result, None


def attach_with_retry(model_path: str):
    """附着失败时补一句权限 / 沙箱提示,方便用户自查。"""
    try:
        return connect.attach(model_path)
    except Exception as exc:
        raise RuntimeError(
            f"附着失败:{exc}\n"
            "  自查:1) Aspen 是否开着并加载了该 .apw;"
            "2) 是否在真实桌面会话运行(沙箱内 COM 不可用);"
            "3) Aspen 与脚本权限是否一致(都不要用管理员)。"
        ) from exc


def main(argv=None) -> int:
    args = parse_args(argv)
    out_path = args.out or os.path.join(_ROOT, "data", "probe_topology.txt")
    rep = Report()
    rep.say("Casebook 拓扑 / 焓字段探测报告")
    rep.say(f"生成时间:{time.strftime('%Y-%m-%d %H:%M:%S')}")
    rep.say(f"脚本:{os.path.abspath(__file__)}")
    rep.say(f"Python:{sys.version.split()[0]}")
    rep.say(f"工作目录:{os.getcwd()}")
    print("\n开始探测:先读流股(约每条 5 次 COM 调用),再读设备与公用工程;每段有进度条。")

    try:
        models = connect.list_open_models()
    except Exception as exc:
        rep.say("")
        rep.say("【失败】读不到 Aspen 的 ROT(运行中对象表),因此无法附着任何模型。")
        rep.say(f"  原始错误:{exc}")
        rep.say("  常见原因:")
        rep.say("    1. 跑在 DSH / 沙箱进程里 —— 沙箱内 ROT 可能为空,必须在真实桌面会话跑(双击 .bat)")
        rep.say("    2. Aspen 没打开,或没有加载 .apw")
        rep.say("    3. Aspen 以管理员身份运行,而本脚本是普通权限")
        rep.save(out_path)
        print(f"\n[报告] {out_path}")
        return 1

    if args.model:
        needle = args.model.lower()
        models = [m for m in models if needle in m.replace("/", "\\").lower()]
    if not models:
        rep.say("")
        rep.say("【失败】ROT 里没有可探测的模型。")
        rep.say("  请先在 Aspen Plus 里打开要分析的 .apw,再重新运行。")
        rep.say("  若同时开着多个模型,可用 --model 指定(路径子串)。")
        rep.save(out_path)
        print(f"\n[报告] {out_path}")
        return 1

    rep.say(f"已打开的模型 {len(models)} 个:")
    for path in models:
        rep.say("  - " + str(path))

    failures = 0
    for index, model_path in enumerate(models, 1):
        rep.say("")
        rep.say(f">>> 正在探测 {index}/{len(models)}:{model_path}")
        data = _defaults()
        try:
            session = attach_with_retry(model_path)
        except Exception as exc:
            failures += 1
            rep.head(f"模型 {model_path} —— 附着失败", ch="!")
            rep.say("  " + str(exc))
            continue
        rep._session = session
        phases = (
            ("streams", "读流股(每条的温度/流量/上下游 + 样流全字段)",
             lambda: probe_streams(session)),
            ("locations", "扫 Data 顶层与 Stream-Sum",
             lambda: probe_locations(session)),
            ("blocks", "读设备连接(IN/OUT_STRMID + 端口字段)",
             lambda: probe_blocks(session, data["streams"]["names"])),
            ("utilities", "读公用工程 Utilities",
             lambda: probe_utilities(session)),
        )
        for key, label, fn in phases:
            result, err = run_phase(label, fn)
            if result is None:
                failures += 1
                data["errors"].append(f"{label} 失败:{err!r}")
            else:
                data[key] = result

        try:
            render_model(rep, model_path, data)
            render_stream_details(rep, data["streams"])
            render_location_details(rep, data["locations"])
            render_block_details(rep, data["blocks"])
            render_type_samples(rep, data["blocks"])
            render_utility_details(rep, data["utilities"])
            render_balance_check(rep, data["blocks"], data["streams"])
        except Exception as exc:
            failures += 1
            rep.say("")
            rep.say(f"【异常】渲染报告失败:{exc}")
            rep.say(traceback.format_exc().rstrip())

    rep.say("")
    rep.say("=" * FORMAT_WIDTH)
    rep.say("完成。把这份 txt 原样发回即可。")
    rep.say("=" * FORMAT_WIDTH)
    rep.save(out_path)
    print(f"\n[报告] {out_path}")
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    if _IMPORT_ERROR is not None:
        print("导入失败,无法探测:", _IMPORT_ERROR)
        print("请确认:1) 在项目根目录运行;2) 已装 pywin32(requirements.txt)。")
        sys.exit(3)
    sys.exit(main())
