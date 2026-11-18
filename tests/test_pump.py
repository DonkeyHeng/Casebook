"""泵:派生量、p_v 读取与存储、设备表泵列、分类导出。

用例正文原样搬自 `tools/selftest.py`(迁移到 pytest 时只改了位置)。
"""

from __future__ import annotations

from app.aspen import reader
from tests._harness import (
    check,
    check_eq,
)
from tools import fake_session


def test_pump() -> None:
    """design.pump:密度换算、扬程、NPSHₐ、电机档位。口径见 docs/aspen-field-map.md §13。"""
    from app.design import pump

    check_eq(
        "泵:摩尔密度 × 分子量 → 质量密度", round(pump.mass_density_kgm3(47.86, 19.79), 1), 947.1
    )
    check_eq("泵:缺分子量时密度为 None", pump.mass_density_kgm3(47.86, None), None)
    check_eq("泵:缺摩尔密度时密度为 None", pump.mass_density_kgm3(None, 19.79), None)

    head = pump.head_from_dp(1.785, 2.10, 947.0)
    check("泵:压差反算扬程 ≈ 3.39 m", head is not None and abs(head - 3.39) < 0.02, f"实际 {head}")
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
    check_eq("泵:p_v 为正无穷视为无效", pump.sanitize_pv_bar(float("inf")), None)
    check_eq("泵:p_v 为负无穷视为无效", pump.sanitize_pv_bar(float("-inf")), None)

    import math

    zero = pump.npsh_available_m(1.01325, 1.01325, 900.0)
    check(
        "泵:泡点进料 NPSHₐ 归零且不带负号",
        zero == 0.0 and math.copysign(1.0, zero) > 0,
        f"实际 {zero!r}",
    )
    tiny = pump.npsh_available_m(1.0, 1.000001, 1000.0)
    check(
        "泵:极小的负 NPSHₐ 按显示精度归零",
        tiny == 0.0 and math.copysign(1.0, tiny) > 0,
        f"实际 {tiny!r}",
    )

    kw, note = pump.pick_motor(131.466)
    check_eq("泵:131.47 kW 轴功率 → 160 kW 档", kw, 160.0)
    check("泵:推导说明里带档位数字", "160" in note, note)
    check_eq("泵:零功率不选电机", pump.pick_motor(0.0)[0], None)
    check_eq("泵:负功率(回收功)不选电机", pump.pick_motor(-3.4)[0], None)
    check_eq("泵:缺轴功率", pump.pick_motor(None)[0], None)
    check(
        "泵:超出档位表给出说明",
        pump.pick_motor(1000.0)[0] is None and "超出" in pump.pick_motor(1000.0)[1],
    )


