"""判定与焓平衡的回归自测 —— 不需要 Aspen、不需要网络、不需要 pytest。

    python -m tools.selftest                  # 只跑纯函数用例(秒级)
    python -m tools.selftest --db data/cases.db   # 再加"库里每个工况"的一致性检查
    python -m tools.selftest -v               # 逐条打印通过的用例

为什么要有这个文件
------------------
`judge()` 的判定规则和 `balance.py` 的容差口径**没有别的测试网**。实测中被改错过三次:
  ① 用 `UOSSTAT=1` 判 error —— 把"7 个告警、0 个报错"的样本模型判成"不建议用来算收率";
  ② 用 `PER_ERROR=1` 判 warning —— 一条理由都写不出来却判了黄;
  ③ 用撕裂流 `ERR > ERR_TOL` 判 error —— Aspen 控制面板根本没报这个错。
三次都是"看起来更严格",实际是误报。改动这两处的人(包括 AI)必须能立刻看到行为变了。

三组用例
--------
1. `judge()`:状态码 + 模块 BLKSTAT + 撕裂流 → ok / warning / error 的边界
2. `balance.compute()`:热负荷 / 换热器冷侧 / 机械功比值 / 绝热 / 不校验 / 无法校验
3. `--db`:库里每个工况"存下来的结论"必须等于"现在规则重算的结论"
   —— 这条不过,说明规则改了但没跑 `python -m tools.repair_cases --apply`
"""
from __future__ import annotations

import argparse
import json
import sys

from app.aspen import balance as balance_rules
from app.aspen import reader
from app.store import impact as impact_rules

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(label)
    else:
        FAILED.append((label, detail))


def check_eq(label: str, got, want) -> None:
    check(label, got == want, f"期望 {want!r},实际 {got!r}")


# ---------------------------------------------------------------- 判定规则

def test_judge() -> None:
    """judge():只有硬错误才判 error,软信号最多 warning。"""
    clean = {"RSTAT": 0, "CVSTAT": 0, "CSSTAT": 2, "UOSSTAT": 0, "PER_ERROR": 0}

    # ① 全部正常 → ok(CSSTAT=2 之类不代表失败,别被它带偏)
    r = reader.judge(clean, "", [], [])
    check_eq("judge: 全零 → ok", r["severity"], "ok")
    check("judge: warning/ok 的数据算可用", r["converged"] is True)

    # ② 真实样本(天大 105 流股):7 个模块全是 status=2 告警、UOSSTAT=1、PER_ERROR=1
    warnings = [{"name": f"M{n}", "status": 2, "message": "PHASE STABILITY CHECK"} for n in range(1, 8)]
    codes = dict(clean, UOSSTAT=1, PER_ERROR=1)
    r = reader.judge(codes, "", [], warnings)
    check_eq("judge: 7 个告警 + UOSSTAT/PER_ERROR=1 → warning(不是 error)",
             r["severity"], "warning")
    check_eq("judge: 告警设备全部列出", len(r["block_warnings"]), 7)

    # ③ 真实样本(#22,121 流股):T0202/T0205 是 status=1,CVSTAT=1
    errors = [
        {"name": "T0202", "status": 1, "message": "COLUMN DRIES UP OR COLUMN FLOWS VIOLATE BUILT-IN LIMITS"},
        {"name": "T0205", "status": 1, "message": "COMPONENT BALANCE EQUATIONS FAILED TO CONVERGE."},
    ]
    r = reader.judge(dict(clean, CVSTAT=1, UOSSTAT=1), "", [], errors + warnings)
    check_eq("judge: 模块 status=1 → error", r["severity"], "error")
    check("judge: error 的数据不算可用", r["converged"] is False)
    check_eq("judge: 报错设备点名", r["block_errors"], ["T0202", "T0205"])

    # ④ 只有 CVSTAT 非 0:收敛块自己出错,数据不可信
    r = reader.judge(dict(clean, CVSTAT=2), "", [], [])
    check_eq("judge: CVSTAT≠0 → error", r["severity"], "error")

    # ⑤ UOSSTAT 非 0 但一个模块都没定位到 —— 只能提示,不能判 error
    r = reader.judge(dict(clean, UOSSTAT=1), "", [], [])
    check_eq("judge: UOSSTAT=1 且无模块细节 → warning", r["severity"], "warning")

    # ⑥ 撕裂流超容差:只提示(Aspen 自己没报错时别替它下结论)
    tears = [{"stream": "T3", "variable": "INFO-VAR", "error": 1.656, "tolerance": 0.0393}]
    r = reader.judge(clean, "", tears, [])
    check_eq("judge: 撕裂流超容差 → warning(不是 error)", r["severity"], "warning")
    check("judge: 撕裂流理由里带原始数值",
          any("1.656" in x for x in r["reasons"]), str(r["reasons"]))

    # ⑦ Aspen 自己的汇总文本里有 completed with errors → 硬错误
    r = reader.judge(clean, "Calculation completed with errors", [], [])
    check_eq("judge: errtxt 含 completed with errors → error", r["severity"], "error")

    # ⑧ RSTAT / PER_ERROR 只记录、不参与判定
    r = reader.judge(dict(clean, RSTAT=1, PER_ERROR=1), "", [], [])
    check_eq("judge: RSTAT/PER_ERROR=1 但无模块问题 → ok", r["severity"], "ok")


