"""衡算报告 .docx 导出 —— 「衡算报告」页与 `tools/balance_report.py` 共用。

三件事
------
1. 从库里现取三张表 + `column_stages`,**不碰 Aspen、不重算焓平衡**:
   焓平衡的 ΔH / 对照值 / 状态 / 说明直接读 `blocks` 里已存的结论;
2. 物料衡算判两层(工段界区 + 逐设备),能量衡算出设备表 + 汇总 + 塔负荷明细;
3. 用 python-docx 写成三线表 .docx,返回 `(bytes, fname)` —— 与 `family_xlsx`
   同样的接口,Web 路由可以直接 `send_header` + 写字节。

依赖是**懒导入**的
------------------
`python-docx` 不在标准库里,老用户升级后没装也不该让整个服务起不来。
所以 `docx` 只在生成本身里 import,缺包时抛 `ImportError`,由调用方
(路由 / CLI)翻成一句人话,与 `family_xlsx` 缺 openpyxl 的处理一致。

报告里的数字
------------
* 取整沿用 `family_xlsx._r`:质量 / 摩尔 / 功率 / 压力 2 位,温度 1 位,
  相对误差 3 位(按 % 显示);
* 空值**留空**,不写 0 —— 库里 `None` 就是「没读到」,写 0 会被当成真值;
* 超差行整行浅红底,状态列写「超差」。
"""

from __future__ import annotations

import io
from datetime import datetime

from app.design import energy_report, mass_balance, stream_table
from app.design import sections as section_rules
from app.export import docx_kit as kit
from app.export.docx_kit import (
    KINDS,
    _body_section,
    _Captions,
    _cover,
    _font,
    _heading,
    _para,
    _rgb,
    _setup,
    _three_line,
    kind_meta,
)
from app.store import db

TITLE = "物料与能量衡算报告"


# 焓平衡状态(库里存的就是这四个)
_BALANCE_STATE = energy_report.STATE_TEXT
# 三种塔负荷来源
_SOURCE_TEXT = {"block": "块字段", "stage": "逐级剖面", "": "未采到"}

# 表宽(cm),A4 页宽 21 − 左右各 2.5 = 16.0
_W_ROW_6 = (2.0, 4.6, 2.9, 3.0, 1.8, 1.7)
_W_ENERGY = (2.0, 0.9, 2.2, 2.2, 2.1, 1.6, 2.3, 1.3, 1.4)
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
        out[block["name"]] = [dict(r) for r in db.read_column_stages(conn, case_id, block["name"])]
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

    composition: dict[str, list[dict]] = {}
    for row in db.read_all_composition(conn, case_id):
        composition.setdefault(row["stream"], []).append(dict(row))
    wide = stream_table.build(streams, connections, parts, composition)

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
        "blocks": blocks,
        "wide": wide,
        "composition": composition,
        "picked": list(picked) if picked else None,
    }


# ---------------------------------------------------------------- 数字格式


def _r(value, digits: int):
    """按位数取整;None / 非数字一律留空(与 family_xlsx 同口径)。"""
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _num(value, digits: int) -> str:
    """数值 → 文本;None 给空串;负零归一成 0(否则表里会出现成片的 -0.00)。"""
    value = _r(value, digits)
    if value is None:
        return ""
    if abs(value) < 0.5 * 10 ** (-digits):
        value = 0.0
    return f"{value:,.{digits}f}"


def _percent(rel) -> str:
    """相对误差 → 「0.012%」;None 给空串。"""
    value = _r(rel, 6)
    return "" if value is None else f"{value * 100:.3f}%"


