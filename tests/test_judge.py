"""判定:收敛 / 报错 / 空模型怎么判。

用例正文原样搬自 `tools/selftest.py`(迁移到 pytest 时只改了位置)。
"""

from __future__ import annotations

from app.aspen import reader
from tests._harness import (
    check,
    check_eq,
)


def test_judge() -> None:
    """judge():只有硬错误才判 error,软信号最多 warning。"""
    clean = {"RSTAT": 0, "CVSTAT": 0, "CSSTAT": 2, "UOSSTAT": 0, "PER_ERROR": 0}

    # ① 全部正常 → ok(CSSTAT=2 之类不代表失败,别被它带偏)
    r = reader.judge(clean, "", [], [])
    check_eq("judge: 全零 → ok", r["severity"], "ok")
    check("judge: warning/ok 的数据算可用", r["converged"] is True)

    # ② 真实样本:7 个模块全是 status=2 告警、UOSSTAT=1、PER_ERROR=1
    warnings = [
        {"name": f"M{n}", "status": 2, "message": "PHASE STABILITY CHECK"} for n in range(1, 8)
    ]
    codes = dict(clean, UOSSTAT=1, PER_ERROR=1)
    r = reader.judge(codes, "", [], warnings)
    check_eq(
        "judge: 7 个告警 + UOSSTAT/PER_ERROR=1 → warning(不是 error)", r["severity"], "warning"
    )
    check_eq("judge: 告警设备全部列出", len(r["block_warnings"]), 7)

    # ③ 真实样本(#22,121 流股):T0202/T0205 是 status=1,CVSTAT=1
    errors = [
        {
            "name": "T0202",
            "status": 1,
            "message": "COLUMN DRIES UP OR COLUMN FLOWS VIOLATE BUILT-IN LIMITS",
        },
        {
            "name": "T0205",
            "status": 1,
            "message": "COMPONENT BALANCE EQUATIONS FAILED TO CONVERGE.",
        },
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
    check(
        "judge: 撕裂流理由里带原始数值", any("1.656" in x for x in r["reasons"]), str(r["reasons"])
    )

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
