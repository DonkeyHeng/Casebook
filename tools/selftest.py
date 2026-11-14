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
import shutil
import sys
from pathlib import Path

from app.aspen import balance as balance_rules
from app.aspen import reader
from app.store import db
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


def test_std_import() -> None:
    """标准表导入:文件名白名单、编码/必需列/行数,以及"先试加载一遍再落盘"。

    自测一律往临时目录里导,不碰 data/preview/std/docx/ 下正在用的表。
    """
    import shutil
    from pathlib import Path

    from app.design import pipe_data

    # 临时目录放在 data/preview/ 下(已被 .gitignore 覆盖):系统 Temp 目录在沙箱里
    # 清理时会 chmod 失败,自测得自己 rmtree
    tmp_root = Path(__file__).resolve().parents[1] / "data" / "preview" / "_selftest-std"
    shutil.rmtree(tmp_root, ignore_errors=True)
    tmp_root.mkdir(parents=True, exist_ok=True)

    names = [s["name"] for s in pipe_data.STD_FILES]
    check_eq("std: 三个 CSV 文件名", names, ["t1-17395-table1.csv", "t4-17395-table4.csv",
                                            "t3-20801-tableB1.csv"])
    check("std: 每张表都写了来源与必需列",
          all(s.get("from") and s.get("columns") for s in pipe_data.STD_FILES))
    check("std: 白名单外的文件名不收",
          pipe_data.std_file_spec("t1-17395-table1.csv") is not None
          and pipe_data.std_file_spec("whatever.csv") is None)

    spec = pipe_data.std_file_spec("t1-17395-table1.csv")
    errs, _ = pipe_data._validate_std_text(spec, "   ")
    check_eq("std: 空文件被拒", errs, ["文件是空的"])
    good = "D_mm,S_mm\n" + "\n".join(f"{d},2.5" for d in range(6, 30))
    errs, _ = pipe_data._validate_std_text(spec, good.replace("D_mm", "D"))
    check("std: 缺列被拒", any("缺列" in e for e in errs), f"{errs}")
    errs, _ = pipe_data._validate_std_text(spec, "D_mm,S_mm\n10,2.5\n")
    check("std: 行数太少被拒", any("至少" in e for e in errs), f"{errs}")
    errs, _ = pipe_data._validate_std_text(spec, good.replace("2.5", "两毫米"))
    check("std: 读不出的壁厚被拒", any("外径" in e for e in errs), f"{errs}")
    errs, info = pipe_data._validate_std_text(spec, good)
    check_eq("std: 合规的尺寸表通过", errs, [])
    check_eq("std: 统计到 24 个外径", info.get("od_count"), 24)
    errs, _ = pipe_data._validate_std_text(spec, good + "\ufffd")
    check("std: 乱码(非 UTF-8)被拒", any("UTF-8" in e for e in errs), f"{errs}")

    base = tmp_root / "a" / "docx"
    ok, errs, status = pipe_data.import_standards(
        {"files": {"t1-17395-table1.csv": good}}, target_dir=base, reload=False)
    check_eq("std: 单张表能导入", (ok, errs), (True, []))
    check("std: 文件写到了目标目录", (base / "t1-17395-table1.csv").is_file())
    rows = [f["rows"] for f in status["files"] if f["name"] == "t1-17395-table1.csv"]
    check_eq("std: 导入后状态里是 24 行", rows, [24])

    # 一个文件不合格 → 一张都不写(别留半份)
    ok, errs, _ = pipe_data.import_standards(
        {"files": {"t1-17395-table1.csv": good,
                   "t4-17395-table4.csv": "foo,bar\n1,2\n"}},
        target_dir=base, reload=False)
    check_eq("std: 有一个不合格就整单拒绝", ok, False)
    check("std: 错误里点名是哪个文件",
          any("t4-17395-table4.csv" in e for e in errs), f"{errs}")
    check("std: 被拒时没写进任何文件", not (base / "t4-17395-table4.csv").exists())

    ok, errs, _ = pipe_data.import_standards({"files": {}}, target_dir=base, reload=False)
    check_eq("std: 没选文件时明确报错", ok, False)

    # 表 B.1 还要能对上本项目用到的牌号,否则整个选型还是跑不起来
    real = Path(__file__).resolve().parents[1] / "data" / "preview" / "std" / "docx"
    stress_path = real / "t3-20801-tableB1.csv"
    base = tmp_root / "b" / "docx"
    if stress_path.is_file():
        ok, errs, _ = pipe_data.import_standards(
            {"files": {"t3-20801-tableB1.csv": stress_path.read_text(encoding="utf-8-sig")}},
            target_dir=base, reload=False)
        check_eq("std: 真表 B.1 导入通过", (ok, errs), (True, []))
    else:
        print("  (本机没有表 B.1,跳过真表导入的检查)")
    ok, errs, _ = pipe_data.import_standards(
        {"files": {"t3-20801-tableB1.csv": "标准,牌号,许用应力_20\nGB/T 9999,XYZ,100\n"}},
        target_dir=base, reload=False)
    check_eq("std: 牌号对不上的表 B.1 被拒", ok, False)
    check("std: 拒绝理由写清要哪些牌号",
          any("牌号" in e for e in errs), f"{errs}")

    # reload_standards:按目录重读;目录里没表就是空(页面显示未录),不是异常
    old_count = len(pipe_data.OD_TO_WALLS)
    try:
        pipe_data.reload_standards(tmp_root / "no-such-std-dir")
        check_eq("std: 目录里没表时重载成空", len(pipe_data.OD_TO_WALLS), 0)
    finally:
        pipe_data.reload_standards()
    check_eq("std: 重载后原来的表还在", len(pipe_data.OD_TO_WALLS), old_count)
    shutil.rmtree(tmp_root, ignore_errors=True)


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


