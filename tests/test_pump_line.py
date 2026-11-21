"""泵两侧管段:计算公式、缺项、修正 NPSHₐ,以及 pipe_run / pipe_setting 的读写。

不依赖 Aspen,也不依赖标准表 —— 管径与规格用构造的行传入,口径可手算复核。
"""

from __future__ import annotations

import math
from pathlib import Path

from app.design import pump as pump_rules
from app.design import pump_line, pump_selection
from tests._harness import check, check_eq


def test_selection_head_decimal_boundaries():
    """选型扬程按十进制取整,二进制浮点不能把整数边界多进 1 m。"""
    for head, want in ((50.0, 55), (90.0, 99), (100.0, 110), (110.0, 121)):
        check_eq(f"选型扬程: H={head:g} → {want}", pump_rules.selection_head_m(head), want)
    for bad in (None, float("nan"), float("inf"), float("-inf"), 0.0, -1.0):
        check_eq(f"选型扬程: 非有限/非正 {bad!r} → None", pump_rules.selection_head_m(bad), None)


def _block(**over):
    base = {
        "name": "P1",
        "head_m": 20.0,
        "p_in_bar": 2.0,
        "pv_bar": 0.5,
        "rho_kgm3": 1000.0,
        "mu_cp": 1.0,
        "flow_m3h": 100.0,
        "type": "Pump",
    }
    base.update(over)
    return base


def _stream(name, vol=100.0, mass=100000.0):
    return {"name": name, "vol_flow_m3h": vol, "mass_flow_kgh": mass, "vap_frac": 0.0}


def _rows():
    return [
        {
            "stream": "S1",
            "dn": 100,
            "od_mm": 114.3,
            "wall_mm": 4.0,
            "vol_flow_m3h": 100.0,
            "design_pressure_mpa": 1.0,
            "stress_mpa": 130.0,
            "c2_mm": 0.0,
        },
        {
            "stream": "S2",
            "dn": 80,
            "od_mm": 88.9,
            "wall_mm": 4.0,
            "vol_flow_m3h": 100.0,
            "design_pressure_mpa": 1.0,
            "stress_mpa": 130.0,
            "c2_mm": 0.0,
        },
    ]


def _conns():
    return [
        {"block": "P1", "stream": "S1", "direction": "in"},
        {"block": "P1", "stream": "S2", "direction": "out"},
    ]


def test_pump_line_formula():
    """H = 压升 + 两侧 ΔZ + 两侧 Σhf;修正 NPSH 只扣吸入段。"""
    runs = {
        "S1": {"l_m": 10.0, "zeta_sum": 5.0, "dz_m": 2.0, "version": 3},
        "S2": {"l_m": 20.0, "zeta_sum": 10.0, "dz_m": 3.0, "version": 0},
    }
    view = pump_line.compute(
        _block(),
        _conns(),
        [_stream("S1"), _stream("S2")],
        _rows(),
        [],
        runs=runs,
        saved={"npshr_m": 3.0, "margin_m": 0.5},
    )
    s, d = view["suction"], view["discharge"]
    check(s["hf_m"] > 0 and d["hf_m"] > 0, "两侧 Σhf 都应算出")
    expected = 20.0 + 2.0 + 3.0 + s["hf_m"] + d["hf_m"]
    check(abs(view["totals"]["design_head_m"] - expected) < 1e-6, "合计公式")
    check_eq("选型扬程", view["totals"]["selection_head_m"], math.ceil(round(expected * 1.1, 9)))

    device = (2.0 - 0.5) * 1e5 / (1000.0 * 9.80665)
    check(abs(view["npsh"]["device_m"] - device) < 1e-6, "装置 NPSHₐ")
    corrected = device - 2.0 - s["hf_m"]
    check(abs(view["npsh"]["corrected_m"] - corrected) < 1e-6, "修正 NPSHₐ 只扣吸入段")
    check_eq("3.0+0.5 ≤ 修正值时应判满足", view["npsh"]["state"], "ok")

    # 保存后回显:前端要拿 run.* 回填输入框,拿 version 做下一次乐观锁。
    check_eq("吸入段 l_m 回显", s["run"]["l_m"], 10.0)
    check_eq("吸入段 Σζ 回显", s["run"]["zeta_sum"], 5.0)
    check_eq("吸入段 version 回显", s["version"], 3)

    # 手算复核吸入段:u = Q/A,Σhf = (λL/D + Σζ)u²/(2g)
    q = 100.0 / 3600.0
    area = math.pi / 4.0 * (s["id_mm"] / 1000.0) ** 2
    check(abs(s["u_ms"] - q / area) < 1e-6, "流速 = 体积流量 / 内径面积")


