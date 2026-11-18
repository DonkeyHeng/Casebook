"""tools/fake_session.py -- 用脱敏 JSON 夹具回放 Aspen COM 节点树。

为什么需要
----------
`app/aspen/reader.py` 里的路径都是按实测写死的。版本换结构(V11 的 `PBUB` 没有
`TOTAL` 层)、或某个字段从目录变成叶子,读出来只是**静默变空**,不会报错 ——
泵页 p_v 整列空掉、粘度全空,都是这样发生的。单条路径的桩测不出跨字段的连锁影响,
只有"整棵树回放"能守住。

夹具格式(schema 1)
------------------
`tools/fixtures/<case>/tree.json`:

    {
      "case": "v15-typical",
      "meta": {"schema": 1, "source": "collector", "aspen_version": "V15", ...},
      "nodes": {
        "\\\\Data\\\\Streams\\\\S01": {"kids": ["Output"], "attrs": {"6": "MATERIAL"}},
        "\\\\Data\\\\Streams\\\\S01\\\\Output\\\\HMX": {"kids": ["MIXED"], "v": 1, "u": "J/kmol"},
        "\\\\Data\\\\Streams\\\\S01\\\\Output\\\\RES_TEMP": {"v": 85.0, "u": "C"}
      }
    }

约定
----
* 路径一律以 `\\` 开头、不以 `\\` 结尾,与 reader 里的拼法一致。
* `kids` 写了就以它为准(顺序 = Elements 顺序);**没写**就从 nodes 的键前缀推导,
  按自然序排(`"10"` 在 `"2"` 之后,塔板号不会错位)。
* `kids: []` 表示"确认是叶子"。目录想被当成叶子必须显式写,否则推导会给出子节点。
* `v` 缺省 None,`u` 缺省 "",`attrs` 只放读得到的 OptionPack 属性(6 = 可读类型)。
* `err` 表示"取 Value 时抛异常",用来模拟 COM 抽风 / 节点被锁。

变异(方案 C)
-------------
`drop_subtree` / `as_leaf` / `patch` 在同一份夹具上改结构,不用另存一份 JSON:
删掉 `PBUB\\MIXED\\TOTAL` 就是 V11 布局,清掉子节点就是"目录变叶子"。
"""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures"

_MISSING = object()


# ---------------------------------------------------------------- 夹具读写


def natural_key(name: str):
    """`"10"` 排在 `"2"` 之后的排序键(塔板号 / 数组下标)。"""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", str(name))]


def norm_path(path: str) -> str:
    """统一成 `\\` 开头、不以 `\\` 结尾的写法(与 reader 的拼接结果一致)。"""
    p = str(path or "").strip().replace("/", "\\")
    if not p.startswith("\\"):
        p = "\\" + p
    return p.rstrip("\\") or "\\"


def load(name: str) -> dict[str, Any]:
    """读夹具:`load("v15-typical")` → 带 case / meta / nodes 的 dict。"""
    path = FIXTURES / name / "tree.json"
    if not path.exists():
        raise FileNotFoundError(f"找不到夹具:{path}")
    with path.open(encoding="utf-8") as fh:
        tree = json.load(fh)
    nodes = tree.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        raise ValueError(f"夹具没有 nodes:{path}")
    for key in nodes:
        if key != norm_path(key):
            raise ValueError(f"夹具路径写法不合规(要以 \\ 开头、不以 \\ 结尾):{key!r}")
    return tree


def children_of(nodes: dict, path: str) -> list[str]:
    """节点下的子节点名:显式 `kids` 优先,否则按前缀推导(自然序)。"""
    path = norm_path(path)
    entry = nodes.get(path) or {}
    if "kids" in entry:
        return [str(k) for k in entry["kids"]]
    prefix = path if path.endswith("\\") else path + "\\"
    seen: list[str] = []
    for key in nodes:
        if key.startswith(prefix) and key != path:
            head = key[len(prefix) :].split("\\")[0]
            if head and head not in seen:
                seen.append(head)
    return sorted(seen, key=natural_key)


# ---------------------------------------------------------------- 回放对象


def join_path(parent: str, name: str) -> str:
    """拼子路径;根节点 `\\` 不产生 `\\\\name` 这种双斜杠。"""
    return f"{parent}\\{name}" if parent != "\\" else f"\\{name}"


class FakeElements:
    """COM 的 `Elements` 集合:只实现 reader 会用到的 `Count` 与 `Item`。"""

    def __init__(self, session: FakeSession, path: str):
        self._session = session
        self._path = path
        self._names = children_of(session.nodes, path)
        self.Count = len(self._names)

    def Item(self, index: int) -> FakeNode:
        return self._session.node(join_path(self._path, self._names[index]))


