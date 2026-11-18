r"""打印当前打开的 Aspen 里每条流股的粘度节点、值与单位(只读)。

为什么需要它
------------
粘度要模型在物性集里挂上 VISC 才有值,结果挂在 `Output\STRM_UPP\VISC\MIXED`
(和 p_v 的 PBUB 同一层)。**节点存在不等于有值**:物性集没重跑成功时,节点在、值是 None。
所以这个脚本除了打候选路径,还会把 `Output` 下**所有有值的叶子**列出来 ——
万一 Aspen 把粘度放在别的名字下,一眼就能看见。

用法
----
    python tools/probe_viscosity.py              # 前 3 条流股
    python tools/probe_viscosity.py --all        # 全部
    python tools/probe_viscosity.py --stream 0101
    python tools/probe_viscosity.py --leaves     # 额外列出所有有值叶子
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.aspen import connect, reader  # noqa: E402


def _leaves(session, base: str, depth: int = 5, prefix: str = "") -> list[tuple[str, object, str]]:
    """枚举 base 下 depth 层内的叶子(只返回有值的)。"""
    out: list[tuple[str, object, str]] = []
    for name in reader.child_names(session.find(base)):
        path = rf"{base}\{name}"
        label = f"{prefix}{name}"
        kids = reader.child_names(session.find(path))
        if kids:
            value, unit = reader.fetch(session, path)
            out.append((f"{label}/", value, unit))
            if depth > 1:
                out.extend(_leaves(session, path, depth - 1, f"{label}\\"))
            continue
        value, unit = reader.fetch(session, path)
        out.append((label, value, unit))
    return out


def dump(session, stream: str, show_leaves: bool = False) -> None:
    base = rf"\Data\Streams\{stream}\Output"
    up = rf"{base}\STRM_UPP"
    ups = reader.child_names(session.find(up))
    print(f"\n=== 流股 {stream} | STRM_UPP 子节点: {ups}")
    if not ups:
        print("  没有 STRM_UPP:物性集没挂到流股(报告选项 → 流股 → 物性集),或模型没重跑")
    for name in ups:
        path = rf"{up}\{name}"
        sub = reader.child_names(session.find(path))
        if not sub:
            value, unit = reader.fetch(session, path)
            print(f"  - {name} = {value!r} 单位 {unit!r}")
            continue
        print(f"  - {name}/ (目录)")
        for leaf in sub:
            lp = rf"{path}\{leaf}"
            lv, lu = reader.fetch(session, lp)
            mark = "  ← 空" if lv is None else ""
            print(f"      {leaf} = {lv!r} 单位 {lu!r}{mark}")
    for rel in reader.VISC_STREAM_PATHS:
        value, unit = reader.fetch(session, rf"{base}\{rel}")
        cp = reader._to_cp(value, unit)
        if value is not None:
            print(f"  命中 {rel}: {value!r} {unit!r} → {cp!r} cP")
    if show_leaves:
        print("  --- Output 下 5 层内的叶子(目录带 /,供人工辨认粘度字段)---")
        for label, value, unit in _leaves(session, base):
            print(f"      {label} = {value!r} 单位 {unit!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description="打印流股粘度节点(只读)")
    ap.add_argument("--stream", default="", help="只查这一条")
    ap.add_argument("--all", action="store_true", help="查全部流股")
    ap.add_argument("--leaves", action="store_true", help="额外列出所有有值叶子")
    args = ap.parse_args()

    models = connect.list_open_models()
    if not models:
        print("没有打开着的 Aspen 模型")
        return 1
    session = connect.attach(models[0])
    if args.stream:
        dump(session, args.stream, args.leaves)
        return 0
    streams = reader.child_names(session.find(r"\Data\Streams"))
    for stream in streams if args.all else streams[:3]:
        dump(session, stream, args.leaves)
    return 0


if __name__ == "__main__":
    sys.exit(main())
