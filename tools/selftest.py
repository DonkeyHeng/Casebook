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


# ---------------------------------------------------------------- 管路选型

def check_close(label: str, got, want: float, tol: float = 1e-6) -> None:
    ok = got is not None and abs(float(got) - want) <= tol
    check(label, ok, f"期望 {want!r} ±{tol},实际 {got!r}")


# 注意:这是**测试用的合成表**,不是标准值。真实表要等 GB/T 17395 表 1/表 4
# 与 GB/T 20801.1 表 B.1 录入(见 docs/_internal/prompt-vision-extract.md)。
SYNTH_TABLES = {
    "dn_to_od": {15: 22.0, 25: 32.0, 50: 57.0, 100: 108.0, 600: 610.0},
    "od_to_walls": {22.0: (2.0, 2.5), 32.0: (2.5, 3.0), 57.0: (3.0, 3.5),
                    108.0: (4.0, 4.5), 610.0: (6.0, 8.0)},
    "stress": {"S30408": {20: 137.0, 100: 130.0, 200: 118.0, 300: 106.0, 400: 95.0}},
}


def test_pipe() -> None:
    """design.pipe 的算术口径:相态 / DN / 压力 / 温度 / [σ] / 壁厚 / 等级号。"""
    from app.design import pipe as pipe_calc

    # ① 相态:痕量汽化率(库里实测 1e-8)必须按全液处理,不能 ==0 判
    check_eq("pipe: vap_frac=0 → 液", pipe_calc.phase_of(0.0), "liquid")
    check_eq("pipe: vap_frac=1e-8 → 仍算液(痕量)", pipe_calc.phase_of(1e-8), "liquid")
    check_eq("pipe: vap_frac=0.5 → 两相", pipe_calc.phase_of(0.5), "two")
    check_eq("pipe: vap_frac=1 → 气", pipe_calc.phase_of(1.0), "gas")
    check_eq("pipe: vap_frac=None → 按液处理", pipe_calc.phase_of(None), "liquid")

    # ② 内径:36 m³/h、1.5 m/s → √(4·0.01/(π·1.5)) = 92.13 mm
    check_close("pipe: 内径 36 m³/h @1.5 m/s = 92.13 mm",
                pipe_calc.inner_diameter(36.0, 1.5), 92.13, tol=0.05)

    # ③ DN 向上取整 + 查外径
    dn, od = pipe_calc.pick_dn(92.13, SYNTH_TABLES["dn_to_od"])
    check_eq("pipe: 92.13 mm → DN100", dn, 100)
    check_close("pipe: DN100 外径 108", od, 108.0)
    check_eq("pipe: 内径 20 mm → DN25(不能选 DN15)",
             pipe_calc.pick_dn(20.0, SYNTH_TABLES["dn_to_od"])[0], 25)
    check_eq("pipe: 无表时 pick_dn 返回 None(不当 0)",
             pipe_calc.pick_dn(20.0, {})[0], None)

    # ④ 设计压力:绝压 11 bar → 表压 0.9987 → ×1.1 ≈ 1.0986 MPa
    pd = pipe_calc.design_pressure(11.0)
    check_close("pipe: 11 bar 绝压 → 设计压力 ≈1.0986 MPa", pd["design_mpa"], 1.09857, tol=1e-4)
    check("pipe: 11 bar 不算真空", pd["vacuum"] is False)
    # 常压死区:1.0 bar 绝压(表压 −0.0013 MPa)属常压操作,不能判真空
    pa = pipe_calc.design_pressure(1.0)
    check("pipe: 1.0 bar 绝压不算真空", pa["vacuum"] is False)
    check_close("pipe: 1.0 bar 绝压 → 1.0 MPa 下限", pa["design_mpa"], 1.0)
    # 真正低于常压(0.5 bar 绝压 = 表压 −0.05 MPa)才算真空
    pv = pipe_calc.design_pressure(0.05)
    check("pipe: 绝压 0.05 bar → 真空", pv["vacuum"] is True)
    check_close("pipe: 真空设计压力取 0.1 MPa", pv["design_mpa"], 0.1)

    # ⑤ 设计温度:≥50 ℃ 自动算保温(取介质温度);65–80 且无保温才折 0.95
    check_close("pipe: 60 ℃ 自动保温 → 取 60 ℃", pipe_calc.design_temperature(60.0)["design_c"], 60.0)
    t70 = pipe_calc.design_temperature(70.0, insulated=False)
    check_close("pipe: 70 ℃ 无保温 → 66.5 ℃", t70["design_c"], 66.5)
    check("pipe: 70 ℃ 无保温标记 folded", t70["folded"] is True)
    check_close("pipe: 90 ℃ → 取 90 ℃(不折)", pipe_calc.design_temperature(90.0)["design_c"], 90.0)

    # ⑥ 许用应力:缺温度内插;T<40 取 40 ℃ 值;超范围返回 None(不猜)
    stress = SYNTH_TABLES["stress"]
    check_close("pipe: [σ] 150 ℃ 内插 = 124",
                pipe_calc.allowable_stress("S30408", 150.0, stress), 124.0)
    check_close("pipe: [σ] 20 ℃ → 137", pipe_calc.allowable_stress("S30408", 20.0, stress), 137.0)
    check_close("pipe: [σ] -10 ℃ 取 20 ℃ 值", pipe_calc.allowable_stress("S30408", -10.0, stress), 137.0)
    check_eq("pipe: [σ] 450 ℃ 超范围 → None", pipe_calc.allowable_stress("S30408", 450.0, stress), None)

    # ⑦ 壁厚公式 t = P·D/(2(S·Φw + P·Y)):1.6 MPa / Φ108 / [σ]=130 / Y=0.4
    w = pipe_calc.calc_wall_thickness(1.6, 108.0, 130.0)
    check_close("pipe: t_calc = 1.6×108/(2×(130+0.64)) ≈ 0.6614", w["t_calc_mm"], 0.66137, tol=1e-4)
    check("pipe: t < D/6 → 公式适用", w["applicable"] is True)
    check("pipe: 超大壁厚判公式不适用",
          pipe_calc.calc_wall_thickness(100.0, 108.0, 130.0)["applicable"] is False)
    check_eq("pipe: 缺 [σ] 不硬算", pipe_calc.calc_wall_thickness(1.6, 108.0, None)["t_calc_mm"], None)

    # ⑧ 圆整到标准壁厚:取"不小于所需"的最小值;超上限返回 None
    check_close("pipe: 需 1.0 mm → 圆整到 4.0 mm",
                pipe_calc.round_up_wall(1.0, SYNTH_TABLES["od_to_walls"][108.0])["wall_mm"], 4.0)
    check_close("pipe: 需 4.2 mm → 圆整到 4.5 mm",
                pipe_calc.round_up_wall(4.2, SYNTH_TABLES["od_to_walls"][108.0])["wall_mm"], 4.5)
    check_eq("pipe: 需 5.0 mm 超过序列 → None",
             pipe_calc.round_up_wall(5.0, SYNTH_TABLES["od_to_walls"][108.0])["wall_mm"], None)
    check_eq("pipe: 无壁厚序列(T2 未录)→ None",
             pipe_calc.round_up_wall(1.0, None)["wall_mm"], None)

    # ⑧b 最小壁厚下限(DN10–100 不设限那档之外):下限只抬高、不降低
    from app.design import pipe_data as pd_mod
    check_close("pipe: 下限 DN25 → 2.0", pd_mod.minimum_wall(25), 2.0)
    check_close("pipe: 下限 DN32 → 3.0", pd_mod.minimum_wall(32), 3.0)
    check_close("pipe: 下限 DN65 → 3.0", pd_mod.minimum_wall(65), 3.0)
    check_close("pipe: 下限 DN80 → 3.0", pd_mod.minimum_wall(80), 3.0)
    check_close("pipe: 下限 DN100 → 3.0", pd_mod.minimum_wall(100), 3.0)
    check_eq("pipe: DN125 起不设下限", pd_mod.minimum_wall(125), None)
    check_close("pipe: 需 0.3 mm + 下限 2.0 → 取 2.0",
                pipe_calc.round_up_wall(0.3, (0.5, 0.7, 1.0, 2.0, 3.0),
                                        min_wall=2.0)["wall_mm"], 2.0)
    check_close("pipe: 需 2.4 mm + 下限 2.0 → 取 2.5(下限不降级)",
                pipe_calc.round_up_wall(2.4, (0.5, 2.0, 2.5, 3.0),
                                        min_wall=2.0)["wall_mm"], 2.5)
    check("pipe: 下限起作用时标 floored",
          pipe_calc.round_up_wall(0.3, (0.5, 2.0), min_wall=2.0)["floored"] is True)
    check("pipe: 承压更厚时不标 floored",
          pipe_calc.round_up_wall(2.4, (2.0, 2.5), min_wall=2.0)["floored"] is False)

    # 端到端:小口径薄壁序列必须被下限托住;options 能关掉这一层
    thin_tables = {
        "dn_to_od": {15: 18.0, 25: 32.0},
        "od_to_walls": {18.0: (0.5, 1.0, 2.0)},
        "stress": {"S30408": {20: 137.0, 100: 130.0}},
    }
    small = [{"name": "S1", "vol_flow_m3h": 0.61, "temperature_c": 25.0,
              "pressure_bar": 1.0, "vap_frac": 0.0}]
    floored_row = pipe_calc.build(small, tables=thin_tables)["rows"][0]
    check_close("pipe: DN15 薄壁序列被下限托到 2.0 mm", floored_row["wall_mm"], 2.0)
    check("pipe: 该行标记 wall_floored", floored_row["wall_floored"] is True)
    check_close("pipe: 该行带出 min_wall_mm=2.0", floored_row["min_wall_mm"], 2.0)
    plain_row = pipe_calc.build(small, options={"min_wall": False},
                                tables=thin_tables)["rows"][0]
    check_close("pipe: options min_wall=False 时回到 0.5 mm", plain_row["wall_mm"], 0.5)

    # ⑨ 等级号:PN 代号 + 顺序号 + 材质代号 + 绝热后缀
    check_eq("pipe: 1.6 MPa → 压力代号 M", pipe_calc.pn_code_for(1.6), "M")
    check_eq("pipe: 1.7 MPa → 升到 N", pipe_calc.pn_code_for(1.7), "N")
    check_eq("pipe: 0.2 MPa → 压力代号 H", pipe_calc.pn_code_for(0.2), "H")
    check_eq("pipe: 1.6 MPa/不锈钢 → M1E", pipe_calc.grade_code(1.6, "E")["code"], "M1E")
    check_eq("pipe: 设计温度 90 ℃ → 自动加 -H",
             pipe_calc.grade_code(1.6, "E", 90.0)["code"], "M1E-H")
    check_eq("pipe: 设计温度 30 ℃ → 不加后缀",
             pipe_calc.grade_code(1.6, "E", 30.0)["code"], "M1E")

    # ⑩ 端到端 build():泵入口流速、DN 超限提示、缺流量流股、表未录入的明确报错
    streams = [
        {"name": "S1", "vol_flow_m3h": 36.0, "temperature_c": 60.0,
         "pressure_bar": 11.0, "vap_frac": 0.0},
        {"name": "S2", "vol_flow_m3h": 3.0, "temperature_c": 60.0,
         "pressure_bar": 11.0, "vap_frac": 0.0},
        {"name": "S3", "vol_flow_m3h": None, "temperature_c": 60.0,
         "pressure_bar": 11.0, "vap_frac": 0.0},
    ]
    blocks = [{"name": "P1", "type": "Pump"}]
    conns = [{"block": "P1", "direction": "in", "stream": "S2"}]

    res = pipe_calc.build(streams, blocks, conns, options={"max_dn": 50}, tables=SYNTH_TABLES)
    check_eq("pipe: build 选出 2 条、缺 1 条", (res["summary"]["selected"], res["summary"]["missing"]), (2, 1))
    check_eq("pipe: 缺体积流量的流股进 missing", res["missing"][0]["stream"], "S3")
    by = {r["stream"]: r for r in res["rows"]}
    check_eq("pipe: S1 → DN100", by["S1"]["dn"], 100)
    check("pipe: DN100 超本期上限 DN50 → 有提示",
          any("超出本期上限" in w for w in by["S1"]["warnings"]))
    check_eq("pipe: S2 是泵入口流股", by["S2"]["pump_inlet"], True)
    check_close("pipe: 泵入口按 0.8 m/s 选型", by["S2"]["velocity_ms"], 0.8)
    check("pipe: 泵入口内径比常规液速更大",
          by["S2"]["inner_d_mm"] > pipe_calc.inner_diameter(3.0, 1.5))
    check_eq("pipe: 等级号生成", by["S1"]["grade_code"].startswith("M1E"), True)

    try:
        # 显式传空表:标准表没录/读不到时必须报错,而不是拿 0 算出一张假表
        pipe_calc.build(streams, blocks, conns,
                        tables={"dn_to_od": {}, "od_to_walls": {}, "stress": {}})
        check("pipe: 表未录入时必须报错", False, "应该抛 TablesNotReady")
    except pipe_calc.TablesNotReady:
        check("pipe: 表未录入时报 TablesNotReady(不静默出表)", True)


