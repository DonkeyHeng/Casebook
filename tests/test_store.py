"""工况库:库内一致性、模型身份与选型台账、λ 口径持久化。

用例正文原样搬自 `tools/selftest.py`(迁移到 pytest 时只改了位置)。
"""

from __future__ import annotations

import json

from app.aspen import balance as balance_rules
from app.aspen import reader
from tests._harness import (
    check,
    check_eq,
)


def check_db(db_path: str) -> None:
    """库里存的结论 == 现在规则重算的结论(否则要跑 repair_cases --apply)。

    名字不叫 test_ 是有意的:它要一个库路径参数,pytest 收不了带必填参数的用例。
    由 `tools/selftest.py --db <库>` 调用。
    """
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
            streams = [
                dict(r)
                for r in conn.execute(
                    "SELECT name, enthalpy_flow_kw FROM streams WHERE case_id = ?", (cid,)
                )
            ]
            blocks = [
                dict(r)
                for r in conn.execute(
                    "SELECT name, type, status, duty_kw, power_kw, dh_kw, ref_kw, ref_kind, "
                    "balance_state FROM blocks WHERE case_id = ?",
                    (cid,),
                )
            ]
            conns = [
                dict(r)
                for r in conn.execute(
                    "SELECT block, direction, stream, side FROM connections WHERE case_id = ?",
                    (cid,),
                )
            ]

            codes = json.loads(case["status_json"] or "{}")
            tears = json.loads(case["tear_json"] or "[]")
            verdict = reader.judge(codes, case["errtxt"], tears, blocks)

            if case["severity"] == "empty":
                check(f"{tag}: empty 工况不能被判成 error", verdict["severity"] != "error")
            else:
                check_eq(f"{tag}: severity 与规则重算一致", case["severity"], verdict["severity"])
            check_eq(
                f"{tag}: converged 与 severity 一致",
                bool(case["converged"]),
                case["severity"] != "error",
            )
            check_eq(
                f"{tag}: 报错设备名与 blocks 一致",
                list(verdict["block_errors"]),
                sorted(b["name"] for b in blocks if b["status"] == 1),
            )

            # 焓平衡:存下来的逐台判定必须能重算出来(repair_cases 的口径)
            if case["severity"] != "empty":
                # 与 repair_cases 同一口径:塔的对照值要带逐级 duty(首末级),
                # 否则库里存的 ok 在这里会被重算成「无法校验」
                stages = {}
                for b in blocks:
                    if b.get("type") in ("RadFrac", "Absorber", "Extract"):
                        rows = [dict(r) for r in db.read_column_stages(conn, cid, b["name"])]
                        if rows:
                            stages[b["name"]] = rows
                res = balance_rules.compute(streams, blocks, conns, stages=stages)
                bad = [
                    (b["name"], b["balance_state"], res[b["name"]]["balance_state"])
                    for b in blocks
                    if b["balance_state"] != res[b["name"]]["balance_state"]
                ]
                check(f"{tag}: 焓平衡逐台可重算", not bad, f"{len(bad)} 台不一致,例如 {bad[:3]}")

                summary = db.balance_summary(conn, cid)
                counted = sum(summary.values())
                check_eq(f"{tag}: 焓平衡四态台数 = 设备数", counted, len(blocks))

            # 拓扑:连接引用的流股必须都在流股表里(防止读漏/读串)
            names = {s["name"] for s in streams}
            ghosts = sorted({c["stream"] for c in conns} - names)
            check(f"{tag}: 连接里没有不存在的流股", not ghosts, f"多出 {ghosts[:5]}")
            check_eq(
                f"{tag}: 连接条数与库里一致",
                len(conns),
                conn.execute(
                    "SELECT COUNT(*) AS n FROM connections WHERE case_id = ?", (cid,)
                ).fetchone()["n"],
            )
    finally:
        conn.close()


