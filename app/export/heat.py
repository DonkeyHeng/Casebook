"""换热器导出:EDR 输入参数清单(xlsx,三页签)。

页签结构见 `docs/heat-exchanger-selection-plan.md` §9.3:

* `EDR工艺输入` —— 一台设备两行(管程 / 壳程),给 EDR 的 Process 页;
* `EDR结构输入` —— 一台一行,给 EDR 的 Geometry 页(标准行或人工覆盖);
* `结果与提示` —— 需求面积、公称面积、U、压降、判定与缺失项。

只导"已经算出来的"与"已经存下来的人工决定";空值留空并写说明,不补 0、
不把假设值写成校核值。结构输入优先取人工覆盖的几何,没有覆盖时取推荐标准行
(与抽屉里看到的是同一次 `rating.evaluate()` 结果)。
"""

from __future__ import annotations

import io

from app.design.heat import assemble, params, rating, standards
from app.design.heat import data as hx_data
from app.store import db, heat_config

SHEET_PROCESS = "EDR工艺输入"
SHEET_GEOMETRY = "EDR结构输入"
SHEET_RESULT = "结果与提示"
SHEET_NOTE = "说明"

PROCESS_HEADERS = [
    "位号",
    "侧别",
    "管程/壳程",
    "流股",
    "相态",
    "质量流量 kg/h",
    "体积流量 m³/h",
    "进口温度 ℃",
    "出口温度 ℃",
    "进口压力 bar",
    "出口压力 bar",
    "允许压降 bar",
    "设计温度 ℃",
    "设计压力 bar",
    "污垢热阻 m²·K/W",
    "数据来源",
]

GEOMETRY_HEADERS = [
    "位号",
    "型式",
    "壳径 mm",
    "管径×壁厚 mm",
    "管长 mm",
    "管数",
    "管程",
    "管心距 mm",
    "排列",
    "折流板型式",
    "间距 mm",
    "圆缺率",
    "壳体材质",
    "管束材质",
    "标准行",
    "来源",
]

RESULT_HEADERS = [
    "位号",
    "A_req m²",
    "A_std m²",
    "裕量 %",
    "U_back W/(m²·K)",
    "U_calc W/(m²·K)",
    "Δp 管 bar",
    "Δp 壳 bar",
    "判定 L1",
    "判定 L2",
    "拦截/告警",
    "缺失项",
    "备注",
]

PHASE_TEXT = {
    "liquid": "液相",
    "gas": "气相",
    "two_phase": "两相",
    "condensing": "冷凝",
    "boiling": "蒸发",
}
ARRANGE_TEXT = {
    "triangular": "正三角形",
    "rotated_triangular": "转角三角形",
    "square": "正方形",
    "rotated_square": "转角正方形",
}
UTILITY_TEXT = {
    "aspen": "公用工程实测",
    "preset": "公用工程预设",
    "manual": "公用工程人工",
}
GEOM_SOURCE_TEXT = {"standard": "标准系列行", "manual": "人工覆盖"}
VERDICT_TEXT = {
    "blocked": "已拦截",
    "unknown": "缺数据",
    "fail": "未通过",
    "reference": "参考",
    "pass": "简捷通过",
}


def collect(conn, case_id: int):
    model_key = db.model_key(conn, case_id) or ""
    blocks = [dict(b) for b in db.read_blocks(conn, case_id)]
    conns = [dict(c) for c in db.read_connections(conn, case_id)]
    streams = [dict(s) for s in db.read_streams(conn, case_id)]
    designs = {row["block"]: dict(row) for row in db.read_hx_design(conn, model_key)}
    selections = {row["block"]: dict(row) for row in db.read_hx_selection(conn, model_key)}
    fouling, _, _ = heat_config.load_config("fouling", hx_data.FOULING_DEFAULTS)
    u_ref, _, _ = heat_config.load_config("u-reference", hx_data.U_REFERENCE_DEFAULTS)
    presets, _, _ = heat_config.load_config("utility-presets", hx_data.UTILITY_PRESET_DEFAULTS)
    configs = {"fouling": fouling, "u-reference": u_ref, "utility-presets": presets}
    return assemble.assemble(blocks, conns, streams, designs, selections, configs)


