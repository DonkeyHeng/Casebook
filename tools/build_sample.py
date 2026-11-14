"""build_sample.py -- 从当前打开的 Aspen 模型重读一份工况,存成**脱敏的公开示例库**。

用法(模型已经在 Aspen 里打开):

    python -m tools.build_sample                    # 产出 samples/case-23-error.db,工况 id 固定 23
    python -m tools.build_sample --out 路径 --case-id N

为什么要这个脚本
----------------
示例库必须跟着代码一起更新:旧示例是第一期读的,没有泵参数、没有压升,
拿它演示第二期会全是「—」。重读一次把新字段补齐,比手改 SQLite 可靠。

脱敏(见 AGENTS.md §4)
----------------------
* `run_user` → `user`
* `model_path` → 只留文件名,不带目录
其余是真实读数,不改。

只读:不调用 Run / Reinit / Save。
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.aspen import connect, reader  # noqa: E402
from app.store import db  # noqa: E402

DEFAULT_OUT = ROOT / "samples" / "case-23-error.db"
DEFAULT_CASE_ID = 23  # 文件名里是 23,库里也让它一致


def sanitize(case: dict) -> None:
    """把能定位到人的东西换掉:只留模型文件名,运行用户固定成 user。"""
    model = case.setdefault("model", {})
    name = os.path.basename(str(model.get("path") or "").replace("/", "\\"))
    model["path"] = name
    model["name"] = name
    run = case.setdefault("convergence", {}).setdefault("run", {})
    if run.get("USER"):
        run["USER"] = "user"


def renumber(conn, old: int, new: int) -> None:
    """把刚存进去的工况改成指定 id(文件名和 id 对得上,文档里也不用改)。

    子表先改、父表后改,中间必然有一段时间外键对不上,所以临时关掉外键检查 ——
    只在这几行里关,改完立刻打开。
    """
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        for table in ("streams", "blocks", "connections", "stream_components", "column_stages"):
            conn.execute(f"UPDATE {table} SET case_id=? WHERE case_id=?", (new, old))
        conn.execute("UPDATE cases SET id=? WHERE id=?", (new, old))
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def build_from_db(src_path: str, src_case: int, out: Path, new_id: int) -> dict:
    """从**已读工况**复制出示例库:`--from-db data/cases.db --case 38`。

    不用 Aspen、不用重读:工况里已经有什么(组成、塔板、泵的 p_v)就复制什么,
    再做同样的脱敏。适合"库里已经有一份刚读好的工况,只想更新示例库"。
    """
    out.unlink(missing_ok=True)
    dst = db.open_db(str(out))
    src = sqlite3.connect(src_path)
    src.row_factory = sqlite3.Row
    try:
        case = src.execute("SELECT * FROM cases WHERE id=?", (src_case,)).fetchone()
        if case is None:
            raise SystemExit(f"{src_path} 里没有工况 #{src_case}")

        cols = [c for c in case.keys() if c != "id"]
        row = dict(case)
        model = os.path.basename(str(row.get("model_path") or "").replace("/", "\\"))
        row["model_path"] = model
        row["model_name"] = model or row.get("model_name")
        if row.get("run_user"):
            row["run_user"] = "user"
        dst.execute(
            f"INSERT INTO cases (id, {', '.join(cols)}) VALUES (?, {', '.join('?' * len(cols))})",
            [new_id] + [row[c] for c in cols],
        )

        for table in ("streams", "blocks", "connections", "stream_components", "column_stages"):
            rows = src.execute(f"SELECT * FROM {table} WHERE case_id=?", (src_case,)).fetchall()
            if not rows:
                continue
            fields = [c for c in rows[0].keys() if c != "case_id"]
            dst.executemany(
                f"INSERT INTO {table} (case_id, {', '.join(fields)}) "
                f"VALUES (?, {', '.join('?' * len(fields))})",
                [[new_id] + [r[f] for f in fields] for r in rows],
            )
        dst.commit()

        def count(sql: str) -> int:
            return dst.execute(sql).fetchone()[0]

        stats = {
            "streams": count("SELECT COUNT(*) FROM streams"),
            "blocks": count("SELECT COUNT(*) FROM blocks"),
            "connections": count("SELECT COUNT(*) FROM connections"),
            "stages": count("SELECT COUNT(*) FROM column_stages"),
            "components": count("SELECT COUNT(*) FROM stream_components"),
            "pumps": count("SELECT COUNT(*) FROM blocks WHERE lower(coalesce(type,''))='pump'"),
            "pumps_pv": count(
                "SELECT COUNT(*) FROM blocks WHERE lower(coalesce(type,''))='pump' "
                "AND pv_bar IS NOT NULL"
            ),
        }
    finally:
        src.close()
        dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dst.close()
    for suffix in ("-wal", "-shm"):
        stray = Path(str(out) + suffix)
        if stray.exists():
            stray.unlink(missing_ok=True)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="重读当前 Aspen 模型,生成脱敏示例库")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help=f"输出路径(默认 {DEFAULT_OUT})")
    ap.add_argument("--case-id", type=int, default=DEFAULT_CASE_ID, help="库里的工况 id")
    ap.add_argument("--from-db", default="", help="从这个工况库复制(不连 Aspen);需同时给 --case")
    ap.add_argument("--case", type=int, default=0, help="--from-db 时复制哪个工况 id")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.from_db:
        if not args.case:
            print("--from-db 需要同时给 --case <工况 id>")
            return 2
        stats = build_from_db(args.from_db, args.case, out, args.case_id)
        print(
            f"已写出 {out}({out.stat().st_size:,} 字节),工况 #{args.case_id}"
            f"(来自 {args.from_db} 的 #{args.case})"
        )
        print(
            f"  {stats['streams']} 流股 / {stats['blocks']} 模块 / "
            f"{stats['connections']} 连接 / {stats['stages']} 塔板级 / "
            f"{stats['components']} 组成"
        )
        print(f"  泵 {stats['pumps']} 台,p_v 有值 {stats['pumps_pv']} 台")
        return 0

    session = connect.attach()
    print(f"已附着: {session.model_path}")
    case = reader.read_case(session, include_composition=True, include_profiles=True)
    sanitize(case)

    out.unlink(missing_ok=True)
    conn = db.open_db(str(out))
    try:
        cid = db.save_case(conn, case)
        if cid != args.case_id:
            renumber(conn, cid, args.case_id)
            cid = args.case_id

        def count(sql: str) -> int:
            return conn.execute(sql).fetchone()[0]

        streams = count("SELECT COUNT(*) FROM streams")
        blocks = count("SELECT COUNT(*) FROM blocks")
        conns = count("SELECT COUNT(*) FROM connections")
        stages = count("SELECT COUNT(*) FROM column_stages")
        comps = count("SELECT COUNT(*) FROM stream_components")
        pumps = count("SELECT COUNT(*) FROM blocks WHERE lower(coalesce(type,''))='pump'")
        heads = count(
            "SELECT COUNT(*) FROM blocks WHERE lower(coalesce(type,''))='pump' "
            "AND head_m IS NOT NULL"
        )
        sev = conn.execute("SELECT severity, block_errors FROM cases").fetchone()
    finally:
        conn.close()

    print(f"已写出 {out}({out.stat().st_size:,} 字节),工况 #{cid}")
    print(f"  {streams} 流股 / {blocks} 模块 / {conns} 连接 / {stages} 塔板级 / {comps} 组成")
    print(f"  泵 {pumps} 台,扬程有值 {heads} 台")
    print(f"  判定 {sev[0]},报错模块 {sev[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