def test_install_sample() -> None:
    """内置示例库:能装进空库;覆盖前把原库备份成 .bak。"""
    from app.api import server

    root = Path(__file__).resolve().parents[1]
    tmp = root / "data" / "preview" / "_selftest-sample"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        db_path = tmp / "cases.db"
        ok, msg = server._install_sample(str(db_path), alert=False)
        check_eq("示例:装库成功", ok, True)
        check("示例:提示里有流股数", "121" in msg)

        conn = db.open_db(str(db_path))
        try:
            cases = db.list_cases(conn)
        finally:
            conn.close()
        check_eq("示例:工况条数", len(cases), 1)
        check_eq("示例:工况 id", cases[0]["id"], 23)
        check_eq("示例:流股数", cases[0]["stream_count"], 121)

        ok2, msg2 = server._install_sample(str(db_path), alert=False)
        check_eq("示例:重复装载也成功", ok2, True)
        check("示例:覆盖前已备份", (tmp / "cases.db.bak").is_file())
        check("示例:提示里写了备份", "备份" in msg2)
        conn = db.open_db(str(db_path))
        try:
            check_eq("示例:覆盖后仍是同一条工况", len(db.list_cases(conn)), 1)
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 设备家族

def test_block_family() -> None:
    """block_family:把几十种 Aspen 类型收敛成 7 个页签,未知类型不能丢。"""
    from app.design import block_family as fam

    check_eq("家族:RadFrac → 塔", fam.family_of("RadFrac"), "column")
    check_eq("家族:Pump → 泵(独立一族)", fam.family_of("Pump"), "pump")
    check_eq("家族:Turbine → 流体输送", fam.family_of("Turbine"), "move")
    check_eq("家族:未知类型 → 其他", fam.family_of("Welding"), "other")
    check_eq("家族:空类型 → 其他", fam.family_of(None), "other")
    check_eq("家族:大小写不敏感", fam.family_of("radfrac"), "column")

    blocks = [{"type": "Pump"}, {"type": "Pump"}, {"type": "RadFrac"}]
    check_eq("默认家族:有塔落塔", fam.pick_default(blocks), "column")
    check_eq("默认家族:无塔落第一个非空",
             fam.pick_default([{"type": "Pump"}, {"type": "Heater"}]), "heat")
    check_eq("默认家族:空清单回落塔", fam.pick_default([]), "column")
    check_eq("家族计数", fam.counts(blocks), {"pump": 2, "column": 1})
    check_eq("家族表顺序",
             [f["key"] for f in fam.describe()],
             ["column", "heat", "flash", "react", "pump", "move", "mix", "other"])


