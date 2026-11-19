"""泵两侧管段:计算公式、缺项、修正 NPSHₐ,以及 pipe_run / pipe_setting 的读写。

不依赖 Aspen,也不依赖标准表 —— 管径与规格用构造的行传入,口径可手算复核。
"""

from __future__ import annotations

import math
from pathlib import Path

from app.design import pump_line
from tests._harness import check, check_eq


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
    check_eq("选型扬程", view["totals"]["selection_head_m"], math.ceil(expected * 1.1))

    device = (2.0 - 0.5) * 1e5 / (1000.0 * 9.81)
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
    """台账视图用修正 NPSH 判三态,同时保留装置值。"""
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
    check(view["npsh_a_m"] is not None, "装置值保留")
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