def test_empty() -> None:
    """mark_empty():没 Run 过的模型必须判 empty,不能配绿勾。"""
    conv = {"severity": "ok", "converged": True, "reasons": []}
    r = reader.mark_empty(dict(conv, reasons=[]), coverage=0.0, run_id=None)
    check_eq("empty: 数值 0% 有值 → empty", r["severity"], "empty")
    check("empty: 不可用", r["converged"] is False)
    check("empty: 理由提示先 Run", any("Run" in x for x in r["reasons"]), str(r["reasons"]))

    done = {"severity": "ok", "converged": True, "reasons": []}
    r = reader.mark_empty(dict(done, reasons=[]), coverage=0.9, run_id="x")
    check_eq("empty: 数值齐全时不改判定", r["severity"], "ok")

    err = {"severity": "error", "converged": False, "reasons": ["CVSTAT=1"]}
    r = reader.mark_empty(dict(err, reasons=list(err["reasons"])), coverage=0.0, run_id=None)
    check_eq("empty: 已有硬错误时不覆盖", r["severity"], "error")


# ---------------------------------------------------------------- 焓平衡口径

def _streams(**h) -> list[dict]:
    return [{"name": k, "enthalpy_flow_kw": v} for k, v in h.items()]


def _blocks(*rows) -> list[dict]:
    return [dict(r) for r in rows]


def _conns(*rows) -> list[dict]:
    return [dict(block=b, direction=d, stream=s, side=side) for b, d, s, side in rows]