def test_pump_pv_read() -> None:
    r"""reader.read_stream_vapor_pressure():p_v 的取法,不需要 Aspen。

    背景(2026-11-14 实测):`Output\PL` 是**目录节点**,读出来是标志位 `1` 且带 bar 单位;
    混合物泡点压力在 `STRM_UPP\PBUB\MIXED\TOTAL`。所以规则是:
    有子节点的节点跳过 → 必须有单位 → 换算成 bar → 必须 > 0。
    """

    def _session(nodes):
        """单点用例的迷你夹具:`{路径: {"v": 值, "u": 单位, "kids": [子节点]}}`。"""
        return fake_session.FakeSession({"case": "inline-pv", "nodes": nodes})

    base = r"\Data\Streams\0133\Output"
    pv_path = base + r"\STRM_UPP\PBUB\MIXED\TOTAL"

    got = reader.read_stream_vapor_pressure(
        _session({pv_path: {"v": 0.53383137, "u": "bar"}}), "0133"
    )
    check(
        "泵 p_v:泡点压力叶子读出 0.5338 bar",
        got is not None and abs(got - 0.5338) < 1e-3,
        f"实际 {got}",
    )

    got = reader.read_stream_vapor_pressure(
        _session({base + r"\PL": {"v": 1, "u": "bar", "kids": ["MIXED"]}}), "0133"
    )
    check_eq("泵 p_v:目录节点(PL 值恒 1)不当读数", got, None)

    got = reader.read_stream_vapor_pressure(_session({pv_path: {"v": 0.0, "u": "bar"}}), "0133")
    check_eq("泵 p_v:读数为 0 视为没有", got, None)

    got = reader.read_stream_vapor_pressure(_session({pv_path: {"v": 50.0, "u": "kpa"}}), "0133")
    check(
        "泵 p_v:单位按 pressure 换算(50 kPa → 0.5 bar)",
        got is not None and abs(got - 0.5) < 1e-9,
        f"实际 {got}",
    )

    got = reader.read_stream_vapor_pressure(_session({pv_path: {"v": 0.5}}), "0133")
    check_eq("泵 p_v:单位认不出时留空", got, None)

    # V11 布局(实测):PBUB 下面没有 TOTAL 层,值是 MIXED\LIQUID。
    # 只按 `...\MIXED\TOTAL` 取值会逐台返回 None,泵页 p_v 整列变空 —— 这条守住它。
    v11 = base + r"\STRM_UPP\PBUB\MIXED\LIQUID"
    got = reader.read_stream_vapor_pressure(_session({v11: {"v": 0.1875, "u": "bar"}}), "0133")
    check(
        "泵 p_v:V11 布局(PBUB\\MIXED\\LIQUID)也能读到",
        got is not None and abs(got - 0.1875) < 1e-3,
        f"实际 {got}",
    )

    # 从容器节点取:PBUB(值恒 1)→ MIXED → LIQUID,下钻命中真值
    got = reader.read_stream_vapor_pressure(
        _session(
            {
                base + r"\STRM_UPP\PBUB": {"v": 1, "u": "bar", "kids": ["MIXED"]},
                base + r"\STRM_UPP\PBUB\MIXED": {"v": 1, "u": "bar", "kids": ["LIQUID"]},
                base + r"\STRM_UPP\PBUB\MIXED\LIQUID": {"v": 3.33615131, "u": "bar"},
            }
        ),
        "0133",
    )
    check(
        "泵 p_v:容器 PBUB 按相下钻,不把标志位 1 当读数",
        got is not None and abs(got - 3.3362) < 1e-3,
        f"实际 {got}",
    )

    # PL 的液相叶子是按组分的分压,认不出组分名时必须放弃,不能拿第一个组分顶上
    got = reader.read_stream_vapor_pressure(
        _session(
            {
                base + r"\STRM_UPP\PL": {"v": 1, "u": "bar", "kids": ["MIXED"]},
                base + r"\STRM_UPP\PL\MIXED": {"v": 1, "u": "bar", "kids": ["LIQUID"]},
                base + r"\STRM_UPP\PL\MIXED\LIQUID": {"v": 1, "u": "bar", "kids": ["H2O"]},
                base + r"\STRM_UPP\PL\MIXED\LIQUID\H2O": {"v": 0.0424791, "u": "bar"},
            }
        ),
        "0133",
    )
    check_eq("泵 p_v:PL 的组分分压不当混合物 p_v", got, None)


def test_side_temperatures() -> None:
    """enrich_side_temperatures():进出/冷侧温度取自连接流股,不需要 Aspen。"""
    streams = [
        {"name": "S1", "temperature_c": 120.0, "vap_frac": 0.0},
        {"name": "S2", "temperature_c": 80.0, "vap_frac": 0.5},
        {"name": "CW1", "temperature_c": 25.0, "vap_frac": 0.0},
        {"name": "CW2", "temperature_c": 35.0, "vap_frac": 0.0},
    ]
    blocks = [{"name": "HX1"}, {"name": "H1"}, {"name": "ORPHAN"}]
    connections = [
        {"block": "HX1", "direction": "in", "stream": "S1", "side": "hot"},
        {"block": "HX1", "direction": "out", "stream": "S2", "side": "hot"},
        {"block": "HX1", "direction": "in", "stream": "CW1", "side": "cold"},
        {"block": "HX1", "direction": "out", "stream": "CW2", "side": "cold"},
        {"block": "H1", "direction": "in", "stream": "S1", "side": ""},
        {"block": "H1", "direction": "out", "stream": "S2", "side": ""},
    ]
    reader.enrich_side_temperatures(streams, blocks, connections)
    check_eq("设备温度:换热器热侧进 120 ℃", blocks[0]["inlet_temp_c"], 120.0)
    check_eq("设备温度:换热器热侧出 80 ℃", blocks[0]["outlet_temp_c"], 80.0)
    check_eq("设备温度:换热器冷侧进 25 ℃", blocks[0]["cold_in_temp_c"], 25.0)
    check_eq("设备温度:换热器冷侧出 35 ℃", blocks[0]["cold_out_temp_c"], 35.0)
    check_eq("设备温度:普通设备进出口温度", blocks[1]["inlet_temp_c"], 120.0)
    check_eq("设备温度:非换热设备冷侧留空", blocks[1]["cold_in_temp_c"], None)
    check_eq("设备温度:出口汽化率取主出口流股", blocks[0]["outlet_vap_frac"], 0.5)
    check_eq("设备温度:没有连接的设备全部留空", blocks[2]["inlet_temp_c"], None)


