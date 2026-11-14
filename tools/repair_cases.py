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

只改这些:判定(`severity` / `converged` / `reasons_json`)、单位缺口
(`unit_gaps` / `unit_gaps_json`)、能换算的数值回填、**焓平衡自检**
(`blocks` 的 `dh_kw` / `ref_kw` / `ref_kind` / `balance_state` / `balance_note`),
以及**泵参数**(`head_m` / `flow_m3h` / `pump_eff` / `p_in_bar` / `p_out_bar` /
`dp_bar` / `rho_kgm3` / `pump_vap_frac` / `motor_kw`)、**设备进出温度**
(`inlet_temp_c` / `outlet_temp_c` / `cold_in_temp_c` / `cold_out_temp_c` / `outlet_vap_frac`,
用已存的流股 + 连接关系算,不需要 Aspen)。
**不碰**流股名、设备名、剖面、组成,也不动用户手填的 `pv_bar`。

焓平衡只在有 `connections` 的工况上重算(2026-11-01 之前读的工况没有拓扑数据)。
"""

from __future__ import annotations

import argparse
import json

from app.aspen import balance, reader, units
from app.design import pump as pump_rules
from app.store import db

# 流股列 ← raw_json 里的字段 + 量纲
STREAM_COLUMNS = [(key, field, quantity) for key, field, quantity in reader.STREAM_FIELDS]

# 设备列 ← 候选字段 + 量纲
BLOCK_COLUMNS = [
    ("duty_kw", reader.BLOCK_DUTY_FIELDS, "power"),
    ("power_kw", reader.BLOCK_POWER_FIELDS, "power"),
    ("area_m2", reader.BLOCK_AREA_FIELDS, "area"),
    ("cond_duty_kw", ("COND_DUTY",), "power"),
    ("reb_duty_kw", ("REB_DUTY",), "power"),
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
            # 总是重算:换算因子可能改过(2026-11-01:cal 从 4.184 改成 4.1868 IT 卡)
            for field in fields:
                item = raw.get(field)
                if not item or item.get("value") is None:
                    continue
                new = units.convert(quantity, item["value"], item.get("unit") or "")
                if new is not None:
                    if b.get(key) != new:
                        b[key] = new
                        filled_blocks += 1
                    break

    # ---- 1.5) 泵参数:按最新单位表从 raw_json 重算
    # 2026-11-08:MET 单位集的 m-kgf/kg / mol/cc 就是这么补出来的 ——
    # 补完单位表,老工况一条命令就能恢复,不用回 Aspen 重读。
    connections = [dict(r) for r in db.read_connections(conn, cid)]
    by_stream = {s["name"]: s for s in streams}
    inflow, outflow = {}, {}
    for row in connections:
        bucket = inflow if row.get("direction") == "in" else outflow
        bucket.setdefault(row.get("block"), []).append(row.get("stream"))

    pumps_filled = 0
    for b in blocks:
        if str(b.get("type") or "").strip().lower() != "pump":
            continue
        raw = b["raw"]

        def conv(field: str, quantity: str):
            item = raw.get(field)
            if not item or item.get("value") is None:
                return None
            return units.convert(quantity, item["value"], item.get("unit") or "")

        mw = (raw.get("MW_LIQ") or {}).get("value")
        # 进口/出口压力取自流股;没有拓扑的工况只重算泵块自己的字段
        inlet = outlet = None
        if connections:
            inlet = next((by_stream[s] for s in inflow.get(b["name"], []) if s in by_stream), None)
            outlet = next(
                (by_stream[s] for s in outflow.get(b["name"], []) if s in by_stream), None
            )

        values = {
            "head_m": conv("HEAD_CAL", "length"),
            "flow_m3h": conv("VFLOW", "volume_flow"),
            "dp_bar": conv("IN_PRES", "pressure"),
        }
        # 密度口径与 reader 一致:单相优先用进口流股的 m/V,拿不到才退回泵块 RHO_LIQ×MW
        vap = inlet.get("vap_frac") if inlet else None
        rho_from_flow = None
        if inlet and (vap is None or float(vap) <= 0.001):
            mass, vol = inlet.get("mass_flow_kgh"), inlet.get("vol_flow_m3h")
            if mass and vol:
                rho_from_flow = float(mass) / float(vol)
        values["rho_kgm3"] = (
            rho_from_flow
            if rho_from_flow is not None
            else pump_rules.mass_density_kgm3(conv("RHO_LIQ", "molar_density"), mw)
        )
        values["pump_eff"] = (raw.get("CEFF") or {}).get("value")
        if connections:
            values["p_in_bar"] = inlet.get("pressure_bar") if inlet else None
            values["p_out_bar"] = outlet.get("pressure_bar") if outlet else None
            values["pump_vap_frac"] = vap
        values["motor_kw"] = pump_rules.pick_motor(b.get("power_kw"))[0]
        for key, new in values.items():
            if b.get(key) != new:
                b[key] = new
                pumps_filled += 1

    # ---- 2) 重算判定
    codes = _json(case["status_json"], {})
    tears = _json(case["tear_json"], [])
    verdict = reader.judge(
        codes,
        case["errtxt"],
        tears,
        [{"name": b["name"], "status": b["status"], "message": b["message"]} for b in blocks],
    )
    # 没 Run 过的模型单独一档(和 read_case 里同一套规则)
    reader.mark_empty(verdict, reader.value_coverage(streams), case["run_id"])

    # ---- 3) 重算单位缺口(用回填之后的数值)
    gaps = reader.unit_gaps(streams, blocks)

    # ---- 4) 重算焓平衡(只对存了连接的工况;老工况没有拓扑,跳过不动)
    balance_changed = 0
    if connections:
        # 塔的对照值要用逐级 duty(首级冷凝器 + 末级再沸器);库里已有剖面就直接读
        stages = {}
        for b in blocks:
            if b.get("type") in ("RadFrac", "Absorber", "Extract"):
                rows = [dict(r) for r in db.read_column_stages(conn, cid, b["name"])]
                if rows:
                    stages[b["name"]] = rows
        results = balance.compute(streams, blocks, connections, stages=stages)
        for b in blocks:
            new = results.get(b["name"]) or {}
            if b.get("balance_state") != new.get("balance_state") or b.get("dh_kw") != new.get(
                "dh_kw"
            ):
                balance_changed += 1
            b["balance"] = new

    # ---- 5) 补设备进出温度(用已存的流股 + 连接关系,不用回 Aspen)
    if connections:
        reader.enrich_side_temperatures(streams, blocks, connections)

    changed = []
    if case["severity"] != verdict["severity"]:
        changed.append(f"判定 {case['severity']} → {verdict['severity']}")
    if bool(case["converged"]) != bool(verdict["converged"]):
        changed.append(f"可用 {bool(case['converged'])} → {verdict['converged']}")
    if (case["unit_gaps"] or 0) != gaps["count"]:
        changed.append(f"单位缺口 {case['unit_gaps'] or 0} → {gaps['count']}")
    if filled_streams or filled_blocks:
        changed.append(f"回填 {filled_streams} 个流股字段 / {filled_blocks} 个设备字段")
    if pumps_filled:
        changed.append(f"泵参数 {pumps_filled} 个字段重算")
    if balance_changed:
        changed.append(f"焓平衡 {balance_changed} 台设备结论有变")

    if apply:
        conn.executemany(
            "UPDATE streams SET temperature_c=?, pressure_bar=?, mass_flow_kgh=?,"
            " mole_flow_kmolh=?, vap_frac=?, vol_flow_m3h=? WHERE case_id=? AND name=?",
            [
                (
                    s["temperature_c"],
                    s["pressure_bar"],
                    s["mass_flow_kgh"],
                    s["mole_flow_kmolh"],
                    s["vap_frac"],
                    s["vol_flow_m3h"],
                    cid,
                    s["name"],
                )
                for s in streams
            ],
        )
        conn.executemany(
            "UPDATE blocks SET duty_kw=?, power_kw=?, area_m2=?,"
            " cond_duty_kw=?, reb_duty_kw=?,"
            " dh_kw=?, ref_kw=?, ref_kind=?, balance_state=?, balance_note=?,"
            " head_m=?, flow_m3h=?, pump_eff=?, p_in_bar=?, p_out_bar=?, dp_bar=?,"
            " rho_kgm3=?, pump_vap_frac=?, motor_kw=?,"
            " inlet_temp_c=?, outlet_temp_c=?, cold_in_temp_c=?, cold_out_temp_c=?,"
            " outlet_vap_frac=?"
            " WHERE case_id=? AND name=?",
            [
                (
                    b["duty_kw"],
                    b["power_kw"],
                    b["area_m2"],
                    b.get("cond_duty_kw"),
                    b.get("reb_duty_kw"),
                    (b.get("balance") or {}).get("dh_kw"),
                    (b.get("balance") or {}).get("ref_kw"),
                    (b.get("balance") or {}).get("ref_kind"),
                    (b.get("balance") or {}).get("balance_state"),
                    (b.get("balance") or {}).get("balance_note"),
                    b.get("head_m"),
                    b.get("flow_m3h"),
                    b.get("pump_eff"),
                    b.get("p_in_bar"),
                    b.get("p_out_bar"),
                    b.get("dp_bar"),
                    b.get("rho_kgm3"),
                    b.get("pump_vap_frac"),
                    b.get("motor_kw"),
                    b.get("inlet_temp_c"),
                    b.get("outlet_temp_c"),
                    b.get("cold_in_temp_c"),
                    b.get("cold_out_temp_c"),
                    b.get("outlet_vap_frac"),
                    cid,
                    b["name"],
                )
                for b in blocks
            ],
        )
        conn.execute(
            "UPDATE cases SET severity=?, converged=?, reasons_json=?,"
            " unit_gaps=?, unit_gaps_json=? WHERE id=?",
            (
                verdict["severity"],
                1 if verdict["converged"] else 0,
                json.dumps(verdict["reasons"], ensure_ascii=False),
                gaps["count"],
                json.dumps(gaps, ensure_ascii=False),
                cid,
            ),
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
