"""给已存工况补齐流股组成(逐条从当前打开的 Aspen 现读,写回同一个工况)。

为什么要单独一个脚本
--------------------
网页上的组成是「点开哪条读哪条」,一次只能补一条,121 条流股没法手点。
而主读取刻意不读组成(36 次 COM/流股,慢约 4 倍),所以补数只能在
「读完之后、Aspen 还开着且没重跑过」这段时间里做。

用法
----
    python tools/fill_composition.py                 # 最新工况 + data/cases.db
    python tools/fill_composition.py --case 23
    python tools/fill_composition.py --case 23 --force   # 已存过也重读

前提(与网页按钮相同,见 app/api/composition.py)
------------------------------------------------
  1. Aspen 打开的就是这份工况的**来源模型**(.apw 路径一致)
  2. 运行 ID 还是这份工况的 run_id(模型没重跑过)

不满足会立刻中止并把原因打出来;单条流股没有组分(纯能量流)只跳过,不算失败。
全部只读 Aspen,只写本地库。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 允许两种跑法:`python tools/fill_composition.py`(sys.path[0] 是 tools/)
# 和 `python -m tools.fill_composition`(从项目根 import)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.api import composition  # noqa: E402
from app.store import db  # noqa: E402

# 出现这些原因说明后面每一条都会同样失败,不必再试
_FATAL = ("没有打开着的 Aspen", "不是这个工况的来源模型", "已经重新运行过了")


def main() -> int:
    ap = argparse.ArgumentParser(description="给已存工况补齐流股组成(只读 Aspen,写本地库)")
    ap.add_argument("--db", default="data/cases.db", help="库文件路径(默认 data/cases.db)")
    ap.add_argument("--case", type=int, metavar="ID", help="工况 ID(默认最新一份)")
    ap.add_argument("--force", action="store_true", help="库里已存过也重读一遍")
    args = ap.parse_args()

    conn = db.open_db(args.db)
    case = db.get_case(conn, args.case) if args.case else db.latest_case(conn)
    if case is None:
        print("没有可补的工况。" if args.case else "库里还没有工况。")
        return 1

    streams = db.read_streams(conn, case["id"])
    print(f"工况 #{case['id']}:{case['model_name']}")
    print(f"模型:{case['model_path']}")
    print(f"运行 ID:{case['run_id']}")
    print(f"流股:{len(streams)} 条,逐条读组成(约 0.3 s/条)…\n")

    ok = skipped = 0
    started = time.time()
    for i, row in enumerate(streams, 1):
        name = row["name"]
        try:
            composition.get(conn, case, name, force=args.force)
            ok += 1
        except composition.CompositionError as exc:
            msg = str(exc)
            if any(k in msg for k in _FATAL):
                print(f"\n[中止] {msg}")
                break
            skipped += 1
        if i % 20 == 0:
            print(f"  {i}/{len(streams)}  成功 {ok},跳过 {skipped}", flush=True)

    print(
        f"\n完成:{ok} 条写入,{skipped} 条跳过(无组分/纯能量流),用时 {time.time() - started:.1f} s"
    )
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