def test_family_export() -> None:
    """设备页「导出 Excel」:单分类工作簿的表名/列/NPSHₐ/未知分类报错。"""
    import io
    from pathlib import Path

    import openpyxl

    from app.export import family_xlsx as family_export
    from app.store import db

    scratch = Path("data/preview/_selftest-family-export.db")
    scratch.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = db.open_db(str(scratch))
        case = {
            "model": {"path": "x.apw", "name": "x.apw", "read_at": "2026-11-14 00:00:00"},
            "convergence": {},
            "streams": [],
            "columns": [],
            "connections": [],
            "blocks": [
                {
                    "name": "P1",
                    "type": "Pump",
                    "status": 0,
                    "message": "",
                    "flow_m3h": 8.5,
                    "head_m": 41.5,
                    "pump_eff": 0.4,
                    "power_kw": 131.466,
                    "motor_kw": 160.0,
                    "p_in_bar": 1.8,
                    "p_out_bar": 2.1,
                    "dp_bar": 0.3,
                    "rho_kgm3": 900.0,
                    "mu_cp": 0.845,
                    "pump_vap_frac": 0.0,
                    "pv_bar": 0.3,
                    "raw": {},
                    "balance": {},
                },
                {
                    "name": "E1",
                    "type": "Heater",
                    "status": 1,
                    "message": "测试",
                    "duty_kw": 12.5,
                    "area_m2": 3.2,
                    "top_temp_c": 80.0,
                    "dh_kw": 12.4,
                    "ref_kw": 12.5,
                    "balance_state": "ok",
                    "balance_note": "通过",
                    "raw": {},
                    "balance": {},
                },
                {
                    "name": "T1",
                    "type": "RadFrac",
                    "status": 0,
                    "message": "",
                    "nstage": 20,
                    "top_temp_c": 40.0,
                    "bottom_temp_c": 120.0,
                    "cond_duty_kw": -500.0,
                    "reb_duty_kw": 700.0,
                    "raw": {},
                    "balance": {},
                },
            ],
            "stats": {
                "stream_count": 0,
                "block_count": 3,
                "column_count": 0,
                "connection_count": 0,
                "elapsed_sec": 0,
            },
            "unit_gaps": {},
        }
        cid = db.save_case(conn, case)

        data, label, fname = family_export.build_family_workbook(conn, cid, "pump")
        check_eq("分类导出:泵页表名", label, "泵")
        check("分类导出:文件名带分类", "_pump_" in fname, fname)
        ws = openpyxl.load_workbook(io.BytesIO(data)).active
        head = [c.value for c in ws[1]]
        check("分类导出:泵表头含 NPSHₐ", "NPSHₐ m" in head, str(head))
        row = [c.value for c in ws[2]]
        npsh_col = head.index("NPSHₐ m")
        check(
            "分类导出:NPSHₐ = (1.8-0.3)/(900g) ≈ 17.0 m",
            row[npsh_col] == 17.0,
            f"实际 {row[npsh_col]!r}",
        )
        check_eq("分类导出:泵行 p_v 取 4 位", row[head.index("p_v bar")], 0.3)
        check("分类导出:泵表头含粘度", "粘度 cP" in head, str(head))
        check_eq("分类导出:泵行粘度 0.845 cP", row[head.index("粘度 cP")], 0.845)

        # 泵分类多带一页选型提示词:与页面按钮、AI 包同一份正文
        from app.design import pump_prompt

        wb_pump = openpyxl.load_workbook(io.BytesIO(data))
        check_eq("分类导出:泵分类多带提示词页", wb_pump.sheetnames, ["泵", "选泵提示词"])
        sheet_lines = [
            "" if cell.value is None else str(cell.value)
            for (cell,) in wb_pump["选泵提示词"].iter_rows(min_col=1, max_col=1)
        ]
        check_eq(
            "分类导出:提示词页逐行等于常量",
            "\n".join(sheet_lines).rstrip("\n"),
            pump_prompt.PUMP_SELECTION_PROMPT.rstrip("\n"),
        )

        data, label, _ = family_export.build_family_workbook(conn, cid, "heat")
        ws = openpyxl.load_workbook(io.BytesIO(data)).active
        head = [c.value for c in ws[1]]
        check_eq("分类导出:换热页表名", label, "换热")
        check("分类导出:换热表头含热负荷与状态", "热负荷 kW" in head and "状态" in head, str(head))
        check_eq(
            "分类导出:非泵分类不带提示词页",
            openpyxl.load_workbook(io.BytesIO(data)).sheetnames,
            ["换热"],
        )
        check(
            "分类导出:换热表头含面积与冷热侧温度",
            "面积 m²" in head and "冷侧进 ℃" in head and "进口 ℃" in head,
            str(head),
        )
        check_eq("分类导出:换热只导本分类(1 行)", ws.max_row, 2)

        data, label, _ = family_export.build_family_workbook(conn, cid, "column")
        ws = openpyxl.load_workbook(io.BytesIO(data)).active
        head = [c.value for c in ws[1]]
        check_eq("分类导出:塔页表名", label, "塔")
        check(
            "分类导出:塔表头含冷凝器/再沸器负荷",
            "冷凝器 kW" in head and "再沸器 kW" in head,
            str(head),
        )
        check_eq("分类导出:塔只导本分类(1 行)", ws.max_row, 2)

        try:
            family_export.build_family_workbook(conn, cid, "nope")
            ok = False
        except ValueError:
            ok = True
        check("分类导出:未知分类抛 ValueError", ok)

        try:
            family_export.build_family_workbook(conn, 999999, "pump")
            ok = False
        except LookupError:
            ok = True
        check("分类导出:不存在的工况抛 LookupError", ok)
        conn.close()
    finally:
        try:
            scratch.unlink(missing_ok=True)
        except OSError:
            pass


