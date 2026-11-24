"""换热器选型一览表 .docx —— 「换热」页的 Word 导出。

列序是冻结口径(`docs/heat-exchanger-selection-plan.md` §9.2),不跟页面列顺序走:
15 列固定,空值统一 `—`(不补 0),表尾给假设项计数与标准表状态。

版式复用 `docx_kit` 的三线表与封面,只在这里决定"这张表装什么数、每列多宽"。
"""

from __future__ import annotations

import io
import time

from app.design.heat import standards
from app.export import docx_kit as kit
from app.export import heat as heat_export
from app.store import db

TITLE = "换热器选型一览表"

#: §9.2 冻结的 15 列,任何导出都按这个顺序,不新增/删减。
HEADERS = [
    "位号",
    "设备名称",
    "型号",
    "公称面积|m²",
    "管数",
    "管长|m",
    "管径|mm",
    "管程数",
    "壳径|mm",
    "壳体材质",
    "管束材质",
    "台数",
    "执行标准",
    "数据来源",
    "备注",
]

#: 15 列合计 16.2 cm;字号降到 8 pt,优先保证位号/型号不折行。
WIDTHS = (1.3, 2.0, 2.1, 1.0, 0.7, 0.7, 0.7, 0.65, 0.7, 1.1, 1.1, 0.5, 1.1, 1.3, 1.25)

TYPE_NAMES = {
    "HeatX": "管壳式换热器",
    "MHeatX": "多股换热器",
    "Heater": "加热器",
    "HeaterX": "加热器",
}

SHELL_STD = ("HeatX", "MHeatX")

AREA_SOURCE_TEXT = {
    "aspen": "Aspen简捷",
    "manual": "人工面积",
    "edr": "EDR校核",
    "none": "缺面积",
}

UTILITY_SOURCE_TEXT = {
    "aspen": "公用工程实测",
    "preset": "公用工程预设",
    "manual": "公用工程人工",
}


def _text(value, digits: int = 2) -> str:
    """数值 → 文本;空值写 `—`,不写 0。"""
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        text = f"{float(value):.{digits}f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"
    return str(value).strip() or "—"


def _num(value, digits: int = 2) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        text = f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def _data_source(case: dict) -> str:
    """数据来源 = 面积来源 + 公用工程来源;没有公用工程的场合只写面积来源。"""
    area_case = case.get("area") or {}
    area = AREA_SOURCE_TEXT.get(area_case.get("source"), "缺面积")
    if area_case.get("source") == "none" and area_case.get("required_m2"):
        # 没有公称面积但有需求面积:如实写"估算",不写"缺面积"
        area = "需求面积(估算)"
    parts = [f"面积:{area}"]
    if case.get("utility"):
        utility = UTILITY_SOURCE_TEXT.get((case.get("utility") or {}).get("source"), "公用工程")
        parts.append(utility)
    return " · ".join(parts)


def _remark(row: dict, l2: dict | None = None) -> str:
    """备注:判定 + 缺失几何 + 警告;空项不占位。"""
    case = row["case"]
    stored = row.get("design_row") or {}
    code = row.get("model_code") or {}
    verdict = {
        "blocked": "已拦截",
        "unknown": "缺数据",
        "reference": "L1 参考",
        "pass": "通过",
        "fail": "不合格",
    }.get(case.get("verdict"), "未判定")
    parts = [verdict]
    if l2:
        parts.append(f"L2:{l2.get('verdict_text') or '缺数据'}")
        if l2.get("std_skipped"):
            parts.append("未加载标准表")
    missing = code.get("missing") or []
    if missing:
        parts.append("缺:" + "/".join(str(m) for m in missing))
    geom = heat_export._selected_geometry(row, l2 or {})
    if not stored.get("area_nominal_m2") and geom.get("area_nominal_m2"):
        parts.append("公称面积取推荐标准行")
    warnings = case.get("warning_text") or case.get("warnings") or []
    for warning in warnings:
        parts.append(str(warning))
    return ";".join(parts)


def _row_values(row: dict, l2: dict | None = None) -> list[str]:
    case = row["case"]
    stored = row.get("design_row") or {}
    selection = row.get("selection") or {}
    code = row.get("model_code") or {}
    area = case.get("area") or {}
    geom = heat_export._selected_geometry(row, l2 or {})
    nominal = stored.get("area_nominal_m2") or geom.get("area_nominal_m2")
    area_value = nominal if nominal is not None else area.get("used_m2")
    length_mm = stored.get("geom_tube_length_mm") or geom.get("tube_length_mm")
    return [
        row.get("block") or "—",
        selection.get("service_name") or TYPE_NAMES.get(row.get("type"), row.get("type")) or "—",
        code.get("code") or code.get("code_partial") or "—",
        _num(area_value, 2),
        _text(stored.get("geom_tube_count") or geom.get("tube_count"), 0),
        _num(length_mm / 1000 if isinstance(length_mm, (int, float)) else None, 2),
        _num(stored.get("geom_tube_od_mm") or geom.get("tube_od_mm"), 1),
        _text(stored.get("geom_tube_passes") or geom.get("tube_passes"), 0),
        _num(stored.get("geom_shell_id_mm") or geom.get("shell_id_mm"), 1),
        _text(selection.get("shell_material")),
        _text(selection.get("tube_material")),
        _text(selection.get("qty") or 1, 0),
        _text(selection.get("std_code") or ("GB/T 151" if row.get("type") in SHELL_STD else None)),
        _data_source(case),
        _remark(row, l2),
    ]


