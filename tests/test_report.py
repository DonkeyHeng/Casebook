"""衡算报告、物料宽表、波及范围、三个出口的一致性。

用例正文原样搬自 `tools/selftest.py`(迁移到 pytest 时只改了位置)。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.design import mass_balance as mass_rules
from app.design import stream_table as stream_rules
from app.store import db
from app.store import impact as impact_rules
from tests._harness import (
    _blocks,
    _conns,
    _mb_conns,
    check,
    check_eq,
)


def test_stream_table() -> None:
    """stream_table.build():流股做列、属性与组分做行(设计文档里的「物料平衡书」形态)。"""
    streams = [
        {
            "name": "0101",
            "temperature_c": 25.0,
            "pressure_bar": 1.0,
            "mass_flow_kgh": 100.0,
            "mole_flow_kmolh": 10.0,
            "vol_flow_m3h": 0.1,
            "vap_frac": 0.0,
        },
        {
            "name": "0102",
            "temperature_c": 60.0,
            "pressure_bar": 1.0,
            "mass_flow_kgh": 100.0,
            "mole_flow_kmolh": 10.0,
            "vol_flow_m3h": 0.2,
            "vap_frac": 1.0,
        },
        {
            "name": "W-1",
            "temperature_c": 25.0,
            "pressure_bar": 1.0,
            "mass_flow_kgh": 50.0,
            "mole_flow_kmolh": 5.0,
            "vol_flow_m3h": 0.05,
            "vap_frac": 0.0,
        },
    ]
    conns = _mb_conns(("E1", "in", "0101"), ("E1", "out", "0102"))
    comp = {
        "0101": [
            {"component": "WATER", "x_liq": 1.0, "y_vap": 0.0, "mass_flow_kgh": 90.0},
            {"component": "BEN", "x_liq": 0.0, "y_vap": 0.0, "mass_flow_kgh": 10.0},
        ],
        "0102": [
            {"component": "WATER", "x_liq": 1.0, "y_vap": 1.0, "mass_flow_kgh": 100.0},
            {"component": "BEN", "x_liq": 0.0, "y_vap": 0.0, "mass_flow_kgh": None},
        ],
    }
    parts = {"01": {"label": "01 工段", "in": ["0101"], "out": ["0102"]}}
    res = stream_rules.build(streams, conns, parts, comp)

    zone = res["zones"][0]
    check_eq("宽表: 进口列数", len(zone["in_columns"]), 1)
    check_eq("宽表: 组分顺序按出现顺序", res["components"], ["WATER", "BEN"])
    check_eq(
        "宽表: 段合计与误差",
        (zone["mass_in_kgh"], zone["mass_out_kgh"], zone["state"]),
        (100.0, 100.0, mass_rules.PASS),
    )
    check_eq("宽表: 读到组分质量流量", zone["has_mass_comp"], True)

    col = zone["in_columns"][0]
    check_eq("宽表: 相态(液相)", col["phase"], "液相")
    check_eq(
        "宽表: 组分摩尔流量 = 摩尔分率 × 总摩尔流量", round(col["mole_comp"]["WATER"], 3), 10.0
    )
    check_eq("宽表: 组分质量流量取库值", col["mass_comp"]["BEN"], 10.0)

    # 气相流股用 y_vap,液相用 x_liq:同一条流股两相时按汽化率加权
    gas = zone["out_columns"][0]
    check_eq("宽表: 气相流股用 y_vap", gas["phase"], "气相")
    check_eq("宽表: 气相的组分摩尔流量", round(gas["mole_comp"]["WATER"], 3), 10.0)

    # 全流程:没有上游的是外部进料、没有下游的是出界流股
    # W-1 既没有上游也没有下游(孤立流股),两头都会算进去 —— 这是有意的:
    # 全流程表宁可在两边都列出来,也不要把一股料悄悄丢掉
    check_eq("宽表: 全流程进口数", len(res["overall"]["in_columns"]), 2)
    check_eq("宽表: 全流程出口数", len(res["overall"]["out_columns"]), 2)
    check_eq("宽表: 年量按 8000 h 折算", round(res["summary"]["feed"][0]["t_per_year"], 1), 800.0)

    # 没有组分质量流量(老库)→ 整段不出,并标出来
    res2 = stream_rules.build(
        streams,
        conns,
        parts,
        {"0101": [{"component": "WATER", "x_liq": 1.0, "y_vap": 0.0, "mass_flow_kgh": None}]},
    )
    check_eq("宽表: 老库没有组分质量流量 → 不出质量段", res2["zones"][0]["has_mass_comp"], False)
    check_eq(
        "宽表: 老库仍能出摩尔段",
        round(res2["zones"][0]["in_columns"][0]["mole_comp"]["WATER"], 3),
        10.0,
    )

    # 相态文字:0/1/中间值/缺失
    check_eq("宽表: 汽化率 0 → 液相", stream_rules.phase_text(0.0), "液相")
    check_eq("宽表: 汽化率 1 → 气相", stream_rules.phase_text(1.0), "气相")
    check_eq("宽表: 汽化率 0.25 → 两相", stream_rules.phase_text(0.25), "两相(0.250)")
    check_eq("宽表: 汽化率缺失 → —", stream_rules.phase_text(None), "—")


def test_report_export() -> None:
    """report_export:数据组装 + .docx 渲染(缺 python-docx 时不算失败)。"""
    from app.export import report as report_export

    scratch = Path("data/preview/_selftest-report.db")
    scratch.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile("samples/case-23-error.db", scratch)
        conn = db.open_db(str(scratch))
        case_id = db.list_cases(conn)[0]["id"]

        data = report_export.build_data(conn, case_id)
        check("report: 组装出界区衡算", data["zone"]["summary"]["total"] >= 1)
        check("report: 组装出逐设备表", data["devices"]["summary"]["total"] >= 1)
        check("report: 组装出能量表", len(data["energy"]["devices"]) >= 1)
        check("report: 未给边界时标记为自动草稿", data["auto_boundary"] is True)
        check_eq(
            "report: 文件名带工况号与 balance",
            report_export.filename(case_id).startswith(f"Casebook_case{case_id}_balance_"),
            True,
        )

        try:
            blob = report_export.render(data)
        except ImportError:
            print("  (没装 python-docx,跳过渲染用例)")
            blob = None
        if blob is not None:
            check("report: 生成的是 docx(zip 头)", blob[:2] == b"PK")
            check("report: docx 不是空文件", len(blob) > 10_000, f"实际 {len(blob)} 字节")
            import io as _io

            from docx import Document

            doc = Document(_io.BytesIO(blob))
            check("report: 表格数量合理", len(doc.tables) >= 4, f"实际 {len(doc.tables)} 张")
            text = "\n".join(p.text for p in doc.paragraphs)
            check("report: 封面标题在", report_export.TITLE in text)
            check("report: 附注写了非工程设计依据", "非工程设计依据" in text)
            check("report: 附注写了数据来源", "数据来源" in text)
            # 按册导出:物料册没有能量章,能量册没有物料章,文件名带册别
            mass_doc = report_export.render(data, "mass")
            mass_text = "\n".join(
                p.text
                for p in __import__("docx").Document(__import__("io").BytesIO(mass_doc)).paragraphs
            )
            check("report: 物料册不含能量章", "二、能量衡算" not in mass_text)
            check("report: 物料册有封面册别", "物料衡算册" in mass_text)
            energy_doc = report_export.render(data, "energy")
            energy_text = "\n".join(
                p.text
                for p in __import__("docx")
                .Document(__import__("io").BytesIO(energy_doc))
                .paragraphs
            )
            check("report: 能量册不含物料章", "一、物料衡算" not in energy_text)
            check("report: 能量册有封面册别", "能量衡算册" in energy_text)
            check_eq(
                "report: 文件名带册别",
                (
                    report_export.filename(case_id, "mass").count("_mass_"),
                    report_export.filename(case_id, "energy").count("_energy_"),
                ),
                (1, 1),
            )
        conn.close()
    finally:
        try:
            scratch.unlink(missing_ok=True)
        except OSError:
            pass


def test_report_picked() -> None:
    """勾选设备后的明细:焓流表与能量平衡表要真的渲染出来。

    这条路径只在页面上勾了设备时才走,长期没有自测覆盖 —— 之前把 rows 按用途
    改名时漏改了一处引用,只有真勾选才会 NameError,最后是 ruff 的 F821 捞出来的。
    """
    import io as _io

    from app.export import report as report_export

    scratch = Path("data/preview/_selftest-report-picked.db")
    scratch.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile("samples/case-23-error.db", scratch)
        conn = db.open_db(str(scratch))
        try:
            case_id = db.list_cases(conn)[0]["id"]
            base = report_export.build_data(conn, case_id)
            devices = base["energy"]["devices"]
            check("report-picked: 样本里有能量设备", bool(devices))
            if not devices:
                return
            picked = [devices[0]["block"]]
            data = report_export.build_data(conn, case_id, picked=picked)
            check_eq("report-picked: picked 传进数据里", data.get("picked"), picked)
            try:
                blob = report_export.render(data, "energy")
            except ImportError:
                print("  (没装 python-docx,跳过渲染用例)")
                return
            from docx import Document

            doc = Document(_io.BytesIO(blob))
            texts = [p.text for p in doc.paragraphs]
            for table in doc.tables:
                for row in table.rows:
                    texts.append(" | ".join(cell.text for cell in row.cells))
            joined = "\n".join(texts)
            check(
                "report-picked: 有「选中设备的进出口明细」这一节", "选中设备的进出口明细" in joined
            )
            check("report-picked: 明细里有进口焓流表", "进口焓流 kW" in joined)
            check("report-picked: 明细里有能量平衡表", "ΣHin kW" in joined)
        finally:
            conn.close()
    finally:
        scratch.unlink(missing_ok=True)


def test_impact() -> None:
    """impact.build():报错设备 → 哪些流股的数据不能用。"""
    blocks = _blocks(
        {"name": "T0202", "status": 1},
        {"name": "T0205", "status": 1},
        {"name": "T0201", "status": 2},
        {"name": "H1", "status": 0},
    )
    conns = _conns(
        ("T0202", "in", "0101", None),
        ("T0202", "out", "0102", None),
        ("T0205", "in", "0102", None),
        ("T0205", "out", "0103", None),
        ("T0201", "in", "0104", None),
        ("T0201", "out", "0105", None),
        ("H1", "in", "0106", None),
        ("H1", "out", "0107", None),
    )
    got = impact_rules.build(conns, blocks)
    by_stream = {s["stream"]: s for s in got["streams"]}

    check_eq("impact: 报错设备点名", got["error_blocks"], ["T0202", "T0205"])
    check_eq("impact: 告警设备点名", got["warning_blocks"], ["T0201"])
    check_eq("impact: 受影响流股数 = 3 + 2", got["summary"]["total"], 5)
    check_eq("impact: 报错影响流股数", got["summary"]["error"], 3)
    check_eq("impact: 告警影响流股数", got["summary"]["warning"], 2)
    check(
        "impact: 正常设备的流股不受影响",
        "0106" not in by_stream and "0107" not in by_stream,
        str(sorted(by_stream)),
    )
    check_eq(
        "impact: 同一条流股被两台报错设备夹住 → 合并成一条",
        [b["block"] for b in by_stream["0102"]["blocks"]],
        ["T0202", "T0205"],
    )
    check_eq("impact: 报错优先于告警", by_stream["0101"]["severity"], "error")
    check_eq("impact: 告警流股标记", by_stream["0104"]["severity"], "warning")
    check("impact: 报错流股排在前面", got["streams"][0]["severity"] == "error")


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

    from app.api import server
    from app.export import aipack
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
                " AND head_m IS NOT NULL",
                (row["id"],),
            ).fetchone()[0]
            if n and (best is None or n > best[1]):
                best = (row["id"], n)
        if best is None:
            print("  跳过:库里没有带扬程的泵")
            return
        cid, n_pumps = best
        blocks = {
            b["name"]: dict(b)
            for b in db.read_blocks(conn, cid)
            if str(b["type"] or "").strip().lower() == "pump"
        }
        xlsx = server.build_workbook(conn, cid)
        pack, _fname = aipack.build_zip(conn, cid)
    finally:
        conn.close()

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(xlsx), read_only=True)
    rows = list(wb["泵"].iter_rows(values_only=True))
    header = list(rows[0])
    excel = {r[0]: dict(zip(header, r, strict=False)) for r in rows[1:]}

    with zipfile.ZipFile(io.BytesIO(pack)) as zf:
        text = zf.read("pumps.csv").decode("utf-8-sig")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    csv_header = lines[0].split(",")
    csv = {ln.split(",")[0]: dict(zip(csv_header, ln.split(","), strict=False)) for ln in lines[1:]}

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
        ("粘度cP", "粘度 cP", "mu_cp"),
        ("NPSHa m", "NPSHₐ m", None),
        ("p_v bar", "p_v bar", "pv_bar"),
    ]
    bad = []
    for name, db_row in blocks.items():
        for csv_key, xls_key, db_key in pairs:
            a = num(csv.get(name, {}).get(csv_key))
            b = num(excel.get(name, {}).get(xls_key))
            if a is None and b is None:
                continue
            if a is None or b is None or abs(a - b) > 1e-6:
                bad.append(f"{name}.{csv_key}: 包 {a} / Excel {b}")
                continue
            if db_key:
                d = num(db_row.get(db_key))
                if d is not None and abs(a - d) > 1e-6:
                    bad.append(f"{name}.{db_key}: 包 {a} / 库 {d}")
    check(
        f"出口一致:AI 包 / Excel / 库 同值(工况 {cid},{n_pumps} 台泵)", not bad, ";".join(bad[:3])
    )

    # 对比页字段:接口与前端必须同一组(接口加了而界面不显示 = 白算)
    src = _Path("app/api/routes/meta.py").read_text(encoding="utf-8")
    hit = re.search(r"block_fields = \[(.*?)\]", src, re.S)
    server_fields = set(re.findall(r'"([a-z_0-9]+)"', hit.group(1))) if hit else set()
    from tests._harness import frontend_js

    js = frontend_js()  # BLOCK_COMPARE_FIELDS / FAMILY_COLS 在 views/table.js,统一扫全部前端 JS
    hit_js = re.search(r"BLOCK_COMPARE_FIELDS = \[(.*?)\];", js, re.S)
    js_fields = set(re.findall(r'key: "([a-z_0-9]+)"', hit_js.group(1))) if hit_js else set()
    check(
        "对比字段:两边都有扬程/流量",
        {"head_m", "flow_m3h"} <= server_fields and {"head_m", "flow_m3h"} <= js_fields,
        f"接口 {sorted(server_fields)} / 前端 {sorted(js_fields)}",
    )
    check(
        "对比字段:前端字段都在接口里",
        js_fields <= server_fields,
        f"前端多出 {sorted(js_fields - server_fields)}",
    )

    # 泵页表格列:粘度列在页面与三个导出里都要有(接口有 mu_cp 而页面不显示 = 白读)
    hit_pump_cols = re.search(r"pump:\s*\[(.*?)\]", js, re.S)
    pump_cols = hit_pump_cols.group(1) if hit_pump_cols else ""
    check("泵页列:表格含粘度列(visc)", '"visc"' in pump_cols, pump_cols)
    check(
        "泵页列:粘度列取 mu_cp",
        bool(re.search(r'visc:\s*\{\s*key:\s*"mu_cp"', js)),
        "BLOCK_COLS.visc 未指向 mu_cp",
    )