# ---------------------------------------------------------------- 每台设备的 L2


def _side_props(case: dict, props_rows: list[dict]) -> dict:
    """把 `hx_props` 缓存整成 `side_props`;没有缓存的侧照样给一条 missing 记录。"""
    by_stream = {row["stream"]: dict(row) for row in props_rows}
    out = {}
    for key in ("hot", "cold", "process", "utility"):
        side = (case.get("sides") or {}).get(key)
        if not side:
            continue
        out[key] = params.side_props(side, by_stream.get(side.get("stream")), case.get("duty_kw"))
    return out


def _l2(row: dict, props_rows) -> dict:
    """按缓存物性算一遍 L2(不连 Aspen)。"""
    case = row["case"]
    side_props = _side_props(case, props_rows)
    return rating.evaluate(
        case,
        side_props,
        stored=row.get("design_row") or {},
        snap=standards.snapshot(),
    )


def _selected_geometry(row: dict, l2: dict) -> dict:
    """结构输入取哪条几何:人工覆盖优先,其次推荐标准行,都没有就空。"""
    stored = row.get("design_row") or {}
    if stored.get("geom_source") == "manual" and stored.get("geom_tube_od_mm"):
        geom = {k[5:]: v for k, v in stored.items() if k.startswith("geom_")}
        geom["area_nominal_m2"] = stored.get("area_nominal_m2")
        geom["geom_source"] = "manual"
        geom["std_row_key"] = stored.get("std_row_key")
        return geom
    candidate = l2.get("recommended") or (l2.get("candidates") or [None])[0]
    if not candidate:
        return {}
    return {
        "shell_id_mm": candidate.get("shell_id_mm"),
        "tube_od_mm": candidate.get("tube_od_mm"),
        "tube_wall_mm": candidate.get("tube_wall_mm"),
        "tube_length_mm": candidate.get("tube_length_mm"),
        "tube_passes": candidate.get("tube_passes"),
        "tube_pitch_mm": candidate.get("tube_pitch_mm"),
        "tube_count": candidate.get("tube_count"),
        "arrangement": candidate.get("arrangement"),
        "area_nominal_m2": candidate.get("area_std_m2"),
        "baffle_mm": candidate.get("baffle_mm"),
        "std_row_key": candidate.get("std_row_key"),
        "geom_source": "standard" if candidate.get("std_row_key") else "manual",
    }


# ---------------------------------------------------------------- 行构造


def _phase(side: dict, side_key: str) -> str:
    if side_key == "utility":
        return side.get("phase") or "unknown"
    vap_in = side.get("vap_in")
    vap_out = side.get("vap_out")
    if vap_in is None:
        return "unknown"
    if vap_out is not None and abs(vap_out - vap_in) > params.PHASE_EPS:
        return "two_phase"
    return "gas" if vap_in >= 0.5 else "liquid"


def _process_rows(row: dict) -> list[dict]:
    case = row["case"]
    tube_fluid = case.get("tube_fluid")
    out = []
    for key in ("hot", "cold", "process", "utility"):
        side = (case.get("sides") or {}).get(key)
        if not side:
            continue
        role = "管程" if key == tube_fluid else "壳程"
        cond = (case.get("design") or {}).get("tube" if role == "管程" else "shell") or {}
        fouling = (case.get("fouling") or {}).get("tube" if role == "管程" else "shell")
        source = (
            "Aspen 实测"
            if key != "utility"
            else UTILITY_TEXT.get((case.get("utility") or {}).get("source"), "公用工程")
        )
        out.append(
            {
                "位号": row.get("block"),
                "侧别": {"hot": "热侧", "cold": "冷侧", "process": "工艺侧", "utility": "公用工程"}[
                    key
                ],
                "管程/壳程": role,
                "流股": side.get("stream") or "",
                "相态": PHASE_TEXT.get(_phase(side, key), "未知"),
                "质量流量 kg/h": side.get("mass_flow_kgh"),
                "体积流量 m³/h": side.get("vol_flow_m3h"),
                "进口温度 ℃": side.get("t_in_c"),
                "出口温度 ℃": side.get("t_out_c"),
                "进口压力 bar": side.get("p_bar"),
                "出口压力 bar": None,
                "允许压降 bar": cond.get("dp_allow_bar"),
                "设计温度 ℃": cond.get("t_design_c"),
                "设计压力 bar": cond.get("p_design_bar"),
                "污垢热阻 m²·K/W": fouling,
                "数据来源": source,
            }
        )
    return out