def test_model_identity_and_selection() -> None:
    """模型身份键 / 选型台账的实时计算与持久化(不碰 Aspen、不编数据)。

    这些是第二期新加的口径:`cases.model_hash` 决定「同模型」怎么判,
    台账只存人工决定的字段、数值按当前工况实时算。放在自测里是为了
    以后改身份口径或三态规则时能立刻看到行为变了。
    """
    from app.design import model_identity
    from app.design import pump_selection as selection_rules
    from app.store import db

    # ① 身份键:hash 优先、老工况退回文件名、读不到文件不编 hash
    check_eq("身份: 无 hash 退回文件名", model_identity.key_of(None, "m.bkp"), "m.bkp")
    check_eq("身份: 有 hash 优先", model_identity.key_of("abc", "m.bkp"), "sha256:abc")
    check("身份: 空路径返回 None", model_identity.hash_file("") is None)
    check("身份: 不存在的模型返回 None", model_identity.hash_file("__no_such_model__.bkp") is None)
    check(
        "身份: SQL 键表达式与 Python 口径同形",
        "sha256:" in model_identity.same_model_key_sql("c")
        and "model_name" in model_identity.same_model_key_sql("c"),
    )

    # ② 台账实时计算:数值全来自工况,三态必须写明缺什么
    block = {
        "head_m": 10.0,
        "flow_m3h": 4.32,
        "p_in_bar": 2.0,
        "pv_bar": 0.5,
        "rho_kgm3": 723.007,
        "motor_kw": 1.5,
        "pump_eff": 0.7,
    }
    design = {"dz_m": 2.0, "hf_m": 1.0}
    view = selection_rules.compute_view(block, design, {"npshr_m": 3.0, "margin_m": 0.5})
    check_eq("台账: 设计扬程 = 压升 + ΔZ + Σhf", view["design_head_m"], 13.0)
    check_eq("台账: 选型扬程 ceil(1.1×H)", view["selection_head_m"], 15)
    check_eq("台账: 三态满足", view["npsh_state"], "ok")
    check_eq("台账: 判据阈值 = NPSHᵣ + 余量", view["npsh_threshold_m"], 3.5)
    check("台账: NPSHₐ 由工况算出", view["npsh_a_m"] is not None)

    no_pv = selection_rules.compute_view({**block, "pv_bar": None}, design, {"npshr_m": 3.0})
    check("台账: 缺 p_v 判无法判定", no_pv["npsh_state"] == "unknown")
    check("台账: 无法判定写明缺 p_v", "p_v" in no_pv["npsh_reason"])
    no_npshr = selection_rules.compute_view(block, design, {})
    check("台账: 缺 NPSHᵣ 判无法判定", no_npshr["npsh_state"] == "unknown")
    check("台账: 无法判定写明缺 NPSHᵣ", "NPSHᵣ" in no_npshr["npsh_reason"])
    check_eq(
        "台账: 超出 NPSHₐ 判不满足",
        selection_rules.compute_view(block, design, {"npshr_m": 100.0})["npsh_state"],
        "bad",
    )
    check(
        "台账: 缺 ΔZ/Σhf 时不给设计扬程",
        selection_rules.compute_view(block, {}, {})["design_head_m"] is None,
    )
    check(
        "台账: 余量缺省用建议值并标记",
        no_npshr["margin_is_default"] is True and no_npshr["margin_used_m"] == 0.5,
    )
    check("台账: 不写「验过」这类断言", "验过" not in view["npsh_reason"])

    # ③ 库层:身份键切换、台账读写与全空清除、overrides 原样往返
    conn = db.open_db(":memory:")
    try:
        conn.execute(
            "INSERT INTO cases (id, model_path, model_name, read_at) VALUES (1, 'p', 'm.bkp', 't')"
        )
        check_eq("库: 老工况身份键退回文件名", db.model_key(conn, 1), "m.bkp")
        conn.execute("UPDATE cases SET model_hash = 'abc' WHERE id = 1")
        check_eq("库: 新工况身份键用 hash", db.model_key(conn, 1), "sha256:abc")
        key = db.model_key(conn, 1)
        check("库: 台账初始为空", db.read_pump_selection(conn, key, "P1") is None)
        db.save_pump_selection(conn, key, "P1", {"model_no": "IS65-50-160", "qty": 2})
        got = db.read_pump_selection(conn, key, "P1")
        check_eq("库: 台账型号读回", got["model_no"], "IS65-50-160")
        check_eq("库: 台账台数读回", got["qty"], 2)
        db.save_pump_selection(conn, key, "P1", dict.fromkeys(db.SELECTION_FIELDS))
        check("库: 台账全空清除", db.read_pump_selection(conn, key, "P1") is None)
        payload = '{"zeta_sum":17.43,"overrides":{"id_mm":52.5}}'
        db.save_pump_design(conn, key, "P1", 2.0, 1.0, 40.0, payload)
        check_eq(
            "库: overrides 随设计参数原样读回",
            db.read_pump_design(conn, key, "P1")["zeta_json"],
            payload,
        )
    finally:
        conn.close()

    # ④ 服务端台账校验:非法输入在连库前拒绝,不写任何东西
    from app.api.server import Handler

    handler = object.__new__(Handler)
    handler.db_path = "__must_not_be_created__.db"
    errors: list = []
    replies: list = []
    handler._read_json_body = lambda: {"model_no": 123}
    handler._error = lambda msg, code=400: errors.append((msg, code))
    handler._json = lambda payload, code=200: replies.append(payload)
    handler._set_pump_selection("1", "P1")
    check("接口: 型号不是文字时拒绝", bool(errors) and "必须是文字" in errors[0][0])
    check("接口: 非法输入不回成功", not replies)