def filename(case_id: int, kind: str = "all") -> str:
    """文件名带册别:case42_mass_… / case42_energy_… / case42_balance_…。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = {"mass": "mass", "energy": "energy"}.get(kind, "balance")
    return f"Casebook_case{case_id}_{tag}_{stamp}.docx"


# ---------------------------------------------------------------- 文档骨架


# 分组行横着合并(组分 · 摩尔流量 / 组分 · 质量流量):合并后要把宽度一起
# 改成整行宽,否则文字仍在首列那一格里折行。索引是「rows 列表里的下标」,
# 表格行号要加上表头行数(head_rows):有分组表头时是 2,没有时是 1。


# ---------------------------------------------------------------- 各章内容


def _toc(doc, kind: str = "all", has_detail: bool = False) -> None:
    """目录页:按册列出条目(手写目录,不依赖 Word 的 TOC 域)。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    meta = kind_meta(kind)
    _para(doc, "", None, space_after=4)
    _para(doc, "目　录", Pt(16), True, WD_ALIGN_PARAGRAPH.CENTER, space_after=14, name="黑体").runs[
        0
    ].font.color.rgb = _rgb(meta["accent"])
    _para(doc, meta["book"], Pt(9.5), False, WD_ALIGN_PARAGRAPH.CENTER, space_after=22).runs[
        0
    ].font.color.rgb = _rgb("808080")

    entries: list[tuple[str, int]] = [("校核结论摘要", 0)]
    if kind in ("mass", "all"):
        entries += [
            ("一、物料衡算", 0),
            ("1.1 各工段物料平衡表", 1),
            ("1.2 全流程物料平衡表", 1),
            ("1.3 物料消耗与产出汇总", 1),
        ]
    if kind in ("energy", "all"):
        ch = "2" if kind == "all" else "1"
        entries.append((("二" if ch == "2" else "一") + "、能量衡算", 0))
        entries.append((f"{ch}.1 逐设备能量衡算", 1))
        if has_detail:
            entries.append((f"{ch}.2 选中设备的进出口明细", 1))
        base = 3 if has_detail else 2
        entries += [
            (f"{ch}.{base} 全流程能量汇总", 1),
            (f"{ch}.{base + 1} 塔的冷凝器 / 再沸器负荷", 1),
        ]
    entries.append(("附注", 0))

    # TOC 域:Word / WPS 打开时按大纲级别自动生成带页码的目录(field 标了 dirty)。
    # 下面这段手写条目是**域的占位结果** —— 万一打开时没自动更新,读者仍能看到
    # 章节清单,而不是一片空白。
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt

    opener = doc.add_paragraph()
    opener.paragraph_format.space_after = Pt(0)
    node = opener.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    begin.set(qn("w:dirty"), "true")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = ' TOC \\o "1-2" \\h \\z \\u '
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    node._r.append(begin)
    node._r.append(instr)
    node._r.append(sep)

    last = opener
    for text, level in entries:
        para = doc.add_paragraph()
        para.paragraph_format.space_after = Pt(8)
        para.paragraph_format.left_indent = Pt(28 * level)
        run = _font(para.add_run(text), Pt(12 if level == 0 else 11), level == 0)
        if level == 0:
            run.font.color.rgb = _rgb(meta["accent"])
        last = para

    end_node = last.add_run()
    ending = OxmlElement("w:fldChar")
    ending.set(qn("w:fldCharType"), "end")
    end_node._r.append(ending)
    doc.add_page_break()