def test_pump_prompt() -> None:
    """选泵提示词:一份正文、三个出口(页面按钮 / Excel 工作表 / AI 包)。

    最要紧的是字段覆盖断言 —— 导出加了列而提示词没提,AI 会把那列当没看见,
    而这种漏改在页面上完全看不出来(上一轮加粘度列就差点漏掉提示词)。
    """
    from pathlib import Path as _P

    from app.design import pump_prompt
    from app.export import family_xlsx as family_export

    text = pump_prompt.PUMP_SELECTION_PROMPT
    check("提示词:正文非空", len(text) > 1500, f"实际 {len(text)} 字符")
    check("提示词:开头是角色设定", text.startswith("# 角色与任务"), text[:16])

    missing = [h for h in family_export.PUMP_HEADERS if h not in text]
    check(
        "提示词:覆盖泵表每一列",
        not missing,
        f"没提到 {missing};导出加列时同步改 app/design/pump_prompt.py",
    )

    html = _P("app/web/index.html").read_text(encoding="utf-8")
    check("提示词:泵页有复制按钮", 'id="pumpPromptCopy"' in html)
    check("提示词:有操作指引浮层", 'id="pumpPromptTip"' in html)
    check(
        "提示词:浮层文案含三段要点",
        "已复制 AI 选泵提示词！" in html
        and "上传刚导出的 Excel 文件并粘贴提示词" in html
        and "剧毒（如 HCN）" in html,
    )
    from tests._harness import frontend_js

    js = frontend_js()  # 提示词相关代码在 views/pump.js,统一扫全部前端 JS
    check(
        "提示词:前端从接口取正文",
        "fetchPumpPromptText" in js and "/api/pump-prompt" in js,
        "前端不许内置第二份",
    )
    from app.api import routes as route_table

    check(
        "提示词:接口已注册",
        any(
            item.method == "GET" and item.pattern.pattern.endswith("/api/pump-prompt")
            for item in route_table.ROUTES
        ),
        "路由表里没有 GET /api/pump-prompt",
    )