def test_model_identity_fallback() -> None:
    """模型保存后哈希变:手填数据要能从同模型旧版本借到,且必须标明来源。

    2026-11-19 实测的问题:身份键 = 文件内容 sha256,保存一次就换一版,
    于是「改完再读」被当成另一个模型 —— 台账 / 设计参数 / 手填 CAS 全部
    查不到(行还在库里)。这里钉住三件事:
      ① 兜底能借到,并且 source 指出工况 id 与档位;
      ② 精确匹配 `previous_case_same_model` 的口径没被偷偷改掉;
      ③ 不同模型(不同路径;哪怕文件名相同)绝不跨模型搬。
    """
    from app.store import db

    conn = db.open_db(":memory:")
    try:
        cases = [
            (1, "D:/a/m.bkp", "m.bkp", "aaa", "t1"),
            (2, "D:/a/m.bkp", "m.bkp", "bbb", "t2"),  # 同一份文件,保存后换哈希
            (3, "D:/b/m.bkp", "m.bkp", "ccc", "t3"),  # 同名、不同路径
            (4, "D:/c/n.bkp", "n.bkp", "ddd", "t4"),  # 另一个模型
        ]
        for case in cases:
            conn.execute(
                "INSERT INTO cases (id, model_path, model_name, model_hash, read_at) "
                "VALUES (?,?,?,?,?)",
                case,
            )
        old_key = db.model_key(conn, 1)
        check_eq("兜底: 旧版本键是内容哈希", old_key, "sha256:aaa")
        db.save_pump_selection(conn, old_key, "P1", {"model_no": "IS65-50-160", "qty": 2})
        db.save_pump_design(conn, old_key, "P1", 3.5, 1.2, 40.0, '{"zeta_sum": 17.4}')
        db.save_component_cas(conn, old_key, "H2O", "7732-18-5")
        conn.execute("INSERT INTO blocks (case_id, name, pv_bar) VALUES (1, 'P1', 0.5)")
        conn.execute("INSERT INTO blocks (case_id, name, pv_bar) VALUES (2, 'P1', NULL)")
        conn.commit()

        found = db.find_pump_selection(conn, 2, "P1")
        check_eq("兜底: 同路径新版本借到台账", found["via"], "same-model")
        check_eq("兜底: 台账数值原样", found["row"]["model_no"], "IS65-50-160")
        check_eq("兜底: 来源工况 id", found["source"]["case_id"], 1)
        check_eq("兜底: 来源档位 same-path", found["source"]["match"], "same-path")

        found = db.find_pump_design(conn, 2, "P1")
        check_eq("兜底: 设计参数借到", found["row"]["dz_m"], 3.5)
        cas = db.find_component_cas(conn, 2)
        check_eq("兜底: 手填 CAS 借到", cas["rows"].get("H2O"), "7732-18-5")

        check_eq("兜底: 精确同键口径未改", db.previous_case_same_model(conn, 2), None)
        prev = db.previous_model_case(conn, 2)
        check_eq("兜底: 宽松沿用找到 #1", prev["case_id"], 1)

        result = db.inherit_pump_pv(conn, 2)
        check_eq("兜底: p_v 从旧版本沿用", result["source_case_id"], 1)
        check_eq("兜底: p_v 沿用档位", result["match"], "same-path")
        check_eq("兜底: p_v 只填空行", result["copied"], 1)

        # 同名不同路径 = 可能是别人发来的另一份模型:只能自己重填,不许借
        found = db.find_pump_selection(conn, 3, "P1")
        check_eq("兜底: 同名不同路径不借", found["via"], "none")
        check("兜底: 同名不同路径拿不到台账", found["row"] is None)
        check_eq("兜底: 同名也不进候选", db.model_case_candidates(conn, 3), [])

        found = db.find_pump_selection(conn, 4, "P1")
        check_eq("兜底: 另一个模型不搬", found["via"], "none")
        check("兜底: 另一个模型拿不到台账", found["row"] is None)

        check_eq(
            "兜底: 旧键数据仍在库里(孤儿行未被删)",
            db.read_pump_selection(conn, old_key, "P1")["model_no"],
            "IS65-50-160",
        )
    finally:
        conn.close()