def test_pump_line_motor_design_first_and_fallback():
    """建议电机:设计扬程口径优先;管段不齐/两相/缺效率回退 Aspen;H≤0 不选。"""
    runs = {
        "S1": {"l_m": 10.0, "zeta_sum": 5.0, "dz_m": 2.0},
        "S2": {"l_m": 20.0, "zeta_sum": 10.0, "dz_m": 3.0},
    }
    block = _block(power_kw=80.0, power_source="BRAKE_POWER", pump_eff=0.7)
    full = pump_line.compute(
        block, _conns(), [_stream("S1"), _stream("S2")], _rows(), [], runs=runs
    )
    motor = full["totals"]["motor"]
    design_kw = full["totals"]["shaft_kw_estimate"]["design_kw"]
    check("电机:设计扬程口径有 design_kw", design_kw is not None and design_kw > 0, str(design_kw))
    check_eq("电机:优先设计扬程口径", motor["basis"], "design")
    check_eq("电机:功率来源标注", motor["power_source"], "设计扬程口径")
    check_eq("电机:基准功率就是 design_kw", motor["basis_kw"], design_kw)
    check(
        "电机:设计口径与 Aspen 口径确实可能不同",
        motor["kw"] != pump_rules.pick_motor(80.0)[0],
        f"design={motor['kw']} aspen={pump_rules.pick_motor(80.0)[0]}",
    )

    partial = pump_line.compute(
        block,
        _conns(),
        [_stream("S1"), _stream("S2")],
        _rows(),
        [],
        runs={"S1": runs["S1"], "S2": {"l_m": 20.0, "zeta_sum": None, "dz_m": 3.0}},
    )
    fallback = partial["totals"]["motor"]
    check_eq("电机:管段未齐回退 Aspen", fallback["basis"], "aspen")
    check(
        "电机:回退口径写明原因",
        "管段未齐" in fallback["power_source"],
        fallback["power_source"],
    )
    check_eq("电机:回退基准取 Aspen 轴功率", fallback["basis_kw"], 80.0)

    reverse = pump_line.compute(
        _block(head_m=-50.0, power_kw=80.0),
        _conns(),
        [_stream("S1"), _stream("S2")],
        _rows(),
        [],
        runs=runs,
    )
    reverse_motor = reverse["totals"]["motor"]
    check_eq("电机:H≤0 反向/自流不选电机", reverse_motor["kw"], None)
    check(
        "电机:H≤0 标注反向/自流",
        "反向/自流" in reverse_motor["power_source"],
        reverse_motor["power_source"],
    )


