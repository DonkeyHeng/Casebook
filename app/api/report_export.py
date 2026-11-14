"""衡算报告 .docx 导出 —— 「衡算报告」页与 `tools/balance_report.py` 共用。

三件事
------
1. 从库里现取三张表 + `column_stages`,**不碰 Aspen、不重算焓平衡**:
   焓平衡的 ΔH / 对照值 / 状态 / 说明直接读 `blocks` 里已存的结论;
2. 物料衡算判两层(工段界区 + 逐设备),能量衡算出设备表 + 汇总 + 塔负荷明细;
3. 用 python-docx 写成三线表 .docx,返回 `(bytes, fname)` —— 与 `family_export`
   同样的接口,Web 路由可以直接 `send_header` + 写字节。

依赖是**懒导入**的
------------------
`python-docx` 不在标准库里,老用户升级后没装也不该让整个服务起不来。
所以 `docx` 只在生成本身里 import,缺包时抛 `ImportError`,由调用方
(路由 / CLI)翻成一句人话,与 `family_export` 缺 openpyxl 的处理一致。

报告里的数字
------------
* 取整沿用 `family_export._r`:质量 / 摩尔 / 功率 / 压力 2 位,温度 1 位,
  相对误差 3 位(按 % 显示);
* 空值**留空**,不写 0 —— 库里 `None` 就是「没读到」,写 0 会被当成真值;
* 超差行整行浅红底,状态列写「超差」。
"""
from __future__ import annotations

import io
from datetime import datetime

from app.design import energy_report
from app.design import mass_balance
from app.design import sections as section_rules
from app.store import db

TITLE = "物料与能量衡算报告"

# 焓平衡状态(库里存的就是这四个)
_BALANCE_STATE = energy_report.STATE_TEXT
# 三种塔负荷来源
_SOURCE_TEXT = {"block": "块字段", "stage": "逐级剖面", "": "未采到"}

# 表宽(cm),A4 页宽 21 − 左右各 2.5 = 16.0
_W_ROW_6 = (2.2, 3.6, 3.0, 3.0, 2.1, 2.1)
_W_ENERGY_8 = (2.4, 1.0, 2.5, 2.5, 1.9, 2.8, 1.4, 1.5)
_W_SUMMARY_3 = (5.6, 4.2, 6.2)
_W_TOWER_5 = (2.8, 2.4, 3.2, 3.2, 4.4)


# ---------------------------------------------------------------- 取数

def _balance_results(blocks) -> dict:
    """把库里已存的焓平衡结论翻成 `balance.compute()` 的形状(不重算)。"""
    out = {}
    for block in blocks:
        out[block["name"]] = {
            "dh_kw": block.get("dh_kw"),
            "ref_kw": block.get("ref_kw"),
            "ref_kind": block.get("ref_kind") or "",
            "balance_state": block.get("balance_state") or energy_report.NO_DATA,
            "balance_note": block.get("balance_note") or "",
        }
    return out


def _stages(conn, case_id: int, blocks) -> dict:
    """塔的逐级剖面(给塔负荷回退用);只查塔,不查全部设备。"""
    out = {}
    for block in blocks:
        if block.get("type") not in ("RadFrac", "Absorber", "Extract"):
            continue
        out[block["name"]] = [dict(r) for r in
                              db.read_column_stages(conn, case_id, block["name"])]
    return out


def build_data(conn, case_id: int, parts=None, picked=None) -> dict:
    """把报告要用的数据一次算齐(纯数据,不生成文档)。

    Args:
        conn: 已打开的工况库连接
        case_id: 工况 id
        parts: 确认后的工段边界;None 时用自动草稿(报告里会标「未经人工确认」)
        picked: 逐设备表要列哪些设备;None = 全部

    Returns:
        {"case": {...}, "zone": 界区衡算, "devices": 逐设备衡算,
         "energy": 能量衡算, "auto_boundary": bool, "towers": [...]}
    """
    case = db.get_case(conn, case_id)
    if case is None:
        raise LookupError(f"没有 id={case_id} 的工况")

    streams = [dict(r) for r in db.read_streams(conn, case_id)]
    blocks = [dict(r) for r in db.read_blocks(conn, case_id)]
    connections = [dict(r) for r in db.read_connections(conn, case_id)]

    auto = parts is None
    if auto:
        parts = section_rules.draft(blocks, connections)

    zone = mass_balance.boundary_build(streams, parts)
    devices = mass_balance.build(streams, blocks, connections, picked=picked)

    balance = _balance_results(blocks)
    tower = energy_report.tower_duties(blocks, _stages(conn, case_id, blocks))
    energy = energy_report.build(blocks, streams, connections, balance, tower)

    return {
        "case": dict(case),
        "zone": zone,
        "devices": devices,
        "energy": energy,
        "auto_boundary": auto,
        "parts": parts,
        "picked": list(picked) if picked else None,
    }


