"""命令行入口:把当前打开的 Aspen 工况读进本地库。

用法:
  python -m app.cli --list                      # 看现在开着哪些模型
  python -m app.cli --read                      # 读当前模型 → 存库 → 打印摘要
  python -m app.cli --read --composition        # 连组成一起读(慢一些)
  python -m app.cli --cases                     # 列出已存工况
  python -m app.cli --show 3                    # 看第 3 个工况的明细

默认库文件:data/cases.db
"""

from __future__ import annotations

import argparse
import json
import sys

from app.aspen import connect, reader
from app.aspen.connect import AspenNotRunning
from app.store import db

DEFAULT_DB = "data/cases.db"


def _fmt(value, digits: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return str(value)


def cmd_list() -> int:
    try:
        models = connect.list_open_models()
    except AspenNotRunning as exc:
        print(exc)
        return 1
    if not models:
        print("没有打开着的模型。")
        return 1
    print("当前打开着的模型:")
    for m in models:
        print(f"  - {m}")
    return 0


def cmd_read(args) -> int:
    try:
        session = connect.attach(args.model)
    except AspenNotRunning as exc:
        print(exc)
        return 1

    print(f"已附着:{session.model_path}")

    def progress(done: int, total: int, label: str) -> None:
        if total and (done % 50 == 0 or done == total):
            print(f"  读取中 {done}/{total}  {label}", file=sys.stderr, flush=True)

    print("正在读取数据…", file=sys.stderr)
    case = reader.read_case(
        session,
        include_composition=args.composition,
        include_profiles=not args.no_profiles,
        progress=progress,
    )

    conn = db.open_db(args.db)
    case_id = db.save_case(conn, case)

    conv = case["convergence"]
    print()
    print("=" * 62)
    print(f"工况 #{case_id} 已入库:{args.db}")
    print("=" * 62)
    print(f"  模型      {case['model']['path']}")
    print(f"  读取时间  {case['model']['read_at']}")
    print(f"  运行 ID   {conv['run'].get('RUNID')}  ({conv['run'].get('ITDATE')})")
    print(f"  流股/设备 {case['stats']['stream_count']} / {case['stats']['block_count']}")
    conns = case.get("connections") or []
    sides = sum(1 for c in conns if c.get("side"))
    print(
        f"  连接关系  {len(conns)} 条(设备 ↔ 流股)" + (f",换热器分侧 {sides} 条" if sides else "")
    )
    bal = (case.get("stats") or {}).get("balance_summary") or {}
    if bal:
        print(
            f"  焓平衡    通过 {bal.get('ok', 0)} / 超差 {bal.get('bad', 0)}"
            f" / 无法校验 {bal.get('no_data', 0)} / 不校验 {bal.get('skip', 0)}"
        )
    print(f"  耗时      {case['stats']['elapsed_sec']} s")
    print()

    flag = {"ok": "✅ 收敛", "warning": "⚠️  有告警", "error": "❌ 有错误"}[conv["severity"]]
    print(f"  收敛判定  {flag}(converged={conv['converged']})")
    for reason in conv["reasons"]:
        print(f"            - {reason}")

    if conv["errtxt"]:
        print("\n  Aspen 原始提示:")
        for line in str(conv["errtxt"]).splitlines():
            print(f"    {line}")

    problems = [b for b in case["blocks"] if b.get("status") not in (None, 0)]
    if problems:
        print("\n  有问题的模块:")
        for b in problems:
            mark = "❌" if b.get("status") == 1 else "⚠️"
            print(f"    {mark} {b['name']:<10}{(b.get('type') or '-'):<10}{b.get('message') or ''}")

    codes = {k: v for k, v in conv["codes"].items()}
    print("\n  状态码:", json.dumps(codes, ensure_ascii=False))

    if conv["tears"]:
        print("\n  撕裂流:")
        for t in conv["tears"]:
            print(f"    {t['stream']:<12} {t['variable']:<18} 误差={t['error']}")

    print("\n  —— 流股前 8 条 ——")
    print(f"  {'流股':<12}{'温度℃':>10}{'压力bar':>10}{'质量流量kg/h':>16}{'汽化率':>10}")
    for s in case["streams"][:8]:
        print(
            f"  {s['name']:<12}{_fmt(s['temperature_c']):>10}{_fmt(s['pressure_bar'], 3):>10}"
            f"{_fmt(s['mass_flow_kgh'], 1):>16}{_fmt(s['vap_frac'], 3):>10}"
        )

    print("\n  —— 设备前 8 个 ——")
    print(f"  {'设备':<10}{'类型':<10}{'热负荷kW':>12}{'功率kW':>10}{'级数':>6}")
    for b in case["blocks"][:8]:
        print(
            f"  {b['name']:<10}{(b['type'] or '-'):<10}{_fmt(b['duty_kw']):>12}"
            f"{_fmt(b['power_kw']):>10}{b['nstage'] if b['nstage'] is not None else '-':>6}"
        )
    return 0


def cmd_cases(args) -> int:
    conn = db.open_db(args.db)
    rows = db.list_cases(conn)
    if not rows:
        print("库里还没有工况。")
        return 0
    print(f"{'ID':>4}  {'读取时间':<20}{'模型':<28}{'状态':<8}{'流股':>6}{'设备':>6}")
    for r in rows:
        print(
            f"{r['id']:>4}  {r['read_at']:<20}{(r['model_name'] or '-')[:26]:<28}"
            f"{r['severity']:<8}{r['stream_count']:>6}{r['block_count']:>6}"
        )
    return 0


def cmd_show(args) -> int:
    conn = db.open_db(args.db)
    case = db.get_case(conn, args.show)
    if case is None:
        print(f"没有 id={args.show} 的工况")
        return 1
    print(f"工况 #{case['id']}  {case['model_path']}")
    print(f"  读取时间 {case['read_at']}   状态 {case['severity']}   converged={case['converged']}")
    print(f"  理由 {case['reasons_json']}")
    if case["errtxt"]:
        print("  Aspen 提示:")
        for line in str(case["errtxt"]).splitlines():
            print(f"    {line}")

    streams = db.read_streams(conn, case["id"])
    print(f"\n  流股 {len(streams)} 条,前 10 条:")
    for s in streams[:10]:
        print(
            f"    {s['name']:<12}{_fmt(s['temperature_c']):>10}℃"
            f"{_fmt(s['pressure_bar'], 3):>10}bar{_fmt(s['mass_flow_kgh'], 1):>14}kg/h"
        )

    blocks = db.read_blocks(conn, case["id"])
    print(f"\n  设备 {len(blocks)} 个,前 10 个:")
    for b in blocks[:10]:
        print(f"    {b['name']:<10}{str(b['type'] or '-'):<10}负荷={_fmt(b['duty_kw'])} kW")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="把打开的 Aspen 工况读进本地库")
    ap.add_argument("--list", action="store_true", help="列出当前打开的模型")
    ap.add_argument("--read", action="store_true", help="读取当前模型并存库")
    ap.add_argument("--cases", action="store_true", help="列出已存工况")
    ap.add_argument("--show", type=int, metavar="ID", help="查看某个工况")
    ap.add_argument("--model", help="指定模型路径(默认取第一个打开的)")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"库文件路径(默认 {DEFAULT_DB})")
    ap.add_argument("--composition", action="store_true", help="同时读取流股组成(较慢)")
    ap.add_argument("--no-profiles", action="store_true", help="不读塔板剖面(快 10 秒左右)")
    args = ap.parse_args()

    if args.list:
        return cmd_list()
    if args.read:
        return cmd_read(args)
    if args.cases:
        return cmd_cases(args)
    if args.show is not None:
        return cmd_show(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