def _summary_chapter(doc, data: dict, cap: _Captions, kind: str = "all") -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    zone_sum = data["zone"]["summary"]
    dev_sum = data["devices"]["summary"]
    energy = data["energy"]["summary"]

    cap.start("1")
    _heading(doc, "校核结论摘要", 1)

    if kind in ("mass", "all"):
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
        states = [
            mass_balance.PASS,
            mass_balance.FAIL,
            mass_balance.NO_DATA,
            mass_balance.TOO_SMALL,
        ]
        headers = ["项目", "通过", "超差", "无法校验", "流量过小", "合计"]
        rows = [
            ["工段界区衡算"] + [zone_sum[s] for s in states] + [zone_sum["total"]],
            ["逐设备质量守恒"] + [dev_sum[s] for s in states] + [dev_sum["total"]],
        ]
        _three_line(
            doc,
            headers,
            rows,
            (3.6, 2.0, 2.0, 2.4, 2.0, 2.0),
            cap.next("质量守恒校验结果"),
            status_col=2,
        )

    if kind in ("energy", "all"):
        order = [energy_report.OK, energy_report.BAD, energy_report.NO_DATA]
        by_state = energy["by_state"]
        rows = [[energy_report.STATE_TEXT[s], by_state.get(s, 0)] for s in order]
        rows.append(["不校验（换热器热侧，不单独校验）", by_state.get(energy_report.SKIP, 0)])
        _three_line(
            doc,
            ["焓平衡状态", "表内行数"],
            rows,
            (7.8, 3.2),
            cap.next("焓平衡结论分布"),
            status_col=0,
        )
        text = (
            f"能量汇总：总加热 {energy['heat_total_kw']:,.2f} kW"
            f"（其中塔再沸器 {energy['tower_reb_kw']:,.2f} kW）、"
            f"总冷却 {energy['cool_total_kw']:,.2f} kW"
            f"（其中塔冷凝器 {energy['tower_cond_kw']:,.2f} kW）、"
            f"泵与压缩机总轴功 {energy['shaft_kw']:,.2f} kW。"
        )
        _para(doc, text)


def _phase_of(column) -> str:
    return column.get("phase") or "—"


def _chunks(columns, size: int):
    """把一簇列切成若干块(宽表一页放不下时,按 ZJU 的「表 x-1（1）（2）」拆)。"""
    return [columns[i : i + size] for i in range(0, len(columns), size)]


def _widths(ncols: int):
    """项目 + 单位 + n 个流股列的宽度(cm),合计 16。"""
    rest = 16.0 - 4.0  # 项目 2.2 + 单位 1.8
    each = round(rest / max(ncols, 1), 2)
    return (2.2, 1.8) + (each,) * ncols


def _wide_table(
    doc, table: dict, cap: _Captions, chapter: str, max_cols: int = 5, title: str = ""
) -> None:  # noqa: D401
    """一张物料平衡宽表:流股做列、属性与组分做行。

    列多的时候按 `max_cols` 拆成「表 x-1（1）（2）…」,进口与出口永不混在同一块,
    这样任何一块表格横着都读得下去(与设计文档里「全流程物料衡算表(1)(2)」同做法)。
    """
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    segments = [("进口", table.get("in_columns") or []), ("出口", table.get("out_columns") or [])]
    blocks = []
    for group, cols in segments:
        for chunk in _chunks(list(cols), max_cols):
            if chunk:
                blocks.append((group, chunk))

    if not blocks:
        _para(doc, "（本表没有可用流股）")
        return

    def row(label, unit, values, bold=False):
        return ([label, unit, *values], bold, None)

    total = len(blocks)
    for index, (group, cols) in enumerate(blocks, start=1):
        suffix = f"（{index}）" if total > 1 else ""
        caption = cap.next(f"{title}{suffix}")
        rows: list = [
            row("相态", "—", [_phase_of(c) for c in cols]),
            row("温度", "℃", [_num(c.get("temperature_c"), 1) for c in cols]),
            row("压力", "bar", [_num(c.get("pressure_bar"), 2) for c in cols]),
            row("质量流量", "kg/h", [_num(c.get("mass_flow_kgh"), 2) for c in cols], True),
            row("摩尔流量", "kmol/h", [_num(c.get("mole_flow_kmolh"), 2) for c in cols]),
            row("体积流量", "m³/h", [_num(c.get("vol_flow_m3h"), 2) for c in cols]),
        ]

        components = table.get("components") or []
        span_rows: list[int] = []
        if components:
            span_rows.append(len(rows))
            rows.append(row("组分 · 摩尔流量 kmol/h", "", [""] * len(cols), True))
            for comp in components:
                rows.append(
                    row(
                        f"　{comp}",
                        "kmol/h",
                        [_num((c.get("mole_comp") or {}).get(comp), 3) for c in cols],
                    )
                )
        if table.get("has_mass_comp"):
            span_rows.append(len(rows))
            rows.append(row("组分 · 质量流量 kg/h", "", [""] * len(cols), True))
            for comp in components:
                rows.append(
                    row(
                        f"　{comp}",
                        "kg/h",
                        [_num((c.get("mass_comp") or {}).get(comp), 3) for c in cols],
                    )
                )

        headers = ["项目", "单位", *[c["stream"] for c in cols]]
        _three_line(
            doc, headers, rows, _widths(len(cols)), caption, header_extra=group, span_rows=span_rows
        )

    # 表下给该段的合计与误差(界区校验结论就写在这一行,不再单独出小表)
    state_text = mass_balance.STATE_TEXT.get(table.get("state"), "")
    note = (
        f"Σ进 {_num(table.get('mass_in_kgh'), 2)} kg/h、"
        f"Σ出 {_num(table.get('mass_out_kgh'), 2)} kg/h,"
        f"相对误差 {_percent(table.get('rel_err')) or '—'}"
        f"（容差 {mass_balance.TOL_REL:.1%}），判定：{state_text}"
    )
    if not table.get("has_mass_comp"):
        note += "。组分质量流量未读取（重读一次工况后自动出现），表中只给摩尔流量"
    para = _para(doc, note, None, False, WD_ALIGN_PARAGRAPH.LEFT, space_after=8)
    para.runs[0].font.size = Pt(9)
    para.runs[0].font.color.rgb = _rgb("595959")


