"""打印当前打开的 Aspen 里每条流股的粘度节点、值与单位(只读)。

为什么需要它
------------
粘度要模型在物性集里挂上 VISC 才有值,且结果挂在 `Output\STRM_UPP\VISC`
(和 p_v 的 PBUB 同一层)。不同 Aspen 版本/单位集下叶子名与单位可能不同,
读不到时用这个脚本一眼看清,不用猜。

用法
----
    python tools/probe_viscosity.py              # 前 10 条流股
    python tools/probe_viscosity.py --all        # 全部
    python tools/probe_viscosity.py --stream 0101
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.aspen import connect, reader  # noqa: E402


def dump(session, stream: str) -> None:
    base = rf"\Data\Streams\{stream}\Output"
    up = rf"{base}\STRM_UPP"
    ups = reader.child_names(session.find(up))
    print(f"\n=== 流股 {stream} | STRM_UPP 子节点: {ups}")
    if not ups:
        print("  没有 STRM_UPP:物性集没挂到流股(报告选项 → 流股 → 物性集),或模型没重跑")
        return
    for name in ups:
        path = rf"{up}\{name}"
        sub = reader.child_names(session.find(path))
        value, unit = reader.fetch(session, path)
        if sub:
            print(f"  - {name}/ (目录,子节点 {sub})")
            for leaf in sub:
                lv, lu = reader.fetch(session, rf"{path}\{leaf}")
                print(f"      {leaf} = {lv!r} 单位 {lu!r}")
        else:
            print(f"  - {name} = {value!r} 单位 {unit!r}")
    for rel in reader.VISC_STREAM_PATHS:
        value, unit = reader.fetch(session, rf"{base}\{rel}")
        print(f"  尝试 {rel}: {value!r} {unit!r} → {reader._to_cp(value, unit)!r} cP")


def main() -> int:
    ap = argparse.ArgumentParser(description="打印流股粘度节点(只读)")
    ap.add_argument("--stream", default="", help="只查这一条")
    ap.add_argument("--all", action="store_true", help="查全部流股")
    args = ap.parse_args()

    models = connect.list_open_models()
    if not models:
        print("没有打开着的 Aspen 模型")
        return 1
    session = connect.attach(models[0])
    if args.stream:
        dump(session, args.stream)
        return 0
    streams = reader.child_names(session.find(r"\Data\Streams"))
    for stream in (streams if args.all else streams[:10]):
        dump(session, stream)
    return 0


if __name__ == "__main__":
    sys.exit(main())