def test_pump_line_zeta_and_lambda_bounds():
    """Σζ 未填不给 Σhf;顾毓珍超 Re 上限回退 Colebrook;轴功率只做照会。"""
    runs = {
        "S1": {"l_m": 10.0, "zeta_sum": None, "dz_m": 2.0},
        "S2": {"l_m": 20.0, "zeta_sum": 10.0, "dz_m": 3.0},
    }
    view = pump_line.compute(
        _block(pump_eff=0.7),
        _conns(),
        [_stream("S1"), _stream("S2")],
        _rows(),
        [],
        runs=runs,
        saved={"npshr_m": 3.0},
    )
    s = view["suction"]
    check_eq("缺 Σζ:Σhf 不给", s["hf_m"], None)
    check_eq("缺 Σζ:状态为未填", s["status"], "unset")
    check("缺 Σζ:缺项列表写明", "管件系数 Σζ" in (s["missing"] or []), str(s["missing"]))
    check_eq("缺 Σζ:合计 Σhf 不给", view["totals"]["hf_m"], None)
    check_eq("缺 Σζ:设计扬程不给", view["totals"]["design_head_m"], None)
    check("缺 Σζ:hf_complete 为假", view["totals"]["hf_complete"] is False)
    check_eq("缺 Σζ:轴功率照会也不给", view["totals"]["shaft_kw_estimate"]["design_kw"], None)

    # 顾毓珍只覆盖 3e3–3e6:超过上限回退 Colebrook,而不是返回空 λ 让人以为没填数据
    re_hi = pump_line.hydraulics.solve(
        vol_m3h=50.0,
        rho_kgm3=900.0,
        mu_cp=0.05,
        id_mm=100.0,
        l_m=50.0,
        zeta_sum=5.0,
        method="gu",
    )
    check(re_hi["re"] > 3e6, f"Re 应超上限:{re_hi['re']}")
    check("Re 超 3e6:λ 有值", re_hi["lam"] is not None, str(re_hi["lam"]))
    check_eq("Re 超 3e6:口径标成 colebrook", re_hi["method_used"], "colebrook")
    check("Re 超 3e6:说明写明超上限", "上限" in (re_hi["method_note"] or ""), re_hi["method_note"])
    check(
        "Re 超 3e6:说明写明顾毓珍清洁钢管公式",
        "顾毓珍清洁钢管公式" in (re_hi["method_note"] or "")
        and "3×10³ ≤ Re ≤ 3×10⁶" in (re_hi["method_note"] or ""),
        re_hi["method_note"],
    )
    check("Re 超 3e6:Σhf 有值", re_hi["hf_m"] is not None)

    # 手填 λ 优先:缺密度/粘度仍算 Σhf,Re<2300 也不被 64/Re 覆盖
    manual = pump_line.hydraulics.solve(
        100.0,
        None,
        None,
        100.0,
        50.0,
        5.0,
        method="manual",
        lam_manual=0.03,
    )
    check("manual: 缺密度/粘度仍算 Σhf", manual["hf_m"] is not None, str(manual))
    check_eq("manual: 不再把密度/粘度列缺项", manual["missing"], [])
    check_eq("manual: λ 保持手填值", manual["lam"], 0.03)
    check(
        "manual: 注明 Re 未计算",
        "Re 未计算" in (manual["method_note"] or ""),
        manual["method_note"],
    )
    laminar = pump_line.hydraulics.solve(
        0.05,
        900.0,
        500.0,
        50.0,
        20.0,
        3.0,
        method="manual",
        lam_manual=0.03,
    )
    check("manual: 层流 Re<2300", laminar["re"] < 2300, str(laminar["re"]))
    check_eq("manual: 层流不覆盖手填 λ", laminar["lam"], 0.03)
    manual_missing = pump_line.hydraulics.solve(
        100.0,
        900.0,
        1.0,
        100.0,
        50.0,
        5.0,
        method="manual",
        lam_manual=None,
    )
    check_eq("manual: 手填 λ 为空时明确缺项", manual_missing["missing"], ["手填 λ"])
    manual_nonpos = pump_line.hydraulics.solve(
        100.0,
        900.0,
        1.0,
        100.0,
        50.0,
        5.0,
        method="manual",
        lam_manual=0.0,
    )
    check_eq("manual: 手填 λ ≤0 按未填处理", manual_nonpos["missing"], ["手填 λ"])
    manual_inf = pump_line.hydraulics.solve(
        100.0,
        900.0,
        1.0,
        100.0,
        50.0,
        5.0,
        method="manual",
        lam_manual=float("inf"),
    )
    check_eq("manual: 手填 λ 非有限按未填处理", manual_inf["missing"], ["手填 λ"])

    # 轴功率照会:P = ρQHη 的倒数式,只在两侧齐全且给了 η 时出现,不参与选档
    full = pump_line.compute(
        _block(pump_eff=0.7),
        _conns(),
        [_stream("S1"), _stream("S2")],
        _rows(),
        [],
        runs={
            "S1": {"l_m": 10.0, "zeta_sum": 5.0, "dz_m": 2.0},
            "S2": {"l_m": 20.0, "zeta_sum": 10.0, "dz_m": 3.0},
        },
        saved={"npshr_m": 3.0},
    )
    est = full["totals"]["shaft_kw_estimate"]
    expect = 1000.0 * 100.0 * 20.0 * 9.80665 / (3.6e6 * 0.7)
    check("轴功率照会:压升扬程口径有值", est["check_kw"] is not None, str(est))
    check(abs(est["check_kw"] - expect) < 1e-9, f"{est['check_kw']} vs {expect}")
    check("轴功率照会:设计扬程口径也有值", est["design_kw"] is not None, str(est))
    check("轴功率照会:写明 design_kw 参与电机选档", "参与电机选档" in est["rule"], est["rule"])
    check("轴功率照会:check_kw 仅照会", "check_kw 仅照会" in est["rule"], est["rule"])

    # Q 口径:照会 check 用泵块 VFLOW,design 用吸入流股体积流量;不等时给提示
    mismatch = pump_line.compute(
        _block(pump_eff=0.7, flow_m3h=120.0),
        _conns(),
        [_stream("S1"), _stream("S2")],
        _rows(),
        [],
        runs={
            "S1": {"l_m": 10.0, "zeta_sum": 5.0, "dz_m": 2.0},
            "S2": {"l_m": 20.0, "zeta_sum": 10.0, "dz_m": 3.0},
        },
        saved={"npshr_m": 3.0},
    )
    mm = mismatch["totals"]
    check("流量口径:VFLOW 与流股不等时给提示", "不一致" in (mm["flow_note"] or ""), str(mm))
    expect_design = 1000.0 * 100.0 * mm["design_head_m"] * 9.80665 / (3.6e6 * 0.7)
    check(
        "流量口径:design_kw 按吸入流股流量",
        abs(mm["shaft_kw_estimate"]["design_kw"] - expect_design) < 1e-9,
        f"{mm['shaft_kw_estimate']['design_kw']} vs {expect_design}",
    )