def _group_row(doc, group: str):
    """占位:分组行由 `_three_line` 的 header_extra 处理(保留接口,便于以后加合并)。"""
    return group


def _figure_slot(doc, text: str) -> None:
    """图片插入处:一个带虚线框的大空位,人工把流程图贴进来。

    工具不生成流程图(服务端没有 SVG 渲染器,流程图目前由前端画),所以这里
    留一个显眼的空位 + 一行说明,而不是假装"没有流程图"。
    """
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt

    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para.paragraph_format.space_before = Pt(104)  # 上下合计约 8cm
    para.paragraph_format.space_after = Pt(104)
    run = para.add_run(f"【{text}：图片插入处】")
    _font(run, Pt(12), True, "黑体")
    run.font.color.rgb = _rgb("7F7F7F")

    p_pr = para._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    for edge in ("top", "left", "bottom", "right"):
        node = OxmlElement(f"w:{edge}")
        node.set(qn("w:val"), "dashed")
        node.set(qn("w:sz"), "6")
        node.set(qn("w:color"), "999999")
        node.set(qn("w:space"), "12")
        borders.append(node)
    p_pr.append(borders)


def _mass_chapter(doc, data: dict, cap: _Captions, chapter: str = "1") -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    cap.start(chapter)
    label = "一" if chapter == "1" else "二"
    wide = data["wide"]
    _heading(doc, f"{label}、物料衡算", 1)
    _para(
        doc,
        (
            "表中流股做列、属性与组分做行：进口列组是该工段确认的进界流股，"
            "出口列组是出界流股。表下给该段 Σ进 / Σ出 与相对误差——"
            f"相对误差 ≤ {mass_balance.TOL_REL:.1%} 判通过，"
            f"进出合计都低于 {mass_balance.MIN_FLOW_KGH:g} kg/h 时不判定。"
            "组分行按 Aspen 里的组分 ID 原样列出，不做中文映射。"
        ),
        None,
        False,
        WD_ALIGN_PARAGRAPH.LEFT,
    )
    if data.get("auto_boundary"):
        _para(
            doc,
            "本报告未载入人工确认的工段边界，进 / 出界流股按设备名段号自动推导，"
            "建议在「衡算报告」页确认后重新生成。",
            None,
            False,
            WD_ALIGN_PARAGRAPH.LEFT,
        )

    _heading(doc, f"{chapter}.1 各工段物料平衡表", 2)
    if not wide["zones"]:
        _para(
            doc,
            "（本工况没有可出的工段：可能被人工删除，或设备位号里没有段号；"
            "此时只出下面的全流程表）",
        )
    for zone in wide["zones"]:
        _heading(doc, zone["label"], 3)
        _figure_slot(doc, f"{zone['label']}流程图")
        _wide_table(doc, zone, cap, chapter, title=f"{zone['label']}物料平衡表")

    # 附注里要列"本册该贴几张图":流程图是人工贴的,漏贴没人提醒(2026-11-15)
    data["_figures"] = [f"{zone['label']}流程图" for zone in wide["zones"]] + ["全流程图"]

    _heading(doc, f"{chapter}.2 全流程物料平衡表", 2)
    _para(
        doc,
        (
            "进口列 = 没有上游的流股（外部进料 / 公用工程补水），"
            "出口列 = 没有下游的流股（产品、副产、三废）。"
            "公用工程流股（冷却水、蒸汽等）也在其中，是否算入原料见 1.3 的说明。"
        ),
        None,
        False,
        WD_ALIGN_PARAGRAPH.LEFT,
    )
    _figure_slot(doc, "全流程图")
    _wide_table(doc, wide["overall"], cap, chapter, title="全流程物料平衡表")

    _heading(doc, f"{chapter}.3 物料消耗与产出汇总", 2)
    summary = wide["summary"]
    hours = summary["hours_per_year"]
    _para(
        doc,
        f"按年运行 {hours:g} h 折算年量。原料与产品的归类由人确认；"
        "未确认时按「外部进料 / 出界流股」原样列出，不擅自贴标签。",
        None,
        False,
        WD_ALIGN_PARAGRAPH.LEFT,
    )

    rows = [
        [item["stream"], item["phase"], _num(item["mass_flow_kgh"], 2), _num(item["t_per_year"], 2)]
        for item in summary["feed"]
    ]
    _three_line(
        doc,
        ["流股", "相态", "质量流量 kg/h", "年量 t/a"],
        rows,
        (4.6, 2.4, 4.4, 4.4),
        cap.next("外部进料（原料 / 公用工程）"),
    )

    rows = [
        [item["stream"], item["phase"], _num(item["mass_flow_kgh"], 2), _num(item["t_per_year"], 2)]
        for item in summary["outlet"]
    ]
    _three_line(
        doc,
        ["流股", "相态", "质量流量 kg/h", "年量 t/a"],
        rows,
        (4.6, 2.4, 4.4, 4.4),
        cap.next("出界流股（产品 / 副产 / 三废）"),
    )