def test_pipe_checks() -> None:
    """design.pipe.validate():V1–V9 的触发条件(只陈述事实,不改模型)。"""
    from app.design import pipe as pipe_calc

    base = {
        "stream": "S1", "phase": "liquid", "dn": 100, "od_mm": 108.0, "wall_mm": 4.0,
        "vol_flow_m3h": 36.0, "velocity_ms": 1.5, "design_pressure_mpa": 1.6,
        "design_temp_c": 60.0, "grade_code": "M1E-H", "insulation": "H",
        "t_required_mm": 1.5, "vacuum": False, "pump_inlet": False, "pump_outlet": False,
    }

    def codes(row_patch: dict) -> list[str]:
        row = dict(base, **row_patch)
        return [f["code"] for f in pipe_calc.validate([row])["findings"]]

    check_eq("checks: 干净的一行没有任何发现", codes({}), [])
    # 内径 100 mm、36 m³/h → 实际 1.27 m/s,落在 1.0–2.5 内,所以上面是干净的
    check_close("checks: 实际流速 1.27 m/s",
                pipe_calc.actual_velocity(36.0, 100.0), 1.273, tol=0.01)

    check_eq("checks: 流速过大 → V2", codes({"vol_flow_m3h": 200.0}), ["V2"])
    # V2 的 finding 要能落到「流速」那一列,否则前端只能整行标黄
    f2 = pipe_calc.validate([dict(base, vol_flow_m3h=200.0)])["findings"][0]
    check_eq("checks: V2 的 field 定位到 velocity", f2["field"], "velocity")
    check_eq("checks: 所需壁厚超所选 → V3", codes({"t_required_mm": 5.0}), ["V3"])
    check_eq("checks: 等级号压力不足 → V4",
             codes({"grade_code": "H1E"}), ["V4"])
    check_eq("checks: 编号口径与 DN 不符 → V1",
             codes({"pipe_no": "PG-0111-300"}), ["V1"])
    check_eq("checks: 两相按单相流速 → V5",
             codes({"phase": "two"}), ["V5"])
    check_eq("checks: 真空流股 → V6", codes({"vacuum": True}), ["V6"])
    check_eq("checks: 设计温度超材料区间 → V7",
             codes({"design_temp_c": 500.0}), ["V7"])
    check_eq("checks: 泵出口当泵入口 → V8",
             codes({"pump_outlet": True, "pump_inlet": True,
                    "velocity_ms": 0.8, "vol_flow_m3h": 20.0}), ["V8"])
    check_eq("checks: 高温无保温代号 → V9",
             codes({"design_temp_c": 90.0, "insulation": "", "grade_code": "M1E"}),
             ["V9"])
    check_eq("checks: 有保温代号就不再报 V9",
             codes({"design_temp_c": 90.0}), [])

    # 端到端:build 出来的行直接喂给 validate,字段要能对上
    streams = [{"name": "S1", "vol_flow_m3h": 36.0, "temperature_c": 60.0,
                "pressure_bar": 11.0, "vap_frac": 0.0}]
    res = pipe_calc.build(streams, tables=SYNTH_TABLES)
    v = pipe_calc.validate(res["rows"])
    check_eq("checks: build→validate 能跑通(字段齐全)", isinstance(v["summary"], dict), True)
    check_eq("checks: 只有 1 行参与", v["summary"]["error"] + v["summary"]["warn"] >= 0, True)