def test_pump_line_missing_side():
    """缺排出流股:只给「已计阻力」,设计扬程与选型扬程都不给。"""
    runs = {"S1": {"l_m": 10.0, "zeta_sum": 5.0, "dz_m": 2.0, "version": 0}}
    view = pump_line.compute(
        _block(),
        [{"block": "P1", "stream": "S1", "direction": "in"}],
        [_stream("S1")],
        _rows(),
        [],
        runs=runs,
        saved={"npshr_m": 3.0},
    )
    check_eq("排出侧状态", view["discharge"]["status"], "missing")
    check(view["totals"]["design_head_m"] is None, "缺一侧不给设计扬程")
    check(view["totals"]["selection_head_m"] is None, "缺一侧不给选型扬程")
    check(view["totals"]["partial_hf_m"] > 0, "仍显示已计阻力")
    check(view["npsh"]["corrected_m"] is not None, "吸入段齐全时修正 NPSH 可算")


def test_pump_line_ambiguous_and_pick():
    """同一侧两条流股:不猜,指定后才认。"""
    conns = [
        {"block": "P1", "stream": "S1", "direction": "in"},
        {"block": "P1", "stream": "S1b", "direction": "in"},
        {"block": "P1", "stream": "S2", "direction": "out"},
    ]
    side = pump_line.stream_side(conns, "P1", "in")
    check_eq("多条进口应待指定", side["status"], "ambiguous")
    picked = pump_line.stream_side(conns, "P1", "in", "S1b")
    check_eq("指定后应认这一条", picked["status"], "linked")
    check_eq("指定的是哪条就认哪条", picked["stream"], "S1b")


def test_pump_line_store():
    """pipe_run 的乐观锁、空行删除;pipe_setting 按模型整套存取。"""
    from app.store import db

    conn = db.open_db(":memory:")
    key = "model-x"
    check(db.read_pipe_run(conn, key, "S1") is None, "新库没有管段记录")

    saved, version = db.save_pipe_run(
        conn, key, "S1", {"l_m": 10.0, "zeta_sum": 5.0, "lambda_method": "colebrook"}, 0
    )
    check(saved, "应写入")
    check_eq("第一次写入版本 1", version, 1)
    row = db.read_pipe_run(conn, key, "S1")
    check_eq("读回管长", row["l_m"], 10.0)

    conflict = False
    try:
        db.save_pipe_run(conn, key, "S1", {"l_m": 12.0}, 0)
    except db.VersionConflict as exc:
        conflict = True
        check_eq("冲突时带当前版本", exc.current_version, 1)
    check(conflict, "旧版本号应被拒绝")

    db.save_pipe_run(conn, key, "S1", {"l_m": 12.0}, 1)
    check_eq("带对版本可覆盖", db.read_pipe_run(conn, key, "S1")["l_m"], 12.0)

    db.save_pipe_run(conn, key, "S1", {"note": "清空"}, 2)
    db.save_pipe_run(conn, key, "S1", {}, 3)
    check(db.read_pipe_run(conn, key, "S1") is None, "全空即删行")

    check(db.read_pipe_options(conn, key) is None, "参数没存过")
    db.save_pipe_options(conn, key, {"pressure_factor": 1.15, "velocity": {"liquid": 1.5}})
    opts = db.read_pipe_options(conn, key)
    check_eq("参数读回", opts["pressure_factor"], 1.15)
    db.save_pipe_options(conn, key, {})
    check(db.read_pipe_options(conn, key) is None, "空参数即删除")
    conn.close()