# ---------------------------------------------------------------- 数字格式

def _r(value, digits: int):
    """按位数取整;None / 非数字一律留空(与 family_export 同口径)。"""
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _num(value, digits: int) -> str:
    """数值 → 文本;None 给空串。"""
    value = _r(value, digits)
    return "" if value is None else f"{value:,.{digits}f}"


def _percent(rel) -> str:
    """相对误差 → 「0.012%」;None 给空串。"""
    value = _r(rel, 6)
    return "" if value is None else f"{value * 100:.3f}%"


def filename(case_id: int) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"Casebook_case{case_id}_balance_{stamp}.docx"


# ---------------------------------------------------------------- 文档骨架

def _setup(doc, case: dict) -> None:
    """页面设成 A4,默认字体设成宋体。"""
    from docx.shared import Cm, Pt
    from docx.oxml.ns import qn

    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(2.5)
    section.right_margin = Cm(2.5)
    section.top_margin = Cm(2.5)
    section.bottom_margin = Cm(2.5)

    style = doc.styles["Normal"]
    style.font.size = Pt(10.5)
    style.font.name = "宋体"
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")


def _font(run, size=None, bold=None, name="宋体"):
    from docx.oxml.ns import qn

    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if size is not None:
        run.font.size = size
    if bold is not None:
        run.font.bold = bold


def _para(doc, text="", size=None, bold=False, align=None, space_after=None,
          name="宋体"):
    from docx.shared import Pt

    para = doc.add_paragraph()
    if align is not None:
        para.alignment = align
    if space_after is not None:
        para.paragraph_format.space_after = Pt(space_after)
    if text:
        _font(para.add_run(text), size, bold, name)
    return para


def _heading(doc, text, level=1):
    """章节标题:黑体,不依赖 docx 内置样式(样式名各版本不一致)。"""
    from docx.shared import Pt

    sizes = {1: Pt(15), 2: Pt(12.5), 3: Pt(11)}
    para = doc.add_paragraph()
    para.paragraph_format.space_before = Pt(10)
    para.paragraph_format.space_after = Pt(4)
    _font(para.add_run(text), sizes.get(level, Pt(11)), True, "黑体")
    return para


