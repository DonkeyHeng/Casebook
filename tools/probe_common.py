"""探针脚本共用的 COM 读取件。

为什么单独一个模块
------------------
`probe_aspen.py`(COM 诊断)、`probe_pump.py`(泵字段)、`probe_pv.py`(蒸气压)、
`probe_blocks.py`(塔负荷)与 `tools/dump_fixture.py`(夹具采集)都要同样的几件东西:
附着已打开的 Aspen、在结果树里找节点、读子节点与值单位。

原先这些散在两个文件里,而且 `probe_pump.py` 的 children/name_of/value_of/unit_of
与 `probe_aspen.py` 的 child_nodes/node_name/node_value/node_unit 是**同一份逻辑的
两份拷贝**。收在一处之后只留一份实现,旧名字保留成别名,调用点不用改。

只读:这里没有任何写模型的动作。
"""

from __future__ import annotations

import json
import os
import time


class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.data: dict = {"sections": [], "generated": time.strftime("%Y-%m-%d %H:%M:%S")}

    def head(self, title: str) -> None:
        self.lines.append("")
        self.lines.append("=" * 78)
        self.lines.append(title)
        self.lines.append("=" * 78)
        self.data["sections"].append({"title": title, "items": []})

    def say(self, msg: str = "") -> None:
        print(msg)
        self.lines.append(msg)

    def item(self, key: str, value) -> None:
        text = f"  {key}: {value}"
        print(text)
        self.lines.append(text)
        if self.data["sections"]:
            self.data["sections"][-1]["items"].append({"key": key, "value": _jsonable(value)})

    def table(self, headers: list[str], rows: list[list]) -> None:
        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(str(cell)))
        line = "  " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
        self.say(line)
        self.say("  " + "  ".join("-" * w for w in widths))
        for row in rows:
            self.say("  " + "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))
        if self.data["sections"]:
            self.data["sections"][-1]["items"].append(
                {"key": "table", "value": {"headers": headers, "rows": rows}}
            )

    def save(self, out_dir: str) -> None:
        os.makedirs(out_dir, exist_ok=True)
        txt = os.path.join(out_dir, "probe_report.txt")
        js = os.path.join(out_dir, "probe_report.json")
        with open(txt, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines) + "\n")
        with open(js, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n[报告] {txt}")
        print(f"[报告] {js}")


def _jsonable(v):
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def rot_apw_monikers() -> list[str]:
    """ROT 里所有 .apw/.bkp 文件 moniker。

    V15 手工启动的 Aspen 只注册这个,不注册 Apwn.Document。
    """
    import pythoncom

    ctx = pythoncom.CreateBindCtx(0)
    out: list[str] = []
    for moniker in pythoncom.GetRunningObjectTable():
        try:
            name = moniker.GetDisplayName(ctx, None)
        except Exception:
            continue
        if name.lower().endswith((".apw", ".bkp", ".apwz", ".bkpz")):
            out.append(name)
    return out


def attach(rep: Report, progids: list[str]):
    """附加策略:文件 moniker 优先,ProgID 兜底。"""
    rep.head("2. 附加到已在运行的 Aspen 实例")
    import win32com.client

    try:
        monikers = rot_apw_monikers()
    except Exception as e:
        rep.item("ROT 读取失败", f"{type(e).__name__}: {e}")
        monikers = []

    rep.item("ROT 里的模型文件", monikers if monikers else "(空)")

    for name in monikers:
        try:
            doc = win32com.client.GetObject(name)
            tree = doc.Tree
        except Exception as e:
            rep.say(f"  [x] {name}: {type(e).__name__}: {e}")
            continue
        rep.say(f"  [OK] 已附着: {name}")
        return name, doc, tree

    rep.say("  文件 moniker 不可用,改试 ProgID:")
    for progid in progids:
        try:
            doc = win32com.client.GetActiveObject(progid)
            tree = doc.Tree
        except Exception as e:
            rep.say(f"    [x] {progid}: {type(e).__name__}")
            continue
        rep.say(f"    [OK] {progid}")
        return progid, doc, tree

    rep.say("  [结论] 没有可附着的实例。检查:")
    rep.say("         1) Aspen 是否开着(窗口标题应为 `xxx.apw - Aspen Plus V15`)")
    rep.say("         2) 脚本是否在真实桌面会话跑(DSH 沙箱里 ROT 是空的)")
    rep.say("         3) Aspen 是否以管理员身份启动(权限不同则无法附加)")
    return None, None, None


def find(tree, path: str):
    try:
        return tree.FindNode(path)
    except Exception:
        return None


def node_name(node) -> str:
    try:
        return str(node.Name)
    except Exception:
        return "?"


def node_value(node):
    if node is None:
        return None
    try:
        return node.Value
    except Exception:
        return None


def node_unit(node) -> str:
    """UnitString 有时是属性、有时是方法,统一处理。"""
    if node is None:
        return ""
    try:
        u = node.UnitString
        return str(u() if callable(u) else u)
    except Exception:
        return ""


def child_nodes(node) -> list:
    if node is None:
        return []
    out = []
    try:
        els = node.Elements
        for i in range(els.Count):
            try:
                out.append(els.Item(i))
            except Exception:
                pass
    except Exception:
        pass
    return out


def child_names(node) -> list[str]:
    return [node_name(c) for c in child_nodes(node)]


def read(tree, path: str):
    """返回 (值, 单位);读不到返回 (None, '')。"""
    nd = find(tree, path)
    return node_value(nd), node_unit(nd)


# ---- 旧名字:probe_pump.py 那一套的别名(调用点照旧能用) ----

children = child_nodes
name_of = node_name
value_of = node_value
unit_of = node_unit