def _balance_row(block, energy_row, devices_row) -> dict:
    """一台设备的能量平衡量:W(机械功)、Q(热负荷)、ΣHin、ΣHout、偏差。"""
    h_in = energy_row.get("h_in_kw") if energy_row else None
    h_out = energy_row.get("h_out_kw") if energy_row else None
    ref_kind = (energy_row or {}).get("dev_kind") or ""
    # Q 取表行里已经算好的对照值:塔的对照值是「首级冷凝器 + 末级再沸器」,
    # 不等于块上的 duty_kw(塔根本没有 duty_kw)
    q = (energy_row or {}).get("dev_kw") if ref_kind == "duty" else None
    w = (energy_row or {}).get("dev_kw") if ref_kind == "power" else None
    error = None
    # 塔 / 反应器 / 缺数据的设备不参与校验:它们的 ΔH 本来就不等于 Q+W
    # (塔有冷凝 / 再沸 / 中间回流),算出来只会被误读成"超差",所以留空
    if h_in is not None and h_out is not None and (energy_row or {}).get("state") in ("ok", "bad"):
        error = round((h_out - h_in) - ((q or 0.0) + (w or 0.0)), 3)
    return {"w_kw": w, "q_kw": q, "h_in_kw": h_in, "h_out_kw": h_out, "error_kw": error}