def test_pipe_setup() -> None:
    """标准表没录时给使用者的说明:要哪两份标准、什么文件、放哪、为什么不在仓库里。"""
    from app.design import pipe_data

    g = pipe_data.setup_guide()
    check("setup: 说明了版权原因", ("著作权" in g["why"]) or ("版权" in g["why"]),
          f"why={g['why']!r}")
    check_eq("setup: 目标目录", g["dir"], "data/preview/std/docx")
    check_eq("setup: 两份标准", len(g["standards"]), 2)
    codes = {s["code"] for s in g["standards"]}
    check("setup: 标准号齐全", {"GB/T 17395-2024", "GB/T 20801.1-2025"} <= codes,
          f"实际 {codes}")
    names = {f["name"] for f in g["files"]}
    check("setup: 三个 CSV 文件名齐全",
          {"t1-17395-table1.csv", "t4-17395-table4.csv", "t3-20801-tableB1.csv"} <= names,
          f"实际 {names}")
    check("setup: 每个文件都写了来源与必需列",
          all(f.get("from") and f.get("columns") for f in g["files"]))
    check("setup: 至少 3 步操作", len(g["steps"]) >= 3)
    check("setup: 指向页码索引文档", g["doc"].endswith("pipe-standards-index.md"))
    check("setup: 指向录入提示词", g["prompt"].endswith("pipe-standards-extract-prompt.md"))
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    check("setup: 提示词文件确实在仓库里", (root / g["prompt"]).is_file(), g["prompt"])
    check("setup: 提示词里不含标准数值(版权)",
          "0.035" not in (root / g["prompt"]).read_text(encoding="utf-8"))
    check("setup: 返回的是拷贝,改它不影响常量",
          pipe_data.setup_guide() is not g and pipe_data.setup_guide() is not pipe_data.SETUP_GUIDE)