class FakeNode:
    """一个回放节点:`Name` / `Value` / `UnitString` / `Elements` / 属性。"""

    def __init__(self, session: FakeSession, path: str):
        self._session = session
        self._path = path
        self.Name = path.rsplit("\\", 1)[-1] or "\\"
        self.Elements = FakeElements(session, path)

    @property
    def Value(self):
        self._session.touched.add(self._path)
        entry = self._session.entry(self._path)
        if "err" in entry:
            raise RuntimeError(str(entry["err"]))
        return entry.get("v")

    @property
    def UnitString(self) -> str:
        self._session.touched.add(self._path)
        return str(self._session.entry(self._path).get("u") or "")

    def HasAttribute(self, index: int) -> bool:
        self._session.touched.add(self._path)
        attrs = self._session.entry(self._path).get("attrs") or {}
        return str(index) in attrs

    def AttributeValue(self, index: int):
        self._session.touched.add(self._path)
        attrs = self._session.entry(self._path).get("attrs") or {}
        if str(index) not in attrs:
            # 真 COM 是抛异常,reader 靠 try/except 得到 None,别用 None 冒充属性值
            raise AttributeError(f"{self._path} 没有属性 {index}")
        return attrs[str(index)]

    def __repr__(self) -> str:
        return f"<FakeNode {self._path}>"


class _Doc:
    def __init__(self, name: str):
        self.Name = name


class FakeSession:
    """假的 COM session:接口与 `app.aspen.connect.AspenSession` 对齐(只读部分)。"""

    def __init__(self, tree: dict[str, Any], *, model_path: str | None = None):
        meta = tree.get("meta") or {}
        self.tree = tree
        self.case = str(tree.get("case") or "")
        self.meta = meta
        self.nodes: dict[str, Any] = tree["nodes"]
        self.model_path = (
            model_path or meta.get("model_path") or f"model/{self.case or 'fixture'}.apw"
        )
        self.process_id = 0
        self.doc = _Doc(str(meta.get("server") or "Aspen Plus (fixture)"))
        self.reads: list[str] = []  # 所有 find 过的路径(含未命中)
        self.hits: set[str] = set()  # find 命中的路径
        self.touched: set[str] = set()  # 真正取过值 / 单位 / 属性 / 元素的路径

    # -- 内部
    def entry(self, path: str) -> dict:
        return self.nodes.get(norm_path(path)) or {}

    def node(self, path: str) -> FakeNode:
        return FakeNode(self, norm_path(path))

    # -- 与真实 session 同名
    def find(self, path: str):
        p = norm_path(path)
        self.reads.append(p)
        if p in self.nodes or (p != "\\" and children_of(self.nodes, p)):
            self.hits.add(p)
            return FakeNode(self, p)
        return None

    # -- 自检用
    def unused_leaves(self) -> list[str]:
        """夹具里的叶子节点、但整轮读取一次都没取过值 —— 用来发现"死数据"。

        只看叶子:容器(`Output` / `B_TEMP`)靠 `Elements` 导航,不会被 `find` 命中,
        把它们算进来只会淹没真问题。容器下的叶子若走 `Elements` 取过值,已记进 touched。
        """
        used = self.hits | self.touched
        return sorted(
            (p for p in self.nodes if p not in used and not children_of(self.nodes, p)),
            key=natural_key,
        )

    def unused_outside(self, allowed: list[str]) -> list[str]:
        """`unused_leaves()` 里不在 allowlist 的那些。allowlist 支持 `*` 通配。

        夹具里有些节点是**故意留着不读**的(比如 PL 的组分叶子 —— 它的用处正是
        证明 reader 不会去拿它当 p_v),这些写进 `meta.unread_ok`。
        """
        return [p for p in self.unused_leaves() if not any(fnmatch.fnmatch(p, a) for a in allowed)]


# ---------------------------------------------------------------- 变异


def clone(tree: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(tree))


def drop_subtree(tree: dict[str, Any], *paths: str) -> dict[str, Any]:
    """删掉若干路径及其所有后代(模拟"这版模型没有这一层节点")。"""
    out = clone(tree)
    nodes = out["nodes"]
    for raw in paths:
        p = norm_path(raw)
        for key in [k for k in nodes if k == p or k.startswith(p + "\\")]:
            del nodes[key]
        parent, _, name = p.rpartition("\\")
        pent = nodes.get(norm_path(parent))
        if pent and "kids" in pent:
            pent["kids"] = [k for k in pent["kids"] if str(k) != name]
    return out


def as_leaf(tree: dict[str, Any], path: str) -> dict[str, Any]:
    """把目录节点改成叶子:清掉后代与 kids(模拟"同一字段这版是标量")。"""
    out = clone(tree)
    nodes = out["nodes"]
    p = norm_path(path)
    for key in [k for k in nodes if k.startswith(p + "\\")]:
        del nodes[key]
    entry = nodes.setdefault(p, {})
    entry["kids"] = []
    return out


def patch(
    tree: dict[str, Any],
    path: str,
    *,
    value: Any = _MISSING,
    unit: Any = _MISSING,
    err: Any = _MISSING,
) -> dict[str, Any]:
    """改一个节点的值 / 单位 / 异常,用来在好夹具上造单点故障。"""
    out = clone(tree)
    entry = out["nodes"].setdefault(norm_path(path), {})
    if value is not _MISSING:
        entry["v"] = value
    if unit is not _MISSING:
        entry["u"] = unit
    if err is not _MISSING:
        if err is None:
            entry.pop("err", None)
        else:
            entry["err"] = str(err)
    return out