def _energy_chapter(doc, data: dict, cap: _Captions, chapter: str = "2") -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    cap.start(chapter)
    label = "一" if chapter == "1" else "二"
    energy = data["energy"]
    _heading(doc, f"{label}、能量衡算", 1)
    _para(
        doc,
        (
            "以设备为基本单位，连续系统的能量平衡写作 Q + W = ΣHout − ΣHin："
            "Q 是设备热负荷、W 是输入系统的机械功（泵 / 压缩机的轴功率）、"
            "ΣHin、ΣHout 是进出口各物料焓之和，单位 kW。"
            "焓值与焓平衡判定取自工况库里已有的结论，本报告不重算；"
            "「偏差」列 = (ΣHout − ΣHin) − (Q + W)，即闭合误差。"
            "换热器只校冷侧（整体 ΔH 恒约等于 0），故冷、热两侧各一行；"
            "塔的对照值是首级（冷凝器）+ 末级（再沸器）负荷，反应器的对照值是反应热 QCALC；"
            "带换热伴流的多股反应器净 ΔH 会被伴流抵消，判「无法校验」而不是超差。"
        ),
        None,
        False,
        WD_ALIGN_PARAGRAPH.LEFT,
    )

    # 设备 → 能量行(换热器两侧各一行)
    by_block: dict[str, list[dict]] = {}
    for row in energy["devices"]:
        by_block.setdefault(row["block"], []).append(row)

    blocks = {b["name"]: b for b in data["blocks"]}

    _heading(doc, f"{chapter}.1 逐设备能量衡算", 2)
    for group in energy["groups"]:
        energy_rows = []
        for row in group["devices"]:
            block = blocks.get(row["block"]) or {}
            bal = _balance_row(block, row, row)
            state_text = energy_report.STATE_TEXT.get(row["state"], row["state"])
            bad = row["state"] == energy_report.BAD
            # 不再单列「类型」:上面的分组标题就是家族(塔 / 换热 / 反应 / 泵…),
            # 9pt 表体下每省一列,数值列就能宽 0.4cm,不会把 −183,646.07 折成两行
            energy_rows.append(
                (
                    [
                        row["block"],
                        row["side"] or "—",
                        _num(bal["h_in_kw"], 2),
                        _num(bal["h_out_kw"], 2),
                        _num(bal["q_kw"], 2),
                        _num(bal["w_kw"], 2),
                        _num(bal["error_kw"], 2),
                        state_text,
                    ],
                    False,
                    "FDE8E8" if bad else None,
                )
            )
        _three_line(
            doc,
            ["设备", "侧", "ΣHin|kW", "ΣHout|kW", "Q 热负荷|kW", "W 机械功|kW", "偏差|kW", "判定"],
            energy_rows,
            (2.6, 0.8, 2.5, 2.5, 2.1, 2.1, 1.7, 1.7),
            cap.next(f"{group['label']}能量衡算"),
        )

    picked = data.get("picked")
    if picked:
        _heading(doc, f"{chapter}.2 选中设备的进出口明细", 2)
        _para(
            doc,
            "只对本次勾选的设备出明细：先流股表（进出口流股的温压与流量、焓流），"
            "再平衡表（W / Q / ΣHin / ΣHout / 偏差），与设计文档的写法一致。",
            None,
            False,
            WD_ALIGN_PARAGRAPH.LEFT,
        )
        for name in picked:
            device_rows = by_block.get(name) or []
            if not device_rows:
                continue
            block = blocks.get(name) or {}
            _heading(doc, f"{name}（{block.get('type') or '未知类型'}）", 3)
            body = []
            for row in device_rows:
                body.append(
                    (["进口焓流 kW", row["side"] or "—", _num(row["h_in_kw"], 2)], False, None)
                )
                body.append(
                    (["出口焓流 kW", row["side"] or "—", _num(row["h_out_kw"], 2)], False, None)
                )
            _three_line(
                doc, ["项目", "侧", "数值"], body, (6.0, 3.0, 6.0), cap.next(f"{name} 焓流")
            )
            bal = _balance_row(block, device_rows[0], device_rows[0])
            _three_line(
                doc,
                ["W kW", "Q kW", "ΣHin kW", "ΣHout kW", "偏差 kW"],
                [
                    [
                        _num(bal["w_kw"], 2),
                        _num(bal["q_kw"], 2),
                        _num(bal["h_in_kw"], 2),
                        _num(bal["h_out_kw"], 2),
                        _num(bal["error_kw"], 2),
                    ]
                ],
                (3.0, 3.0, 3.4, 3.4, 3.0),
                cap.next(f"{name} 能量平衡"),
            )

    _heading(doc, f"{chapter}.{3 if picked else 2} 全流程能量汇总", 2)
    summary = energy["summary"]
    summary_rows = [
        (
            [
                "总加热负荷",
                _num(summary["heat_total_kw"], 2),
                f"正热负荷合计 {_num(summary['heat_pos_kw'], 2)} kW"
                f" + 塔再沸器 {_num(summary['tower_reb_kw'], 2)} kW",
            ],
            True,
            None,
        ),
        (
            [
                "其中：塔再沸器",
                _num(summary["tower_reb_kw"], 2),
                f"{summary['tower_count']} 座塔，已含在上行",
            ],
            False,
            None,
        ),
        (
            [
                "总冷却负荷",
                _num(summary["cool_total_kw"], 2),
                f"负热负荷合计 {_num(summary['cool_neg_kw'], 2)} kW"
                f" + 塔冷凝器 {_num(summary['tower_cond_kw'], 2)} kW",
            ],
            True,
            None,
        ),
        (
            [
                "其中：塔冷凝器",
                _num(summary["tower_cond_kw"], 2),
                f"{summary['tower_count']} 座塔，已含在上行",
            ],
            False,
            None,
        ),
        (
            [
                "泵与压缩机总轴功",
                _num(summary["shaft_kw"], 2),
                "Pump / Compr / MCompr / Turbine 的轴功率合计",
            ],
            False,
            None,
        ),
    ]
    if summary.get("reaction_net_kw"):
        summary_rows.append(
            (
                [
                    "未计入：反应器热负荷",
                    _num(summary["reaction_net_kw"], 2),
                    "反应热不是换热设备的公用工程负荷，按约定不校验",
                ],
                False,
                None,
            )
        )
    _three_line(
        doc,
        ["项目", "数值 kW", "构成与说明"],
        summary_rows,
        _W_SUMMARY_3,
        cap.next("全流程能量汇总"),
    )

    if energy["towers"]:
        _heading(doc, f"{chapter}.{4 if picked else 3} 塔的冷凝器 / 再沸器负荷", 2)
        tower_rows = []
        for tower in energy["towers"]:
            tower_rows.append(
                [
                    tower["block"],
                    tower["type"] or "",
                    _num(tower["cond_kw"], 2),
                    _num(tower["reb_kw"], 2),
                    _SOURCE_TEXT.get(tower["source"], tower["source"]),
                ]
            )
        _three_line(
            doc,
            ["塔", "类型", "冷凝器 kW", "再沸器 kW", "来源"],
            tower_rows,
            _W_TOWER_5,
            cap.next("塔的冷凝器 / 再沸器负荷"),
        )
        _para(
            doc,
            (
                "说明：块字段（COND_DUTY / REB_DUTY）优先；"
                "取不到时用逐级剖面的首级（冷凝器）与末级（再沸器）回退，"
                "部分冷凝器的塔首级可能不等于冷凝器总负荷。"
            ),
            None,
        )