def build_hx_docx(conn, case_id: int) -> tuple[bytes, str]:
    """生成《换热器选型一览表》,返回 (字节, 文件名)。

    Raises:
        LookupError: 没有这个工况,或该工况没有换热类设备。
        ImportError: 没装 python-docx。
    """
    case = db.get_case(conn, case_id)
    if case is None:
        raise LookupError(case_id)
    case = dict(case)

    rows = heat_export.collect(conn, case_id)
    if not rows:
        raise LookupError("这个工况没有换热类设备")
    props_rows = db.read_hx_props(conn, case_id)
    l2_rows = {row["block"]: heat_export._l2(row, props_rows) for row in rows}

    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt
    except ImportError as exc:  # pragma: no cover - 取决于运行环境
        raise ImportError("没有安装 python-docx,无法导出换热器 Word") from exc

    kit.activate("hx")
    doc = Document()
    kit._setup(doc, case, "hx")
    kit._cover(
        doc,
        case,
        "hx",
        extra_rows=[
            ("换热设备", f"{len(rows)} 台"),
            ("列数", "15 列(§9.2 冻结口径)"),
        ],
    )
    kit._body_section(doc, case, "hx")

    kit._heading(doc, TITLE, 1)
    kit._para(
        doc,
        f"本表按换热类设备逐台给出型号、几何与执行标准,共 {len(rows)} 台。"
        "面积、公用工程、U、LMTD 的来源逐台写在「数据来源」列;空值写「—」表示未取得,"
        "不是 0。本表是 L1 参考结果,不替代 EDR rating 与强度校核。",
        None,
        False,
        WD_ALIGN_PARAGRAPH.LEFT,
    )

    kit._three_line(
        doc,
        HEADERS,
        [_row_values(row, l2_rows.get(row["block"])) for row in rows],
        WIDTHS,
        caption="表 1  换热器选型明细",
        font_pt=8.0,
        center_cols=(3, 4, 5, 6, 7, 8, 11),
    )

    assumptions = sum(
        len(row["case"].get("warning_text") or row["case"].get("warnings") or []) for row in rows
    )
    blocked = sum(1 for row in rows if row["case"].get("verdict") == "blocked")
    unknown = sum(1 for row in rows if row["case"].get("verdict") == "unknown")
    kit._gap(doc, 10)
    std = standards.standard_state()
    l2_counts: dict[str, int] = {}
    for item in l2_rows.values():
        l2_counts[item.get("verdict_text") or "缺数据"] = (
            l2_counts.get(item.get("verdict_text") or "缺数据", 0) + 1
        )
    summary = (
        f"汇总:共 {len(rows)} 台;假设/未确认项 {assumptions} 项;"
        f"已拦截 {blocked} 台;缺数据 {unknown} 台。"
        f"L2 判定:"
        + "、".join(f"{k} {v} 台" for k, v in l2_counts.items())
        + f"。标准表状态:{std['label']}"
        + (f"({std['reason']})" if std.get("reason") else "")
        + "。"
    )
    para = kit._para(doc, summary, Pt(8), False, WD_ALIGN_PARAGRAPH.LEFT, space_after=4)
    for run in para.runs:
        run.font.color.rgb = kit._rgb("595959")

    notes = [
        "注 1:型号按 GB/T 151 编码格式给出;缺项以 ? 占位,完整编码需补齐壳体 DN、"
        "公称面积、管长、管径与管程数后再生成。",
        "注 2:面积来源为「Aspen简捷」时,面积由 Aspen 简捷设计里的假设 U 推算,"
        "不是 rating 结果;反算 U 仅供对照。",
        "注 3:公用工程来源为「公用工程实测」时取 Aspen 公用工程节点温度;"
        "「预设/人工」为界面选择或手填,不得写成实测。",
    ]
    for line in notes:
        note = kit._para(doc, line, Pt(7.5), False, WD_ALIGN_PARAGRAPH.LEFT, space_after=2)
        note.paragraph_format.line_spacing = 1.35
        note.paragraph_format.left_indent = Pt(24)
        note.paragraph_format.first_line_indent = Pt(-24)
        for run in note.runs:
            run.font.color.rgb = kit._rgb("595959")

    buffer = io.BytesIO()
    doc.save(buffer)
    stamp = time.strftime("%Y%m%d_%H%M")
    return buffer.getvalue(), f"Casebook_heat-exchangers_case{case_id}_{stamp}.docx"