# ---------------------------------------------------------------- 泵

def test_pump() -> None:
    """design.pump:密度换算、扬程、NPSHₐ、电机档位。口径见 docs/aspen-field-map.md §13。"""
    from app.design import pump

    check_eq("泵:摩尔密度 × 分子量 → 质量密度",
             round(pump.mass_density_kgm3(47.86, 19.79), 1), 947.1)
    check_eq("泵:缺分子量时密度为 None", pump.mass_density_kgm3(47.86, None), None)
    check_eq("泵:缺摩尔密度时密度为 None", pump.mass_density_kgm3(None, 19.79), None)

    head = pump.head_from_dp(1.785, 2.10, 947.0)
    check("泵:压差反算扬程 ≈ 3.39 m", head is not None and abs(head - 3.39) < 0.02,
          f"实际 {head}")
    check("泵:反向工况扬程为负", pump.head_from_dp(2.0, 1.0, 900.0) < 0)
    check_eq("泵:缺密度时扬程为 None", pump.head_from_dp(1.0, 2.0, None), None)

    npsh = pump.npsh_available_m(0.5, 0.2, 950.0)
    check("泵:NPSHₐ ≈ 3.22 m", npsh is not None and abs(npsh - 3.22) < 0.02, f"实际 {npsh}")
    check_eq("泵:缺 p_v 时 NPSHₐ 为 None", pump.npsh_available_m(0.5, None, 950.0), None)

    # p_v 闸门:0 / 负数 / 非数字都不是有效蒸气压(防止把目录标志位 1 当读数)
    check_eq("泵:p_v 为 0 视为无效", pump.sanitize_pv_bar(0), None)
    check_eq("泵:p_v 为负视为无效", pump.sanitize_pv_bar(-1.0), None)
    check_eq("泵:p_v 非数字视为无效", pump.sanitize_pv_bar("abc"), None)
    check_eq("泵:p_v 为 NaN 视为无效", pump.sanitize_pv_bar(float("nan")), None)
    check("泵:p_v 正常值原样返回", pump.sanitize_pv_bar(0.5338) == 0.5338)

    import math
    zero = pump.npsh_available_m(1.01325, 1.01325, 900.0)
    check("泵:泡点进料 NPSHₐ 归零且不带负号",
          zero == 0.0 and math.copysign(1.0, zero) > 0, f"实际 {zero!r}")
    tiny = pump.npsh_available_m(1.0, 1.000001, 1000.0)
    check("泵:极小的负 NPSHₐ 按显示精度归零",
          tiny == 0.0 and math.copysign(1.0, tiny) > 0, f"实际 {tiny!r}")

    kw, note = pump.pick_motor(131.466)
    check_eq("泵:131.47 kW 轴功率 → 160 kW 档", kw, 160.0)
    check("泵:推导说明里带档位数字", "160" in note, note)
    check_eq("泵:零功率不选电机", pump.pick_motor(0.0)[0], None)
    check_eq("泵:负功率(回收功)不选电机", pump.pick_motor(-3.4)[0], None)
    check_eq("泵:缺轴功率", pump.pick_motor(None)[0], None)
    check("泵:超出档位表给出说明",
          pump.pick_motor(1000.0)[0] is None and "超出" in pump.pick_motor(1000.0)[1])


