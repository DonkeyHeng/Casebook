"""生成《物料与能量衡算报告》.docx —— 命令行入口(无界面批量用)。

    python -m tools.balance_report --list                     # 看有哪些工况
    python -m tools.balance_report --case 40                  # 全部设备
    python -m tools.balance_report --case 40 --families mix,column
    python -m tools.balance_report --case 40 --blocks E0101,T0202
    python -m tools.balance_report --case 40 --out D:/reports

和 Web 端「衡算报告」页用的是同一套代码(`app/api/report_export.py`),
所以报告里的数字与界面完全一致;本工具只负责挑设备、挑工况、落盘。

工段边界
--------
默认按设备名段号自动推草稿(报告里会注明「未经人工确认」)。要人工确认的边界,
把 `samples/sections.example.json` 复制成 `data/config/sections.json` 再改 ——
本工具会按工况 id 自动读,不用额外参数。

设备怎么选
----------
`--blocks` 与 `--families` 可以一起用,取并集;都不给就是全部设备。
家族 key 见 `python -m tools.balance_report --families`(误填会列出来)。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from app.api import report_export
from app.design import block_family
from app.design import sections as section_rules
from app.store import db

CONFIG_PATH = os.path.join("data", "config", "sections.json")


def _read_sections(case_id: int):
    """读 data/config/sections.json(没有就返回空;坏文件给一句人话)。"""
    if not os.path.exists(CONFIG_PATH):
        return {}, {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"! 读不了 {CONFIG_PATH}:{exc}(改用自动草稿)")
        return {}, {}
    return section_rules.load(data, case_id)


def _pick(blocks, args) -> list[str] | None:
    """--blocks 与 --families 取并集;都不给返回 None(= 全部)。"""
    picked: set[str] = set()
    for name in (args.blocks or "").replace(" ", "").split(","):
        if name:
            picked.add(name)
    for key in (args.families or "").replace(" ", "").split(","):
        if key:
            picked.update(b["name"] for b in blocks
                          if block_family.family_of(b.get("type")) == key)
    return sorted(picked) if picked else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="生成物料与能量衡算报告(.docx)")
    parser.add_argument("--db", default=os.path.join("data", "cases.db"),
                        help="工况库路径(默认 data/cases.db)")
    parser.add_argument("--case", type=int, default=0, help="工况 id")
    parser.add_argument("--blocks", default="", help="设备名,逗号分隔")
    parser.add_argument("--families", default="",
                        help="设备家族 key,逗号分隔(如 mix,column)")
    parser.add_argument("--out", default=os.path.join("data", "preview"),
                        help="输出目录(默认 data/preview)")
    parser.add_argument("--list", action="store_true", help="列出库里的工况")
    args = parser.parse_args(argv)

    if not os.path.exists(args.db):
        print(f"找不到工况库:{args.db}")
        return 2

    conn = db.open_db(args.db)
    cases = db.list_cases(conn)

    if args.list:
        print(f"{args.db} 共 {len(cases)} 个工况:")
        for row in cases:
            print(f"  #{row['id']:<4} {row['model_name'] or ''}  "
                  f"{row['read_at'] or ''}  流股 {row['stream_count']} / "
                  f"设备 {row['block_count']}")
        return 0

    if not args.case:
        print("需要 --case <工况 id>,或用 --list 先看有哪些工况")
        return 2

    if args.case not in {row["id"] for row in cases}:
        print(f"库里没有 id={args.case} 的工况(--list 可查)")
        return 2

    blocks = [dict(r) for r in db.read_blocks(conn, args.case)]
    picked = _pick(blocks, args)
    if picked is not None:
        unknown = [p for p in picked if p not in {b["name"] for b in blocks}]
        if unknown:
            print(f"! 这些设备名在工况 #{args.case} 里不存在,已忽略:"
                  f"{'、'.join(unknown)}")
        picked = [p for p in picked if p not in unknown] or None
        labels = [k for k in (args.families or "").replace(" ", "").split(",") if k]
        bad = [k for k in labels if k not in block_family.FAMILY_LABELS]
        if bad:
            print(f"! 未知家族 key:{'、'.join(bad)};"
                  f"可用:{'、'.join(block_family.FAMILY_LABELS)}")

    confirmed, overrides = _read_sections(args.case)
    if confirmed:
        parts = section_rules.merge(
            section_rules.draft(blocks, [dict(r) for r in
                                         db.read_connections(conn, args.case)]),
            confirmed)
        print(f"工段边界:{CONFIG_PATH} 里 #%d 的 {len(confirmed)} 个工段已确认"
              % args.case)
    else:
        parts = None
        print("工段边界:未找到人工确认的边界,按设备名段号自动推导")

    try:
        data = report_export.build_data(conn, args.case, parts, picked)
        blob = report_export.render(data)
    except ImportError as exc:
        print(f"无法生成: {exc}\n先装依赖:{sys.executable} -m pip install "
              f"\u0022python-docx>=1.1\u0022")
        return 3
    except LookupError as exc:
        print(f"无法生成:{exc}")
        return 2

    fname = report_export.filename(args.case)
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, fname)
    with open(path, "wb") as handle:
        handle.write(blob)

    n_dev = data["devices"]["summary"]["total"]
    print(f"已生成:{path}")
    print(f"  物表 {data['zone']['summary']['total']} 个工段 + {n_dev} 台设备,"
          f"能量表 {len(data['energy']['devices'])} 行,"
          f"塔负荷 {data['energy']['summary']['tower_count']} 座,"
          f"{len(blob) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