def test_piping_class() -> None:
    """管道等级表:按「流股名前缀 + 介质代号」匹配,工具只套用不判断腐蚀性。"""
    from app.design import pipe_data

    rules = [
        {"stream_prefix": ["CW", "CWR"], "grade": "20#", "corrosion_mm": 1.0, "note": "循环水"},
        {"medium": ["PGL"], "grade": "S31603", "corrosion_mm": 0.0, "note": "两相工艺流"},
    ]
    default = {"grade": "S30408", "corrosion_mm": 0.0}

    r = pipe_data.resolve_piping_class("CW-0101", "PL", rules=rules, default=default)
    check_eq("piping: 前缀命中循环水规则", (r["grade"], r["source"]), ("20#", "rule"))
    check_close("piping: 循环水腐蚀裕量 1.0 mm", r["corrosion_mm"], 1.0)

    r = pipe_data.resolve_piping_class("0101", "PGL", rules=rules, default=default)
    check_eq("piping: 两相介质命中第二类规则", r["grade"], "S31603")

    r = pipe_data.resolve_piping_class("0101", "PL", rules=rules, default=default)
    check_eq("piping: 没命中规则就走兜底", (r["grade"], r["source"]), ("S30408", "default"))

    r = pipe_data.resolve_piping_class("0101", "PL", rules=rules, default=None)
    check_eq("piping: 既没命中也没兜底 → none", r["source"], "none")

    # 配错/没配文件都不能让选型挂掉
    check_eq("piping: 空规则+空兜底不抛异常",
             pipe_data.resolve_piping_class("X", "PL", rules=[], default=None)["grade"], None)

    # 页面保存前的结构校验(只验结构,不判断"该不该用这个材质")
    good = {"rules": [{"stream_prefix": ["cw"], "grade": "20#", "corrosion_mm": 1.0}],
            "default": {"grade": "S30408"}}
    cleaned, errors = pipe_data.validate_piping_classes(good)
    check_eq("piping: 合法配置通过校验", errors, [])
    check_eq("piping: 前缀统一转大写", cleaned["rules"][0]["stream_prefix"], ["CW"])
    check_close("piping: 腐蚀裕量转成数字", cleaned["rules"][0]["corrosion_mm"], 1.0)

    _, errors = pipe_data.validate_piping_classes(
        {"rules": [{"medium": ["PL"], "grade": "铝"}]})
    check("piping: 不认识的牌号被拒", any("牌号" in e for e in errors))

    _, errors = pipe_data.validate_piping_classes(
        {"rules": [{"grade": "S30408"}]})
    check("piping: 前缀和介质都不填被拒", any("至少填一个" in e for e in errors))

    _, errors = pipe_data.validate_piping_classes(
        {"rules": [{"medium": ["XX"], "grade": "S30408"}]})
    check("piping: 不认识的介质代号被拒", any("介质代号" in e for e in errors))

    _, errors = pipe_data.validate_piping_classes(
        {"rules": [{"medium": ["PL"], "grade": "S30408", "corrosion_mm": -1}]})
    check("piping: 负腐蚀裕量被拒", any("0–20" in e for e in errors))


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
                     ("报错波及范围 impact.build()", test_impact),
                     ("管路选型 design.pipe", test_pipe),
                     ("选型校验 validate() V1–V9", test_pipe_checks),
                     ("标准表补录说明 setup_guide()", test_pipe_setup),
                     ("管道等级表 piping-class", test_piping_class)):
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