def test_pump_pv_read() -> None:
    r"""reader.read_stream_vapor_pressure():p_v 的取法,不需要 Aspen。

    背景(2026-11-14 实测):`Output\PL` 是**目录节点**,读出来是标志位 `1` 且带 bar 单位;
    混合物泡点压力在 `STRM_UPP\PBUB\MIXED\TOTAL`。所以规则是:
    有子节点的节点跳过 → 必须有单位 → 换算成 bar → 必须 > 0。
    """

    class _Kid:
        def __init__(self, name):
            self.Name = name

    class _Elements:
        def __init__(self, kids):
            self._kids = kids
            self.Count = len(kids)

        def Item(self, i):
            return self._kids[i]

    class _Node:
        def __init__(self, value=None, unit="", kids=()):
            self.Value = value
            self.UnitString = unit
            self.Elements = _Elements(list(kids))

    class _Session:
        def __init__(self, nodes):
            self._nodes = nodes

        def find(self, path):
            return self._nodes.get(path)

    base = r"\Data\Streams\0133\Output"
    pv_path = base + r"\STRM_UPP\PBUB\MIXED\TOTAL"

    got = reader.read_stream_vapor_pressure(_Session({pv_path: _Node(0.53383137, "bar")}), "0133")
    check("泵 p_v:泡点压力叶子读出 0.5338 bar",
          got is not None and abs(got - 0.5338) < 1e-3, f"实际 {got}")

    got = reader.read_stream_vapor_pressure(
        _Session({base + r"\PL": _Node(1, "bar", [_Kid("MIXED")])}), "0133")
    check_eq("泵 p_v:目录节点(PL 值恒 1)不当读数", got, None)

    got = reader.read_stream_vapor_pressure(_Session({pv_path: _Node(0.0, "bar")}), "0133")
    check_eq("泵 p_v:读数为 0 视为没有", got, None)

    got = reader.read_stream_vapor_pressure(_Session({pv_path: _Node(50.0, "kpa")}), "0133")
    check("泵 p_v:单位按 pressure 换算(50 kPa → 0.5 bar)",
          got is not None and abs(got - 0.5) < 1e-9, f"实际 {got}")

    got = reader.read_stream_vapor_pressure(_Session({pv_path: _Node(0.5, "")}), "0133")
    check_eq("泵 p_v:单位认不出时留空", got, None)


def test_block_columns() -> None:
    """blocks 表:新库建表必须带第二期泵列(老库补列在同一条 _BLOCK_ADDED_COLUMNS 上)。"""
    from pathlib import Path

    from app.store import db

    need = {"head_m", "flow_m3h", "pump_eff", "p_in_bar", "p_out_bar",
            "rho_kgm3", "pump_vap_frac", "motor_kw"}
    # 不用 TemporaryDirectory:这台机器上沙箱化后的系统临时目录 chmod 会被拒,
    # 清理阶段会抛 PermissionError。库文件放项目内的 preview 目录(gitignore 覆盖)。
    scratch = Path("data/preview/_selftest-block-columns.db")
    scratch.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = db.open_db(str(scratch))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(blocks)")}
        conn.close()
    finally:
        # Windows 上库文件句柄偶尔还没释放,删不掉不影响结论(目录已 gitignore)
        try:
            scratch.unlink(missing_ok=True)
        except OSError:
            pass
    check("设备表:新库带泵列", need <= cols, f"缺 {sorted(need - cols)}")
    added = {name for name, _ddl in db._BLOCK_ADDED_COLUMNS}
    check("设备表:老库补列清单覆盖泵列", need <= added, f"缺 {sorted(need - added)}")