def _geometry_row(row: dict, l2: dict, selection: dict) -> dict:
    geom = _selected_geometry(row, l2)
    od = geom.get("tube_od_mm")
    wall = geom.get("tube_wall_mm")
    code = (row.get("model_code") or {}).get("code") or (row.get("model_code") or {}).get(
        "code_partial"
    )
    return {
        "位号": row.get("block"),
        "型式": code or "",
        "壳径 mm": geom.get("shell_id_mm"),
        "管径×壁厚 mm": f"{od:g}×{wall:g}" if od and wall is not None else None,
        "管长 mm": geom.get("tube_length_mm"),
        "管数": geom.get("tube_count"),
        "管程": geom.get("tube_passes"),
        "管心距 mm": geom.get("tube_pitch_mm"),
        "排列": ARRANGE_TEXT.get(geom.get("arrangement"), geom.get("arrangement")),
        "折流板型式": "单弓形" if geom.get("baffle_mm") else None,
        "间距 mm": geom.get("baffle_mm"),
        "圆缺率": None,
        "壳体材质": selection.get("shell_material"),
        "管束材质": selection.get("tube_material"),
        "标准行": geom.get("std_row_key") or "",
        "来源": GEOM_SOURCE_TEXT.get(geom.get("geom_source"), "未选几何"),
    }


def _result_row(row: dict, l2: dict) -> dict:
    case = row["case"]
    cand = l2.get("manual") or l2.get("recommended") or (l2.get("candidates") or [{}])[0] or {}
    case.get("design") or {}
    missing = []
    if l2.get("std_skipped"):
        missing.append("标准系列表")
    for item in l2.get("props_missing") or []:
        if item not in missing:
            missing.append(item)
    for side in (case.get("sides") or {}).values():
        if side.get("vol_flow_m3h") is None and side.get("stream"):
            label = f"{side.get('stream')} 体积流量"
            if label not in missing:
                missing.append(label)
    notes = []
    if l2.get("std_skipped"):
        est = l2.get("estimate") or {}
        if est.get("note"):
            notes.append(est["note"])
    if l2.get("notes"):
        notes.extend(str(n) for n in l2["notes"])
    warn = list(case.get("blocker_text") or []) + list(case.get("warning_text") or [])
    warn.extend(str(w) for w in (cand.get("warnings") or []))
    return {
        "位号": row.get("block"),
        "A_req m²": (case.get("area") or {}).get("required_m2"),
        "A_std m²": cand.get("area_std_m2") or cand.get("area_m2"),
        "裕量 %": (
            round((case.get("area") or {}).get("margin") * 100)
            if (case.get("area") or {}).get("margin") is not None
            else None
        ),
        "U_back W/(m²·K)": (case.get("u") or {}).get("back_calc_wm2k"),
        "U_calc W/(m²·K)": cand.get("u_dirty_wm2k"),
        "Δp 管 bar": (cand.get("tube") or {}).get("dp_bar"),
        "Δp 壳 bar": (cand.get("shell") or {}).get("dp_bar"),
        "判定 L1": VERDICT_TEXT.get(case.get("verdict"), case.get("verdict")),
        "判定 L2": l2.get("verdict_text") or "缺数据",
        "拦截/告警": ";".join(dict.fromkeys(warn)),
        "缺失项": ";".join(dict.fromkeys(missing)),
        "备注": " ".join(dict.fromkeys(notes)),
    }


# ---------------------------------------------------------------- 写 xlsx