def test_pump_design_source_passthrough() -> None:
    """接口层:设计参数借自同模型旧版本时,响应必须带 source(界面据此标来源)。"""
    import os
    import tempfile

    from app.api.server import Handler
    from app.store import db

    fd, path = tempfile.mkstemp(prefix="cb_source_", suffix=".db")
    os.close(fd)
    try:
        conn = db.open_db(path)
        conn.execute(
            "INSERT INTO cases (id, model_path, model_name, model_hash, read_at) "
            "VALUES (1,'D:/a/m.bkp','m.bkp','aaa','t1')"
        )
        conn.execute(
            "INSERT INTO cases (id, model_path, model_name, model_hash, read_at) "
            "VALUES (2,'D:/a/m.bkp','m.bkp','bbb','t2')"
        )
        db.save_pump_design(conn, db.model_key(conn, 1), "P1", 3.5, 1.2, 40.0, '{"zeta_sum": 17.4}')
        conn.commit()
        conn.close()

        handler = object.__new__(Handler)
        handler.db_path = path
        replies: list = []
        handler._error = lambda msg, code=400: None
        handler._json = lambda payload, code=200: replies.append(payload)
        handler._pump_design("2", "P1")
        check_eq("接口: 借旧版本时带 source", replies[-1]["source"]["case_id"], 1)
        check_eq("接口: 借来的 ΔZ 可读", replies[-1]["dz_m"], 3.5)
        check_eq("接口: zeta_json 解析成对象", replies[-1]["zeta_json"]["zeta_sum"], 17.4)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_lambda_method() -> None:
    """λ 口径:前端三法可选,后端只认白名单,默认口径不落空挂账。

    跑完整 GET/POST 往返,用的是临时库,不碰用户的 data/cases.db。
    """
    import os
    import tempfile

    from app.api.server import Handler
    from app.store import db

    fd, path = tempfile.mkstemp(prefix="cb_lambda_", suffix=".db")
    os.close(fd)
    try:
        conn = db.open_db(path)
        conn.execute(
            "INSERT INTO cases (id, model_path, model_name, read_at) VALUES (1, 'p', 'm.bkp', 't')"
        )
        conn.commit()
        conn.close()

        handler = object.__new__(Handler)
        handler.db_path = path
        errors: list = []
        replies: list = []
        handler._error = lambda msg, code=400: errors.append((msg, code))
        handler._json = lambda payload, code=200: replies.append(payload)

        def post(body):
            errors.clear()
            replies.clear()
            handler._read_json_body = lambda: body
            handler._set_pump_design("1", "P1")
            return errors[0][0] if errors else None

        def get():
            replies.clear()
            handler._pump_design("1", "P1")
            return replies[-1]

        err = post(
            {
                "l_m": 40.0,
                "zeta_json": {
                    "zeta_sum": 17.43,
                    "fittings": {"el90": 3},
                    "overrides": {},
                    "lambda": {"method": "gu", "manual": None},
                },
            }
        )
        check_eq("λ: 顾毓珍口径可保存", err, None)
        check_eq("λ: 顾毓珍口径读回", get()["zeta_json"]["lambda"]["method"], "gu")

        err = post(
            {
                "l_m": 40.0,
                "zeta_json": {
                    "zeta_sum": None,
                    "fittings": {},
                    "overrides": {},
                    "lambda": {"method": "manual", "manual": 0.028},
                },
            }
        )
        check_eq("λ: 手填口径可保存", err, None)
        check_eq("λ: 手填 λ 值读回", get()["zeta_json"]["lambda"]["manual"], 0.028)

        err = post(
            {
                "l_m": 40.0,
                "zeta_json": {
                    "zeta_sum": None,
                    "fittings": {},
                    "overrides": {},
                    "lambda": {"method": "colebrook", "manual": None},
                },
            }
        )
        check_eq("λ: 改回默认口径可保存", err, None)
        check("λ: 默认口径不落空挂账", "lambda" not in (get()["zeta_json"] or {}))

        for body, want in (
            ({"zeta_json": {"lambda": {"method": "bad"}}}, "lambda.method"),
            ({"zeta_json": {"lambda": {"method": "manual", "manual": 5}}}, "超出合理范围"),
            ({"zeta_json": {"lambda": {"method": "manual", "manual": "x"}}}, "必须是数字"),
            ({"zeta_json": {"lambda": "gu"}}, "必须是对象"),
        ):
            got = post(body)
            check(f"λ: 非法输入被拒({want})", got is not None and want in got)
        check("λ: 非法输入不回成功", not replies)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