def test_pump_pv_store() -> None:
    """pv_bar / pv_source:逐台存、读得回、清得掉,来源分得清。"""
    from pathlib import Path

    from app.store import db

    scratch = Path("data/preview/_selftest-pv.db")
    scratch.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = db.open_db(str(scratch))
        case = {
            "model": {"path": "x.apw", "name": "x.apw", "read_at": "2026-11-08 00:00:00"},
            "convergence": {}, "streams": [], "columns": [], "connections": [],
            "blocks": [{"name": "P1", "type": "Pump", "power_kw": 131.466,
                        "head_m": 41.5, "flow_m3h": 8.5, "pump_eff": 0.4,
                        "p_in_bar": 1.8, "p_out_bar": 2.1, "rho_kgm3": 947.0,
                        "pump_vap_frac": 0.0, "motor_kw": 160.0,
                        "status": 0, "message": "", "raw": {}, "balance": {}},
                       {"name": "P2", "type": "Pump", "status": 0, "message": "",
                        "pv_bar": 0.5338, "pv_source": "aspen", "raw": {}, "balance": {}}],
            "stats": {"stream_count": 0, "block_count": 1, "column_count": 0,
                      "connection_count": 0, "elapsed_sec": 0},
            "unit_gaps": {},
        }
        cid = db.save_case(conn, case)
        check_eq("pv:初始为空", dict(db.read_blocks(conn, cid)[0])["pv_bar"], None)
        check("pv:读入时来源记为 aspen",
              dict(db.read_blocks(conn, cid)[1])["pv_source"] == "aspen")
        check("pv:写入返回 True", db.set_pump_pv(conn, cid, "P1", 3.2) is True)
        check_eq("pv:读回 3.2", dict(db.read_blocks(conn, cid)[0])["pv_bar"], 3.2)
        check_eq("pv:手填来源记为 manual",
                 dict(db.read_blocks(conn, cid)[0])["pv_source"], "manual")
        check_eq("pv:设备名写错返回 False", db.set_pump_pv(conn, cid, "NOPE", 1.0), False)
        db.set_pump_pv(conn, cid, "P1", None)
        check_eq("pv:可以清空", dict(db.read_blocks(conn, cid)[0])["pv_bar"], None)
        check_eq("pv:清空时来源一并清掉",
                 dict(db.read_blocks(conn, cid)[0])["pv_source"], None)
        conn.close()
    finally:
        try:
            scratch.unlink(missing_ok=True)
        except OSError:
            pass


def test_pump_pv_inherit() -> None:
    """pv_bar 沿用:只填同模型更早工况的空格子,不覆盖手填值,不跨模型搬。"""
    from pathlib import Path

    from app.store import db

    scratch = Path("data/preview/_selftest-pv-inherit.db")
    scratch.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = db.open_db(str(scratch))

        def make(model, names):
            case = {
                "model": {"path": model, "name": model, "read_at": "2026-11-08 00:00:00"},
                "convergence": {}, "streams": [], "columns": [], "connections": [],
                "blocks": [{"name": n, "type": "Pump", "status": 0, "message": "",
                            "raw": {}, "balance": {}} for n in names],
                "stats": {"stream_count": 0, "block_count": len(names), "column_count": 0,
                          "connection_count": 0, "elapsed_sec": 0},
                "unit_gaps": {},
            }
            return db.save_case(conn, case)

        old = make("m.apw", ["P1", "P2"])
        db.set_pump_pv(conn, old, "P1", 3.2)
        db.set_pump_pv(conn, old, "P2", 1.0)

        new = make("m.apw", ["P1", "P2", "P3"])
        db.set_pump_pv(conn, new, "P2", 9.9)          # 手填过,不能被覆盖
        got = db.inherit_pump_pv(conn, new)
        check_eq("p_v 沿用:来源工况", got["source_case_id"], old)
        check_eq("p_v 沿用:拷贝 1 台(P1)", got["copied"], 1)
        check_eq("p_v 沿用:跳过 1 台(目标已手填)", got["skipped"], 1)
        rows = {r["name"]: r["pv_bar"] for r in db.read_blocks(conn, new)}
        check_eq("p_v 沿用:P1 拿到 3.2", rows["P1"], 3.2)
        check_eq("p_v 沿用:P2 保持手填 9.9", rows["P2"], 9.9)
        check_eq("p_v 沿用:P3 仍为空", rows["P3"], None)

        other = make("other.apw", ["P1"])
        check_eq("p_v 沿用:不同模型不搬", db.inherit_pump_pv(conn, other)["source_case_id"], None)
        conn.close()
    finally:
        try:
            scratch.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------- 单位表