def _write_sheet(ws, headers, rows, widths, hint, freeze="A3"):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E79")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.append([hint] + [None] * (len(headers) - 1))
    hint_cell = ws.cell(row=2, column=1)
    hint_cell.font = Font(size=8, italic=True, color="595959")
    hint_cell.alignment = Alignment(horizontal="left", vertical="center")
    for item in rows:
        ws.append([item.get(h) for h in headers])
    for index, head in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(index)].width = widths.get(head, 13)
    ws.row_dimensions[1].height = 34
    ws.freeze_panes = freeze
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{ws.max_row}"


def _note_sheet(wb) -> None:
    from openpyxl.styles import Alignment, Font

    note = wb.create_sheet(SHEET_NOTE)
    lines = [
        ("口径", "压力 bar、温度 ℃、面积 m²、污垢 m²·K/W、U W/(m²·K)、长度 mm"),
        ("空值", "留空 = 未取得,不是 0;不要自行填 0 后当成实测"),
        ("页签", "工艺输入 = Process 页;结构输入 = Geometry 页;结果与提示 = 判定与缺失项"),
        ("面积来源", "aspen = Aspen 简捷面积(假设 U 推算,非 rating);manual = 人工;edr = EDR 校核"),
        ("结构来源", "标准系列行 = GB/T 28712.2 选行;人工覆盖 = 抽屉里手填的几何"),
        (
            "判定",
            "L1 只到 reference;L2 的 pass 需全部输入实测且压降/面积都过;含假设一律顶到 reference",
        ),
        ("边界", "只覆盖管壳式单相显热;板式/空冷器、机械强度、振动与两相分区不在本期"),
        ("", ""),
        ("来源标签", ""),
    ]
    for key, value in hx_data.SOURCE_LABELS.items():
        lines.append((key, value))
    for line in lines:
        note.append(line)
    note.column_dimensions["A"].width = 16
    note.column_dimensions["B"].width = 84
    for cell in note["A"]:
        cell.font = Font(bold=True)
    for cell in note["B"]:
        cell.alignment = Alignment(wrap_text=True, vertical="top")


def build_edr_checklist(conn, case_id: int) -> bytes:
    """EDR 输入参数清单 xlsx:工艺输入 / 结构输入 / 结果与提示 三页签 + 说明。"""
    import openpyxl

    rows = collect(conn, case_id)
    if not rows:
        raise LookupError("这个工况没有换热类设备")

    props_rows = db.read_hx_props(conn, case_id)
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = SHEET_PROCESS
    ws2 = wb.create_sheet(SHEET_GEOMETRY)
    ws3 = wb.create_sheet(SHEET_RESULT)

    process, geometry, result = [], [], []
    for row in rows:
        l2 = _l2(row, props_rows)
        process.extend(_process_rows(row))
        geometry.append(_geometry_row(row, l2, row.get("selection") or {}))
        result.append(_result_row(row, l2))

    _write_sheet(
        ws1,
        PROCESS_HEADERS,
        process,
        {"管程/壳程": 9, "流股": 14, "相态": 9, "数据来源": 18, "位号": 12},
        "单位:mm 不用;流量 kg/h 与 m³/h,温度 ℃,压力 bar,污垢 m²·K/W。空值 = 未取得。",
    )
    _write_sheet(
        ws2,
        GEOMETRY_HEADERS,
        geometry,
        {"位号": 12, "型式": 32, "管径×壁厚 mm": 14, "标准行": 24, "来源": 12},
        "单位:mm;「标准行」是 GB/T 28712.2 的行键(DN|外径|管心距|管程|管长)。空值 = 未选几何。",
    )
    _write_sheet(
        ws3,
        RESULT_HEADERS,
        result,
        {"位号": 12, "A_req m²": 11, "A_std m²": 11, "拦截/告警": 44, "缺失项": 30, "备注": 40},
        "A_req 含裕量;U_calc 按 Dittus-Boelter / Kern 算,只在物性齐全时给出;空值 = 未取得。",
    )
    _note_sheet(wb)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