def _shade(cell, hex_fill: str) -> None:
    """给单元格铺底色(超差行标红用)。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    cell._tc.get_or_add_tcPr().append(shd)


def _three_line(doc, headers, rows, widths, caption=""):
    """三线表:顶线 1.5 pt、表头下线 0.75 pt、底线 1.5 pt,无竖线无内部横线。

    python-docx 没有内置三线表样式,只能直接写 `w:tblBorders`。
    `w:sz` 的单位是 1/8 pt,所以 1.5 pt = 12、0.75 pt = 6。
    """
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    if caption:
        _para(doc, caption, Pt(10), True, space_after=2)

    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    borders = OxmlElement("w:tblBorders")
    for edge, size in (("top", 12), ("bottom", 12), ("left", 0), ("right", 0),
                       ("insideH", 0), ("insideV", 0)):
        node = OxmlElement(f"w:{edge}")
        if size:
            node.set(qn("w:val"), "single")
            node.set(qn("w:sz"), str(size))
            node.set(qn("w:color"), "000000")
        else:
            node.set(qn("w:val"), "none")
            node.set(qn("w:sz"), "0")
        borders.append(node)
    table._tbl.tblPr.append(borders)

    def fill(cell, text, bold=False, align=WD_ALIGN_PARAGRAPH.CENTER):
        para = cell.paragraphs[0]
        para.alignment = align
        para.paragraph_format.space_after = Pt(0)
        _font(para.add_run("" if text is None else str(text)), Pt(9.5), bold)

    for index, head in enumerate(headers):
        fill(table.rows[0].cells[index], head, True)
    # 表头下方的线(三线表的第二条)
    for cell in table.rows[0].cells:
        tc_pr = cell._tc.get_or_add_tcPr()
        borders_cell = OxmlElement("w:tcBorders")
        node = OxmlElement("w:bottom")
        node.set(qn("w:val"), "single")
        node.set(qn("w:sz"), "6")
        node.set(qn("w:color"), "000000")
        borders_cell.append(node)
        tc_pr.append(borders_cell)

    for row_index, row in enumerate(rows, start=1):
        cells = table.rows[row_index].cells
        cells_data, bold, fill_hex = (row if isinstance(row, tuple)
                                      else (row, False, None))
        for index, value in enumerate(cells_data):
            align = (WD_ALIGN_PARAGRAPH.LEFT if index == 0
                     else WD_ALIGN_PARAGRAPH.RIGHT)
            fill(cells[index], value, bold, align)
            if fill_hex:
                _shade(cells[index], fill_hex)

    for row in table.rows:
        for index, width in enumerate(widths):
            row.cells[index].width = Cm(width)
    return table


# ---------------------------------------------------------------- 各章内容

class _Captions:
    """表号计数器:全文连续编号,跨章传同一个实例。"""

    def __init__(self, start: int = 0):
        self.n = start

    def next(self, text: str = "") -> str:
        self.n += 1
        return f"表 {self.n}  {text}" if text else f"表 {self.n}"

def _cover(doc, case: dict) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    _para(doc, "", Pt(10), space_after=60)
    _para(doc, TITLE, Pt(24), True, WD_ALIGN_PARAGRAPH.CENTER, space_after=18, name="黑体")
    _para(doc, "（来自本工具工况快照，非工程设计依据）", Pt(11), False,
          WD_ALIGN_PARAGRAPH.CENTER, space_after=90)

    rows = [
        ("工况", case.get("model_name") or ""),
        ("工况号", f"#{case.get('id')}"),
        ("读取时间", case.get("read_at") or ""),
        ("Aspen 版本", case.get("aspen_version") or ""),
        ("收敛判定", {"ok": "通过", "warning": "告警", "error": "报错"}.get(
            case.get("severity") or "", case.get("severity") or "")),
        ("流股 / 设备", f"{case.get('stream_count') or 0} / {case.get('block_count') or 0}"),
        ("生成时间", datetime.now().strftime("%Y-%m-%d %H:%M")),
    ]
    for label, value in rows:
        para = _para(doc, "", Pt(11), False, WD_ALIGN_PARAGRAPH.CENTER, space_after=6)
        _font(para.add_run(f"{label}："), Pt(11), True)
        _font(para.add_run(str(value)), Pt(11), False)

    doc.add_page_break()


def _summary_chapter(doc, data: dict, cap: "_Captions") -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    zone_sum = data["zone"]["summary"]
    dev_sum = data["devices"]["summary"]
    energy = data["energy"]["summary"]

    _heading(doc, "校核结论摘要", 1)
    text = (
        f"本次校核 {zone_sum['total']} 个工段、{dev_sum['total']} 台设备。"
        f"工段界区衡算：通过 {zone_sum[mass_balance.PASS]} 个、"
        f"超差 {zone_sum[mass_balance.FAIL]} 个、"
        f"无法校验 {zone_sum[mass_balance.NO_DATA]} 个。"
        f"逐设备质量守恒：通过 {dev_sum[mass_balance.PASS]} 台、"
        f"超差 {dev_sum[mass_balance.FAIL]} 台、"
        f"无法校验 {dev_sum[mass_balance.NO_DATA]} 台、"
        f"流量过小 {dev_sum[mass_balance.TOO_SMALL]} 台。"
    )
    _para(doc, text, None, False, WD_ALIGN_PARAGRAPH.LEFT)

    states = [mass_balance.PASS, mass_balance.FAIL, mass_balance.NO_DATA,
              mass_balance.TOO_SMALL]
    headers = ["项目", "通过", "超差", "无法校验", "流量过小", "合计"]
    rows = [
        ["工段界区衡算"] + [zone_sum[s] for s in states] + [zone_sum["total"]],
        ["逐设备质量守恒"] + [dev_sum[s] for s in states] + [dev_sum["total"]],
    ]
    _three_line(doc, headers, rows, (3.6, 2.0, 2.0, 2.4, 2.0, 2.0),
                cap.next("质量守恒校验结果"))

    order = [energy_report.OK, energy_report.BAD, energy_report.NO_DATA,
             energy_report.SKIP]
    by_state = energy["by_state"]
    rows = [[energy_report.STATE_TEXT[s], by_state.get(s, 0)] for s in order]
    _three_line(doc, ["焓平衡状态", "表内行数"], rows, (6.0, 4.0),
                cap.next("焓平衡结论分布"))

    text = (
        f"能量汇总：总加热 {energy['heat_total_kw']:,.2f} kW"
        f"（其中塔再沸器 {energy['tower_reb_kw']:,.2f} kW）、"
        f"总冷却 {energy['cool_total_kw']:,.2f} kW"
        f"（其中塔冷凝器 {energy['tower_cond_kw']:,.2f} kW）、"
        f"泵与压缩机总轴功 {energy['shaft_kw']:,.2f} kW。"
    )
    _para(doc, text)


def _row_triplet(rows) -> list:
    """流股行 → [方向, 流股, 质量流量, 摩尔流量, 温度, 压力]。"""
    out = []
    for row in rows:
        out.append([row["stream"], _num(row["mass_flow_kgh"], 2),
                    _num(row["mole_flow_kmolh"], 2),
                    _num(row["temperature_c"], 1),
                    _num(row["pressure_bar"], 2)])
    return out


def _stream_table(doc, rows_in, rows_out, mass_in, mass_out, mole_in, mole_out,
                  rel, state_text, caption) -> None:
    """一张衡算小表:进 → 进合计 → 出 → 出合计 → 相对误差。"""
    body = [["进", *r] for r in _row_triplet(rows_in)]
    body.append(["合计", f"进料合计（{len(rows_in)} 股）", _num(mass_in, 2),
                 _num(mole_in, 2), "", ""])
    body += [["出", *r] for r in _row_triplet(rows_out)]
    body.append(["合计", f"出料合计（{len(rows_out)} 股）", _num(mass_out, 2),
                 _num(mole_out, 2), "", ""])
    bad = state_text == mass_balance.STATE_TEXT[mass_balance.FAIL]
    body.append(["误差", f"相对误差 · {state_text}", _percent(rel), "", "", ""])
    rows = []
    for index, cells in enumerate(body):
        is_total = cells[1].startswith(("进料合计", "出料合计", "相对误差"))
        rows.append((cells, is_total, "FDE8E8" if bad and index == len(body) - 1 else None))
    _three_line(doc, ["向", "流股", "质量流量 kg/h", "摩尔流量 kmol/h",
                      "温度 ℃", "压力 bar"], rows, _W_ROW_6, caption)


def _mass_chapter(doc, data: dict, cap: "_Captions") -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    _heading(doc, "一、物料衡算", 1)
    note = ("界区衡算判据：Σ(进界流股质量流量) = Σ(出界流股质量流量)，"
            "相对误差 ≤ 0.5% 判通过，> 0.5% 判超差。"
            f"本轮计算标红阈值 {mass_balance.TOL_REL:.1%}，"
            f"进、出界合计都低于 {mass_balance.MIN_FLOW_KGH:g} kg/h 时不做判定。")
    if data["auto_boundary"]:
        note += "本报告未载入人工确认的工段边界，界区流股按设备名段号自动推导。"
    _para(doc, note, None, False, WD_ALIGN_PARAGRAPH.LEFT)

    # 1) 工段界区表
    _heading(doc, "1.1 工段界区衡算", 2)
    if not data["zone"]["parts"]:
        _para(doc, "（该工况没有推出工段，可检查设备命名或手工确认边界流股）")
    for part in data["zone"]["parts"]:
        _heading(doc, part["label"], 3)
        _stream_table(doc, part["in_rows"], part["out_rows"],
                      part["mass_in_kgh"], part["mass_out_kgh"],
                      part["mole_in_kmolh"], part["mole_out_kmolh"],
                      part["rel_err"], mass_balance.STATE_TEXT[part["state"]],
                      cap.next(f"{part['label']} 界区衡算"
                               f"（{len(part['in_rows'])} 进 / {len(part['out_rows'])} 出）"))
        if part["note"]:
            _para(doc, f"说明：{part['note']}", None)

    # 2) 逐设备表(按工段分小节)
    _heading(doc, "1.2 逐设备质量守恒", 2)
    by_section: dict[str, list[dict]] = {}
    for device in data["devices"]["devices"]:
        by_section.setdefault(device["section"], []).append(device)
    if not by_section:
        _para(doc, "（没有选中任何设备）")
    for key in sorted(by_section, key=lambda k: (k == section_rules.UTILITY_KEY, k)):
        group = by_section[key]
        _heading(doc, section_rules.label_for(key), 3)
        for device in group:
            caption_text = (f"{device['block']}（{device['type'] or '未知类型'}）"
                            f"物料衡算")
            if device["note"]:
                caption_text += f" · {mass_balance.STATE_TEXT[device['state']]}"
            _stream_table(doc, device["in_rows"], device["out_rows"],
                          device["mass_in_kgh"], device["mass_out_kgh"],
                          device["mole_in_kmolh"], device["mole_out_kmolh"],
                          device["rel_err"],
                          mass_balance.STATE_TEXT[device["state"]],
                          cap.next(caption_text))


def _energy_chapter(doc, data: dict, cap: "_Captions") -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    energy = data["energy"]
    _heading(doc, "二、能量衡算", 1)
    _para(doc, ("ΔH = Σ(出口流股能量流) − Σ(进口流股能量流)，单位 kW；"
                "ΔH 与焓平衡状态直接取工况库里已有的结论，本报告不重算。"
                "换热器只校验冷侧（整体 ΔH 恒约等于 0），故分冷、热两行；"
                "塔与反应器按约定不校验。"), None, False, WD_ALIGN_PARAGRAPH.LEFT)

    _heading(doc, "2.1 逐设备能量衡算", 2)
    if data.get("picked"):
        _para(doc, "（本节按全流程设备列出：物料衡算的设备勾选不缩小能量表范围）", None)
    if not energy["devices"]:
        _para(doc, "（该工况没有可列的设备）")
    for group in energy["groups"]:
        _heading(doc, f"{group['label']}（{len(group['devices'])} 行）", 3)
        rows = []
        for row in group["devices"]:
            state_text = energy_report.STATE_TEXT.get(row["state"], row["state"])
            bad = row["state"] == energy_report.BAD
            label = f"{row['dev_kw']:,.2f}" if row["dev_kw"] is not None else ""
            if label and row["dev_kind"]:
                label += "（轴功率）" if row["dev_kind"] == "power" else "（热负荷）"
            rows.append(([row["block"], row["side"] or "—",
                          _num(row["h_in_kw"], 2), _num(row["h_out_kw"], 2),
                          _num(row["dh_kw"], 2), label,
                          _num(row["delta_kw"], 2), state_text],
                         False, "FDE8E8" if bad else None))
        _three_line(doc, ["设备", "侧", "进口焓流 kW", "出口焓流 kW", "ΔH kW",
                          "热负荷 / 轴功率 kW", "偏差 kW", "焓平衡"],
                    rows, _W_ENERGY_8, cap.next(f"{group['label']} 能量衡算"))

    _heading(doc, "2.2 全流程能量汇总", 2)
    summary = energy["summary"]
    rows = [
        (["总加热负荷", _num(summary["heat_total_kw"], 2),
          f"正热负荷合计 {_num(summary['heat_pos_kw'], 2)} kW"
          f" + 塔再沸器 {_num(summary['tower_reb_kw'], 2)} kW"], True, None),
        (["其中：塔再沸器", _num(summary["tower_reb_kw"], 2),
          f"{summary['tower_count']} 座塔，已含在上行"], False, None),
        (["总冷却负荷", _num(summary["cool_total_kw"], 2),
          f"负热负荷合计 {_num(summary['cool_neg_kw'], 2)} kW"
          f" + 塔冷凝器 {_num(summary['tower_cond_kw'], 2)} kW"], True, None),
        (["其中：塔冷凝器", _num(summary["tower_cond_kw"], 2),
          f"{summary['tower_count']} 座塔，已含在上行"], False, None),
        (["泵与压缩机总轴功", _num(summary["shaft_kw"], 2),
          "Pump / Compr / MCompr / Turbine 的轴功率合计"], False, None),
    ]
    if summary.get("reaction_net_kw"):
        rows.append((["未计入：反应器热负荷", _num(summary["reaction_net_kw"], 2),
                      "反应热不是换热设备的公用工程负荷，按约定不校验"],
                     False, None))
    _three_line(doc, ["项目", "数值 kW", "构成与说明"], rows, _W_SUMMARY_3,
                cap.next("全流程能量汇总"))

    if energy["towers"]:
        _heading(doc, "2.3 塔的冷凝器 / 再沸器负荷", 2)
        rows = []
        for tower in energy["towers"]:
            rows.append([tower["block"], tower["type"] or "",
                         _num(tower["cond_kw"], 2), _num(tower["reb_kw"], 2),
                         _SOURCE_TEXT.get(tower["source"], tower["source"])])
        _three_line(doc, ["塔", "类型", "冷凝器 kW", "再沸器 kW", "来源"], rows,
                    _W_TOWER_5, cap.next("塔的冷凝器 / 再沸器负荷"))
        _para(doc, ("说明：块字段（COND_DUTY / REB_DUTY）优先；"
                    "取不到时用逐级剖面的首级（冷凝器）与末级（再沸器）回退，"
                    "部分冷凝器的塔首级可能不等于冷凝器总负荷。"), None)


def _notes_chapter(doc, data: dict) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    _heading(doc, "附注", 1)
    case_id = data["case"].get("id")
    notes = [
        f"数据来源：本工具读取工况 #{case_id} 的快照（{data['case'].get('read_at') or '未知时间'}），"
        "全程只读，未调用 Aspen 的 Run / Reinit / Save。",
        "基准与单位：质量流量 kg/h、摩尔流量 kmol/h、温度 ℃、压力 bar、"
        "热负荷与能量流 kW；单位已在读取时归一，本报告不做二次换算。",
        f"质量守恒容差：相对误差 ≤ {mass_balance.TOL_REL:.1%} 判通过；"
        f"进、出合计都低于 {mass_balance.MIN_FLOW_KGH:g} kg/h 时记「流量过小」，不做判定。"
        "缺连接或流股缺流量记「无法校验」，不等于通过。",
        "质量守恒是新结论，与焓平衡判定分开记录；本报告不把质量结论写进焓平衡状态。",
        "能量衡算的 ΔH 与焓平衡状态取自库内已有结论，未重算；"
        "换热器只校验冷侧，塔与反应器按约定不校验（塔有冷凝器 / 再沸器 / 中间回流，"
        "反应器的 QCALC 不是换热负荷），不使用「凑平」的数字。",
        "塔负荷来源：块字段优先，缺失时用逐级剖面首 / 末级回退，"
        "并在明细表的「来源」列标出。",
        "相对误差口径不区分「漏流股」与「求解器残差」，量级小时属于正常收敛误差，"
        "量级大（10% 以上）才值得查模型。",
        "本报告由 Casebook 自动生成，用于内部校核与存档，不能作为工程设计依据。",
    ]
    for text in notes:
        _para(doc, f"· {text}", None, False, WD_ALIGN_PARAGRAPH.LEFT, space_after=4)


# ---------------------------------------------------------------- 入口

def render(data: dict) -> bytes:
    """把 `build_data()` 的结果渲染成 .docx 字节。

    缺 python-docx 时抛 `ImportError`。CLI 要先打印摘要再落盘,所以数据组装
    与渲染分成两步,避免算两遍。
    """
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover - 取决于运行环境
        raise ImportError("没有安装 python-docx,无法生成 Word 报告") from exc

    doc = Document()
    cap = _Captions()
    _setup(doc, data["case"])
    _cover(doc, data["case"])
    _summary_chapter(doc, data, cap)
    _mass_chapter(doc, data, cap)
    doc.add_page_break()
    _energy_chapter(doc, data, cap)
    doc.add_page_break()
    _notes_chapter(doc, data)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def build_report(conn, case_id: int, parts=None, picked=None) -> tuple[bytes, str]:
    """一步生成 .docx,返回 `(字节, 文件名)`。

    缺 python-docx 时抛 `ImportError`;工况不存在时抛 `LookupError`。
    """
    data = build_data(conn, case_id, parts, picked)
    return render(data), filename(case_id)