def _notes_chapter(doc, data: dict) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    _heading(doc, "附注", 1)
    case_id = data["case"].get("id")
    notes = [
        f"数据来源：本工具读取工况 #{case_id} 的快照"
        f"（{data['case'].get('read_at') or '未知时间'}），"
        "全程只读，未调用 Aspen 的 Run / Reinit / Save。",
        "基准与单位：质量流量 kg/h、摩尔流量 kmol/h、温度 ℃、压力 bar、"
        "热负荷与能量流 kW；单位已在读取时归一，本报告不做二次换算。",
        f"质量守恒容差：相对误差 ≤ {mass_balance.TOL_REL:.1%} 判通过；"
        f"进、出合计都低于 {mass_balance.MIN_FLOW_KGH:g} kg/h 时记「流量过小」，不做判定。"
        "缺连接或流股缺流量记「无法校验」，不等于通过。",
        "质量守恒是新结论，与焓平衡判定分开记录；本报告不把质量结论写进焓平衡状态。",
        "能量衡算的 ΔH 与焓平衡状态取自库内已有结论，未重算。换热器只校验冷侧；"
        "塔按「首级冷凝器 + 末级再沸器」的净热负荷校验（93 座塔实测中间级 duty 全为 0，"
        "遇中间换热器时该口径会偏小）；反应器按反应热 QCALC 校验，只对单进单出成立，"
        "带换热伴流的多股反应器记「无法校验」。",
        "塔负荷来源：块字段优先，缺失时用逐级剖面首 / 末级回退，并在明细表的「来源」列标出。",
        "相对误差口径不区分「漏流股」与「求解器残差」，量级小时属于正常收敛误差，"
        "量级大（10% 以上）才值得查模型。",
        "本报告由 Casebook 自动生成，用于内部校核与存档，不能作为工程设计依据。",
    ]
    figures = data.get("_figures") or []
    if figures:
        notes.append(
            f"本册共 {len(figures)} 张流程图需人工贴入：{'、'.join(figures)}。"
            "正文各处已留虚线框并标注「图片插入处」，贴完请逐条核对。"
        )
    for text in notes:
        _para(doc, f"· {text}", None, False, WD_ALIGN_PARAGRAPH.LEFT, space_after=4)


