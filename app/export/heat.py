"""换热器导出:L1 一览表与 EDR 输入参数清单(xlsx)。

只导"已经算出来的"与"已经存下来的人工决定";空值留空并写说明,
不补 0、不把假设值写成校核值。列顺序与 `docs/heat-exchanger-selection-plan.md`
§7.3 的清单一致(同名列可直接对照 Aspen EDR 输入页)。
"""

from __future__ import annotations

import io

from app.design.heat import assemble
from app.design.heat import data as hx_data
from app.store import db, heat_config


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


def build_edr_checklist(conn, case_id: int) -> bytes:
    """EDR 输入参数清单 xlsx(按冷热侧 / 压降 / 污垢 / 几何分组)。"""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    rows = collect(conn, case_id)
    if not rows:
        raise LookupError("这个工况没有换热类设备")
    checklist = assemble.edr_checklist(rows)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "EDR输入清单"

    headers = list(checklist[0].keys())
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E79")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for item in checklist:
        ws.append([item.get(h) for h in headers])

    widths = {
        "设备位号": 12,
        "设备类型": 10,
        "用途/名称": 20,
        "热负荷 kW": 12,
        "ΔTm(自算) ℃": 12,
        "ΔTm(Aspen) ℃": 12,
        "面积 m²": 10,
        "面积来源": 10,
        "需求面积 m²": 12,
        "U 反算 W/(m²·K)": 16,
        "U 设计 W/(m²·K)": 16,
        "U 来源": 10,
        "管程侧": 10,
        "管程设计压力 bar": 16,
        "壳程设计压力 bar": 16,
        "管程设计温度 ℃": 16,
        "壳程设计温度 ℃": 16,
        "管程允许压降 bar": 16,
        "壳程允许压降 bar": 16,
        "管程污垢 m²·K/W": 18,
        "壳程污垢 m²·K/W": 18,
        "公用工程": 10,
        "型号": 34,
        "备注": 40,
    }
    for index, head in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(index)].width = widths.get(head, 14)
    ws.row_dimensions[1].height = 36
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{ws.max_row}"

    # 第二页:说明与来源口径,避免拿到 xlsx 的人把空值当 0
    note = wb.create_sheet("说明")
    lines = [
        ("口径", "压力 bar、温度 ℃、面积 m²、污垢 m²·K/W、U W/(m²·K)、长度 mm"),
        ("空值", "留空 = 未取得,不是 0;不要自行填 0 后当成实测"),
        ("面积来源", "aspen = Aspen 简捷面积(假设 U 推算,非 rating);manual = 人工;edr = EDR 校核"),
        ("公用工程", "aspen = 节点实测温度;preset = 预设假设;manual = 人工录入;无 = 未配置"),
        ("判定", "L1 只到 reference;blocked = 相变/近等温/温度交叉被拦截,请转 EDR 分段"),
        ("边界", "本清单只覆盖管壳式 L1;板式/空冷器、机械强度、振动与两相分区不在本期"),
        ("", ""),
        ("来源标签", ""),
    ]
    for key, value in hx_data.SOURCE_LABELS.items():
        lines.append((key, value))
    for line in lines:
        note.append(line)
    note.column_dimensions["A"].width = 16
    note.column_dimensions["B"].width = 80
    for cell in note["A"]:
        cell.font = Font(bold=True)
    for cell in note["B"]:
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