def test_block_columns() -> None:
    """blocks 表:新库建表必须带第二期泵列(老库补列在同一条 _BLOCK_ADDED_COLUMNS 上)。"""
    from pathlib import Path

    from app.store import db

    need = {
        "head_m",
        "flow_m3h",
        "pump_eff",
        "p_in_bar",
        "p_out_bar",
        "rho_kgm3",
        "pump_vap_frac",
        "motor_kw",
        "pv_bar",
        "pv_source",
        "inlet_temp_c",
        "outlet_temp_c",
        "cold_in_temp_c",
        "cold_out_temp_c",
        "outlet_vap_frac",
        "cond_duty_kw",
        "reb_duty_kw",
    }
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
            "convergence": {},
            "streams": [],
            "columns": [],
            "connections": [],
            "blocks": [
                {
                    "name": "P1",
                    "type": "Pump",
                    "power_kw": 131.466,
                    "head_m": 41.5,
                    "flow_m3h": 8.5,
                    "pump_eff": 0.4,
                    "p_in_bar": 1.8,
                    "p_out_bar": 2.1,
                    "rho_kgm3": 947.0,
                    "pump_vap_frac": 0.0,
                    "motor_kw": 160.0,
                    "status": 0,
                    "message": "",
                    "raw": {},
                    "balance": {},
                },
                {
                    "name": "P2",
                    "type": "Pump",
                    "status": 0,
                    "message": "",
                    "pv_bar": 0.5338,
                    "pv_source": "aspen",
                    "raw": {},
                    "balance": {},
                },
            ],
            "stats": {
                "stream_count": 0,
                "block_count": 1,
                "column_count": 0,
                "connection_count": 0,
                "elapsed_sec": 0,
            },
            "unit_gaps": {},
        }
        cid = db.save_case(conn, case)
        check_eq("pv:初始为空", dict(db.read_blocks(conn, cid)[0])["pv_bar"], None)
        check("pv:读入时来源记为 aspen", dict(db.read_blocks(conn, cid)[1])["pv_source"] == "aspen")
        check("pv:写入返回 True", db.set_pump_pv(conn, cid, "P1", 3.2) is True)
        check_eq("pv:读回 3.2", dict(db.read_blocks(conn, cid)[0])["pv_bar"], 3.2)
        check_eq(
            "pv:手填来源记为 manual", dict(db.read_blocks(conn, cid)[0])["pv_source"], "manual"
        )
        check_eq("pv:设备名写错返回 False", db.set_pump_pv(conn, cid, "NOPE", 1.0), False)
        db.set_pump_pv(conn, cid, "P1", None)
        check_eq("pv:可以清空", dict(db.read_blocks(conn, cid)[0])["pv_bar"], None)
        check_eq("pv:清空时来源一并清掉", dict(db.read_blocks(conn, cid)[0])["pv_source"], None)
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
                "convergence": {},
                "streams": [],
                "columns": [],
                "connections": [],
                "blocks": [
                    {
                        "name": n,
                        "type": "Pump",
                        "status": 0,
                        "message": "",
                        "raw": {},
                        "balance": {},
                    }
                    for n in names
                ],
                "stats": {
                    "stream_count": 0,
                    "block_count": len(names),
                    "column_count": 0,
                    "connection_count": 0,
                    "elapsed_sec": 0,
                },
                "unit_gaps": {},
            }
            return db.save_case(conn, case)

        old = make("m.apw", ["P1", "P2"])
        db.set_pump_pv(conn, old, "P1", 3.2)
        db.set_pump_pv(conn, old, "P2", 1.0)

        new = make("m.apw", ["P1", "P2", "P3"])
        db.set_pump_pv(conn, new, "P2", 9.9)  # 手填过,不能被覆盖
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