def test_balance() -> None:
    """balance.compute():按设备类型选参照值,不是一刀切 1%。"""
    # ① Heater:ΔH 应等于热负荷
    res = balance_rules.compute(
        _streams(feed=10.0, prod=110.0),
        _blocks({"name": "H1", "type": "Heater", "duty_kw": 100.0}),
        _conns(("H1", "in", "feed", None), ("H1", "out", "prod", None)),
    )
    check_eq("balance: Heater 一致 → ok", res["H1"]["balance_state"], balance_rules.OK)
    check_eq("balance: Heater 记录 ΔH", res["H1"]["dh_kw"], 100.0)
    check_eq("balance: Heater 参照类型", res["H1"]["ref_kind"], "duty")

    # ② 差 2 kW(超 1% 也超 0.5 kW)→ 超差
    res = balance_rules.compute(
        _streams(feed=10.0, prod=110.0),
        _blocks({"name": "H1", "type": "Heater", "duty_kw": 98.0}),
        _conns(("H1", "in", "feed", None), ("H1", "out", "prod", None)),
    )
    check_eq("balance: Heater 差 2 kW → 超差", res["H1"]["balance_state"], balance_rules.BAD)

    # ③ 差 0.4 kW:绝对容差 0.5 kW 内 → 通过(小负荷时不能按 1% 卡死)
    res = balance_rules.compute(
        _streams(feed=10.0, prod=110.0),
        _blocks({"name": "H1", "type": "Heater", "duty_kw": 99.6}),
        _conns(("H1", "in", "feed", None), ("H1", "out", "prod", None)),
    )
    check_eq("balance: Heater 差 0.4 kW → 通过", res["H1"]["balance_state"], balance_rules.OK)

    # ④ Heater 没有 QCALC → 无法校验,不许按绝热去猜
    res = balance_rules.compute(
        _streams(feed=10.0, prod=110.0),
        _blocks({"name": "H1", "type": "Heater"}),
        _conns(("H1", "in", "feed", None), ("H1", "out", "prod", None)),
    )
    check_eq("balance: Heater 缺热负荷 → 无法校验", res["H1"]["balance_state"], balance_rules.NO_DATA)

    # ⑤ HeatX 只校验冷侧(整体 ΔH 恒为 0,拿整体比必然 0 对不上 duty)
    res = balance_rules.compute(
        _streams(cin=10.0, cout=60.0, hin=200.0, hout=150.0),
        _blocks({"name": "E1", "type": "HeatX", "duty_kw": 50.0}),
        _conns(("E1", "in", "cin", "cold"), ("E1", "out", "cout", "cold"),
               ("E1", "in", "hin", "hot"), ("E1", "out", "hout", "hot")),
    )
    check_eq("balance: HeatX 冷侧一致 → ok", res["E1"]["balance_state"], balance_rules.OK)
    check_eq("balance: HeatX 参照类型", res["E1"]["ref_kind"], "hx_cold")

    # ⑥ HeatX 缺 duty 或缺分侧信息 → 无法校验
    res = balance_rules.compute(
        _streams(cin=10.0, cout=60.0),
        _blocks({"name": "E1", "type": "HeatX"}),
        _conns(("E1", "in", "cin", "cold"), ("E1", "out", "cout", "cold")),
    )
    check_eq("balance: HeatX 缺 HX_DUTY → 无法校验", res["E1"]["balance_state"], balance_rules.NO_DATA)
    res = balance_rules.compute(
        _streams(cin=10.0, cout=60.0),
        _blocks({"name": "E1", "type": "HeatX", "duty_kw": 50.0}),
        _conns(("E1", "in", "cin", None), ("E1", "out", "cout", None)),
    )
    check_eq("balance: HeatX 缺分侧信息 → 无法校验", res["E1"]["balance_state"], balance_rules.NO_DATA)

    # ⑦ 泵 / 压缩机:按比值区间判,差的是效率(压缩机实测 0.950)
    res = balance_rules.compute(
        _streams(feed=0.0, prod=95.0),
        _blocks({"name": "C1", "type": "Compr", "power_kw": 100.0}),
        _conns(("C1", "in", "feed", None), ("C1", "out", "prod", None)),
    )
    check_eq("balance: 压缩机 ΔH/功率 0.95 → ok", res["C1"]["balance_state"], balance_rules.OK)
    res = balance_rules.compute(
        _streams(feed=0.0, prod=60.0),
        _blocks({"name": "C1", "type": "Compr", "power_kw": 100.0}),
        _conns(("C1", "in", "feed", None), ("C1", "out", "prod", None)),
    )
    check_eq("balance: 压缩机 ΔH/功率 0.60 → 超差", res["C1"]["balance_state"], balance_rules.BAD)
    res = balance_rules.compute(
        _streams(feed=0.0, prod=95.0),
        _blocks({"name": "C1", "type": "Compr"}),
        _conns(("C1", "in", "feed", None), ("C1", "out", "prod", None)),
    )
    check_eq("balance: 压缩机缺轴功率 → 无法校验", res["C1"]["balance_state"], balance_rules.NO_DATA)

    # ⑧ 绝热设备:ΔH ≈ 0,容差按最大焓流的 0.5%(不是绝对 0.5 kW)
    res = balance_rules.compute(
        _streams(a=1000.0, b=500.0, c=1500.4),
        _blocks({"name": "M1", "type": "Mixer"}),
        _conns(("M1", "in", "a", None), ("M1", "in", "b", None), ("M1", "out", "c", None)),
    )
    check_eq("balance: Mixer 差 0.4 kW(20 万 kW 量级)→ 通过",
             res["M1"]["balance_state"], balance_rules.OK)
    res = balance_rules.compute(
        _streams(a=1000.0, b=500.0, c=1600.0),
        _blocks({"name": "M1", "type": "Mixer"}),
        _conns(("M1", "in", "a", None), ("M1", "in", "b", None), ("M1", "out", "c", None)),
    )
    check_eq("balance: Mixer 差 100 kW → 超差", res["M1"]["balance_state"], balance_rules.BAD)

    # ⑨ 塔 / 反应器不校验 —— 否则每座塔都会误报
    res = balance_rules.compute(
        _streams(feed=0.0, top=80.0, bottom=20.0),
        _blocks({"name": "T1", "type": "RadFrac", "duty_kw": 50.0}),
        _conns(("T1", "in", "feed", None), ("T1", "out", "top", None),
               ("T1", "out", "bottom", None)),
    )
    check_eq("balance: 塔 → 不校验", res["T1"]["balance_state"], balance_rules.SKIP)

    # ⑩ 缺焓值(能量流 / 没 Run 过)→ 无法校验,不是失败
    res = balance_rules.compute(
        _streams(feed=None, prod=110.0),
        _blocks({"name": "H1", "type": "Heater", "duty_kw": 100.0}),
        _conns(("H1", "in", "feed", None), ("H1", "out", "prod", None)),
    )
    check_eq("balance: 流股缺焓值 → 无法校验", res["H1"]["balance_state"], balance_rules.NO_DATA)

    # ⑪ 连接不全(读不到进口)→ 无法校验
    res = balance_rules.compute(
        _streams(prod=110.0),
        _blocks({"name": "H1", "type": "Heater", "duty_kw": 100.0}),
        _conns(("H1", "out", "prod", None)),
    )
    check_eq("balance: 连接不全 → 无法校验", res["H1"]["balance_state"], balance_rules.NO_DATA)

    # ⑫ summarize() 与 compute() 一致
    counts = balance_rules.summarize({
        "a": {"balance_state": balance_rules.OK},
        "b": {"balance_state": balance_rules.OK},
        "c": {"balance_state": balance_rules.SKIP},
    })
    check_eq("balance: summarize 计数", (counts["ok"], counts["skip"]), (2, 1))