def test_pump_line_selection_corrected():
    """台账视图用修正 NPSH 判三态,同时保留进口基准值。"""
    from app.design import pump_selection

    lines = pump_line.compute(
        _block(),
        _conns(),
        [_stream("S1"), _stream("S2")],
        _rows(),
        [],
        runs={
            "S1": {"l_m": 50.0, "zeta_sum": 20.0, "dz_m": 4.0},
            "S2": {"l_m": 60.0, "zeta_sum": 20.0, "dz_m": 3.0},
        },
        saved={"npshr_m": 3.0, "margin_m": 0.5},
    )
    view = pump_selection.compute_view(_block(), None, {"npshr_m": 3.0}, lines=lines)
    check(view["npsh_a_m"] is not None, "进口基准值保留")
    check(view["npsh_a_corrected_m"] is not None, "修正值给出现")
    check(view["npsh_state"] in ("ok", "bad", "unknown"), "三态只能这三值")
    check_eq("设计扬程同源", view["design_head_m"], lines["totals"]["design_head_m"])


def test_line_http_roundtrip():
    """POST /lines/{stream} 真跑一遍:首次写入 200、旧版本 409、未知流股 404。

    这条覆盖的是路由 + 存储 + 读回整条链;单测只测 db 函数时,
    `sqlite3.Row` 少了 `.get` 这类错会一直躲到用户点保存才露。
    """
    import json as jsonlib
    import shutil
    import sqlite3
    import tempfile
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from app.api import server as srv
    from tests._harness import REPO_ROOT

    sample = REPO_ROOT / "samples" / "case-23-error.db"
    if not sample.is_file():
        print("  找不到样本库,跳过 HTTP 实跑")
        return
    tmp = Path(tempfile.mkdtemp())
    db_file = tmp / "case.db"
    shutil.copy(sample, db_file)
    conn = sqlite3.connect(db_file)
    case_id = conn.execute("SELECT id FROM cases ORDER BY id LIMIT 1").fetchone()[0]
    stream = conn.execute(
        "SELECT name FROM streams WHERE case_id = ? ORDER BY name LIMIT 1", (case_id,)
    ).fetchone()[0]
    conn.close()

    handler_cls = type("LineProbeHandler", (srv.Handler,), {"db_path": str(db_file)})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def call(method: str, path: str, body=None) -> tuple[int, dict]:
        data = jsonlib.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            base + path,
            data=data,
            method=method,
            headers={"X-Casebook-Client": "1", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, jsonlib.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, {}

    try:
        status, payload = call("POST", f"/api/cases/{case_id}/lines/{stream}", {"l_m": 12.5})
        check_eq("首次保存回 200", status, 200)
        check_eq("首次保存版本 1", payload.get("version"), 1)
        status, _ = call("POST", f"/api/cases/{case_id}/lines/{stream}", {"l_m": 9, "version": 0})
        check_eq("旧版本被拒 409", status, 409)
        status, _ = call("POST", f"/api/cases/{case_id}/lines/NO_SUCH", {"l_m": 9})
        check_eq("未知流股 404", status, 404)
    finally:
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_pipe_options_validate():
    """管道页参数校验:只收已知项,超范围拒绝,缺项沿用默认。

    这段逻辑曾整块落在迁移方法的 return 之后(死代码),路由跑起来才露;
    这里直接卡住校验入口,避免再被一次「保存后参数没变」掩盖。
    """
    from app.api.routes import lines as line_routes

    base = line_routes.LineRoutes._validate_options(None, {"velocity": {"liquid": 1.8}})
    check_eq("液体流速按输入", base["velocity"]["liquid"], 1.8)
    check(base["velocity"]["gas"] > 0, "没填的流速项仍给默认值")

    raised = False
    try:
        line_routes.LineRoutes._validate_options(None, {"velocity": {"nope": 1.2}})
    except ValueError as exc:
        raised = "未知流速项" in str(exc)
    check(raised, "未知流速项应拒绝")

    raised = False
    try:
        line_routes.LineRoutes._validate_options(None, {"pressure_factor": 99})
    except ValueError:
        raised = True
    check(raised, "压力系数超范围应拒绝")

    # K_p < 1 会算出低于操作表压的设计压力，属于工程死区；下限与前端 min=1 对齐。
    raised = False
    try:
        line_routes.LineRoutes._validate_options(None, {"pressure_factor": 0.99})
    except ValueError:
        raised = True
    check(raised, "压力系数 0.99 应拒绝")
    line_routes.LineRoutes._validate_options(None, {"pressure_factor": 1.0})
    from app.design import pump_line_context

    check_eq(
        "旧库存的 K_p<1 读侧抬到 1.0",
        pump_line_context._merge_options({"pressure_factor": 0.5})["pressure_factor"],
        1.0,
    )

    # 管段手填 λ:字段名 lambda_manual;保存层 0.0001–1.0,空值/≤0 在计算层按未填。
    values, _ = line_routes.LineRoutes._validate_run(
        None, {"lambda_method": "manual", "lambda_manual": 0.03}
    )
    check_eq("管段 λ: 手填值落库", values["lambda_manual"], 0.03)
    raised = False
    try:
        line_routes.LineRoutes._validate_run(None, {"lambda_method": "manual", "lambda_manual": 0})
    except ValueError:
        raised = True
    check(raised, "管段 λ: 0 应拒绝")
    raised = False
    try:
        line_routes.LineRoutes._validate_run(
            None, {"lambda_method": "manual", "lambda_manual": -0.1}
        )
    except ValueError:
        raised = True
    check(raised, "管段 λ: 负值应拒绝")
    raised = False
    try:
        line_routes.LineRoutes._validate_run(
            None, {"lambda_method": "manual", "lambda_manual": float("inf")}
        )
    except ValueError:
        raised = True
    check(raised, "管段 λ: 非有限值应拒绝")

    # 自定义规格必须满足 2×壁厚 < 外径;历史脏数据仍由 overrides/pump_line 运行期兜底。
    raised = False
    try:
        line_routes.LineRoutes._validate_run(
            None, {"od_override_mm": 100.0, "wall_override_mm": 50.0}
        )
    except ValueError as exc:
        raised = "2×壁厚" in str(exc)
    check(raised, "自定义规格: 2×壁厚 = 外径应拒绝")
    raised = False
    try:
        line_routes.LineRoutes._validate_run(
            None, {"od_override_mm": 50.0, "wall_override_mm": 30.0}
        )
    except ValueError as exc:
        raised = "2×壁厚" in str(exc)
    check(raised, "自定义规格: 2×壁厚 > 外径应拒绝")
    values, _ = line_routes.LineRoutes._validate_run(
        None, {"od_override_mm": 108.0, "wall_override_mm": 4.0}
    )
    check_eq("自定义规格: 合法薄壁仍落库", values["wall_override_mm"], 4.0)


def test_pump_line_two_phase_and_reverse():
    """两相进口不给 NPSH;H ≤ 0 不给选型扬程。"""
    runs = {
        "S1": {"l_m": 10.0, "zeta_sum": 5.0, "dz_m": 2.0},
        "S2": {"l_m": 20.0, "zeta_sum": 5.0, "dz_m": 3.0},
    }
    two = pump_line.compute(
        _block(pump_vap_frac=0.05),
        _conns(),
        [_stream("S1"), _stream("S2")],
        _rows(),
        [],
        runs=runs,
        saved={"npshr_m": 3.0},
    )
    check_eq("两相:进口基准 NPSHₐ 为空", two["npsh"]["device_m"], None)
    check_eq("两相:修正 NPSHₐ 为空", two["npsh"]["corrected_m"], None)
    check_eq(
        "两相:理由不是「缺 两相进口」",
        two["npsh"]["reason"],
        "两相进口不计算 NPSHₐ，无法判定",
    )
    check("两相:缺项仍保留", "两相进口" in (two["npsh"]["missing"] or []), str(two["npsh"]))
    check_eq("两相:三态无法判定", two["npsh"]["state"], "unknown")
    est = two["totals"]["shaft_kw_estimate"]
    check_eq("两相:check_kw 拒算", est["check_kw"], None)
    check_eq("两相:design_kw 拒算", est["design_kw"], None)
    check_eq("两相:轴功率照会说明", est["note"], "含气/两相进口不计算轴功率照会")
    # 边界:正好 1e-4 按单相,与管道页 <= 1e-4 一致
    edge = pump_line.compute(
        _block(pump_vap_frac=1e-4),
        _conns(),
        [_stream("S1"), _stream("S2")],
        _rows(),
        [],
        runs=runs,
        saved={"npshr_m": 3.0},
    )
    check("两相边界:1e-4 仍算单相 NPSHₐ", edge["npsh"]["device_m"] is not None)

    missing_vap = pump_line.compute(
        _block(pump_vap_frac=None, pump_eff=0.7),
        _conns(),
        [_stream("S1"), _stream("S2")],
        _rows(),
        [],
        runs=runs,
        saved={"npshr_m": 3.0},
    )
    check(
        "缺汽化率:仍按液相算 NPSHₐ",
        missing_vap["npsh"]["device_m"] is not None,
        str(missing_vap["npsh"]),
    )
    check(
        "缺汽化率:仍给设计轴功率",
        missing_vap["totals"]["shaft_kw_estimate"]["design_kw"] is not None,
        str(missing_vap["totals"]["shaft_kw_estimate"]),
    )
    check(
        "缺汽化率:compute 顶层告警",
        "缺汽化率，暂按液相估算，请复核" in missing_vap["warnings"],
        str(missing_vap.get("warnings")),
    )
    check(
        "缺汽化率:totals 告警",
        "缺汽化率，暂按液相估算，请复核" in missing_vap["totals"]["warnings"],
    )
    check(
        "缺汽化率:三态理由带复核提示",
        "缺汽化率，暂按液相估算，请复核" in missing_vap["npsh"]["reason"],
        missing_vap["npsh"]["reason"],
    )
    ledger = pump_selection.compute_view(
        _block(pump_vap_frac=None, pump_eff=0.7),
        saved={"npshr_m": 3.0, "margin_m": 0.5},
        lines=missing_vap,
    )
    check(
        "缺汽化率:台账理由与告警同源",
        "缺汽化率，暂按液相估算，请复核" in ledger["npsh_reason"],
        ledger["npsh_reason"],
    )
    check(
        "缺汽化率:台账 warnings 可见",
        "缺汽化率，暂按液相估算，请复核" in ledger["warnings"],
        str(ledger.get("warnings")),
    )

    rev = pump_line.compute(
        _block(head_m=-50.0),
        _conns(),
        [_stream("S1"), _stream("S2")],
        _rows(),
        [],
        runs=runs,
        saved={"npshr_m": 3.0},
    )
    check("反向:设计扬程为负", rev["totals"]["design_head_m"] < 0)
    check_eq("反向:不给选型扬程", rev["totals"]["selection_head_m"], None)
    check_eq("反向:标记 reverse", rev["totals"]["reverse"], True)

    # 进口绝压 <=0 是物理非法值:NPSHₐ 不计算,三态 unknown,并说明哪一项非法。
    for label, p_in in (("0", 0.0), ("负数", -1.0)):
        bad = pump_line.compute(
            _block(p_in_bar=p_in),
            _conns(),
            [_stream("S1"), _stream("S2")],
            _rows(),
            [],
            runs=runs,
            saved={"npshr_m": 3.0},
        )
        check_eq(f"进口压力 {label}:NPSHₐ 为空", bad["npsh"]["device_m"], None)
        check_eq(f"进口压力 {label}:三态 unknown", bad["npsh"]["state"], "unknown")
        check(
            f"进口压力 {label}:缺项标明非法",
            bad["npsh"]["missing"] == ["进口压力(≤0 非法)"],
            str(bad["npsh"]),
        )

    nan_view = pump_line.compute(
        _block(p_in_bar=float("nan")),
        _conns(),
        [_stream("S1"), _stream("S2")],
        _rows(),
        [],
        runs=runs,
        saved={"npshr_m": 3.0},
    )
    check_eq("进口压力 NaN:NPSHₐ 为空", nan_view["npsh"]["device_m"], None)
    check_eq("进口压力 NaN:三态 unknown", nan_view["npsh"]["state"], "unknown")
    check(
        "进口压力 NaN:按缺失处理",
        nan_view["npsh"]["missing"] == ["进口压力"],
        str(nan_view["npsh"]),
    )


def test_pump_line_custom_wall_and_missing():
    """_custom_wall 走管道同一壁厚函数;缺 Σζ 不能被 solve 的缺项覆盖。"""
    from app.design.pipe import data as pdata

    old_carbon = pdata.OD_TO_WALLS
    old_sst = pdata.OD_TO_WALLS_SST
    pdata.OD_TO_WALLS = {108.0: (1.4, 2.0, 3.0, 4.0)}
    pdata.OD_TO_WALLS_SST = {108.0: (1.6, 2.5, 3.5, 4.5)}
    try:
        row = {
            "design_pressure_mpa": 1.0,
            "stress_mpa": 130.0,
            "design_temp_c": 60.0,
            "c2_mm": 0.0,
        }
        wall_c, note_c = pump_line._custom_wall(100, 108.0, {**row, "material": "20#"})
        wall_s, note_s = pump_line._custom_wall(100, 108.0, {**row, "material": "S31603"})
        check_eq("custom_wall: 碳钢走表 1", (wall_c, note_c), (3.0, ""))
        check_eq("custom_wall: 不锈钢走表 4", (wall_s, note_s), (3.5, ""))
    finally:
        pdata.OD_TO_WALLS = old_carbon
        pdata.OD_TO_WALLS_SST = old_sst

    # 人为让 solve 只报「密度」:调用方已知的 Σζ 不能被覆盖
    original = pump_line.hydraulics.solve

    def fake_solve(**_kwargs):
        return {
            "u_ms": 1.5,
            "re": 1e5,
            "lam": 0.02,
            "hf_m": None,
            "method_used": "colebrook",
            "method_note": "",
            "missing": ["密度"],
        }

    pump_line.hydraulics.solve = fake_solve
    try:
        out = pump_line.compute_line(
            "suction",
            {"status": "linked", "stream": "S1", "reason": ""},
            pipe_rows=_rows(),
            pipe_missing=[],
            streams=[{"name": "S1", "vol_flow_m3h": 100.0, "mass_flow_kgh": None}],
            block=_block(),
            run={"l_m": 10.0, "zeta_sum": None},
        )
    finally:
        pump_line.hydraulics.solve = original
    check_eq("missing: solve 缺项不覆盖 Σζ", out["missing"], ["密度", "管件系数 Σζ"])
    check_eq("missing: solve 的 u 仍保留", out["u_ms"], 1.5)

    # solve 在缺密度/粘度/管长时仍会先算 u;调用方无需退回 velocity_ms
    partial = pump_line.hydraulics.solve(
        vol_m3h=100.0, rho_kgm3=None, mu_cp=None, id_mm=100.0, l_m=None, zeta_sum=None
    )
    check("u: 缺密度/粘度/L 时 solve 仍给流速", partial["u_ms"] is not None, str(partial))

    # _shaft_kw:负密度/负流量不再当成有效值参与乘除
    check_eq("shaft: 负密度返回 None", pump_line._shaft_kw(-900.0, 100.0, 20.0, 0.7), None)
    check_eq("shaft: 负流量返回 None", pump_line._shaft_kw(900.0, -100.0, 20.0, 0.7), None)
    check_eq("shaft: 零流量返回 None", pump_line._shaft_kw(900.0, 0.0, 20.0, 0.7), None)


def test_pump_line_density_positive_finite():
    """管段密度只在 m/V 都为正且结果有限时写入;不回退泵块密度。"""
    cases = [
        ("正流量", 100000.0, 100.0, 1000.0),
        ("质量为零", 0.0, 100.0, None),
        ("质量为负", -100000.0, 100.0, None),
        ("体积流量为负", 100000.0, -100.0, None),
        ("质量为非有限", float("inf"), 100.0, None),
        ("密度计算结果非有限", 1e308, 1e-308, None),
    ]
    for label, mass, vol, want in cases:
        out = pump_line.compute_line(
            "suction",
            {"status": "linked", "stream": "S1", "reason": ""},
            pipe_rows=_rows(),
            pipe_missing=[],
            streams=[_stream("S1", vol=vol, mass=mass)],
            block=_block(rho_kgm3=900.0),
            run={"l_m": 10.0, "zeta_sum": 5.0},
        )
        check_eq(f"rho: {label}", out["rho_kgm3"], want)
        if want is None:
            check(f"rho: {label} 不回退泵块密度", out["rho_kgm3"] != 900.0, str(out))


def test_pump_line_lambda_transition():
    """顾毓珍在 2300–3000 过渡区回退 Colebrook 并标注口径。"""
    runs = {
        "S1": {"l_m": 50.0, "zeta_sum": 5.0, "mu_cp": 141.0, "lambda_method": "gu"},
        "S2": {"l_m": 50.0, "zeta_sum": 5.0, "mu_cp": 141.0, "lambda_method": "gu"},
    }
    view = pump_line.compute(
        _block(), _conns(), [_stream("S1"), _stream("S2")], _rows(), [], runs=runs
    )
    side = view["suction"]
    check(side["re"] is not None and 2300 <= side["re"] < 3000, f"Re={side['re']}")
    check(side["lam"] is not None, "过渡区仍给出 λ")
    check_eq("过渡区回退口径", side["lambda_method_used"], "colebrook")
    check("过渡区有说明", "过渡区" in side["lambda_note"], side["lambda_note"])
    check(
        "过渡区说明写明顾毓珍清洁钢管公式",
        "顾毓珍清洁钢管公式" in side["lambda_note"] and "3×10³ ≤ Re ≤ 3×10⁶" in side["lambda_note"],
        side["lambda_note"],
    )
