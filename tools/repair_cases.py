"""修历史工况:按最新的判定规则和单位表重算,不用回 Aspen 重读一遍。

什么时候需要它
------------
1. **判定规则改了**:2026-11-01 起,`UOSSTAT` / `PER_ERROR` 非 0 不再单独算"错误"
   (实测它们区分不了告警和错误),只按模块级 `BLKSTAT` 和 `CVSTAT` 判。
   改动前入库的工况还是旧结论。
2. **单位表补了新单位**:2026-11-01 补了 `cum/hr`(m³/h)和 `Gcal/hr`,
   当时因为"单位不认识"被留空的数值,其实能从 `raw_json` 里的原始值重新换算出来。

用法:
    python -m tools.repair_cases            # 只看会改什么(dry-run,不写库)
    python -m tools.repair_cases --apply    # 真的写库

只改三样东西:`severity` / `converged` / `reasons_json`(重算判定),以及
`unit_gaps` / `unit_gaps_json`(重算单位缺口),外加把能换算的数值回填。
**不碰**流股名、设备名、剖面、组成。
"""
from __future__ import annotations

import argparse
import json

from app.aspen import reader, units
from app.store import db

# 流股列 ← raw_json 里的字段 + 量纲
STREAM_COLUMNS = [(key, field, quantity) for key, field, quantity in reader.STREAM_FIELDS]

# 设备列 ← 候选字段 + 量纲
BLOCK_COLUMNS = [
    ("duty_kw", reader.BLOCK_DUTY_FIELDS, "power"),
    ("power_kw", reader.BLOCK_POWER_FIELDS, "power"),
    ("area_m2", reader.BLOCK_AREA_FIELDS, "area"),
]


def _json(value, fallback):
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def repair_case(conn, case, apply: bool) -> dict:
    """重算一个工况,返回"改了什么"的摘要。"""
    cid = case["id"]
    streams = [dict(r) for r in db.read_streams(conn, cid)]
    blocks = [dict(r) for r in db.read_blocks(conn, cid)]

    # ---- 1) 按最新单位表回填数值(值来自 raw_json 里的原始值+原始单位)
    filled_streams = 0
    for s in streams:
        raw = _json(s.get("raw_json"), {})
        s["raw"] = raw
        for key, field, quantity in STREAM_COLUMNS:
            item = raw.get(field)
            if not item or item.get("value") is None:
                continue
            new = units.convert(quantity, item["value"], item.get("unit") or "")
            if new is not None and s.get(key) != new:
                s[key] = new
                filled_streams += 1

    filled_blocks = 0
    for b in blocks:
        raw = _json(b.get("raw_json"), {})
        b["raw"] = raw
        for key, fields, quantity in BLOCK_COLUMNS:
            if b.get(key) is not None:
                continue
            for field in fields:
                item = raw.get(field)
                if not item or item.get("value") is None:
                    continue
                new = units.convert(quantity, item["value"], item.get("unit") or "")
                if new is not None:
                    b[key] = new
                    filled_blocks += 1
                    break

    # ---- 2) 重算判定
    codes = _json(case["status_json"], {})
    tears = _json(case["tear_json"], [])
    verdict = reader.judge(codes, case["errtxt"], tears,
                           [{"name": b["name"], "status": b["status"],
                             "message": b["message"]} for b in blocks])

    # ---- 3) 重算单位缺口(用回填之后的数值)
    gaps = reader.unit_gaps(streams, blocks)

    changed = []
    if case["severity"] != verdict["severity"]:
        changed.append(f"判定 {case['severity']} → {verdict['severity']}")
    if bool(case["converged"]) != bool(verdict["converged"]):
        changed.append(f"可用 {bool(case['converged'])} → {verdict['converged']}")
    if (case["unit_gaps"] or 0) != gaps["count"]:
        changed.append(f"单位缺口 {case['unit_gaps'] or 0} → {gaps['count']}")
    if filled_streams or filled_blocks:
        changed.append(f"回填 {filled_streams} 个流股字段 / {filled_blocks} 个设备字段")

    if apply:
        conn.executemany(
            "UPDATE streams SET temperature_c=?, pressure_bar=?, mass_flow_kgh=?,"
            " mole_flow_kmolh=?, vap_frac=?, vol_flow_m3h=? WHERE case_id=? AND name=?",
            [(s["temperature_c"], s["pressure_bar"], s["mass_flow_kgh"],
              s["mole_flow_kmolh"], s["vap_frac"], s["vol_flow_m3h"], cid, s["name"])
             for s in streams],
        )
        conn.executemany(
            "UPDATE blocks SET duty_kw=?, power_kw=?, area_m2=? WHERE case_id=? AND name=?",
            [(b["duty_kw"], b["power_kw"], b["area_m2"], cid, b["name"]) for b in blocks],
        )
        conn.execute(
            "UPDATE cases SET severity=?, converged=?, reasons_json=?,"
            " unit_gaps=?, unit_gaps_json=? WHERE id=?",
            (verdict["severity"], 1 if verdict["converged"] else 0,
             json.dumps(verdict["reasons"], ensure_ascii=False),
             gaps["count"], json.dumps(gaps, ensure_ascii=False), cid),
        )
        conn.commit()

    return {"id": cid, "model": case["model_name"], "changed": changed}


def main() -> int:
    ap = argparse.ArgumentParser(description="重算历史工况的判定与单位换算")
    ap.add_argument("--apply", action="store_true", help="真的写库(默认只看不写)")
    ap.add_argument("--db", default="data/cases.db")
    args = ap.parse_args()

    conn = db.open_db(args.db)
    try:
        cases = conn.execute("SELECT * FROM cases ORDER BY id").fetchall()
        if not cases:
            print("库里没有工况。")
            return 0

        print(f"{'改' if args.apply else '将改(未写入)'} {len(cases)} 个工况:\n")
        touched = 0
        for case in cases:
            r = repair_case(conn, case, args.apply)
            head = f"  #{r['id']:<3} {str(r['model'])[:38]:<40}"
            if r["changed"]:
                touched += 1
                print(head + " | " + "; ".join(r["changed"]))
            else:
                print(head + " | 无需改动")
        print(f"\n{'' if args.apply else '(dry-run)'} 有 {touched} 个工况需要更新")
        if not args.apply and touched:
            print("确认无误后加 --apply 写库")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
