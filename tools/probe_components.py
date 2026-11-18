"""把当前打开的 Aspen 的组分树打出来(只读),用来定位 CAS 号挂在哪个节点下。

为什么需要单独一个脚本
--------------------
`\\Data\\Components` 的第一层实测是**分组名**(Attr-Comps / Comp-Lists / …),不是组分。
组分属性(尤其 CAS 号)到底挂在哪个父节点、字段叫什么,不同 Aspen 版本可能不一样,
靠猜会读空(2026-11-15 就踩过:14 个分组被当成组分写进了库)。

这个脚本把真实结构原样打出来,照着输出改 `app/aspen/reader.py` 里的
`_COMPONENT_PARENTS` / `_CAS_FIELDS` 即可。

用法
----
    python tools/probe_components.py                # 第一个打开着的模型
    python tools/probe_components.py --comp PDO     # 只看某个组分
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:  # pywin32 缺失时不在导入期炸掉,留给 main 报错(--help 与离线检查仍可用)
    from app.aspen import connect, reader  # noqa: E402
except Exception as _exc:  # noqa: BLE001
    connect = reader = None
    _IMPORT_ERROR = _exc
else:
    _IMPORT_ERROR = None


def _dump(session, path: str, depth: int = 2, indent: int = 0) -> None:
    pad = "  " * indent
    for name in reader.child_names(session.find(path)):
        child = rf"{path}\{name}"
        kids = reader.child_names(session.find(child))
        value, unit = reader.fetch(session, child)
        shown = "" if kids else f" = {value!r}{(' ' + unit) if unit else ''}"
        print(f"{pad}- {name}{shown}")
        if kids and depth > 1:
            _dump(session, child, depth - 1, indent + 1)


def main() -> int:
    ap = argparse.ArgumentParser(description="打印 Aspen 组分树(只读)")
    ap.add_argument("--comp", default="", help="只看这个组分(默认取首条流股的第一个组分)")
    ap.add_argument("--depth", type=int, default=2, help="往下打几层(默认 2)")
    args = ap.parse_args()

    if _IMPORT_ERROR is not None:
        print("导入失败,无法探测:", _IMPORT_ERROR)
        print("请确认:1) 在项目根目录运行;2) 已装 pywin32(requirements.txt)。")
        return 3

    models = connect.list_open_models()
    print("打开着的模型:", *models, sep="\n  ")
    if not models:
        print("没有打开着的 Aspen 模型")
        return 1
    session = connect.attach(models[0])

    print("\n=== \\Data\\Components 的第一层 ===")
    _dump(session, r"\Data\Components", depth=1)

    comp = args.comp
    if not comp:
        streams = reader.child_names(session.find(r"\Data\Streams"))
        if streams:
            xs = reader.child_names(session.find(rf"\Data\Streams\{streams[0]}\Output\X"))
            comp = xs[0] if xs else ""
    if not comp:
        print("\n取不到组分名,用 --comp 指定一个再试")
        return 0

    print(f"\n=== 组分 {comp} 在各候选父节点下 ===")
    for parent in reader._COMPONENT_PARENTS:
        base = rf"{parent}\{comp}"
        node = session.find(base)
        print(f"\n[{parent}] find -> {'有' if node is not None else '无'}")
        if node is not None:
            _dump(session, base, depth=args.depth)
    return 0


if __name__ == "__main__":
    sys.exit(main())