def test_units() -> None:
    """units:四种实测出现过的泵字段写法都要认(泵的扬程/密度全靠它)。"""
    from app.aspen import units as u

    cases = [
        ("length", 41.54, "meter", 41.54),            # METCBAR
        ("length", 12.7728155, "m-kgf/kg", 12.7728155),  # MET
        ("length", 1000.0, "ft-lbf/lbm", 304.8),      # ENG(按定义推的,未实测)
        ("molar_density", 47.86, "kmol/cum", 47.86),  # METCBAR
        ("molar_density", 0.027296226, "mol/cc", 27.296226),  # MET
        ("volume_flow", 465.043291, "l/min", 27.90259746),
        ("volume_flow", 9.68000453, "cum/hr", 9.68000453),
        ("pressure", 3.95, "bar", 3.95),
    ]
    for quantity, value, unit, want in cases:
        got = u.convert(quantity, value, unit)
        check(f"单位:{quantity} {unit}",
              got is not None and abs(got - want) < 1e-6,
              f"期望 {want},实际 {got}")

    check_eq("单位:不认识的单位返回 None", u.convert("length", 1.0, "furlong"), None)
    check_eq("单位:值缺失返回 None", u.convert("length", None, "meter"), None)

    # 泵字段单位认不出来时,unit_gaps 必须报出来(否则泵页会静默变空)
    fake = [{"name": "P1", "type": "Pump", "head_m": None, "flow_m3h": None,
             "dp_bar": None, "rho_kgm3": None,
             "raw": {"HEAD_CAL": {"value": 1.0, "unit": "furlong"}}}]
    gaps = reader.unit_gaps([], fake)
    check("单位缺口:报出泵字段", gaps["count"] == 1, f"实际 {gaps}")
    check("单位缺口:带上原始单位", gaps["units"] == ["furlong"], f"实际 {gaps['units']}")


# ---------------------------------------------------------------- 边界夹具

def test_fixtures() -> None:
    """五类边界夹具:没有塔 / 没有泵 / 未知类型 / 泵缺流股 / NPSHₐ 为负。

    这些分支用真实工况一次都撞不上(现有 4 份模型都有塔有泵、全单相),
    所以只能造 —— 用假 dict 喂纯函数,或者在副本库上删一条连接。
    """
    from pathlib import Path

    from app.api import aipack
    from app.design import block_family as fam
    from app.design import pump as pump_rules
    from app.store import db
    from tools import repair_cases as repair

    # ① 没有塔:默认家族要落到第一个非空,而不是「塔」显示 0 行
    check_eq("夹具① 无塔:全泵模型落「泵」", fam.pick_default([{"type": "Pump"}]), "pump")
    check_eq("夹具① 无塔:换热+泵落「换热」",
             fam.pick_default([{"type": "Heater"}, {"type": "Pump"}]), "heat")

    # ② 没有泵:导出不该生成 pumps.csv
    check_eq("夹具② 无泵:has_pumps(空)", aipack.has_pumps([]), False)
    check_eq("夹具② 无泵:只有换热器", aipack.has_pumps([{"type": "Heater"}]), False)
    check("夹具② 无泵:有泵才生成", aipack.has_pumps([{"type": "Pump"}]))
    check_eq("夹具② 无泵:空表只有表头",
             len(aipack._pumps_csv([]).decode("utf-8-sig").strip().splitlines()), 1)

    # ③ 未知类型:落「其他」,不能把设备丢掉
    check_eq("夹具③ 未知类型落「其他」", fam.family_of("Welding"), "other")
    check_eq("夹具③ 未知类型进计数", fam.counts([{"type": "Welding"}]), {"other": 1})

    # ④ 泵缺进出口流股:压力取不到、回填不能炸(副本库上真跑一次)
    scratch = Path("data/preview/_selftest-fixture.db")
    scratch.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = db.open_db(str(scratch))
        cid = db.save_case(conn, {
            "model": {"path": "x.apw", "name": "x.apw", "read_at": "2026-11-08 00:00:00"},
            "convergence": {}, "streams": [], "columns": [], "connections": [],
            "blocks": [{"name": "P1", "type": "Pump", "power_kw": 10.0,
                        "status": 0, "message": "", "raw": {}, "balance": {}}],
            "stats": {"stream_count": 0, "block_count": 1, "column_count": 0,
                      "connection_count": 0, "elapsed_sec": 0},
            "unit_gaps": {},
        })
        case = dict(db.get_case(conn, cid))
        case["errtxt"] = case.get("errtxt") or ""
        result = repair.repair_case(conn, case, apply=False)
        row = dict(db.read_blocks(conn, cid)[0])
        check("夹具④ 缺流股:回填不抛异常", isinstance(result.get("changed"), list))
        check_eq("夹具④ 缺流股:p_in 仍为空", row["p_in_bar"], None)
        check_eq("夹具④ 缺流股:p_out 仍为空", row["p_out_bar"], None)
        conn.close()
    finally:
        try:
            scratch.unlink(missing_ok=True)
        except OSError:
            pass

    # ⑤ p_进口 < p_v:NPSHₐ 必须是负值(说明会汽蚀),不能吞成 None 或 0
    npsh = pump_rules.npsh_available_m(0.5, 1.5, 950.0)
    check("夹具⑤ NPSHₐ 为负", npsh is not None and npsh < 0, f"实际 {npsh}")