# ---------------------------------------------------------------- 入口


def render(data: dict, kind: str = "all") -> bytes:
    """把 `build_data()` 的结果渲染成 .docx 字节。

    缺 python-docx 时抛 `ImportError`。CLI 要先打印摘要再落盘,所以数据组装
    与渲染分成两步,避免算两遍。
    """
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover - 取决于运行环境
        raise ImportError("没有安装 python-docx,无法生成 Word 报告") from exc

    if kind not in KINDS:
        kind = "all"
    kit._ACTIVE_KIND = kind_meta(kind)

    doc = Document()
    cap = _Captions()
    _setup(doc, data["case"], kind)
    _cover(doc, data["case"], kind)
    _toc(doc, kind, bool(data.get("picked")))
    _body_section(doc, data["case"], kind)  # 正文另起一节:页眉页脚只打在正文
    _summary_chapter(doc, data, cap, kind)

    # 章号=表号:分册时那一册的正文从「一」起;合订本依次 一 / 二
    if kind in ("mass", "all"):
        doc.add_page_break()  # 每章另起一页
        _mass_chapter(doc, data, cap, "1")
    if kind in ("energy", "all"):
        doc.add_page_break()
        _energy_chapter(doc, data, cap, "2" if kind == "all" else "1")
    doc.add_page_break()
    _notes_chapter(doc, data)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def build_report(
    conn, case_id: int, parts=None, picked=None, kind: str = "all"
) -> tuple[bytes, str]:
    """一步生成 .docx,返回 `(字节, 文件名)`。

    缺 python-docx 时抛 `ImportError`;工况不存在时抛 `LookupError`。
    """
    data = build_data(conn, case_id, parts, picked)
    return render(data, kind), filename(case_id, kind)