# ---------------------------------------------------------------- 报错波及范围

def test_impact() -> None:
    """impact.build():报错设备 → 哪些流股的数据不能用。"""
    blocks = _blocks(
        {"name": "T0202", "status": 1},
        {"name": "T0205", "status": 1},
        {"name": "T0201", "status": 2},
        {"name": "H1", "status": 0},
    )
    conns = _conns(
        ("T0202", "in", "0101", None), ("T0202", "out", "0102", None),
        ("T0205", "in", "0102", None), ("T0205", "out", "0103", None),
        ("T0201", "in", "0104", None), ("T0201", "out", "0105", None),
        ("H1", "in", "0106", None), ("H1", "out", "0107", None),
    )
    got = impact_rules.build(conns, blocks)
    by_stream = {s["stream"]: s for s in got["streams"]}

    check_eq("impact: 报错设备点名", got["error_blocks"], ["T0202", "T0205"])
    check_eq("impact: 告警设备点名", got["warning_blocks"], ["T0201"])
    check_eq("impact: 受影响流股数 = 3 + 2", got["summary"]["total"], 5)
    check_eq("impact: 报错影响流股数", got["summary"]["error"], 3)
    check_eq("impact: 告警影响流股数", got["summary"]["warning"], 2)
    check("impact: 正常设备的流股不受影响",
          "0106" not in by_stream and "0107" not in by_stream, str(sorted(by_stream)))
    check_eq("impact: 同一条流股被两台报错设备夹住 → 合并成一条",
             [b["block"] for b in by_stream["0102"]["blocks"]], ["T0202", "T0205"])
    check_eq("impact: 报错优先于告警", by_stream["0101"]["severity"], "error")
    check_eq("impact: 告警流股标记", by_stream["0104"]["severity"], "warning")
    check("impact: 报错流股排在前面", got["streams"][0]["severity"] == "error")


# ---------------------------------------------------------------- 库内一致性