# ---------------------------------------------------------------- 出口一致性

def test_export_consistency() -> None:
    """同一台泵的同一组数,在 库(页面数据源)/ Excel / AI 包 三处必须一致。

    三个出口各写各的(见 docs/pump-selection-plan.md §4):效率乘 100、NPSHₐ 由 Python
    另算一遍 —— 任何一处改了都会漂。这里把三份都生成出来逐字段比。
    对比页走 HTTP,测不了;只静态校验「接口字段 ⊇ 前端字段」。
    """
    import io
    import re
    import zipfile
    from pathlib import Path as _Path

    from app.api import aipack, server
    from app.store import db

    if not _Path("data/cases.db").is_file():
        print("  跳过:没有 data/cases.db")
        return
    conn = db.open_db("data/cases.db")
    try:
        best = None
        for row in conn.execute("SELECT id FROM cases"):
            n = conn.execute(
                "SELECT COUNT(*) FROM blocks WHERE case_id=? AND lower(coalesce(type,''))='pump'"
                " AND head_m IS NOT NULL", (row["id"],)).fetchone()[0]
            if n and (best is None or n > best[1]):
                best = (row["id"], n)
        if best is None:
            print("  跳过:库里没有带扬程的泵")
            return
        cid, n_pumps = best
        blocks = {b["name"]: dict(b) for b in db.read_blocks(conn, cid)
                  if str(b["type"] or "").strip().lower() == "pump"}
        xlsx = server.build_workbook(conn, cid)
        pack, _fname = aipack.build_zip(conn, cid)
    finally:
        conn.close()

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(xlsx), read_only=True)
    rows = list(wb["泵"].iter_rows(values_only=True))
    header = list(rows[0])
    excel = {r[0]: dict(zip(header, r)) for r in rows[1:]}

    with zipfile.ZipFile(io.BytesIO(pack)) as zf:
        text = zf.read("pumps.csv").decode("utf-8-sig")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    csv_header = lines[0].split(",")
    csv = {ln.split(",")[0]: dict(zip(csv_header, ln.split(","))) for ln in lines[1:]}

    def num(value):
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # (CSV 列名, Excel 列名, 库里的字段或 None)
    pairs = [
        ("流量m³/h", "流量 m³/h", "flow_m3h"),
        ("扬程m", "扬程 m", "head_m"),
        ("效率%", "效率 %", None),
        ("轴功率kW", "轴功率 kW", "power_kw"),
        ("建议电机kW", "建议电机 kW", "motor_kw"),
        ("NPSHa m", "NPSHₐ m", None),
        ("p_v bar", "p_v bar", "pv_bar"),
    ]
    bad = []
    for name in blocks:
        for csv_key, xls_key, db_key in pairs:
            a = num(csv.get(name, {}).get(csv_key))
            b = num(excel.get(name, {}).get(xls_key))
            if a is None and b is None:
                continue
            if a is None or b is None or abs(a - b) > 1e-6:
                bad.append(f"{name}.{csv_key}: 包 {a} / Excel {b}")
                continue
            if db_key:
                d = num(blocks[name].get(db_key))
                if d is not None and abs(a - d) > 1e-6:
                    bad.append(f"{name}.{db_key}: 包 {a} / 库 {d}")
    check(f"出口一致:AI 包 / Excel / 库 同值(工况 {cid},{n_pumps} 台泵)",
          not bad, ";".join(bad[:3]))

    # 对比页字段:接口与前端必须同一组(接口加了而界面不显示 = 白算)
    src = _Path("app/api/server.py").read_text(encoding="utf-8")
    hit = re.search(r"block_fields = \[(.*?)\]", src, re.S)
    server_fields = set(re.findall(r'"([a-z_0-9]+)"', hit.group(1))) if hit else set()
    js = _Path("app/web/static/app.js").read_text(encoding="utf-8")
    hit_js = re.search(r"BLOCK_COMPARE_FIELDS = \[(.*?)\];", js, re.S)
    js_fields = set(re.findall(r'key: "([a-z_0-9]+)"', hit_js.group(1))) if hit_js else set()
    check("对比字段:两边都有扬程/流量",
          {"head_m", "flow_m3h"} <= server_fields and {"head_m", "flow_m3h"} <= js_fields,
          f"接口 {sorted(server_fields)} / 前端 {sorted(js_fields)}")
    check("对比字段:前端字段都在接口里", js_fields <= server_fields,
          f"前端多出 {sorted(js_fields - server_fields)}")


# ---------------------------------------------------------------- 同源校验

def test_guard() -> None:
    """_guard:Host 白名单 + 写请求客户端标记 + Origin。不启服务,直接喂假请求头。"""
    from app.api import server

    class FakeServer:
        server_address = ("127.0.0.1", 8000)

    class Fake:
        def __init__(self, headers):
            self.headers = headers
            self.server = FakeServer()
            self.err = None

        def _error(self, message, status=400):
            self.err = status

    guard = server.Handler._guard

    good = Fake({"Host": "127.0.0.1:8000", "X-Casebook-Client": "1"})
    check("守卫:同源写请求放行", guard(good, change=True) is True and good.err is None,
          f"err={good.err}")

    read = Fake({"Host": "localhost:8000"})
    check("守卫:GET 不需要客户端标记", guard(read, change=False) is True, f"err={read.err}")

    rebind = Fake({"Host": "evil.example", "X-Casebook-Client": "1"})
    check("守卫:假 Host 拒绝(DNS rebinding)",
          guard(rebind, change=False) is False and rebind.err == 403, f"err={rebind.err}")

    cross = Fake({"Host": "127.0.0.1:8000", "Content-Type": "text/plain"})
    check("守卫:跨站简单请求拒绝",
          guard(cross, change=True) is False and cross.err == 403, f"err={cross.err}")

    origin = Fake({"Host": "127.0.0.1:8000", "X-Casebook-Client": "1",
                   "Origin": "http://evil.example"})
    check("守卫:跨站 Origin 拒绝",
          guard(origin, change=True) is False and origin.err == 403, f"err={origin.err}")


# ---------------------------------------------------------------- runner

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="判定 / 焓平衡 / 波及范围的回归自测")
    parser.add_argument("--db", default="", help="额外检查这个工况库(如 data/cases.db)")
    parser.add_argument("-v", "--verbose", action="store_true", help="逐条打印通过的用例")
    args = parser.parse_args(argv)

    print("Casebook 自测")
    for name, fn in (("判定规则 judge()", test_judge),
                     ("空模型 mark_empty()", test_empty),
                     ("焓平衡 balance.compute()", test_balance),
                     ("报错波及范围 impact.build()", test_impact),
                     ("管路选型 design.pipe", test_pipe),
                     ("选型校验 validate() V1–V9", test_pipe_checks),
                     ("标准表补录说明 setup_guide()", test_pipe_setup),
                     ("标准表导入 import_standards()", test_std_import),
                     ("管道等级表 piping-class", test_piping_class),
                     ("内置示例库 install_sample()", test_install_sample),
                     ("设备家族 block_family", test_block_family),
                     ("泵口径 design.pump", test_pump),
                     ("泵 p_v 读取", test_pump_pv_read),
                     ("设备表泵列 blocks", test_block_columns),
                     ("泵 p_v 存储", test_pump_pv_store),
                     ("泵 p_v 沿用", test_pump_pv_inherit),
                     ("单位表 units", test_units),
                     ("边界夹具 fixtures", test_fixtures),
                     ("出口一致性 exports", test_export_consistency),
                     ("同源校验 guard", test_guard)):
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