def test_db(db_path: str) -> None:
    """库里存的结论 == 现在规则重算的结论(否则要跑 repair_cases --apply)。"""
    from app.store import db

    conn = db.open_db(db_path)
    try:
        cases = conn.execute("SELECT * FROM cases ORDER BY id").fetchall()
        if not cases:
            print("  (库里还没有工况,跳过一致性检查)")
            return
        for case in cases:
            cid = case["id"]
            tag = f"db #{cid}"
            streams = [dict(r) for r in conn.execute(
                "SELECT name, enthalpy_flow_kw FROM streams WHERE case_id = ?", (cid,))]
            blocks = [dict(r) for r in conn.execute(
                "SELECT name, type, status, duty_kw, power_kw, dh_kw, ref_kw, ref_kind, "
                "balance_state FROM blocks WHERE case_id = ?", (cid,))]
            conns = [dict(r) for r in conn.execute(
                "SELECT block, direction, stream, side FROM connections WHERE case_id = ?", (cid,))]

            codes = json.loads(case["status_json"] or "{}")
            tears = json.loads(case["tear_json"] or "[]")
            verdict = reader.judge(codes, case["errtxt"], tears, blocks)

            if case["severity"] == "empty":
                check(f"{tag}: empty 工况不能被判成 error", verdict["severity"] != "error")
            else:
                check_eq(f"{tag}: severity 与规则重算一致", case["severity"], verdict["severity"])
            check_eq(f"{tag}: converged 与 severity 一致",
                     bool(case["converged"]), case["severity"] != "error")
            check_eq(f"{tag}: 报错设备名与 blocks 一致",
                     list(verdict["block_errors"]),
                     sorted(b["name"] for b in blocks if b["status"] == 1))

            # 焓平衡:存下来的逐台判定必须能重算出来(repair_cases 的口径)
            if case["severity"] != "empty":
                res = balance_rules.compute(streams, blocks, conns)
                bad = [(b["name"], b["balance_state"], res[b["name"]]["balance_state"])
                       for b in blocks
                       if b["balance_state"] != res[b["name"]]["balance_state"]]
                check(f"{tag}: 焓平衡逐台可重算", not bad, f"{len(bad)} 台不一致,例如 {bad[:3]}")

                summary = db.balance_summary(conn, cid)
                counted = sum(summary.values())
                check_eq(f"{tag}: 焓平衡四态台数 = 设备数", counted, len(blocks))

            # 拓扑:连接引用的流股必须都在流股表里(防止读漏/读串)
            names = {s["name"] for s in streams}
            ghosts = sorted({c["stream"] for c in conns} - names)
            check(f"{tag}: 连接里没有不存在的流股", not ghosts, f"多出 {ghosts[:5]}")
            check_eq(f"{tag}: 连接条数与库里一致", len(conns),
                     conn.execute("SELECT COUNT(*) AS n FROM connections WHERE case_id = ?",
                                  (cid,)).fetchone()["n"])
    finally:
        conn.close()


# ---------------------------------------------------------------- runner

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="判定 / 焓平衡 / 波及范围的回归自测")
    parser.add_argument("--db", default="", help="额外检查这个工况库(如 data/cases.db)")
    parser.add_argument("-v", "--verbose", action="store_true", help="逐条打印通过的用例")
    args = parser.parse_args(argv)

    print("aspen-insight 自测")
    for name, fn in (("判定规则 judge()", test_judge),
                     ("空模型 mark_empty()", test_empty),
                     ("焓平衡 balance.compute()", test_balance),
                     ("报错波及范围 impact.build()", test_impact)):
        print(f"\n[{name}]")
        fn()

    if args.db:
        print(f"\n[库内一致性 {args.db}]")
        try:
            test_db(args.db)
        except FileNotFoundError:
            print(f"  找不到 {args.db},跳过")

    if args.verbose:
        for label in PASSED:
            print(f"  ok   {label}")

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 条不通过(共 {len(PASSED) + len(FAILED)} 条):")
        for label, detail in FAILED:
            print(f"  - {label}")
            if detail:
                print(f"      {detail}")
        return 1
    print(f"✓ 全部通过:{len(PASSED)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
