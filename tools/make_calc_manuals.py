"""生成两份《计算说明书》.docx —— 管路选型 / 泵。

口径来源: app/design/pipe/*、app/design/pump*.py;版式复用 app/export/docx_kit.py。
运行: python -m tools.make_calc_manuals
输出: docs/casebook-pipe-calc-manual.docx、docs/casebook-pump-calc-manual.docx
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from app.export import docx_kit as kit
from app.version import __version__
from tools import calc_manual_math as cmm

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs"

# 泵册主题色:钢蓝,与管道册墨绿、衡算册冷蓝/砖红都能分开
kit.KINDS["pump"] = {
    "title": "泵计算说明书",
    "book": "泵专业 · 计算册",
    "accent": "235A7A",
    "light": "DEEBF2",
    "zebra": "F6FAFC",
}
PIPE_KIND, PUMP_KIND = "pipe", "pump"
_ACTIVE_KIND_NAME = PIPE_KIND


def _fonts(run, size=None, bold=None, name="宋体"):
    return kit._font(run, size, bold, name)


_CODE_COLOR = "1F2937"
# 全册统一符号:带下划线与 11.1 的紧凑写法都收,避免表 11-1 与 11.1 出现两套视觉。
_SYMBOL_RE = re.compile(
    r"(?<![A-Za-z0-9_])(D_i|D_o|P_d|T_d|t_c|t_req|H_pr|H_sel|Di|Do|Pd|Td|tc)(?![A-Za-z0-9_])"
)
_SYMBOL_PARTS = {
    "D_i": ("D", "i"),
    "D_o": ("D", "o"),
    "P_d": ("P", "d"),
    "T_d": ("T", "d"),
    "t_c": ("t", "c"),
    "t_req": ("t", "req"),
    "H_pr": ("H", "pr"),
    "H_sel": ("H", "sel"),
    "Di": ("D", "i"),
    "Do": ("D", "o"),
    "Pd": ("P", "d"),
    "Td": ("T", "d"),
    "tc": ("t", "c"),
}


def _add_run(
    para,
    text,
    size,
    bold=False,
    font_name="宋体",
    color=None,
    subscript=False,
    eastasia=None,
):
    """写一个 run;code/公式/下标都走这里,避免字号与字体散落在各函数里。"""
    run = para.add_run(text)
    _fonts(run, Pt(size), bold, font_name)
    if eastasia:
        run._element.rPr.rFonts.set(qn("w:eastAsia"), eastasia)
    if color:
        run.font.color.rgb = kit._rgb(str(color))
    if subscript:
        run.font.subscript = True
    return run


def _write_text_with_symbols(
    para,
    text,
    size,
    bold=False,
    font_name="宋体",
    color=None,
    eastasia=None,
    math=True,
):
    """把正文里的变量写成 Word 原生公式（行内 OMML）。

    表格传 math=False：9–13 列宽表用行内公式会被撑高，且字段名与变量在表里
    本来就混排，保持原下标文本更稳。代码片段（反引号内）也不进入这里。
    """
    position = 0
    if math and cmm.has_inline_math(text):
        for match in cmm.inline_matches(text):
            if match.start() > position:
                _add_run(
                    para,
                    text[position : match.start()],
                    size,
                    bold,
                    font_name,
                    color,
                    eastasia=eastasia,
                )
            if not cmm.add_math(para, cmm.inline_latex(match.group(1)), size):
                _add_run(
                    para,
                    match.group(1),
                    size,
                    bold,
                    font_name,
                    color,
                    eastasia=eastasia,
                )
            position = match.end()
        if position < len(text):
            _add_run(
                para,
                text[position:],
                size,
                bold,
                font_name,
                color,
                eastasia=eastasia,
            )
        return

    for match in _SYMBOL_RE.finditer(text):
        if match.start() > position:
            _add_run(
                para,
                text[position : match.start()],
                size,
                bold,
                font_name,
                color,
                eastasia=eastasia,
            )
        token = match.group(1)
        base, sub = _SYMBOL_PARTS[token]
        _add_run(para, base, size, bold, font_name, color, eastasia=eastasia)
        _add_run(
            para,
            sub,
            max(size - (1.5 if len(sub) > 1 else 1.0), 6.5),
            bold,
            font_name,
            color,
            subscript=True,
            eastasia=eastasia,
        )
        position = match.end()
    if position < len(text):
        _add_run(
            para,
            text[position:],
            size,
            bold,
            font_name,
            color,
            eastasia=eastasia,
        )


def _write_inline(
    para,
    text,
    size=10.5,
    bold=False,
    font_name="宋体",
    color=None,
    eastasia=None,
    math=True,
):
    """把成对反引号解析成 Word 行内代码;奇数个反引号时整段退化为普通文本。

    代码 run 用 Consolas、比正文小 0.5 pt、无可见反引号。**粗体标记仍在写入前去掉**,
    因为 Word 里保留 ** 比丢失粗体更糟。
    """
    text = str(text).replace("**", "")
    if "`" not in text:
        _write_text_with_symbols(para, text, size, bold, font_name, color, eastasia, math)
        return
    parts = text.split("`")
    if len(parts) % 2 == 0:
        _write_text_with_symbols(
            para,
            text.replace("`", ""),
            size,
            bold,
            font_name,
            color,
            eastasia,
            math,
        )
        return
    for index, part in enumerate(parts):
        if not part:
            continue
        if index % 2:
            _add_run(
                para,
                part,
                max(size - 0.5, 7.5),
                bold,
                "Consolas",
                color or _CODE_COLOR,
            )
        else:
            _write_text_with_symbols(
                para,
                part,
                size,
                bold,
                font_name,
                color,
                eastasia,
                math,
            )


def _format_inline_paragraph(para):
    """表格单元格建好后再跑一遍:把反引号字段改成行内代码,保留原 run 的字号/加粗。"""
    if "`" not in para.text and not _SYMBOL_RE.search(para.text):
        return
    runs = list(para.runs)
    if not runs:
        return
    base = runs[0]
    size = base.font.size.pt if base.font.size is not None else 9.0
    bold = bool(base.font.bold)
    font_name = base.font.name or "宋体"
    color = str(base.font.color.rgb) if base.font.color and base.font.color.rgb else None
    text = para.text
    for run in runs:
        run._element.getparent().remove(run._element)
    # 表格内不做行内公式：10–13 列宽表会被 OMML 撑高，字段名也容易被误认成变量
    _write_inline(para, text, size, bold, font_name, color, math=False)


def _write_mono_symbols(para, text, size):
    """11.1 节专用:符号真下标,其余字符保持 Consolas。"""
    _write_text_with_symbols(para, text, size, False, "Consolas", "262626", "宋体")


def _page_setup(doc):
    """A4 + 页边距 + 正文样式(与衡算报告一致)。"""
    kit._setup(doc, {})


def _cover(
    doc,
    kind: str,
    title: str,
    book: str,
    rows,
    note: str = "",
    tagline: str = "面向需要复算与核对的使用者 · 口径以本工具当前版本的实际计算为准",
    stamp: str | None = None,
):
    meta = kit.kind_meta(kind)
    kit.activate(kind)
    kit._band(doc, [(meta["accent"], 0.30), (meta["light"], 0.07)])

    kicker = doc.add_paragraph()
    kicker.alignment = WD_ALIGN_PARAGRAPH.CENTER
    kicker.paragraph_format.space_before = Pt(150)
    kicker.paragraph_format.space_after = Pt(0)
    head = _fonts(kicker.add_run("CASEBOOK · 计算说明书"), Pt(8.5), False)
    head.font.color.rgb = kit._rgb("8C8C8C")
    kit._tracked(head, 30)

    kit._gap(doc, 10)
    kit._para(doc, title, Pt(26), True, WD_ALIGN_PARAGRAPH.CENTER, space_after=0, name="黑体").runs[
        0
    ].font.color.rgb = kit._rgb(meta["accent"])

    kit._gap(doc, 26)
    line = doc.add_table(rows=1, cols=1)
    line.autofit = False
    from docx.enum.table import WD_ROW_HEIGHT_RULE, WD_TABLE_ALIGNMENT

    line.alignment = WD_TABLE_ALIGNMENT.CENTER
    kit._set_table_width(line, 4.2)
    cell = line.rows[0].cells[0]
    cell.width = Cm(4.2)
    kit._shade(cell, meta["accent"])
    line.rows[0].height = Cm(0.10)
    line.rows[0].height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
    para = cell.paragraphs[0]
    para.paragraph_format.space_before = Pt(0)
    para.paragraph_format.space_after = Pt(0)
    para.paragraph_format.line_spacing = Pt(1)
    _fonts(para.add_run(""), Pt(1))

    kit._gap(doc, 14)
    kit._para(doc, book, Pt(12), False, WD_ALIGN_PARAGRAPH.CENTER, space_after=0).runs[
        0
    ].font.color.rgb = kit._rgb("595959")
    kit._gap(doc, 6)
    kit._para(
        doc,
        tagline,
        Pt(9),
        False,
        WD_ALIGN_PARAGRAPH.CENTER,
        space_after=0,
    ).runs[0].font.color.rgb = kit._rgb("8C8C8C")

    kit._gap(doc, 70)
    kit._cover_meta(doc, rows, meta)

    kit._gap(doc, 48)
    stamp = kit._para(
        doc,
        stamp or f"版本 v{__version__}　·　{datetime.now().strftime('%Y-%m-%d')}　·　Casebook",
        Pt(8.5),
        False,
        WD_ALIGN_PARAGRAPH.CENTER,
        space_after=0,
    )
    stamp.runs[0].font.color.rgb = kit._rgb("A6A6A6")
    if note:
        kit._gap(doc, 6)
        kit._para(doc, note, Pt(8), False, WD_ALIGN_PARAGRAPH.CENTER, space_after=0).runs[
            0
        ].font.color.rgb = kit._rgb("A6A6A6")
    doc.add_page_break()


def _toc_title(doc) -> None:
    """目录标题:视觉同一级标题,但不带 outlineLvl,免得目录把自己也列进去。"""
    from docx.oxml import OxmlElement

    meta = kit.kind_meta(_ACTIVE_KIND_NAME)
    para = doc.add_paragraph()
    para.paragraph_format.space_before = Pt(6)
    para.paragraph_format.space_after = Pt(9)
    run = _fonts(para.add_run("目录"), Pt(15), True, "黑体")
    run.font.color.rgb = kit._rgb(meta["accent"])
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "3")
    bottom.set(qn("w:color"), meta["accent"])
    borders.append(bottom)
    para._p.get_or_add_pPr().insert_element_before(borders, *kit._PPR_AFTER_PBDR)


def _toc(doc, kind: str):
    """目录页:插入 TOC 域,打开后按 F9 更新页码。"""
    global _ACTIVE_KIND_NAME
    _ACTIVE_KIND_NAME = kind
    kit.activate(kind)
    _toc_title(doc)
    para = doc.add_paragraph()
    para.paragraph_format.space_after = Pt(8)
    run = para.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = r'TOC \o "1-3" \h \z \u'
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = "目录将在 Word 中更新：右键 →“更新域”，或按 F9。"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for node in (begin, instr, sep, text, end):
        run._r.append(node)
    _fonts(run, Pt(10))
    doc.add_page_break()


def _body(doc, kind: str, header_note: str):
    """正文节:页眉标题 + 版本,页脚页码。"""
    meta = kit.kind_meta(kind)
    kit.activate(kind)
    from docx.enum.section import WD_SECTION

    section = doc.add_section(WD_SECTION.NEW_PAGE)
    pg = OxmlElement("w:pgNumType")
    pg.set(qn("w:start"), "1")
    section._sectPr.insert_element_before(
        pg,
        "w:cols",
        "w:formProt",
        "w:vAlign",
        "w:noEndnote",
        "w:titlePg",
        "w:textDirection",
        "w:bidi",
        "w:rtlGutter",
        "w:docGrid",
        "w:printerSettings",
        "w:sectPrChange",
    )
    header = section.header
    header.is_linked_to_previous = False
    head = header.paragraphs[0]
    head.alignment = WD_ALIGN_PARAGRAPH.LEFT
    head.paragraph_format.tab_stops.add_tab_stop(Cm(16.0), WD_TAB_ALIGNMENT.RIGHT)
    _fonts(head.add_run(f"Casebook · {meta['book']}"), Pt(8.5), False)
    _fonts(head.add_run(f"\t{header_note}"), Pt(8.5), False).font.color.rgb = kit._rgb("808080")
    p_pr = head._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "4")
    bottom.set(qn("w:color"), meta["accent"])
    bottom.set(qn("w:space"), "2")
    borders.append(bottom)
    p_pr.append(borders)

    footer = section.footer
    footer.is_linked_to_previous = False
    foot = footer.paragraphs[0]
    foot.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _fonts(foot.add_run("— "), Pt(8.5), False).font.color.rgb = kit._rgb("808080")
    run = foot.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for node in (begin, instr, end):
        run._r.append(node)
    _fonts(run, Pt(8.5))
    _fonts(foot.add_run(" —"), Pt(8.5), False).font.color.rgb = kit._rgb("808080")


def h(doc, text: str, level: int = 1):
    """章节标题:不把装饰符号写进文字(否则目录里会出现 ▍)。"""
    from docx.oxml import OxmlElement

    meta = kit.kind_meta(_ACTIVE_KIND_NAME)
    sizes = {1: Pt(15), 2: Pt(12.5), 3: Pt(11)}
    para = doc.add_paragraph()
    level_node = OxmlElement("w:outlineLvl")
    level_node.set(qn("w:val"), str(max(level - 1, 0)))
    para._p.get_or_add_pPr().insert_element_before(
        level_node, "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange"
    )
    para.paragraph_format.space_before = Pt(15 if level == 1 else 10)
    para.paragraph_format.space_after = Pt(9 if level == 1 else 4)
    para.paragraph_format.keep_with_next = True
    run = _fonts(para.add_run(text), sizes.get(level, Pt(11)), True, "黑体")
    if level == 1:
        run.font.color.rgb = kit._rgb(meta["accent"])
        borders = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "6")
        bottom.set(qn("w:space"), "3")
        bottom.set(qn("w:color"), meta["accent"])
        borders.append(bottom)
        para._p.get_or_add_pPr().insert_element_before(borders, *kit._PPR_AFTER_PBDR)
    elif level == 2:
        para.paragraph_format.left_indent = Cm(0.15)
        borders = OxmlElement("w:pBdr")
        left = OxmlElement("w:left")
        left.set(qn("w:val"), "single")
        left.set(qn("w:sz"), "18")
        left.set(qn("w:space"), "6")
        left.set(qn("w:color"), meta["accent"])
        borders.append(left)
        para._p.get_or_add_pPr().insert_element_before(borders, *kit._PPR_AFTER_PBDR)
    return para


def p(
    doc,
    text: str,
    size: float = 10.5,
    bold: bool = False,
    indent: bool = True,
    space_after: float = 4,
):
    para = doc.add_paragraph()
    if indent:
        para.paragraph_format.first_line_indent = Cm(0.74)
    para.paragraph_format.space_after = Pt(space_after)
    _write_inline(para, text, size, bold)
    return para


def bullets(doc, items, size: float = 10.5):
    for item in items:
        para = doc.add_paragraph()
        para.paragraph_format.left_indent = Cm(0.74)
        para.paragraph_format.first_line_indent = Cm(-0.37)
        para.paragraph_format.space_after = Pt(3)
        _add_run(para, "· ", size, False)
        _write_inline(para, item, size, False)


def formula(doc, text: str, note: str = ""):
    """正式公式：能匹配到 LaTeX 的走 Word 原生公式（OMML）；匹配不到才回退文本。

    一个块里有多行推导时（如 t_c → t → t_req,nom），逐行居中排，避免一行过宽。
    """
    parts = cmm.formula_parts(text)
    if not parts:
        para = doc.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        para.paragraph_format.space_before = Pt(6)
        para.paragraph_format.space_after = Pt(2 if note else 8)
        _write_inline(para, text, 11.0, False, "Cambria Math", eastasia="宋体")
        if note:
            cap = doc.add_paragraph()
            cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
            cap.paragraph_format.space_after = Pt(8)
            _write_inline(cap, note, 8.5, False, "宋体", "7F7F7F")
        return para

    last = None
    for index, (latex, trailing) in enumerate(parts):
        para = doc.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        para.paragraph_format.space_before = Pt(6 if index == 0 else 0)
        para.paragraph_format.space_after = Pt(0)
        if not cmm.add_math(para, latex, 11.0, display=True):
            _write_inline(para, latex, 11.0, False, "Cambria Math", eastasia="宋体")
        last = para
        if trailing:
            cap = doc.add_paragraph()
            cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
            cap.paragraph_format.space_before = Pt(2)
            cap.paragraph_format.space_after = Pt(0)
            _write_inline(cap, trailing, 8.5, False, "宋体", "7F7F7F")
            last = cap
    if last is not None:
        last.paragraph_format.space_after = Pt(8)
    return last


def note(doc, text: str):
    para = doc.add_paragraph()
    para.paragraph_format.left_indent = Cm(0.4)
    para.paragraph_format.space_before = Pt(4)
    para.paragraph_format.space_after = Pt(8)
    from docx.oxml import OxmlElement as OE
    from docx.oxml.ns import qn as _qn

    shd = OE("w:shd")
    shd.set(_qn("w:val"), "clear")
    shd.set(_qn("w:color"), "auto")
    shd.set(_qn("w:fill"), "F2F5F7")
    para._p.get_or_add_pPr().append(shd)
    _write_inline(para, text, 9.0, False, "宋体", "404040")
    return para


def mono(doc, lines, size: float = 8.5, subscripts: bool = False):
    """等宽小字号代码/伪代码块:浅底、左缩进、不首行缩进。"""
    for line in lines:
        para = doc.add_paragraph()
        para.paragraph_format.left_indent = Cm(0.8)
        para.paragraph_format.space_after = Pt(1)
        para.paragraph_format.line_spacing = 1.0
        if subscripts:
            _write_mono_symbols(para, line, size)
        else:
            _add_run(para, line, size, False, "Consolas", "262626", eastasia="宋体")


def _plain(text: str) -> str:
    """兼容旧调用:去掉 Markdown 粗体标记;反引号由 `_write_inline` 解析。"""
    return str(text).replace("**", "")


_LEFT_HINTS = (
    "说明",
    "内容",
    "作用",
    "用途",
    "含义",
    "判据",
    "公式",
    "适用",
    "做什么",
    "落到输出",
    "换算",
    "备注",
)


def table(doc, headers, rows, widths, caption="", font_pt=8.5, center_cols=()):
    # _three_line 把 tuple 行当成 (数据, bold, fill) 三元组,这里统一转 list
    data_rows = [list(row) for row in rows]
    tbl = kit._three_line(
        doc,
        headers,
        data_rows,
        widths,
        caption=caption,
        font_pt=font_pt,
        center_cols=center_cols,
    )
    # 说明类列强制左对齐:满屏中文居中/右对齐都比左对齐难读
    left_cols = [i for i, head in enumerate(headers) if any(k in head for k in _LEFT_HINTS)]
    if left_cols:
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        for row in tbl.rows[1:]:
            for idx in left_cols:
                if idx < len(row.cells):
                    for para in row.cells[idx].paragraphs:
                        para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    for row in tbl.rows:
        for cell in row.cells:
            for para in cell.paragraphs:
                _format_inline_paragraph(para)
    return tbl


def build_pipe(doc):
    kit.activate(PIPE_KIND)
    _page_setup(doc)
    _cover(
        doc,
        PIPE_KIND,
        "管路选型计算说明书",
        "管路专业 · 计算册",
        [
            ("适用功能", "管道页 · 每条流股的管径、壁厚与等级号"),
            ("计算依据", "GB/T 20801 承压公式 · GB/T 17395 尺寸与偏差"),
            ("数据来源", "Aspen 工况快照 + 用户导入的国标 CSV + 人工参数"),
            ("复算方式", "按本文公式、常数与顺序逐项复算"),
        ],
        note="本说明书解释算法口径，不替代国标原文与工程设计审核",
    )
    _toc(doc, PIPE_KIND)
    _body(doc, PIPE_KIND, f"v{__version__}　管路选型计算说明书")

    h(doc, "1　适用范围与计算链", 1)
    p(
        doc,
        "本说明书描述 Casebook「管道」页对一条 Aspen 流股做管路选型的完整算法："
        "需要哪些输入、按什么顺序算、每个系数从哪里来、哪些情况不计算。"
        "只解释口径，不代替 GB/T 20801、GB/T 17395 原文，也不代替设计院的强度与应力审核。",
    )
    p(
        doc,
        "算法由四层叠加，复算时请分开核对：标准公式层（承压与尺寸序列）、"
        "工程判据层（最小壁厚、真空、两相、汽蚀等阈值）、人工参数层（流速、材质、腐蚀裕量）、"
        "校核层（V1–V15 只读检查，不参与计算；V14 未实现）。工程判据层的阈值集中写在 "
        "app/design/pipe/data.py，是可配置常量而不是标准原文，说明书中逐条注明。",
    )
    table(
        doc,
        ["步骤", "做什么", "落到输出"],
        [
            ("S0", "三张标准表就绪检查；剔除热流、功流等非物料流股", "可算流股集合"),
            (
                "S1",
                "体积流量门槛：缺失或 ≤ 0 直接不选型；0 < Q ≤ 0.01 m³/h 单列待人工确认",
                "missing 列表 / flagged 清单",
            ),
            ("S2–S4", "相态、介质代号、目标流速（含泵入口与汽蚀修正）", "phase / 代号 / v"),
            ("S5", "按目标流速算内径；两相另加动压头限速与液相下限", "D_i"),
            ("S6–S7", "设计压力、设计温度", "P_d / T_d"),
            (
                "S8",
                "材质 S30408/S31603 且操作绝压 4.7–6.4 MPa → 强制 R；"
                "P_d 6.4–10.0 MPa 自然落 R 档同样走 R1E；"
                "P_d >10.0 MPa 守卫关闭；温度不作硬门槛",
                "hp_formula / force_code；缺温度时等级号仍 R1E，"
                "壁厚走 S8a 退化，升档标记与 V13 只看自然档",
            ),
            (
                "S8a",
                "普通路径查 [σ]^t：缺操作温度、表 B.1 无该牌号，或 T_d 超出其最高温度",
                "查不到不早退：按工艺最小壁厚给几何 DN，壁厚与所需壁厚留空",
            ),
            (
                "S9",
                "R1E 路径：缺操作温度时不进 R1E 公式（等级号仍是 R1E），按 S8a 退化；"
                "有 T_d 但查不到 [σ]^t（S30408 T_d > 350 ℃，"
                "S31603 超表 B.1 上限或表 B.1 缺 S31603 牌号）",
                "查不到早退进 missing：无输出行、无 DN，不走几何退化",
            ),
            ("S10", "逐个 DN 升序同步试算壁厚与实际内径", "DN / Φ / S"),
            ("S11", "按实际内径复核流速", "v_act 与告警"),
            ("S12", "压力代号 + 顺序号 + 材质代号 + 绝热后缀", "等级号"),
            ("S13", "打标记并跑 V1–V15（V14 未实现）", "warnings / findings"),
        ],
        (1.4, 10.6, 4.0),
        caption="表 1-1　计算链总览",
        font_pt=8.5,
    )
    note(
        doc,
        "顺序不可交换：材质决定用表 1 还是表 4 的壁厚序列，所以必须在选档之前定；"
        "选档与定壁厚是同一轮试算，不是先选 DN 再补壁厚。",
    )

    h(doc, "2　输入与单位", 1)
    h(doc, "2.1　流股读数", 2)
    p(
        doc,
        "读数来自当前工况快照（读取时已按 Aspen 节点自带的单位串换算），计算不实时回读 Aspen。",
    )
    table(
        doc,
        ["字段", "含义", "单位", "在本算法中的作用", "缺值 / 异常行为"],
        [
            ("name", "流股号", "—", "主键、编号主体、泵进出的拓扑键", "缺失则不进入选型"),
            (
                "temperature_c",
                "操作温度",
                "℃",
                "设计温度、保温判定",
                "缺失时保留几何 DN，但 T_d、σ、壁厚为空；"
                "报 warn「缺操作温度，无法计算设计温度与壁厚」",
            ),
            (
                "pressure_bar",
                "操作压力（绝压）",
                "bar",
                "设计压力、真空判定、汽蚀判定",
                "缺失或 ≤0/非有限值：不计算 P_d、壁厚与等级号；详见 4.1 节与表 7-2",
            ),
            (
                "vol_flow_m3h",
                "体积流量",
                "m³/h",
                "内径、流速、两相密度",
                "缺失或 ≤0 进入 missing 列表不选型；0<Q≤0.01 单列待确认，不给 DN 与壁厚",
            ),
            (
                "mass_flow_kgh",
                "质量流量",
                "kg/h",
                "只用于两相 ρm = m/Q",
                "两相下缺失或 ≤0：跳过动压头限速，按两相流速选径并报 warn；单相不使用",
            ),
            (
                "vap_frac",
                "汽化率",
                "—",
                "相态判定的唯一依据",
                "缺失时暂按液相选型并报 warn「缺汽化率，暂按液相选型，请复核」，不是已确认单相",
            ),
            (
                "stream_type",
                "流股类型",
                "—",
                "MATERIAL 才参与选型",
                "热流、功流等非物料流股剔除，不选型",
            ),
        ],
        (2.6, 3.2, 1.2, 4.3, 4.7),
        caption="表 2-1　参与计算的流股字段及缺值行为",
        font_pt=8,
    )
    note(
        doc,
        "**压力的两种用法要分开**：真空判定与汽蚀区间按**绝压**使用，只做 bar × 100 = kPa，"
        "不减大气压（见 2.5 节）；计算设计压力时按第 4.1 节先减 0.1013 MPa 换成表压，"
        "再按倍率、附加项与下限取大，详见第 4.1 节。"
        "质量流量不用于 m³/h 换算：体积流量缺失时直接判「未选型」，不反算。"
        "压力缺失、≤0 或非有限值的完整后果见 4.1 节与表 7-2。",
    )

    h(doc, "2.2　由拓扑派生的三个集合", 2)
    p(
        doc,
        "设备—流股连接关系（connections）与设备类型（blocks）派生出以下集合，"
        "它们只影响选流速、限速与内径下限，不改压力与温度。",
    )
    bullets(
        doc,
        [
            "pump_in：进入泵的流股，按泵入口流速 0.8 m/s 选径；按拓扑判定，"
            "不分相态（气相、两相进口同样取 0.8 m/s）。",
            "pump_out：泵产出的流股，单独参与 V8（既是入口又是出口的错接检查），"
            "且禁止按汽蚀把流速往 0.8 m/s 压。",
            "pressurized：明显升压线，与 pump_out 取并集；判据见下式。"
            "该并集只在汽蚀限速门控中生效，不进入 V8。",
        ],
    )
    formula(
        doc,
        "|Q − Q_f| ≤ 0.02·max(Q, 1e-9)　且　|t − t_f| ≤ 2 ℃　且　p > p_f + 0.5 bar",
        "升压线判据：存在一条上游进料 f 同时满足流量、温度接近与压力升高",
    )
    p(
        doc,
        "该判据不只服务泵：对用非泵模块模拟的液体近等温升压拓扑也作兜底；"
        "`pump_out` 与它取并集，所以泵出口即使不满足压差判据，也仍按泵出口处理，"
        "不会被误当成可放大管径的吸入侧。并集只用于排除汽蚀限速；V8 不读取这个并集，"
        "只看行上的 `pump_outlet` / `pump_inlet` 标记是否同时为真。",
    )

    h(doc, "2.3　可调参数", 2)
    p(
        doc,
        "参数优先级：本次请求显式给出 > 该模型已保存的管道页参数（pipe_setting）> 内置默认。"
        "查询参数（liquid / gas / two / pump_inlet / factor / floor / max_dn）越界或非数字"
        "一律返回 400 拒绝，不会静默夹回；pressure_add_mpa 不在查询参数里，保存时校验 0–10 MPa，"
        "计算时按已保存值直算、不校验；design_temp_margin 与 design_temp_margin_c 只是内部 "
        "options 键（页面没有入口），代码不做范围校验。",
    )
    table(
        doc,
        ["参数", "默认", "允许范围", "作用"],
        [
            ("velocity.liquid", "1.5", "0.01–100 m/s", "液相目标流速 m/s"),
            ("velocity.gas", "15.0", "0.01–100 m/s", "气相目标流速 m/s"),
            ("velocity.two", "8.0", "0.01–100 m/s", "两相目标流速 m/s"),
            ("velocity.pump_inlet", "0.8", "0.01–100 m/s", "泵入口目标流速 m/s"),
            (
                "pressure_factor",
                "1.15",
                "1.0–10（无量纲）",
                "设计压力倍率。K_p < 1.0 会算出低于操作表压的设计压力，"
                "在任意 ΔP_add ≥ 0 下均无法成为控制项；新建与保存统一拒绝，"
                "旧库已存的 <1.0 值在读取时抬到 1.0 并沿用，不做静默削弱",
            ),
            ("pressure_add_mpa", "0.15", "保存 0–10 MPa；计算直算不校验", "设计压力附加项 MPa"),
            ("pressure_floor_mpa", "1.0", "0–100 MPa", "设计压力下限 MPa"),
            ("material", "S30408", "须在表 B.1 内", "全局默认牌号"),
            ("corrosion_mm", "按材质", "等级表 0–20 mm；或内部 options", "腐蚀裕量 C2，mm"),
            ("max_dn", "1000", "1–10000（DN）", "仅事后提示，不截断选档"),
            (
                "design_temp_margin",
                "true",
                "内部 options，页面无入口",
                "是否加设计温度裕量；false 时 T_d = t",
            ),
            ("design_temp_margin_c", "20", "建议 15–30 ℃，代码不校验", "温度裕量 ℃（低温段取负）"),
            (
                "options.stream_no_pattern",
                "^\\d{4}$",
                "正则字符串（内部 options）",
                "V10 流股号高亮判据；仅提示、不改名",
            ),
        ],
        (4.2, 2.4, 3.4, 6.0),
        caption="表 2-2　常用人工参数（含内部 options）",
    )
    p(
        doc,
        "另有若干工程判据开关（真空 95 kPa、两相 2000 Pa、汽蚀 0.8 m/s、DN65/125 屏蔽等），"
        "默认值见第 12 章常数速查，可逐工况覆盖。",
    )
    note(
        doc,
        "表 2-2 里 design_temp_margin / design_temp_margin_c 与 corrosion_mm 不是管道页查询参数："
        "前两者只能经内部 options 传入，代码对 15–30 ℃ 不设硬校验（常量 "
        "DESIGN_TEMP_MARGIN_RANGE_C 只作说明、未被引用）；corrosion_mm 走管道等级表，"
        "保存时按 0–20 mm 校验。",
    )

    h(doc, "2.4　标准表与 DN 序列", 2)
    p(
        doc,
        "壁厚序列与许用应力来自用户导入的三张 CSV：表 1（普通钢管壁厚）、表 4（不锈钢管壁厚）、"
        "表 B.1（材料许用应力）。国标原文不入库，表缺失时接口返回 409 并附配置指引，不吐空表。",
    )
    p(
        doc,
        "DN→外径映射内置在代码里（24 档），不读 CSV。口径分两段，**不要当成同一套系列**："
        "GB/T 17395 规定钢管外径与壁厚系列；化工管道 DN→外径 Φ 的系列 I/II 映射"
        "按 HG/T 20553（或 GB/T 20801.1）落实。"
        "DN10–600 为 GB/T 17395 系列 II（化工 Φ 系列），已与标准表逐档核对；"
        "DN700–1000 为 GB/T 21835 焊接钢管 / HG/T 20553 Ia 系列"
        "（711/813/914/1016 mm，与 ASME B36.10M 的焊接管口径对应），"
        "尚未在标准原文逐档核对，结果里统一标「需按焊接管标准复核」。"
        "两段按 DN 分段并存；保留 Ia 系列、不切换到 720/820/920/1020："
        "换系列会改内径，壁厚、流速与等级号随之变化，"
        "历史工况不可比。自动选档还会跳过 DN＜15（DN10 只留仪表/采样）、DN65 与 DN125"
        "（非优先系列，落到上一档 DN80 / DN150）。",
    )
    table(
        doc,
        ["DN / Φ mm", "DN / Φ mm", "DN / Φ mm"],
        [
            ("10 / 14", "80 / 89", "400 / 426"),
            ("15 / 18", "100 / 108", "450 / 480"),
            ("20 / 25", "125 / 133", "500 / 530"),
            ("25 / 32", "150 / 159", "600 / 630"),
            ("32 / 38", "200 / 219", "700 / 711"),
            ("40 / 45", "250 / 273", "800 / 813"),
            ("50 / 57", "300 / 325", "900 / 914"),
            ("65 / 76", "350 / 377", "1000 / 1016"),
        ],
        (4.2, 4.2, 4.2),
        caption=(
            "表 2-3　DN 与外径（DN10–600 系列 II；DN700–1000 GB/T 21835 / HG/T 20553 Ia；"
            "自动选档跳过 DN10 / DN65 / DN125）"
        ),
        font_pt=8,
        center_cols=(0, 1, 2),
    )

    h(doc, "2.5　单位换算", 2)
    table(
        doc,
        ["从", "到", "换算", "用在哪"],
        [
            ("bar（绝压）", "kPa（绝压）", "×100", "真空判定、汽蚀区间"),
            ("bar（绝压）", "MPa（绝压）", "÷10", "设计压力"),
            ("MPa 绝压", "MPa 表压", "−0.1013", "设计压力"),
            ("m³/h", "m³/s", "÷3600", "计算内径与目标流速"),
            ("m", "mm", "×1000 / ÷1000", "公称尺寸与水力计算互转（D_o、S、d 用 mm；d_m、L 用 m）"),
            ("kg/h ÷ m³/h", "kg/m³", "量纲相除", "两相混合密度 ρm"),
        ],
        (3.2, 3.4, 3.0, 6.4),
        caption="表 2-4　计算中实际发生的全部换算",
    )

    h(doc, "3　选径", 1)
    h(doc, "3.1　相态判定", 2)
    formula(
        doc,
        "x ≤ 1×10⁻⁴ → 液体　；　x > 1 − 1×10⁻⁴ → 气体　；　其余 → 气液共存",
        "x 为汽化率；用容差而非等于 0，因为实测有 1e-8 量级的痕量汽化率。"
        "边界取等号：恰为 1×10⁻⁴ 按单相，泵侧照算 NPSHₐ、读取层按 m/V 取密度；"
        "超过 1×10⁻⁴ 才按两相。**气相边界是严格大于**：恰为 `1−1×10⁻⁴` 仍按气液共存。"
        "该容差是全局唯一常量 `TWO_PHASE_VAP_EPS = 1×10⁻⁴`："
        "管道相态、泵 NPSHₐ 门禁与读取层的密度口径共用它"
        "（《泵计算说明书》第 2、4、7 章同一取值，两册不各写一套）",
    )
    p(
        doc,
        "**缺汽化率**时仍暂按液相选型（不阻断选径），但必须报"
        "「缺汽化率，暂按液相选型，请复核」；这是待复核状态，不是已确认单相。",
    )
    h(doc, "3.2　介质代号", 2)
    p(doc, "液体 PL、气体 PG、气液共存 PGL，代号可按相态覆盖，用于管路编号前缀。")

    h(doc, "3.3　目标流速与汽蚀修正", 2)
    table(
        doc,
        ["相态", "选取值 m/s", "推荐区间 m/s"],
        [
            ("液体", "1.5", "1.0–2.5"),
            ("气体", "15.0", "10.0–25.0"),
            ("气液共存", "8.0", "5.0–10.0"),
            ("泵入口（不分相态）", "0.8", "0.5–1.0"),
        ],
        (5.0, 4.0, 5.0),
        caption="表 3-1　目标流速",
        center_cols=(1, 2),
    )
    p(
        doc,
        "液相且接近沸点时把流速压到 0.8 m/s 以内，条件是同时满足"
        "80–105 ℃、绝压 95–115 kPa，且该流股不是泵入口、不是泵出口、也不在升压线上。"
        "泵入口按拓扑判定、不分相态，恒取 0.8 m/s（气相、两相进口也如此）；"
        "因此若气相或两相流股被拓扑误判成泵入口，按 0.8 m/s 选径会显著偏大，"
        "必须核查泵进出口拓扑与流股方向；升压线严禁按吸入侧放大。",
    )
    formula(
        doc,
        "v = min(v₀, 0.8)　（近沸点液体且非泵入口、非泵出口、非升压线）",
        "汽蚀限速（近沸点液体，且非泵入口、非泵出口、非升压线）",
    )
    note(
        doc,
        "80–105 ℃ / 95–115 kPa 是**常压水体系的经验判据**（水的常压沸点附近）。"
        "非水介质（有机溶剂、酸碱、低温冷媒）或加压/减压体系的泡点压力与这一区间无关，"
        "不能自动套用，必须按实际泡点压力人工复核，必要时手工改目标流速（见第 10 章）。",
    )

    h(doc, "3.4　计算内径", 2)
    formula(
        doc,
        "D_i = 1000 · √( 4·(Q/3600) / (π·v) )　[mm]",
        "Q 为 m³/h，v 为 m/s，结果 mm",
    )

    h(doc, "3.5　两相流的两个额外限制", 2)
    formula(
        doc,
        "ρm = m / Q　；　v_lim = √(2000 / ρm)",
        "均相动压头 ρv² ≤ 2000 Pa 反解流速上限；m 取 kg/h、Q 取 m³/h 时 ρm 量纲即 kg/m³",
    )
    formula(
        doc,
        "D_i = max( D_i, 1000·√(4·(Q/3600)/(π·v_lim)) )",
        "按限速重算内径，取二者较大值",
    )
    p(
        doc,
        "第二条限制：两相流的内径不得小于「产出它的设备」的纯液相进料内径的最大值。"
        "比较的是各流股选档前的计算内径 D_i（不是选档后的实际内径）；"
        "先完成全部流股首轮计算内径 D_i，再对命中下限的两相流股抬高 D_i，"
        "并统一重算其后续选档与壁厚，而不是只补丁单行。"
        "**两相但质量流量缺失或 ≤0** 时，ρm 无法求得，跳过 ρv² ≤ 2000 Pa 的动压头限速，"
        "仍按两相目标流速选型，并必须给出「缺质量流量，未执行两相动压头限速校核」告警；"
        "不得把「没校核」静默当成「已满足」。",
    )
    p(
        doc,
        "**上游液相内径下限的边界提醒**：该下限取产出设备所有纯液相进料内径的最大值。"
        "带微小侧线的闪蒸罐/分离器要特别注意：若设备还有主液相进料，两相出口会被主进料内径抬高；"
        "若拓扑里只看微小侧线，又会低估所需管径。选型前应核对设备进料拓扑，"
        "不能把侧线的细管径直接当主管设计依据。"
        "**多流股换热器（如 HeatX）**也有同类风险：冷热程进料共用同一设备拓扑，"
        "可能把另一程的大流量液相进料内径传导到本程两相出口；选型前须同时核对两程进料。",
    )

    h(doc, "3.6　DN 选档", 2)
    p(
        doc,
        "把可用 DN 升序试一遍（先剔除 65 与 125，跳过小于 DN15 的档），"
        "取第一个满足「实际内径 ≥ 计算内径」的档；壁厚在试算时同步求，见第 5 章。"
        "**缺壁厚或许用应力时**，不能拿外径直接当内径，也不能因此放宽档位；"
        "退化判据为 `D_o − 2×工艺最小壁厚(dn) ≥ D_i`"
        "（`D_o` 即外径；工艺最小壁厚见第 5.3 节），"
        "壁厚仍返回空，并继续遵守 DN10 / DN65 / DN125 的跳过规则。"
        "**缺操作温度时**，普通路径与 R1E 路径都按本退化给几何 DN；"
        "R1E 查不到 `[σ]^t` 的早退条件与例外见 5.4.3 节，此处不展开。",
    )
    p(
        doc,
        "**人工覆盖口径**：`pipe_run` 的 `dn_override` 与自定义外径/壁厚由公共合并层统一生效，"
        "管道页、导出表与校核使用同一份生效行；生效范围、壁厚重算与 V2/V3 校核见 9.3 节。"
        "普通行与 R1E 行的 `t_required_mm` 语义不同，不可直接横向比较，见 5.4.2 节。"
        "泵侧对应说明见《泵计算说明书》9.1 / 9.4 节。",
    )
    mono(
        doc,
        [
            "for dn in 升序(DN 序列):",
            "    if dn in (65, 125) or dn < 15: continue",
            "    D_o = DN_TO_OD[dn]",
            "    可算壁厚 = 压力、温度、许用应力、标准壁厚序列均具备 且 t_c < D_o/6",
            "    S = 圆整壁厚(D_o) if 可算壁厚 else None",
            "    if S is not None: 判据内径 = D_o - 2*S",
            "    else:            判据内径 = D_o - 2*S_min(dn)   # 仅几何估算",
            "    if 判据内径 >= Di: 选定 dn，停止（缺壁厚时 wall_mm 仍为 None）",
        ],
    )
    p(
        doc,
        "全部档位都试不通时，只有计算内径落在 DN1000 的 **动态容差带** 内才允许取 DN1000。"
        "容差带以该档实际圆整壁厚 S 算出的内径 `d_act = Φ1016 − 2S` 为基准，"
        "区间为 `(d_act, 1.05×d_act]`；超径百分比也以 `d_act` 为分母，"
        "不是固定按 996 mm 算。"
        "没有壁厚数据时退化用最小壁厚 10 mm，即 `d_act = 996 mm`，"
        "此时才回到 996–1045.8 mm 的口径（1.05×996 = 1045.8）。"
        "容差判据只在最后一档使用；更大内径判「需并联或专项设计」。"
        "外径 `D_o > 630 mm`（对应标准档 DN > 600）且表里没有该外径的壁厚数据时，"
        "用 HG/T 20553-2011 表 2.0.2-2 的 Ia 采购档 6.3 / 7.1 / 8 / 8.8 / 10 / 11 / "
        "12.5 / 14.2 / 16 / 17.5 / 20 mm 兜底（DN1000 无 6.3、7.1）并提示复核。",
    )

    h(doc, "4　设计压力、设计温度与材质", 1)
    h(doc, "4.1　设计压力", 2)
    formula(
        doc,
        "P_表 = P_绝(MPa) − P_atm　；　P_d = max(0, K_p·P_表, P_表 + ΔP_add, P_floor)　[MPa(G)]",
        "非真空流股；默认 P_atm = 0.1013 MPa、K_p = 1.15、ΔP_add = 0.15 MPa、P_floor = 1.0 MPa",
    )
    p(
        doc,
        "95–101.3 kPa 的表压为负：max 里的 0 把 P_d 兜到 0，不会出现负设计压力；"
        "人工把附加项与下限都设为 0 时同样是 0 MPa，压力代号取 H（常规情形下由最小壁厚控制）。",
    )
    p(
        doc,
        "绝压低于 95 kPa 判为真空：设计压力固定取 0.1 MPa，真空默认压力代号用 L"
        "（PN10，1.0 MPa；按工程最低压力等级配级）；真空身份由布尔字段 `is_vacuum` 独立识别，"
        "不依赖等级号字母前缀。壁厚由最小壁厚与外压筛查控制，不按 PN10（1.0 MPa）内压整档保护。"
        "`V` 在 HG/T 20519-2009 中表示 PN250（25.0 MPa）高压，本项目真空不占用该代号。",
    )
    formula(
        doc,
        "P_绝 < 95 kPa　→　P_d = 0.1 MPa，真空代号 L，is_vacuum = True",
        "真空判定；代号只表示 PN10（1.0 MPa）配级，算法按 is_vacuum 走外压路径",
    )
    p(
        doc,
        "**压力缺失与非法值不兜底**：`pressure_bar` 为空时 `P_d`、壁厚、等级号都返回空；"
        "`pressure_bar ≤ 0` 或不是有限数值时同样不计算，分别给「缺操作压力」与"
        "「操作绝压 ≤ 0 非法」等错误文案。整行仍按第 3.6 节的退化规则给一个几何 DN，"
        "但 `wall_mm`、`t_required_mm`、`grade_code`、`min_wall_mm` 全部留空，"
        "并发 `pressure_error` 与 V15 error。"
        "这样不会把缺压力静默算成 0 MPa 的 H1E，也不会把 0 或负压伪装成真空 L1E。",
    )
    p(
        doc,
        "**P_d > 22.0 MPa** 时超出内置 PN 代号表：仍保留输出行、按实际设计压力计算壁厚，"
        "但等级号返回空，并提示「超出 PN 代号表，未吃 PN 保护，需专项设计」；"
        "不得用某个相邻 PN 档冒充满足。",
    )

    h(doc, "4.2　设计温度与绝热后缀", 2)
    p(
        doc,
        "设计温度按操作温度的符号取裕量方向：t ≥ 0 ℃ 时 T_d = t + 20 ℃，t < 0 ℃ 时 T_d = t − 20 ℃，"
        "即裕量永远落在更危险的一侧（低温侧要更冷）。裕量可经 design_temp_margin 关闭。"
        "绝热后缀按设计温度判定：≥ 65 ℃ 加 -H（保温），≤ 0 ℃ 加 -C（保冷）；"
        "防烫 -P 与伴热 -T 不自动判定，需人工填。",
    )
    formula(
        doc,
        "T_d = t_操作 + ΔT_m（t ≥ 0）；　T_d = t_操作 − ΔT_m（t < 0）",
        "设计温度；默认 ΔT_m = 20 ℃；低温段取偏冷侧，否则 -C 与 V7 会被整体抬高 20 K 而漏判。"
        "低温段对称取 −20 ℃ 是**偏保守的简化策略**，不是标准强制规定；"
        "如有项目规定的低温裕量，应按项目规范覆盖。",
    )
    p(
        doc,
        "0 ℃ 附近要注意：t < 0 ℃ 时 T_d = t − 20 ℃，所以操作 −0.1 ℃ 会直接得到 −20.1 ℃，"
        "同时落入 V7（设计温度 < −20 ℃）并加保冷 -C。若该工况并非真实低温，需人工复核"
        "是否应关闭裕量或改小裕量；代码对裕量数值本身不做范围校验。",
    )
    p(
        doc,
        "**缺操作温度**时保留几何 DN，但不计算设计温度、许用应力与壁厚（`wall_mm`、"
        "`t_required_mm`、`stress_mpa`、`min_wall_mm` 为空）；等级号仍可按设计压力给出，"
        "但因设计温度无法判定，不含绝热后缀。"
        "告警只报一条「缺操作温度，无法计算设计温度与壁厚」，不重复报「缺许用应力」。",
    )

    h(doc, "4.3　材质、腐蚀裕量与许用应力", 2)
    p(
        doc,
        "材质优先级：逐流股人工指定 > 管道等级表命中规则 > 全局默认 S30408。"
        "腐蚀裕量 C2 同序：等级表规则 > 全局参数 > 按材质兜底（20# 取 1.0 mm，其余 0）。"
        "材质以 S3 开头时用表 4 的壁厚序列，否则用表 1。",
    )
    p(
        doc,
        "许用应力按设计温度在表 B.1 中查：低于表内最低温度取最低档，高于最高温度返回空"
        "（不猜蠕变段），落在两档之间线性插值；同一牌号多行时各温度取最小值。"
        "查不到许用应力时，选档退化为按 `D_o − 2×工艺最小壁厚(dn)` 估算几何内径"
        "（`D_o` 即外径，工艺最小壁厚见第 5.3 节，不是强度计算壁厚），"
        "壁厚留空并给出缺项提示；仍跳过 DN10 / DN65 / DN125，不得拿外径直接当内径。"
        "**例外**：R1E 查不到可用 `[σ]^t` 时不走本退化、直接进 missing，"
        "条件与文案见 5.4.3 节。"
        "缺操作温度时两条路径仍都按本退化，只在“查不到 [σ]^t”这一条上分开。",
    )
    p(
        doc,
        "R1E 专用许用应力（S30408 内置字典、S31603 查表 B.1）与腐蚀裕量优先级，"
        "统一见 5.4.3 节；本节只负责普通路径的表 B.1 查值。",
    )

    h(doc, "5　壁厚", 1)
    h(doc, "5.1　承压计算壁厚", 2)
    formula(
        doc,
        "t_c = P·D_o / [ 2·(σ·E_j + P·Y) ]",
        "直管内压；P 为 MPa、D_o 为 mm、σ 为 MPa；E_j = 1.0，Y = 0.4",
    )
    p(
        doc,
        "按 GB/T 20801.1—2025 表 27（`t < D_o/6` 时的 `Y` 值），`Y = 0.4` 的温度档是："
        "铁素体钢 `T_d ≤ 482 ℃`，奥氏体钢 `T_d ≤ 566 ℃`；再往上铁素体钢 510 ℃ 档取 0.5、"
        "538 ℃ 起取 0.7，奥氏体钢 593 ℃ 档取 0.5、621 ℃ 起取 0.7，两列之间可按 6.6.1.1 的规定内插。"
        "本工具不按温度切换 `Y`，一律取 0.4：`Y` 位于分母且为加项，取小值只会得到更厚的壁厚，"
        "属保守简化；超出上述温度档的工况仍由 V7 提示复核，结果不能当作符合表 27 取值的结果。"
        "V7 在 `T_d > 450 ℃` 即保守告警，早于上述各档，超温工况一律先按 V7 复核。",
    )
    p(
        doc,
        "公式形式上的适用条件是 `t_c < D_o/6`，但普通路径只对 `t_c(P_d)` 做这一判断："
        "若 `t_c(P_d) ≥ D_o/6`，`wall_mm` 与 `t_required_mm` 留空，"
        "选档流程仍按 5.3 节工艺最小壁厚做几何退化选档并保留输出行（不进 missing），"
        "压力有效且等级号非空时 `grade_code` 仍正常生成；V3 因 `t_required_mm` 为空自动跳过。"
        "PN 档的 `t_c(PN 档)` 不单独做 `t_c < D_o/6` 判断，只作为 "
        "`t_req = max(t_c(P_d), t_c(PN 档)) + C2` 的厚度项；"
        "因此 `t_c(P_d)` 满足薄壁、而 `t_c(PN 档)` 已超过 `D_o/6` 时，"
        "程序不会触发厚壁早退，仍按两值中的较大者圆整。"
        "R1E 路径按 5.4.1/5.4.2 节公式另行处理，其厚度不参与 PN 整档保护。"
        "该 `max()` 即「压力等级保护」；真空与 R1E 两条路径不参与。"
        "正常自动选档下 PN 档对应压力 ≥ P_d，因此 PN 项通常就是控制项，压力等级保护等效于"
        "按所在 PN 档压力计算承压壁厚；保留 `max()` 是给人工改档、旁路或边界异常留兜底。"
        "高温工况下仍用常温 PN 档压力与高温许用应力组合算壁厚，"
        "没有另做温度折减，结果偏保守。"
        "`P_d` 为空（缺压力或压力非法）时，本节壁厚路径直接返回空，不能按 0 MPa 继续算；"
        "`P_d > 22.0 MPa` 时没有可用的 PN 档，按 4.1 节保留输出行、按实际 P_d 算壁厚，"
        "但等级号与 PN 保护为空并提示专项设计。",
    )
    p(
        doc,
        "**E_j = 1.0 的前提**：无缝管按整体成形取 1.0；焊接管暂按「100% 无损检测（RT/UT）」"
        "假设取 1.0，代码未按接头型式与检测比例折减。实际接头系数低于 1.0 时，"
        "应按 GB/T 20801 的规定折减后重算，不能直接沿用本表结果（见第 10 章）。",
    )

    h(doc, "5.2　负偏差与圆整条件", 2)
    p(
        doc,
        "默认负偏差等级 S2-B 为 −12.5%·S，故默认档 C1 = 0.125·S；"
        "但 C1 不是恒定的 12.5%，而是由偏差等级与外径/壁厚比共同决定："
        "`data.py::negative_deviation` 按 `grade` 查 GB/T 17395 表 8 / 表 9，"
        "表 8 按 S/D 所在区间选规则——对称偏差取 max(pct·S, abs_mm)，仅下偏差取 pct·S；"
        "表 9 直接取 |下偏差|·S；表 8 正常无空档，某档若缺失（只可能是抄漏）返回 0.0，"
        "不能当成「无要求」。"
        "圆整不是简单「向上取一档」，而要同时满足两个不等式：",
    )
    formula(
        doc,
        "S ≥ max(t_req, 最小壁厚)　且　S − C1(S) ≥ t_req",
        "S 取该外径标准壁厚序列中的最小值",
    )

    h(doc, "5.3　工艺最小壁厚", 2)
    table(
        doc,
        ["DN ≤", "25", "65", "100", "150", "300", "600", "1000"],
        [("最小壁厚 mm", "2.0", "3.0", "3.0", "3.5", "4.5", "5.5", "10.0")],
        (2.6, 1.9, 1.9, 1.9, 1.9, 1.9, 1.9, 1.9),
        caption="表 5-1　工艺最小壁厚下限（工程惯例，非标准表）",
        font_pt=8,
        center_cols=(1, 2, 3, 4, 5, 6, 7),
    )
    p(
        doc,
        "下限只抬高不降低；实际壁厚等于下限时标记 wall_floored，说明这一档由刚度与施工要求决定，"
        "不是强度算出来的。真空管道还要与第 5.5 节的外压筛查值取大。"
        "**表 5-1 中 DN≤65 与 DN≤100 同取 3.0 mm**，是分档重叠的现状，不是笔误；"
        "按所在 DN 上限落档，DN100 不会因此取到 2.0 mm。"
        "R1E 路径的小管最小壁厚另见 5.4.1 节。",
    )

    h(doc, "5.4　高压不锈钢 R1E 公式", 2)
    h(doc, "5.4.1　触发路径与守卫（含 V13 判定）", 3)
    p(
        doc,
        "进入 R1E 有两条路径，壁厚公式相同，且**都不参与 PN 整档保护**："
        "① 压力带强制候选：材质 S30408/S31603，操作绝压落在 4.7–6.4 MPa（温度不作硬门槛），"
        "且设计压力 P_d ≤ 10.0 MPa，代号返回 R；若此时自然档**已经**是 R，则按下一条自然落档处理，"
        "不置强制标记、不报 V13。"
        "② 自然落档：设计压力 > 6.4 且 ≤ 10.0 MPa 时，压力代号规则本身给 R（10.0 MPa），"
        "S30408/S31603 同样按 R1E 公式。"
        "**P_d > 10.0 MPa 是硬守卫**：此时不强制升 R，也不走 R1E 公式，"
        "等级号回到自然 S/T/U 档（按 P_d 落在哪一档），壁厚回到 5.1 节通用公式"
        "并恢复对应 PN 整档保护；不会反向降级为 R 档，"
        "也不会出现「等级号 S/T/U、壁厚却仍按 R1E」的混合口径，且不报 V13。"
        "其他同样落入 R 等级号的不锈钢（如 S30403/S32168）仍走 5.1 节通用公式并叠加 "
        "PN100（10.0 MPa）保护；"
        "只有 S30408/S31603 进入 R1E 公式。这就是「只升不降」：升档判据与壁厚公式同开同关，"
        "不为套 R1E 公式而绕开压力等级。",
    )
    p(
        doc,
        "**缺操作温度时不进 R1E 公式，等级号仍是 R1E**：R1E 壁厚公式要求设计温度非空，"
        "缺温度时触发路径与普通路径都按 4.2 节退化给几何 DN，"
        "壁厚、`min_wall_mm`、`t_required_mm`、`stress_mpa` 全部留空。"
        "等级号与温度无关：压力代号、顺序号、材质代号照常拼，"
        "S30408/S31603 在 4.7–6.4 MPa 带内输出仍是 `R1E`（无 -H/-C 后缀），**不会退回 `Q1E`**。"
        "`hp_ss_upgraded` 与 V13 也只看「自然档是否已是 R」，同样不看温度："
        "操作绝压约 4.7–5.67 MPa（P_d ≤ 6.4，自然档 Q）置真并报 V13；"
        "约 5.67–6.4 MPa（P_d > 6.4，自然档 R）为假、不报 V13。"
        "只有「有设计温度但查不到 `[σ]^t`」才分两条路径，详见 5.4.3 节。",
    )
    p(
        doc,
        "R1E 路径同时采用 `HP_SS_MIN_WALL_MM = 3.0 mm` 作为小管最小壁厚下限参与圆整，"
        "覆盖表 5-1 中 DN ≤ 25 的 2.0 mm；该下限只抬高、不降低。",
    )
    p(
        doc,
        "**压力带为什么取 4.7–6.4 MPa**：按默认 pressure_factor = 1.15、附加 0.15 MPa、"
        "下限 1.0 MPa，P_d = 6.4 MPa 对应操作绝压约 5.67 MPa，P_d = 10.0 MPa 对应约 8.80 MPa；"
        "Q1E 与 R1E 的自然分界也就在操作绝压约 5.67 MPa。"
        "压力带取到 6.4 MPa，只是把 4.7–约 5.67 MPa（自然档仍是 Q）这一段硬拉进 R1E，"
        "消除同族相邻流股跨在边界两侧时「一条 Q1E、一条 R1E」的壁厚跳变。"
        "**操作绝压约 5.67–8.80 MPa 不需要再扩大强制带**：这一段 P_d 已落在 6.4–10.0 MPa，"
        "按压力代号规则本来就自然落 R（路径 ②），公式、C 与「不吃 PN 整档保护」的口径"
        "与强制带完全相同，扩大压力带不改变任何结果，只会把「升档」的边界说糊。"
        "**R1E 的真正上界由 `P_d > 10.0 MPa` 守卫给出**：操作绝压约 8.80 MPa 以上"
        "（或高倍率使 P_d 提前越过 10.0 MPa）自然档进入 S（PN160，16.0 MPa），"
        "守卫同时关掉强制代号与 R1E 壁厚公式，壁厚回到通用公式并恢复 PN160 保护；"
        "若只为把 R1E 铺到更宽的压力范围而抬高这道守卫，就等于绕开 PN160 保护去削薄。"
        "固定上限还让 4.7 与 6.4 两个边界只在一处判定，避免同族流股反复跳档。",
    )
    p(
        doc,
        "**操作绝压 5.67–6.4 MPa 重叠区的判定写死**：是否报 V13 不看操作绝压落在哪一段，"
        "只看两个布尔量——压力带候选成立（强制 R 标记为真）与自然压力档已为 R。"
        "只有「候选成立且自然档还不是 R」才置强制标记 `hp_ss_upgraded` 并报 V13。"
        "默认参数下 P_d = 6.4 MPa 对应操作绝压约 5.67 MPa，"
        "所以操作绝压 4.7–约 5.67 MPa（P_d ≤ 6.4，自然档 Q）报 V13；"
        "约 5.67–6.4 MPa（P_d > 6.4，自然档已是 R）仍走 R1E 壁厚公式，但属于自然落档，"
        "不置强制标记、不报 V13。边界随 pressure_factor 与附加项移动，"
        "一律以 P_d 是否大于 6.4 MPa 为准。",
    )

    h(doc, "5.4.2　承压公式与负偏差口径（含自定义 C1=0 语义差异）", 3)
    formula(
        doc,
        "t_c = P_d·D_o / (2·σ·E_j + P_d)　；　t = t_c + C　；　t_req,nom = t / 0.875",
        "E_j 即 5.1 节的焊接接头系数，取 1.0；C 见 5.4.3；t_req,nom 为含负偏差的所需壁厚，"
        "与 5.1/5.2 节不含负偏差的 t_req 分开记。负偏差按 12.5%（即除以 0.875）"
        "预先折入 t_req,nom，圆整与 V3 阶段置 C1 = 0，全流程仅计一次、不重复扣减；"
        "该 12.5% 由 R1E 专用常量固定，不受 5.2 节 grade（表 8 / 表 9）影响",
    )
    p(
        doc,
        "**公式形式**：分母里的 +P_d 等价于 GB/T 20801 通式取 Y = 0.5"
        "（按外径推导的圆筒公式，与 GB 150 的写法一致）。在完全相同的 P、D_o、σ 下，"
        "其承压计算壁厚反而略低于 5.1 节 Y = 0.4；实际 R1E 圆整结果往往更厚，"
        "是因为 S30408 专用许用应力字典取值更保守，且未配置等级表、也未传 options 时默认回退 "
        "C = 1.0 mm，与公式形式本身的保守性无关。",
    )
    p(
        doc,
        "**两路径的 `t_required_mm` 不可直接横向对比**：普通行（5.1/5.2/5.3）的 "
        "`t_required_mm` 不含负偏差，负偏差由圆整与 V3 统一扣 `C1`；"
        "R1E 行的 `t_required_mm` 已把 12.5% 负偏差除入（即上面的 `t_req,nom`），"
        "同时 `C1 = 0`。两路径的数值语义不同，不能把同一 `t_required_mm` 直接套到另一条路径比较。",
    )
    p(
        doc,
        "**自定义规格的 `C1=0` 在两路径下不对称**：普通路径的 `t_required_mm` 不含负偏差，"
        "`overrides.py` 对自定义 `od/wall` 置 `c1_mm = 0` 后，V3 实际比较的是 "
        "`wall_override_mm − 0` 与 `t_required_mm`，等于把人工输入壁厚直接当作可用净厚；"
        "使用者必须在 `wall_override_mm` 中自行预留制造负偏差，否则 V3 可能接受实际净厚不足的规格。"
        "R1E 路径下自定义规格同样 `C1 = 0`，但 12.5% 负偏差已预先除入 `t_required_mm`，"
        "所以仍按含负偏差的要求校核，二者不能互相套用。",
    )
    p(
        doc,
        "该式在圆整前校核承压计算厚度 t_c < D_o/6，不含腐蚀裕量 C；"
        "t = t_c + C 只用于后续所需壁厚与圆整。越界返回空、不硬算厚壁。"
        "C 不参与薄壁判据，避免小口径下 C 占比大时把可算组合误判成厚壁。",
    )

    h(doc, "5.4.3　许用应力与腐蚀裕量优先级", 3)
    p(
        doc,
        "**σ 用哪一套**：S30408 用专用字典（100 ℃ 137、150 ℃ 123、200 ℃ 115、250 ℃ 109、"
        "300 ℃ 104、350 ℃ 100 MPa），100–350 ℃ 线性插值。"
        "该字典是本工具内置的**保守工程取值**，独立于用户导入的表 B.1："
        "表 B.1 更新不会自动改写它，R1E 的 S30408 结果仍按这套字典；"
        "S31603 走表 B.1。"
        "**边界行为**：≤ 100 ℃ 钳位取 137 MPa；> 350 ℃ 直接返回空并早退，提示"
        "「设计温度超出 R1E 许用应力表范围（S30408 只到 350 ℃），需人工给 [σ]^t」，不做高温外推。"
        "S31603 走表 B.1：按表 B.1 的温度范围查值，超出最高温度同样返回空并早退，"
        "提示「设计温度超出表 B.1 许用应力范围，需人工给 [σ]^t」。"
        "若表 B.1 根本不含 S31603 牌号，`allowable_stress` 同样返回空，"
        "复用上述文案早退进 missing：不生成输出行、无 DN，不走几何退化；"
        "此时文案的「超出范围」应理解为「表 B.1 查不到可用 [σ]^t」，不只是温度越界。",
    )
    p(
        doc,
        "**C 从哪来**：先取管道等级表 `corrosion_mm`；等级表未定义时取内部 `options.corrosion_mm`"
        "（global）；两者都没有才回退工具内置的 R1E 默认腐蚀裕量 1.0 mm。"
        "直接以底层壁厚路由计算且未传入 R1E 腐蚀裕量时，底层也按 1.0 mm 回退；"
        "完整选档流程会先折算 R1E 实际 C。"
        "报表同时给出等级表 C2 与 R1E 实际参与计算的 C，"
        "避免出现「C2 列写 3 mm、公式按 1 mm 算」的对不上账。",
    )

    h(doc, "5.4.4　明细列核对与变薄风险", 3)
    p(
        doc,
        "**明细列不能混读**：R1E 行的「计算壁厚」列对应 `t_calc_mm`，"
        "仍是通用公式 `calc_wall_thickness(P_d, D_o, σ)` 的参考值；"
        "同列的 `stress_mpa` 也取表 B.1 普通查值。对 S30408，R1E 公式内部另取 "
        "5.4.3 的内置字典值，因此该行 `stress_mpa` 不等于实际 R1E 公式 σ；"
        "对 S31603，R1E 公式与 `stress_mpa` 同用表 B.1 值，两者一致。"
        "`c2_mm` 是等级表/全局给出的腐蚀裕量，`r1e_c_mm` 是 R1E 实际参与计算的 C，两者可能不同。"
        "真正决定圆整与 V3 的是 `t_required_mm = (t_c + C)/0.875`。"
        "11.2 节 E5 的 2.37 mm 与 4.16 mm 正是这一区别：前者是通用公式参考，"
        "后者才是 R1E 所需壁厚。",
    )
    p(
        doc,
        "**自定义规格的净厚口径**：普通路径未扣负偏差、R1E 已折入 12.5%，"
        "两路径的 `C1=0` 语义与人工预留要求见 5.4.2 节；V3 始终按 `wall_mm − c1_mm` 与 "
        "`t_required_mm` 比较。",
    )
    p(
        doc,
        "**R1E 的变薄风险不只 S31603 与 C2=0**：大口径默认 S30408 在 P_d 靠近 R 档下界"
        "（含操作绝压 4.7–6.4 MPa 的 R1E 区间）时，因跳过 PN100（10.0 MPa）整档保护，"
        "按 R1E 公式算得的壁厚也可能薄于带 PN100（10.0 MPa）保护的通用公式。"
        "若项目要求同等级全覆盖 PN100（10.0 MPa），须人工复核，"
        "不能只看等级号已到 R 就认为保护已足。",
    )

    h(doc, "5.5　真空管道的外压筛查", 2)
    formula(
        doc,
        "t_ext = D_o · ( SF·P_ext / (2E/(1−ν²)) )^(1/3)",
        "长圆筒弹性屈曲；P_ext = 0.1 MPa、SF = 3.0、E = 180000 MPa、ν = 0.3",
    )
    formula(
        doc,
        "t_ext_req = t_ext + C2　；　t_req = max( t_req, t_ext_req )　；　S − C1 ≥ t_req",
        "外压筛查先并入 C2，再与真空内压计算项（t_c(0.1 MPa) + C2）取大（不参与 PN 整档保护）；"
        "负偏差由统一圆整扣减",
    )
    p(
        doc,
        "化简后 t_ext ≈ 0.00912·D_o。外压厚度先算 `t_ext_req = t_ext + C2`，"
        "再并入 `t_req`，按 `S − C1 ≥ t_req` 统一圆整；"
        "报表里的 `external_wall_mm` 仍单独保存裸外压厚度，`t_required_mm` 才是含 C2 的最终要求。"
        "工艺最小壁厚（`MIN_WALL_BY_DN`）与高温真空 10 mm 仍只作为 `min_wall` 名义下限，"
        "由 `min_wall > t_req` 决定是否标 `wall_floored`；"
        "由外压 `t_ext_req` 控制的壁厚只算稳定性要求，不得误打「工艺最小壁厚托底」。"
        "V3 直接比较 `S − C1` 与 `t_required_mm`，与这里的 `t_req` 同一口径；"
        "人工覆盖后的行同样按生效的 od/wall 与外压结果校核。"
        "高温真空（操作 ≥ 250 ℃）且 DN ≥ 400 时，名义下限直接提到 10 mm；"
        "DN ≥ 700 另提示需设加强圈。支撑跨距、椭圆度与温度折减仍需按外压章节复核。",
    )
    p(
        doc,
        "`E = 180000 MPa` 是定值近似，未随材质与温度折减；它只服务本工具的外压筛查，"
        "不能替代按实际材料高温弹性模量做的外压稳定性校核。",
    )

    h(doc, "5.6　圆整与焊接管兜底", 2)
    p(
        doc,
        "圆整在标准壁厚序列中从小到大挑第一个同时满足 5.2 节两个不等式的值；"
        "序列缺失（表 1/表 4 未导入）时留空并注明「缺标准壁厚序列」，不拿经验值顶替。"
        "外径 `D_o > 630 mm`（对应标准档 DN > 600）且表内无该规格时，"
        "用焊接管兜底序列并标「需按焊接管标准复核」。"
        "**有序列但无可用档**时，`round_up_wall` 返回的 `wall_mm` 留空；"
        "`note` 写「所需 {floor:.2f} mm 超过该外径最大标准壁厚 {max(usable):g} mm"
        "(含壁厚下限),需并联或专项设计」，其中 `floor = max(t_required_mm, min_wall_mm)`。"
        "`design_wall_for_od` 仍保留数值型 `t_required_mm` 与 `min_wall_mm`。"
        "`step_dn` 在 `wall_mm` 为空时只用 `min_wall_for(dn)` 反算可用内径做几何退化选档，"
        "返回的仍是空 `wall_mm`；不把最小壁厚冒充实际壁厚。"
        "上层保留空壁厚并把该 `note` 加入 `warnings`，输出行不丢；V3 因 `wall_mm` 为空跳过，"
        "不产生 error finding。",
    )

    h(doc, "6　等级号", 1)
    table(
        doc,
        ["代号", "H", "K", "L", "M", "N", "P", "Q", "R", "S", "T", "U", "V"],
        [
            (
                "标准 PN",
                "PN2.5",
                "PN6",
                "PN10",
                "PN16",
                "PN25",
                "PN40",
                "PN63",
                "PN100",
                "PN160",
                "PN200",
                "—",
                "PN250",
            ),
            (
                "压力/MPa",
                "0.25",
                "0.6",
                "1.0",
                "1.6",
                "2.5",
                "4.0",
                "6.4",
                "10.0",
                "16.0",
                "20.0",
                "22.0",
                "25.0（标准）",
            ),
        ],
        (2.0, 1.2, 1.1, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2, 1.1, 1.5),
        caption="表 6-1　压力代号（H–U 依 HG/T 20519-2009；V 为标准的 PN250 高压代号）",
        font_pt=7.5,
        center_cols=tuple(range(13)),
    )
    note(
        doc,
        "**Q 代号的工程口径**：本工具沿用工程习惯值 6.4 MPa；HG/T 20519-2009 原文为 "
        "PN63（6.3 MPa）。这是本工具的代号取值约定，不代表标准原文；正式出图前应按项目规定核对。",
    )
    note(
        doc,
        "**U / V 两档的读法**：U 为工具保留的 22.0 MPa 高压档，不是 GB/T 1048 的标准优先数，"
        "标准 PN 标记留「—」，不得臆造 PN220；V 对应标准 PN250（25.0 MPa），"
        "但本工具 `PN_CODES` 未收录，等级选择不会生成 V。",
    )
    p(
        doc,
        "等级号 = 压力代号 + 顺序号 + 材质代号 + 绝热后缀。"
        "顺序号默认固定取 `1`（内部 `options.sequence_no` 可覆写）；"
        "压力代号、真空默认 L、`P_d > 22.0 MPa` 的空号规则统一见 4.1 节。"
        "材质代号预置 5 项：20#→B、Q345A/B→C、S30408/S31603→E；"
        "本工具未设耐热合金钢代号 D，遇到铬钼耐热钢不能把 E 当成已确认类别。"
        "未命中预置时按代码原式匹配前缀：不锈钢式 "
        "`^(S\\d{5}|0[0-9]{2}Cr\\d|0?[0-9]Cr\\d|1Cr\\d|2Cr\\d|3Cr\\d)` → E；"
        "碳钢式 `^(Q235|10#|20#|20G|25#)` → B；"
        "低合金式 `^(Q\\d{3}|16Mn|15Mn)` → C；`^(TA\\d|Ti)` → F；"
        "`^(PVC|PP|PE|PTFE|HDPE|FRP)` → G；仍无法识别时兜底为 E，"
        "并追加「材质无法判定，待人工确认」告警。"
        "这套材质代号规则与表 4 的壁厚路由是两套独立规则：表 4 仅按 "
        "`material.startswith('S3')` 路由，例如 `S22053` 得代号 E 但走表 1，"
        "`06Cr19Ni10`、`022Cr19Ni10`、`022Cr17Ni12Mo2` 在未归一成 `S30408`/`S31603` 时"
        "也得 E 但走表 1；代号 E 不等于壁厚路由已完成。"
        "后缀 -H 保温、-C 保冷。管路编号形如 PL-0101-80-L1E-H；"
        "无标准 DN 的自定义规格改用 `Φ{od}`，`grade_code` 为空时编号占位「待确认」，"
        "不出现字面 `-None`。",
    )
    p(
        doc,
        "**匹配顺序是“先预置、再按列表短路，正则内部从左到右”**：先查 "
        "`MATERIAL_CODE_BY_GRADE`（explicit），再按 `MATERIAL_CODE_RULES` 的列举顺序逐条 `match`，"
        "第一条命中即返回；同一条正则内的多个分支也按书写顺序从左到右取第一个能匹配的前缀。"
        "规则顺序：① 不锈钢式 → E；② 碳钢式 → B；③ 低合金式 → C；④ 钛材 → F；⑤ 非金属 → G；"
        "全部不中才返回 unknown。`Q235` 先被碳钢式命中为 B，不会再落入低合金 C；"
        "`Q345A/B`、`20#` 等已在预置表，先于正则返回。",
    )
    p(
        doc,
        "**新国标超低碳不锈钢的边界**：`022Cr19Ni10`、`022Cr17Ni12Mo2` 由专门的 "
        "`0[0-9]{2}Cr\\d` 分支命中，结果为 E（prefix）；`12Cr1MoV`、`15CrMo` 的 Cr 前都是"
        "**两位数字且不以 0 开头**——三位式 `0[0-9]{2}Cr\\d` 要求前导 0 加三位数字，"
        "一位式 `0?[0-9]Cr\\d` 只容一位数字，两者都进不去，"
        "因此仍返回 unknown 并由选型层兜底 E、加告警。"
        "两条卡点并不完全相同：`15CrMo` 是「Cr 前两位数字 + Cr 后不接数字」两条都不满足；"
        "`12Cr1MoV` 的 Cr 后确实接着 `1`，但 Cr 前是 `12`（两位、非 0 开头），"
        "仍命中不了一位式，也不满足三位式的前导 0。"
        "`06Cr19Ni10` 继续由原 `0?[0-9]Cr\\d` 分支命中。"
        "这三条边界由 `tests/test_pipe.py` 固定；正则只决定材质代号，不改变表 4 路由。",
    )
    note(
        doc,
        "**铬钼耐热钢的前缀路径**：`1Cr5Mo`、`9Cr1Mo` 在 `0?[0-9]Cr\\d` 分支 "
        "就被命中（该支写在 `1Cr\\d` / `2Cr\\d` / `3Cr\\d` 之前），"
        "得到 E、不产生告警；`5CrMo` 的 Cr 后不是数字，不命中任何一支，返回 unknown，"
        "选型层兜底记 E 并告警；`15CrMo` 同样 unknown、兜底 E 并告警。"
        "因此 `5CrMo` 与 `15CrMo` 的 E 都是「暂记」而非可靠分类，出等级号前必须人工复核。",
    )
    note(
        doc,
        "**正则冗余项说明**：`1Cr\\d` / `2Cr\\d` / `3Cr\\d` 已被同一式中的 "
        "`0?[0-9]Cr\\d` 覆盖，是代码保留的冗余分支，按原式列出仅供核对，不改变匹配结果。"
        "`20#` 并不靠正则命中：它先在预置表 `MATERIAL_CODE_BY_GRADE` 里以 explicit 短路返回 B，"
        "根本走不到碳钢式；碳钢式里的 `20#` 只是**保留的冗余项**，删掉也不影响结果。"
        "它不会进入不锈钢分支，也不存在两套判定。",
    )
    note(
        doc,
        "**压力代号规范口径**：HG/T 20519.6-1992 已由 HG/T 20519-2009 替代，本表 H–U 按后者；"
        "V 是标准中的 PN250（25.0 MPa）高压代号，本工具 `PN_CODES` 止于 U（22.0 MPa），"
        "V 不参与自动选档。真空 L 与 `is_vacuum` 识别见 4.1 节。",
    )
    p(
        doc,
        "`P_d > 22.0 MPa` 的完整行为见 4.1 节：系列号留空、按实际压力算壁厚、"
        "不吃 PN 整档保护并给专项设计告警；不得借用 V 冒充兜底代号。",
    )

    h(doc, "7　校验码 V1–V15", 1)
    p(
        doc,
        "校验只读已经算好的行，不改结果、不参与计算。每条 finding 带流股、列名、"
        "级别（error / warn）与说明，用来标黄对应列。",
    )
    table(
        doc,
        ["码", "判据", "级别", "说明"],
        [
            (
                "V1",
                "管路编号最后一段为纯数字且与所选 DN 不一致（历史校验）",
                "error",
                "只比较最后一段；选型主流程不自动写入行内历史字段 `pipe_no`"
                "（与 9.1 节 Word 报告导出时由 `_pipe_tag` 生成的展示编号不同），"
                "因此常规流程不会触发；仅单测或外部手工注入 `pipe_no` 时命中。",
            ),
            (
                "V2",
                "实际流速越出所用推荐区间（两相只报上界；泵入口/汽蚀限速按 0.5–1.0 m/s）",
                "warn",
                "与 V5 互斥：V5 先判、命中则 V2 不重复报；两相不报下界 —— "
                "ρv² ≤ 2000 Pa 是流速上限判据，抬高内径后实际流速本就可能低于 5 m/s，"
                "再报「流速不足」会自相矛盾",
            ),
            (
                "V3",
                "扣负偏差后 S−C1 < t_req（R1E 行为 t_req,nom）",
                "error",
                "普通行不含负偏差、R1E 行已折入 12.5%；自定义规格的 `C1=0` 语义见 5.4.2 节；"
                "`t_required_mm` 为空时自动跳过",
            ),
            (
                "V4",
                "等级号对应的 PN 压力（MPa）< P_d（is_vacuum 为真时跳过 PN 数值比较）",
                "error",
                "真空按 `is_vacuum` 布尔识别，配级与 L 代号见 4.1 节；"
                "`grade_code` 为空时 V4 自动跳过",
            ),
            (
                "V5",
                "两相流的目标流速不在 5–10 m/s（只查两相，不查液/气）",
                "warn",
                "泵入口的两相流豁免（泵入口按拓扑不分相态取 0.8 m/s）；"
                "看的是目标流速，不是实际流速；命中后 V2 不重复报同一问题",
            ),
            (
                "V6",
                "真空流股（`is_vacuum` 为真）",
                "warn",
                "「真空工况（按外压筛查，管道等级按PN10配级）:另按外压 0.1 MPa 校核」；"
                "代码原文的 `PN10` 对应标准 PN10（1.0 MPa）；"
                "与 V11 的 DN≥150 专项复核是两条独立提示",
            ),
            ("V7", "设计温度 < −20 或 > 450 ℃", "warn", "材质温度范围"),
            ("V8", "同一流股既是入口又是出口", "error", "泵接错"),
            (
                "V9",
                "grade_code 非空、设计温度 ≥ 80 ℃ 且无绝热代号",
                "warn",
                "`grade_code` 为空时跳过。80 ℃ 告警阈值与 4.2 节自动加 -H 的 65 ℃ "
                "是两个独立常量；常规流程 T_d ≥ 65 ℃ 已带 -H，故通常不触发",
            ),
            (
                "V10",
                "流股号不匹配 `STREAM_NO_PATTERN`（默认 `^\\d{4}$`）",
                "warn",
                "`0101` 通过；`S0101`、`0101A` 均不通过（整串必须恰为四位数字）。"
                "只高亮、不改名；`options.stream_no_pattern` 可覆写默认式（见 2.3 节）",
            ),
            (
                "V11",
                "真空（`is_vacuum` 为真）且 DN ≥ 150",
                "warn",
                "「真空 DN{dn}:已按外压失稳算最小壁厚 {external_wall_mm} mm,"
                "管道等级按PN10配级;仍需按实际支撑跨距复核」；"
                "代码原文的 `PN10` 对应标准 PN10（1.0 MPa）；"
                "花括号为运行期填入值；与 V6 的整条真空说明分开，V11 只报大口径的支撑跨距复核",
            ),
            ("V12", "两相且 DN ≥ 800", "warn", "分层/波状流风险"),
            (
                "V13",
                "已置强制标记（`hp_ss_upgraded`）：材质强制为 R 代号，"
                "且按 P_d 选出的自然压力代号还不是 R",
                "warn",
                "判定与边界统一见 5.4.1 节；自然落 R 与 P_d > 10.0 MPa 关闭 R1E 时不报",
            ),
            (
                "V14",
                "—",
                "预留",
                "预留，未实现",
            ),
            (
                "V15",
                "设计压力为空（缺操作压力，或操作绝压 ≤0/非有限值）",
                "error",
                "只给几何 DN，设计压力、壁厚与等级号为空；message 取 pressure_error，"
                "区分「缺操作压力」与「操作绝压 ≤0 非法」；完整行为见 4.1 节与表 7-2",
            ),
        ],
        (1.2, 7.0, 1.4, 6.4),
        caption="表 7-1　校验码逐条",
        font_pt=8,
        center_cols=(0, 2),
    )
    note(
        doc,
        "校验码到 V15；V14 为预留码，当前未实现。"
        "V3 对普通行、R1E 行、人工覆盖行与外压控制行的完整口径见 5.4 / 5.5 节。",
    )

    h(doc, "7.1　缺值与非法输入的行为对照", 2)
    table(
        doc,
        ["输入情况", "输出行为", "告警 / 校验"],
        [
            (
                "pressure_bar 缺失",
                "几何选档内部仍按工艺最小壁厚判定，但输出字段 min_wall_mm 留空；"
                "design_pressure_mpa、abs_pressure_kpa、wall_mm、t_required_mm、grade_code 全为空，"
                "wall_floored=False，pressure_error 有值",
                "warnings 追加「错误：缺操作压力，无法计算设计压力、壁厚与等级号」；V15 error",
            ),
            (
                "pressure_bar ≤0 或非有限值",
                "同上；不生成 H1E，也不伪装成真空 L1E",
                "warnings 的 error 文案分别为「操作绝压 ≤0 非法…」"
                "「操作压力不是有限数值…」；V15 error",
            ),
            (
                "temperature_c 缺失",
                "几何 DN 保留；design_temp_c、stress_mpa、wall_mm、t_required_mm 为空；"
                "min_wall_mm 同样留空；等级号仍可按设计压力给出，但不含绝热后缀",
                "只报一条「缺操作温度，无法计算设计温度与壁厚」；不重复报缺许用应力；无 V15",
            ),
            (
                "普通路径缺许用应力（表 B.1 无该牌号，或 T_d 超出其最高温度）",
                "不早退：几何 DN 保留；stress_mpa、wall_mm、t_required_mm、min_wall_mm 为空；"
                "压力有效且等级号非空时 grade_code 仍正常生成；V3 因 t_required_mm 为空自动跳过",
                "warn「缺许用应力」；不改走 R1E，也不进 missing",
            ),
            (
                "R1E 查不到可用许用应力（条件见 5.4.3 节）",
                "直接早退进 missing：不生成输出行、无 DN，也不走几何退化；其他流股照常选型",
                "提示文案与“牌号整行缺失”的措辞歧义统一见 5.4.3 节",
            ),
            (
                "vap_frac 缺失",
                "暂按液相选型，壁厚与等级号按液相正常计算",
                "warn「缺汽化率，暂按液相选型，请复核」",
            ),
            (
                "两相且 mass_flow_kgh 缺失或 ≤0",
                "跳过 ρv²≤2000 Pa 动压头限速，仍按两相目标流速选径；DN 可能偏小，phase=two",
                "warn「缺质量流量，未执行两相动压头（ρv²≤2000 Pa）限速校核」；"
                "另保留「气液共存:按两相流速选型,需专项核算」",
            ),
            (
                "P_d > 22.0 MPa",
                "保留输出行；按实际 P_d 计算壁厚；grade_code 为空，min_wall 正常；不吃 PN 保护",
                "warn「设计压力 … MPa 超出 PN 代号表，未吃 PN 保护，需专项设计」",
            ),
            (
                "所需壁厚超该外径标准序列最大规格（含焊接管 20 mm 兜底）",
                "`wall_mm` 留空；`t_required_mm` 与 `min_wall_mm` 保留数值；"
                "DN 选档只用 `min_wall_for(dn)` 做几何退化，返回的仍是空壁厚，"
                "不把最小壁厚当实际壁厚；保留输出行（不进 missing）",
                "warn 备注「所需 … mm 超过该外径最大标准壁厚 … mm(含壁厚下限),"
                "需并联或专项设计」；没有独立 error finding；V3 因 `wall_mm` 为空跳过。"
                "备注中的「所需」取 `max(t_required_mm, min_wall_mm)`，"
                "不是无条件取 `t_required_mm`",
            ),
            (
                "`t_c(P_d) ≥ D_o/6`（普通路径与 R1E 路径，厚壁公式不适用）",
                "普通公式与 R1E 均不硬算厚壁，wall_mm 与 t_required_mm 为空；"
                "选档流程仍按 5.3 节工艺最小壁厚做几何退化选档，保留输出行（不进 missing）；"
                "压力有效且等级号非空时 grade_code 正常生成；V3 因 t_required_mm 为空自动跳过",
                "备注「超出薄壁圆筒公式适用范围（t_c ≥ D_o/6），需另行校核」；"
                "该提示即按厚壁圆筒另行校核之意。PN 档的 `t_c(PN 档)` 不单独做该判断，"
                "故 `t_c(P_d)` 合格而 `t_c(PN 档)` 超限时不早退。"
                "字段口径分路径：普通路径 `min_wall_mm` 留空、`wall_floored` 为假；"
                "R1E 路径先算最小壁厚，再因 `t_required_mm` 留空提前返回，"
                "`min_wall_mm` 为数值（DN 分档下限与 R1E 3.0 mm 取大）、"
                "`wall_floored` 为假；两条路径的 `wall_mm` 与 `t_required_mm` 均留空",
            ),
        ],
        (4.0, 7.0, 5.0),
        caption="表 7-2　缺值/非法输入的确切行为",
        font_pt=8,
    )

    h(doc, "8　特殊工况", 1)
    bullets(
        doc,
        [
            "真空：判定、默认代号 L 与 `is_vacuum` 识别见 4.1 节；壁厚由最小壁厚与外压筛查控制，"
            "DN≥700 提示加强圈，深真空（≤10 kPa）另提示防闪蒸。",
            "低温：操作 ≤ −20 ℃ 时追加焊缝低温韧性与螺栓选型备注；"
            "保冷代号 -C 按设计温度 ≤0 ℃ 判定，而低温段设计温度取 t − 20 ℃，"
            "所以操作 −10 ℃ 就会落到 −30 ℃ 并加 -C。"
            "0 ℃ 附近（如 −0.1 ℃）因 t<0 直接取 t−20，会一步落到 −20 ℃ 以下，"
            "同时触发 -C 与 V7，需人工复核是否确为低温工况。"
            "低温不改变壁厚公式，也不触发材料降级。",
            "两相：8 m/s 基准、2000 Pa 动压头限速、上游液相内径下限；DN≥800 标分层风险。"
            "两相不按下界报流速不足。",
            "汽蚀：近沸点液体压到 0.8 m/s；泵入口与升压线不参与该修正。",
            "极低流量（微量流股）：0 < Q ≤ 0.01 m³/h 不参与自动选径，**不给 DN 与壁厚**，"
            "单列在「未选型 / 待人工确认」清单并回显流量，避免把仪表管、放空或备用线按工艺管选粗。",
            "非标编号：不匹配四位数字保留原名并标 V10，不重映射。",
        ],
    )

    h(doc, "9　输出", 1)
    p(
        doc,
        "管道页、Excel 管道计算书、AI 包 `pipe_schedule.csv` 与 Word 报告（管路一览表）"
        "共用同一份自动选型结果，"
        "输出前统一经公共合并层合并 `pipe_run` 的人工覆盖。一览表列与编号规则见 9.1 节，"
        "生效行与四出口的边界见 9.2 节，人工覆盖与壁厚重算见 9.3 节。",
    )
    h(doc, "9.1　一览表列与编号规则", 2)
    table(
        doc,
        ["列", "内容"],
        [
            ("1–6", "管路编号、温度、压力、状态、体积流量、公称直径"),
            ("7–9", "壁厚、管道材料、管道类型（无缝 / 焊接）"),
            ("10", "校核备注（放不下时折行，不挤主键列）"),
        ],
        (2.6, 13.4),
        caption="表 9-1　一览表十列",
        center_cols=(0,),
    )
    p(
        doc,
        "「公称直径」列显示生效行的 DN：标准档显示数字；人工 DN 与自定义规格均标注"
        "「人工 / 自定义」；自定义规格没有标准 DN 时显示 `Φ{od}`。",
    )
    p(
        doc,
        "编号规则：介质代号-流股号-DN-等级号，例如 PL-0101-80-L1E-H；"
        "等级号自带绝热后缀，编号不另拼标记位。无标准 DN 时 DN 段改用 `Φ{od}`；"
        "`grade_code` 为空时统一占位「待确认」，例如 `PL-0101-80-待确认`，"
        "Word 报告中的管路编号不会出现字面 `-None`。",
    )
    h(doc, "9.2　生效行与导出边界", 2)
    p(
        doc,
        "自动原值保留在 `auto_dn` / `auto_od_mm` / `auto_wall_mm`，"
        "生效行替换 `dn` / `od_mm` / `wall_mm`；"
        "页面、Excel、Word 报告与 AI 包读同一份生效行。"
        "管道页的「等级号」列在 `grade_code` 为空时显示「待确认」徽标"
        "（`app/web/static/pipe.js` 的 `grade_code` 单元格渲染分支），"
        "与 Word 报告编号里 `-待确认` 的占位同义，不是空白也不是 `-None`；"
        "该徽标只是页面提示，不影响选型结果本身。"
        "Excel/CSV 只有独立的「等级号」列、没有同名「管路编号」字段；"
        "`grade_code` 为空时该列保持为空，不写「待确认」，便于下游按空值筛选。"
        "校核状态与风险集中放「校核备注」列，管路编号保持纯净，可直接作 BOM 与 P&ID 的主键。",
    )
    h(doc, "9.3　pipe_run 人工覆盖与壁厚重算", 2)
    p(
        doc,
        "`dn_override` 人工 DN：按 DN→外径表取 OD，复用与自动选档相同的壁厚路由重算壁厚；"
        "材质表 1/表 4、PN 保护、R1E 与真空外压均按生效外径重新判定，R1E 仍受 P_d ≤ 10.0 MPa 守卫。"
        "重算后负偏差 C1 按生效壁厚重新求；R1E 行取 0。"
        "界面与写接口上，人工 DN 与自定义 `od/wall` 二选一；同时提交会被 400 拒绝。"
        "人工 DN 不受自动选档的跳过规则限制：前端下拉从 DN15 起即可直接选 DN65 / DN125，"
        "API 与库层面接受 `DN_TO_OD` 含 DN10 在内的全部档位；自动流程仍跳过 DN10 / DN65 / DN125。"
        "人工 DN 不在标准外径表时只加提示，保留自动规格，不猜壁厚。",
    )
    p(
        doc,
        "自定义 `od_override_mm + wall_override_mm`：壁厚直接取人工值，`c1_mm = 0`，"
        "再用新外径重算 `t_required_mm` 供 V3 使用；"
        "界面/接口同样要求与 `dn_override` 二选一。仅当历史库或直接写库使两者同时存在时，"
        "公共合并层以自定义 `od/wall` 优先于 `dn_override`；管道页、导出表与校核使用同一份生效行。"
        "这里的 `C1 = 0` 只表示自定义规格不在圆整阶段另扣负偏差；R1E 已把 12.5% 负偏差折入 "
        "`t_required_mm`，而普通路径的 `t_required_mm` 不含负偏差，"
        "使用者必须在 `wall_override_mm` 中自行预留制造负偏差。"
        "两条路径的语义差异只在 5.4.2 节展开，此处不重复。",
    )
    p(
        doc,
        "**自定义规格的几何校验**：保存层在 `od/wall` 成对校验之后，再要求 `2×wall < od`；"
        "`2×wall ≥ od` 返回 400，避免存进内径 ≤0 的记录。该校验只拦新写入，"
        "历史库已有的脏行仍可读取：运行期 `overrides` 仅在内径 >0 时写 `effective_inner_mm` 与 "
        "`v_act_ms`；泵侧取内径时对 `od − 2·wall ≤ 0` 返回空，最终判 `unlinkable`，"
        "原因写「没有可用的管内径」。",
    )
    p(
        doc,
        "生效行同时进入 V2 流速反算与 V3 壁厚校核：V2 按生效内径反算实际流速，"
        "V3 按生效 `wall_mm − c1_mm` 与 `t_required_mm` 比较。"
        "缺设计压力或许用应力时 `t_required_mm` 为空，V3 自动跳过并保留缺项提示。"
        "泵侧共用同一份管段记录，字段与联动口径见《泵计算说明书》9.1、9.4 节。",
    )

    h(doc, "10　边界与未实现", 1)
    table(
        doc,
        ["边界", "实际行为"],
        [
            ("DN 上限", "内置到 DN1000（Φ1016）；超出判「需并联或专项设计」；max_dn 只是事后提示"),
            (
                "DN1000 容差",
                "按最后一档实际圆整壁厚 S 算 d_act = Φ1016 − 2S，容差带为 (d_act, 1.05×d_act]，"
                "超径百分比以 d_act 为基准；无壁厚时退化用 S=10 mm，"
                "d_act=996 mm，区间为 996–1045.8 mm",
            ),
            ("压降核算", "不做；只在流速越限与 DN1000 容差时建议复核"),
            ("全厂管网", "不做水力平衡；管长与 Σhf 属于泵两侧管段的管段记录"),
            ("应力分析", "不做；高温只给「需做柔性应力分析」备注"),
            ("两相流型", "不判流型图；只有均相动压头与 DN≥800 风险标记"),
            ("GC 分级", "不做 GC1/GC2/GC3 分级"),
            ("标准表缺失", "接口 409 并附配置指引，不返回空表"),
            (
                "仅缺壁厚序列",
                "选档退化为 D_o − 2×工艺最小壁厚(dn) ≥ D_i，"
                "壁厚留空并注明缺序列；仍跳过 DN10 / DN65 / DN125",
            ),
            (
                "普通路径许用应力查不到",
                "t_req 为空，选档退化为几何估算、壁厚留空，不得拿外径当内径；"
                "仍跳过 DN10 / DN65 / DN125。R1E 不走此退化，见 5.4.3 节",
            ),
            (
                "R1E 查不到可用许用应力（S30408 > 350 ℃；"
                "S31603 超表 B.1 上限或表 B.1 缺 S31603 牌号）",
                "直接早退进 missing，不生成输出行、无 DN，也不走几何退化；完整条件见 5.4.3 节",
            ),
            (
                "pipe_run 人工覆盖",
                "人工 DN / 自定义规格贯通管道页、Excel/Word 报告/AI 导出表与 V2/V3；"
                "生效范围与壁厚重算见 9.3 节",
            ),
            (
                "空 grade_code",
                "管道页「等级号」列显示「待确认」徽标，"
                "Word 报告中的管路编号占位「介质-流股-DN-待确认」；"
                "Excel 与 AI 包「等级号」列保持为空（见 9.1–9.2 节）",
            ),
            (
                "R1E 腐蚀裕量 C",
                "按等级表 > 内部 options > 内置默认 1.0 mm 取值；完整优先级见 5.4.3 节",
            ),
            (
                "S31603 / C2=0 / 大口径 S30408 自然落 R",
                "S31603（查表 B.1）、等级表显式配置 C2=0，或大口径默认 S30408，"
                "在自然落 R 档（6.4 < P_d ≤ 10.0 MPa）走 R1E 时，"
                "因跳过 PN100（10.0 MPa）整档保护且公式等价 Y=0.5，算得壁厚可能薄于带 "
                "PN100（10.0 MPa）保护的通用公式；"
                "若项目要求同等级全覆盖 PN100（10.0 MPa）须人工复核",
            ),
            (
                "不锈钢表 4 路由",
                "仅按材质前缀 S3 路由；非 S3 开头的化学成分牌号/美标牌号/旧国标牌号"
                "（如 06Cr19Ni10、304、0Cr18Ni9）以及双相钢（如 S22053）回退至普通钢管表 1。"
                "材质代号规则是另一套前缀匹配（见第 6 章），两者互不替代："
                "S22053 得代号 E 但走表 1，"
                "06Cr19Ni10 未归一成 S30408 时也得 E 但走表 1",
            ),
            (
                "铬钼耐热钢前缀边界",
                "1Cr5Mo / 9Cr1Mo 被 `0?[0-9]Cr\\d` 先命中得 E、不告警；"
                "5CrMo / 15CrMo 不命中任何前缀，兜底 E 并告警。均须人工复核，详见第 6 章",
            ),
            ("高温 P-T 折减", "等级号取常温 PN 额定值，未按设计温度折减法兰/阀门/垫片额定值"),
            ("DN > 600 / D_o > 630 mm", "无实测壁厚数据时用焊接管兜底序列，并标复核要求"),
            ("焊接管 E_j", "统一取 1.0（按 100% 无损检测假设），未按接头型式与检测比例折减"),
            (
                "汽蚀限速",
                "80–105 ℃、95–115 kPa 只覆盖常压水体系；"
                "非水、加压或减压/真空体系需人工复核（见 3.3 节）",
            ),
        ],
        (3.6, 12.4),
        caption="表 10-1　边界与未实现事项",
    )

    h(doc, "11　复算示例", 1)
    p(
        doc,
        "以下五组数据由本仓库代码对同一套已导入标准表实算得到，可直接按前文公式复现。"
        "默认参数、默认材质 S30408、未配置等级表（通用公式 C2 = 0，R1E 回退 C = 1.0 mm）。",
    )
    table(
        doc,
        ["工况", "输入", "相态", "D_i mm", "DN/Φ", "P_d MPa", "T_d ℃", "σ MPa", "S mm", "等级号"],
        [
            (
                "E1",
                "18 m³/h，60 ℃，2 bar，x=0",
                "液体",
                "65.15",
                "80/89",
                "1.00",
                "80",
                "138",
                "3.0",
                "L1E-H",
            ),
            (
                "E2",
                "1200 m³/h，40 ℃，3 bar，x=1",
                "气体",
                "168.21",
                "200/219",
                "1.00",
                "60",
                "138",
                "4.5",
                "L1E",
            ),
            (
                "E3",
                "50 m³/h（5000 kg/h），90 ℃，1 bar，x=0.2",
                "两相",
                "62.88",
                "80/89",
                "1.00",
                "110",
                "138",
                "3.0",
                "L1E-H",
            ),
            (
                "E4",
                "20 m³/h，80 ℃，0.5 bar，x=0",
                "液体",
                "68.67",
                "80/89",
                "0.10",
                "100",
                "138",
                "3.0",
                "L1E-H",
            ),
            (
                "E5",
                "30 m³/h，200 ℃，50 bar，x=0",
                "液体",
                "84.10",
                "100/108",
                "5.63",
                "220",
                "126（R1E 112.6）",
                "4.5",
                "R1E-H",
            ),
        ],
        (1.0, 3.1, 1.4, 1.4, 1.4, 1.3, 1.0, 2.6, 1.2, 1.2),
        caption="表 11-1　五组实算结果",
        font_pt=7.5,
        center_cols=(0, 2, 3, 4, 5, 6, 7, 8, 9),
    )
    p(
        doc,
        "**表 11-1 的读数口径**：表内均为四舍五入后的展示值，计算按全精度传递；"
        "例如 E5 的 P_d 列写 5.63 MPa，实算按 5.6335 MPa。"
        "复算时以公式复算值为准，不要拿表里的截断值反推壁厚。",
    )
    p(
        doc,
        "**σ 的来源**：本表 σ 列取自测试环境导入的 GB/T 20801.1 表 B.1 CSV，"
        "不是 5.4.3 节 S30408 的 R1E 内置字典。E3 的 T_d = 110 ℃ 在该表 S30408 行中"
        "落在 100–150 ℃ 区间，两列均为 138 MPa，故插值后仍为 138 MPa；"
        "E5 的 126 MPa 是 220 ℃ 的通用公式查表值，112.6 MPa 是 R1E 专用字典值，"
        "两者已在表内分列。",
    )
    p(
        doc,
        "**E4 的防误解提示**：E4 虽与 E1、E3 同为 L1E-H（PN10，1.0 MPa 配级），"
        "但属真空工况（`is_vacuum=True`），壁厚按外压筛查控制、不吃 PN10（1.0 MPa）内压整档保护；"
        "不能只凭等级号相同就认为三者的壁厚控制路径也相同。",
    )
    h(doc, "11.1　E1 逐步复算", 2)
    mono(
        doc,
        [
            "输入：Q=18 m³/h，t=60 ℃，p=2.0 bar(绝)，x=0",
            "1 相态  x=0 → 液体；介质代号 PL；v=1.5 m/s",
            "2 内径  Di = 1000·√(4·(18/3600)/(π·1.5)) = 65.15 mm",
            "3 设计压力  表压 = 0.2−0.1013 = 0.0987 MPa",
            "            Pd = max(0, 1.15×0.0987, 0.0987+0.15, 1.0) = 1.0 MPa",
            "4 设计温度  Td = 60+20 = 80 ℃；Td≥65 → 保温后缀 -H",
            "5 材质 默认 S30408；σ(80 ℃)=138 MPa；C2=0",
            "6 承压壁厚  tc = 1.0×89 / (2×(138+1.0×0.4)) = 0.322 mm",
            "7 等级保护  1.0 MPa → PN10（1.0 MPa）；t(PN) 同样 0.322",
            "8 最小壁厚  DN80 → 3.0 mm；floor = max(0.322, 3.0) = 3.0",
            "9 圆整  S=3.0；C1=0.375；3.0−0.375=2.625 ≥ 0.322 → 取 3.0",
            "10 等级号  L + 1 + E + (-H) = L1E-H",
            "11 实际流速  v = (18/3600)/(π×0.083²/4) = 0.92 m/s",
            "   低于推荐下限 1.0 → 提示（DN65 被屏蔽，选到 DN80 后流速降到 0.92）",
        ],
        subscripts=True,
    )
    h(doc, "11.2　E5（R1E）关键项核对", 2)
    p(
        doc,
        "E5 的 4.5 mm 由 R1E 公式决定：σ 取专用字典在 220 ℃ 插值得 112.6 MPa，"
        "t = P_d·D_o/(2σ+P_d)+C = 5.6335×108/(2×112.6+5.6335)+1 = 3.64 mm，"
        "t/0.875 = 4.16 mm，"
        "圆整到该外径序列的 4.5 mm。此时「计算壁厚」列按通用公式展示的 2.37 mm 只是参考，"
        "决定圆整的是 4.16 mm 的所需壁厚。",
    )
    p(
        doc,
        "E5 的 2.37 mm 是通用公式不含 PN 保护的承压厚度 t_c(P_d)，不是 R1E 的最终所需壁厚。"
        "「计算壁厚」「c2_mm」「r1e_c_mm」属于明细表/导出字段，不列入第 9 章汇总十列表。",
    )
    p(
        doc,
        "**E5 的 C 为什么是 1.0 mm**：表 11-1 说明里的 C2 = 0 是按材质兜底的通用公式腐蚀裕量"
        "（不锈钢取 0）；R1E 的 C 另有优先级：管道等级表 > 内部 `options.corrosion_mm`（global）> "
        "回退 1.0 mm。本示例前两项均未配置，故回退 1.0 mm。"
        "所以通用公式参考值按 C2 = 0 算得 2.37 mm，"
        "R1E 公式按 C = 1.0 算得 3.64 mm，两者不是同一口径；报表同时给出 c2_mm 与 r1e_c_mm，"
        "避免出现「C2 列写 0、公式按 1 mm 算」的对不上账。",
    )

    h(doc, "12　常数速查", 1)
    table(
        doc,
        ["常数", "值", "用途"],
        [
            (
                "默认目标流速",
                "液 1.5 / 气 15.0 / 两相 8.0 / 泵入口 0.8 m/s",
                "S2–S4 选径；泵入口不分相态，汽蚀修正另限 0.8 m/s",
            ),
            ("相态容差 ε", "1e-4", "判液 / 气 / 两相；与泵 NPSH、读取层 ρ 同一常量"),
            ("设计温度裕量", "20 ℃（建议 15–30，不校验）", "T_d = t ± 20（t<0 取 −20）"),
            ("保温阈值", "T_d ≥ 65 ℃", "加 -H"),
            ("保冷阈值", "T_d ≤ 0 ℃", "加 -C"),
            ("真空阈值", "95 kPa 绝压", "判真空；0.1 MPa 设计压力"),
            (
                "真空代号",
                "L（PN10，1.0 MPa；工程最低压力等级）",
                "默认等级代号；内部 vacuum_pn_code 可覆写",
            ),
            ("真空识别", "is_vacuum 布尔字段（兼容旧 vacuum）", "独立于字母前缀，V4/V6/V11 共用"),
            (
                "标准 V 代号",
                "PN250（25.0 MPa）",
                "HG/T 20519-2009 高压含义；本工具 PN_CODES 未收录",
            ),
            ("V9 保温阈值", "80 ℃", "与自动加 -H 的 65 ℃ 独立"),
            ("高温真空下限", "t ≥ 250 ℃ 且 DN ≥ 400 → 10 mm", "真空名义下限抬高"),
            ("深真空提示", "≤ 10 kPa", "提示防闪蒸"),
            ("真空加强圈", "DN ≥ 700", "提示设加强圈"),
            ("压力倍率 / 附加 / 下限", "1.15 / 0.15 MPa / 1.0 MPa", "设计压力"),
            ("大气压 P_atm", "0.1013 MPa", "绝压转表压：P_表 = P_绝 − 0.1013"),
            (
                "腐蚀裕量兜底",
                "20# 为 1.0 mm，其余材质 0；等级表 > 全局 options > 兜底",
                "通用公式 C2",
            ),
            (
                "焊接管兜底序列",
                "6 / 8 / 10 / 12 / 14 / 16 / 18 / 20 mm（D_o > 630 mm 且表内无壁厚）",
                "标「需按焊接管标准复核」",
            ),
            ("低温备注阈值", "操作温度 ≤ −20 ℃", "追加低温韧性/低温螺栓提示"),
            (
                "压力缺值",
                "pressure_bar 为空 / ≤0 / 非有限值",
                "不计算 P_d、壁厚、等级号；V15 error",
            ),
            ("温度缺值", "temperature_c 为空", "只给几何 DN，不计算壁厚与 σ"),
            ("许用应力公式系数", "E_j=1.0，Y=0.4", "承压壁厚"),
            ("负偏差", "默认 S2-B：C1=0.125S", "圆整；其他等级查表 8/表 9"),
            ("工艺最小壁厚", "2.0/3.0/3.0/3.5/4.5/5.5/10.0 mm", "按 DN 分档"),
            ("R1E 小管最小壁厚", "3.0 mm", "覆盖表 5-1 中 DN ≤ 25 的 2.0 mm"),
            ("R1E 压力带", "操作绝压 4.7–6.4 MPa，且 P_d ≤ 10.0 MPa", "强制 R 代号"),
            ("R1E 守卫", "P_d > 10.0 MPa 关闭 R1E；壁厚回通用公式 + PN 整档保护", "只升不降"),
            (
                "R1E 公式",
                "E_j=1.0，C：等级表 > options global > 1.0 mm；t_req,nom 含负偏差",
                "高压不锈钢",
            ),
            (
                "R1E 许用应力",
                "≤100 ℃ 钳 137 MPa；>350 ℃ 给空（仅 S30408；S31603 查表 B.1）",
                "禁止高温外推",
            ),
            ("外压筛查", "P=0.1 MPa，SF=3，E=180000 MPa，ν=0.3", "真空"),
            (
                "外压并入",
                "t_ext_req = t_ext + C2；t_req = max(t_req, t_ext_req)，再按 S−C1 圆整",
                "真空壁厚",
            ),
            ("两相动压头", "2000 Pa", "限速"),
            ("两相分层风险", "DN ≥ 800", "V12 warn"),
            ("V7 材质温度告警", "−20 ℃ / 450 ℃", "超范围 warn"),
            ("汽蚀限速", "0.8 m/s；80–105 ℃；95–115 kPa", "近沸点液体"),
            ("升压线", "ΔQ≤2%，ΔT≤2 ℃，Δp>0.5 bar", "排除汽蚀修正"),
            ("极低流量", "0.01 m³/h", "0<Q≤该值：不选径、不给 DN，单列待确认"),
            ("DN1000 容差", "d_act = Φ1016 − 2S；区间 (d_act, 1.05×d_act]", "最后一档"),
            (
                "DN1000 退化",
                "无壁厚用 S=10 mm → d_act=996 mm，区间 996–1045.8 mm",
                "仅有几何估算时",
            ),
            ("DN 系列", "DN10–600 系列 II；DN700–1000 GB/T 21835 / HG/T 20553 Ia", "外径映射口径"),
            ("自动跳过 DN", "DN＜15（DN10）、DN65、DN125", "不参与选档"),
        ],
        (4.0, 6.6, 5.4),
        caption="表 12-1　常用常数",
        font_pt=8,
    )
    note(
        doc,
        "所有常数集中在 app/design/pipe/data.py 与 constants.py，可经参数覆盖；"
        "清单中的「工程惯例」项（最小壁厚分档等）不是标准原文。",
    )

    h(doc, "附录　核心代码函数映射表", 1)
    p(
        doc,
        "下表仅供代码核对，正文中的规则以章节表述为准。函数名带下划线者为模块内部实现，"
        "不构成对外接口。",
    )
    table(
        doc,
        ["文档对象", "代码位置", "说明"],
        [
            ("整批选型", "app/design/pipe/selection.py::build", "逐流股选径、选壁厚并汇总输出行"),
            ("单流股选型", "selection.py::_select_stream", "按相态、材质、压力等级路由壁厚"),
            ("公称直径选档", "app/design/pipe/step_dn.py::select_dn", "按目标流速与内径选标准 DN"),
            (
                "设计压力",
                "app/design/pipe/step_design.py::design_pressure",
                "表压/绝压换算与 PN 保护",
            ),
            ("设计温度", "step_design.py::design_temperature", "温度裕量与低温对称取值"),
            ("普通许用应力", "step_design.py::allowable_stress", "查表 B.1 并线性插值"),
            ("R1E 许用应力", "step_design.py::hp_ss_allowable_stress", "S30408 内置保守字典"),
            ("R1E 所需壁厚", "step_design.py::hp_ss_required_wall", "含负偏差除入的专用公式"),
            (
                "壁厚圆整",
                "app/design/pipe/step_wall.py::design_wall_for_od",
                "按外径、负偏差与最小壁厚圆整",
            ),
            ("外压筛查", "step_wall.py::external_pressure_min_wall", "真空长圆筒弹性屈曲"),
            ("工艺最小壁厚", "app/design/pipe/data.py::minimum_wall", "按 DN 分档的名义下限"),
            (
                "负偏差",
                "data.py::negative_deviation",
                "按 grade 查表 8/表 9 与 S/D 档取 C1；默认 S2-B 才是 0.125·S",
            ),
            ("压力等级号", "app/design/pipe/step_grade.py::grade_code", "PN 代号、材质代号与后缀"),
            (
                "人工覆盖",
                "app/design/pipe/overrides.py::apply_pipe_run_overrides",
                "合并 pipe_run 的人工规格",
            ),
            ("DN 文本", "overrides.py::pipe_dn_text", "标准 DN 或 Φ 外径文本"),
            ("管路编号", "app/export/pipe_schedule.py::_pipe_tag", "介质-流股-DN-等级号"),
            ("V1–V15 校核", "app/design/pipe/validate.py::validate", "按生效行逐条判定"),
        ],
        (3.4, 7.2, 5.4),
        caption="附表 A-1　管路选型核心函数",
        font_pt=8,
    )


def build_pump(doc):
    kit.activate(PUMP_KIND)
    _page_setup(doc)
    _cover(
        doc,
        PUMP_KIND,
        "泵计算说明书",
        "泵专业 · 计算册",
        [
            ("适用功能", "泵参数 · 设计扬程 · 两侧管段 · 选型台账"),
            (
                "计算依据",
                "达西公式 · Colebrook / 顾毓珍 · GB/T 20801（管径与壁厚侧） · "
                "API 610 / GB/T 3215（电机分段裕量参考）",
            ),
            ("数据来源", "Aspen 工况快照 + 管段输入 + 人工选型台账"),
            ("复算方式", "按本文公式、常数与顺序逐项复算"),
        ],
        note="本说明书解释算法口径，不替代厂家型谱与工程设计审核",
    )
    _toc(doc, PUMP_KIND)
    _body(doc, PUMP_KIND, f"v{__version__}　泵计算说明书")

    h(doc, "1　适用范围与计算链", 1)
    p(
        doc,
        "本说明书覆盖「设备 → 泵」页签背后的全部计算：进口基准 NPSHₐ（未计位差与管损）、"
        "装置有效 NPSHₐ（修正值）、设计扬程与选型扬程、吸入与排出两侧管路的沿程/局部阻力、"
        "建议电机档位，以及选型台账的三态判定。",
    )
    p(
        doc,
        "页面、Excel 泵表、AI 分析包与 Word 报告四条出口调用的都是同一组函数"
        "（pump_line.compute、pump.npsh_available_m、pump_selection.compute_view），"
        "所以口径只有一处；本说明书按这条链路逐段展开。",
    )
    table(
        doc,
        ["环节", "输入", "输出"],
        [
            ("泵块读数", "流量、压升扬程 HEAD_CAL、效率、轴功率、密度", "展示值 + Aspen 基准功率"),
            ("进口基准 NPSHₐ", "进口压力、饱和蒸气压 p_v、密度", "NPSHₐ（未计位差与管损）"),
            ("两侧阻力", "管段输入（L/Σζ/ΔZ/λ 口径）+ 管道页参数", "u/Re/λ/Σhf（两侧各一套）"),
            ("装置有效 NPSHₐ", "进口基准值 − 吸入段位压头 − 吸入段 Σhf", "NPSHₐ 修正值"),
            ("设计扬程", "压升项 + 两侧 ΔZ + 两侧 Σhf", "H 与 H_sel = ceil(1.1H)"),
            (
                "照会轴功率 P_估",
                "2.3 节密度；Q 按口径取泵块 VFLOW 或吸入流股流量；"
                "H 按口径取压升扬程或设计扬程；效率",
                "check_kw / design_kw（仅单相；design_kw 参与电机选档）",
            ),
            (
                "建议电机",
                "前置守卫：两侧管段齐全且 H ≤ 0 直接不选；否则设计扬程口径 design_kw 优先，"
                "再回退 Aspen 轴功率",
                "motor.kw / basis_kw / margin / power_source；页面、台账与导出同值",
            ),
            ("选型台账", "NPSHᵣ + 余量 与 修正 NPSHₐ 比较", "满足 / 不满足 / 无法判定"),
        ],
        (3.4, 8.4, 4.2),
        caption="表 1-1　计算链总览",
    )

    h(doc, "2　输入与数据来源", 1)
    h(doc, "2.1　泵块读数", 2)
    table(
        doc,
        ["字段", "Aspen 节点路径", "单位与换算", "缺值行为"],
        [
            (
                "流量 Q",
                "\\Data\\Blocks\\<泵块名>\\Output\\VFLOW",
                "m³/h；l/min×0.06、l/s×3.6、gal/min（美制 US gal）×0.2271247、ft³/h×0.02831685",
                "不影响两侧管段流速与阻力（两侧各自取流股体积流量）；仅影响泵表展示"
                "与第 7 章照会轴功率 P_估（仅单相；含气/两相进口为空；"
                "design_kw 固定取吸入流股流量，不受 VFLOW 缺失影响）；缺失时 check_kw 为空",
            ),
            (
                "压升扬程 H_pr",
                "\\Data\\Blocks\\<泵块名>\\Output\\HEAD_CAL",
                "m；ft·lbf/lbm×0.3048、J/kg÷9.80665",
                "设计扬程与选型扬程均不给；check_kw 与 design_kw 均为空，"
                "电机选档回退 Aspen 轴功率口径",
            ),
            (
                "效率 η",
                "\\Data\\Blocks\\<泵块名>\\Output\\CEFF",
                "0–1 原样，显示 ×100 %",
                "显示「—」；参与设计扬程口径的 design_kw（从而参与电机选档）；"
                "缺失或 ≤0 时 check_kw 与 design_kw 均返回空，电机回退 Aspen 口径",
            ),
            (
                "摩尔密度 RHO_LIQ",
                "\\Data\\Blocks\\<泵块名>\\Output\\RHO_LIQ",
                "kmol/m³；节点单位串原样时按 `molar_density` 表换算"
                "（kmol/cum 1.0、kmol/l 1000、mol/l 1.0、mol/cc 1000、lbmol/ft³ 16.018463）",
                "缺失、非数值或单位串不认识时留空；它只是泵块密度回退项的因子，"
                "单相优先用进口流股 `m/V`，见 2.3 节",
            ),
            (
                "分子量 MW_LIQ",
                "\\Data\\Blocks\\<泵块名>\\Output\\MW_LIQ",
                "kg/kmol；按 `number` 原样读取，不做换算",
                "缺失或非数值时留空；与 `RHO_LIQ` 任一为空，"
                "泵块质量密度 `RHO_LIQ × MW_LIQ` 即为空，不回退猜测",
            ),
            (
                "轴功率 P",
                "\\Data\\Blocks\\<泵块名>\\Output\\BRAKE_POWER；无值回退 ELEC_POWER",
                "kW；hp×0.7457（美制机械马力）",
                "Aspen 口径的基准功率：设计口径不可用时才使用；BRAKE_POWER 缺值再回退 "
                "ELEC_POWER×0.95；二者都缺且设计口径也不可用时不选电机",
            ),
            (
                "压升 Δp",
                "\\Data\\Blocks\\<泵块名>\\Output\\IN_PRES",
                "bar（绝压）；按 `pressure` 表换算（bar/atm/Pa/kPa/MPa/psi 等，"
                "系数见本表下方注）；表压单位串 psig/barg/kPag/MPag 留空",
                "只入库展示，不参与计算",
            ),
        ],
        (3.2, 4.6, 6.0, 2.2),
        caption="表 2-1　泵块读数与用途",
        font_pt=8,
    )
    note(
        doc,
        "**读取快照的单位换算边界**：`read_pump_fields` 与 `read_streams` 都按每个 Aspen 节点"
        "自带的 UnitString 逐字段换算，目标单位固定为 ℃、bar、kg/h、m³/h、m、kmol/m³；"
        "ENG 单位集下的 `lb/hr`、`ft³/hr`、`gal/min`、`ft-lbf/lbm` 等都在换算表中，"
        "按显式系数换算，不按单位集名整体缩放，也不假设模型一定是 METCBAR。"
        "`MW_LIQ` 走 `number` 原样（kg/kmol）；值为空、单位串缺失或不认识时一律留空，"
        "不猜默认单位。"
        "`pressure` 表只收**绝压**单位串（bar、atm、Pa、kPa、MPa、psi/psia、mmHg/torr、kgf/cm²，"
        "系数依次为 1、1.01325、1e−5、1e−2、10、0.0689475729、0.001333224、0.980665）；"
        "`psig`、`barg`、`kPag`、`MPag` 这类**表压**单位串不在表内，"
        "`units.convert` 查不到 key 就返回空、该字段留空，"
        "**不按「表压 + 1 atm」自行折成绝压**，也不按单位集名整体缩放。"
        "表 2-1 的压升 Δp 与表 2-2 的 p_in / p_out / p_v 都走这条边界。",
    )
    note(
        doc,
        "HEAD_CAL 只是压升项 Δp/(ρg)，不含位压头与管路阻力，界面称「压升扬程」而不是「扬程」；"
        "IN_PRES 的全称是 **Increase in Pressure（压升）**，实测也是压升而不是进口压力，"
        "代码不拿它算 NPSH，只在设备表里展示。"
        "若泵块密度与流股 m/V 偏差较大，不要只凭 HEAD_CAL 与 Aspen 压差下结论；"
        "按 2.3 节用同一密度交叉核对 HEAD_CAL ≈ (p_out − p_in)×10⁵/(ρg)。",
    )

    h(doc, "2.2　流股读数", 2)
    table(
        doc,
        ["字段", "来源", "单位", "用途与缺值行为"],
        [
            (
                "p_in",
                "`\\Data\\Streams\\<流股>\\Output\\RES_PRES`（进口流股）",
                "bar（绝压）；bar 1、atm 1.01325、Pa 1e−5、kPa 1e−2、MPa 10、"
                "psi/psia 0.0689475729、mmHg/torr 0.001333224、kgf/cm² 0.980665",
                "NPSHₐ；缺失、非有限值（NaN/Inf）或 ≤0（绝压非法）均拦截判空，三态判「无法判定」",
            ),
            (
                "p_out",
                "`\\Data\\Streams\\<流股>\\Output\\RES_PRES`（出口流股）",
                "bar（绝压）；换算系数同 p_in",
                "仅展示与压差校验；不参与 NPSHₐ 与 `check_kw`",
            ),
            (
                "mass_flow",
                "`\\Data\\Streams\\<流股>\\Output\\RES_MASSFLOW`",
                "kg/h；kg/s×3600、g/s×3.6、lb/hr×0.45359237、lb/s×1632.932532、"
                "t/hr（公吨）×1000、tonne/hr（公吨）×1000、"
                "ton/hr（短吨，2000 lb）×907.18474",
                "与 `vol_flow` 相除得流股密度 `m/V`；本身不直接充当 Q，也不直接进 `design_kw`。"
                "缺失、非有限或 ≤0 时 `m/V` 密度为空，泵侧回退 `RHO_LIQ × MW_LIQ`，流量不回退",
            ),
            (
                "vol_flow",
                "`\\Data\\Streams\\<流股>\\Output\\RES_VOLFLOW`",
                "m³/h；m3/s×3600、l/min×0.06、l/s×3.6、gal/min×0.2271247、"
                "ft³/h 与 cuft/hr×0.02831685",
                "与 `mass_flow` 得密度 `m/V`；还用于两侧管段流速与阻力、`design_kw` 的 Q，"
                "以及 `VFLOW` 一致性校核中的 `q_in`。"
                "管道页构建结果行已有 `vol_flow_m3h` 时优先取该行（与管道页同源）；"
                "这里的「行」指管道页 `pipe.build()` 输出的选型结果行，"
                "不是只存输入、不存流量的 `pipe_run`（管段记录）。"
                "缺失、非有限或 ≤0 时该侧流量为空、`m/V` 不可用；"
                "但 `check_kw` 只取泵块 VFLOW 与 2.3 节密度（单相优先流股 m/V，"
                "缺失时才回退泵块 `RHO_LIQ × MW_LIQ`），只要 VFLOW/HEAD_CAL/CEFF "
                "与 2.3 节密度有效就仍照算；`design_kw` 的 Q 无管段行流量替代时为空；"
                "`VFLOW` 比较跳过",
            ),
            (
                "p_v",
                "`\\Data\\Streams\\<流股>\\Output\\STRM_UPP\\PBUB\\MIXED\\TOTAL`"
                "（V11 实测 `MIXED\\LIQUID`；候选另含 `STRM_UPP\\PL\\MIXED\\TOTAL` 与去"
                " `STRM_UPP` 前缀的旧路径）",
                "bar（绝压）；换算同 p_in（`pressure` 表，表压单位串留空）",
                "只认有限且 >0 的值；0、负数、NaN 一律视为缺失",
            ),
            (
                "μ",
                "`\\Data\\Streams\\<流股>\\Output\\STRM_UPP\\MUMX\\MIXED\\TOTAL`"
                "（无 TOTAL 时按 MIXED → LIQUID 下钻；旧布局候选 `STRM_UPP\\VISC\\…`）",
                "cP；cP/mPa·s×1、Pa·s 或 kg/(m·s)×1000、poise×100、lb/(ft·hr)×0.4133789",
                "Re 与 λ；管段手填优先于泵块读数；未填时两侧共用进口 MUMX，"
                "排出侧不读出口流股；缺项可逐管段手填",
            ),
            (
                "汽化率",
                "`\\Data\\Streams\\<流股>\\Output\\RES_VFRAC`",
                "—（fraction 原样读取，不换算）",
                ">1e-4 时四出口都不计算 NPSHₐ 与 check_kw / design_kw；"
                "缺失时按液相处理：NPSHₐ、check_kw 与 design_kw 均照算，"
                "但 compute warnings、台账三态理由、页面与导出「提示」列均写"
                "「缺汽化率，暂按液相估算，请复核」；这是估算值，不是已确认单相",
            ),
        ],
        (2.5, 5.3, 1.8, 6.0),
        caption="表 2-2　流股读数",
    )
    p(
        doc,
        "流股字段与泵块字段共用 2.1 节的读取层换算边界："
        "按 Aspen 节点 UnitString 换算到固定目标单位，"
        "ENG 单位串按显式系数处理；温度、压力、质量流量、体积流量任一为空或单位不认识都留空，"
        "不用默认单位补算。",
    )
    p(
        doc,
        "p_v 的候选路径是 `\\Data\\Streams\\<流股>\\Output\\STRM_UPP\\PBUB\\MIXED\\TOTAL`"
        "（V11 实测为 `MIXED\\LIQUID`），"
        "读取时在每一层级按 TOTAL → MIXED → LIQUID 的优先级匹配子节点，并逐层下钻，"
        "直至到达无子节点的叶子（节点名大小写不敏感，不假设固定层数）。"
        "**目录节点与叶子的判定是「有没有子节点」，不是「值是不是 1」**："
        "有子节点即目录，按相名继续下钻；无子节点即叶子，直接取值并做单位换算。"
        "因此真实 p_v = 1 bar 的叶子会被正常保留，不会因为数值等于 1 被过滤；"
        "PBUB / PL 这类目录虽然也读作 1 bar，但因为有子节点，仍按目录处理。"
        "`PL` 的分压在组分叶子，遇到组分名（不是相名）无法下钻时返回空，"
        "不会把目录标志 1 当成 p_v。μ 的 MUMX/VISC 下钻共用同一规则。",
    )
    p(
        doc,
        "`MUMX` 分支优先 `\\Output\\STRM_UPP\\MUMX\\MIXED\\TOTAL`（混合总粘度）；"
        "若模型没有 TOTAL、"
        "只有 `MIXED\\LIQUID`，实际取纯液相粘度。两相流股也走同一路径，"
        "不是无条件取混合粘度；需要改用其他口径时，可在管段记录里手填 μ 覆盖。"
        "旧版模型把 VISC 直接挂在 `\\Output\\STRM_UPP\\` 下，"
        "`STRM_UPP\\VISC\\…` 系列候选仍保留，匹配顺序排在 MUMX 之后。",
    )

    h(doc, "2.3　密度：两条路径", 2)
    mono(
        doc,
        [
            "若 进口流股存在 且 (进口汽化率 为空 或 ≤ 1e-4)：",
            "    ρ = 进口流股 质量流量 / 体积流量          （单相，实测密度）",
            "否则：",
            "    ρ = RHO_LIQ(kmol/m³) × MW_LIQ(kg/kmol)     （泵块值，质量密度）",
        ],
    )
    p(
        doc,
        "两条路径的来源不同：同一台泵的泵块密度与流股 m/V 可能显著不同（实测样本差 24%），"
        "同模型其余泵一致到 0.02%，因此单相优先用流股实测。该密度用于装置 NPSHₐ，"
        "也用于第 7 章照会轴功率 P_估；"
        "进口流股 `vol_flow` 缺失时，只要泵块 `RHO_LIQ × MW_LIQ` 回退可用，"
        "`check_kw` 仍有值；`design_kw` 的 Q 取吸入侧流量，无管段行流量替代时才为空。"
        "两侧管段各自用自己的流股密度（m/V），可以互不相同。",
    )
    p(
        doc,
        "**单相密度回退规则**：进口流股存在且 `vap_frac` 为空或 ≤1e-4 时，"
        "只有 `mass_flow` 与 `vol_flow` 均非空、有限且严格 >0 才走 m/V；"
        "任一缺失、非有限或 ≤0 时自动回退到泵块密度 `RHO_LIQ × MW_LIQ`；"
        "仅当泵块密度也缺失时返回空。负流量不参与相除，避免算出负密度。"
        "该回退只用于 NPSHₐ 与 P_估；两侧管段水力计算不使用泵块密度，见下一段。",
    )
    p(
        doc,
        "**两侧管段各自取本侧密度**：吸入与排出分别用本侧流股的 "
        "`mass/vol`，不采用泵块密度回退；`mass` 或 `vol` 缺失、非有限或 ≤0 时直接保持为空。"
        "自动 λ 口径下该侧进入缺项「密度」；"
        "手填 λ 口径下密度不阻断 Σhf，只让 Re 留空。",
    )
    p(
        doc,
        "**密度偏差的交叉核对**：若泵块密度与流股 m/V 偏差较大，先按"
        "`HEAD_CAL ≈ (p_out − p_in)×10⁵/(ρ·g)` 用同一 ρ 复算，"
        "确认不一致来自密度口径还是压升读数；不要直接用两套密度互相证明。"
        "这一步只做核对，不改 HEAD_CAL 主值，也不把偏差静默平均掉。",
    )

    h(doc, "2.4　两侧管段输入", 2)
    p(
        doc,
        "管段记录（pipe_run）按「模型 + 流股」唯一，泵面板与管道页读写同一行；"
        "只存输入，不存任何计算值。",
    )
    table(
        doc,
        ["字段", "符号", "单位", "范围", "说明"],
        [
            ("l_m", "L", "m", "0–100000", "缺失则该侧 Σhf 为空"),
            ("zeta_sum", "Σζ", "—", "0–10000", "缺失时该侧 Σhf 为空、状态判「未填」"),
            ("fittings_json", "管件数量", "件", "每项 0–10000", "与 Σζ 一起存，便于回显"),
            (
                "dz_m",
                "ΔZ",
                "m",
                "−10000–10000",
                "沿流向的标高差（终点 − 起点）；顺流抬升为正、下降为负（泵吸上填正值，倒灌填负值）",
            ),
            ("mu_cp", "μ", "cP", "0.0001–1e6", "手填优先于 Aspen 读数"),
            ("lambda_method", "λ 口径", "—", "colebrook / gu / manual", "手填时另给 λ 值"),
            (
                "lambda_manual",
                "手填 λ",
                "—",
                "0.0001–1.0",
                "仅在 `lambda_method=manual` 时使用；空值/≤0 在计算层按未填，"
                "保存层对 0、负值与非有限值直接拒绝",
            ),
            (
                "dn_override",
                "人工 DN",
                "—",
                "须在 DN→外径表内",
                "人工 DN：与自定义 `od/wall` 在界面/接口上二选一；经公共合并层同步到管道页、"
                "导出表与 V2/V3；自动原值保留在 auto_dn；须在 DN→外径表内",
            ),
            (
                "od/wall 覆盖",
                "自定义规格",
                "mm",
                "od 1–5000，wall 0.1–100",
                "界面/接口与人工 DN 二选一，须同时填；仅当历史库或直接写库使两者同时存在时，"
                "公共合并层以自定义 `od/wall` 优先于 `dn_override`。"
                "经公共合并层同步到管道页、管道导出表与 V2/V3，C1=0 并按新外径重算 "
                "`t_required_mm`；两路径的负偏差语义见《管路选型计算说明书》5.4.2 节。"
                "保存层另校验 `2×wall < od`，不满足返回 400（无标准 DN 显示 Φ{od}）",
            ),
        ],
        (3.4, 2.2, 1.6, 4.2, 4.6),
        caption="表 2-3　管段记录字段",
        font_pt=8,
    )
    p(
        doc,
        "**两侧 ΔZ 必须共用一个泵基准面**：吸入段 ΔZ 的终点与排出段 ΔZ 的起点都取同一泵基准"
        "（工程惯例为泵轴中心线标高），而不是各取各的设备接管标高。"
        "只有共用一个基准，第 5 章 H 式里的 `ΔZ_吸入 + ΔZ_排出` 才等于"
        "「下游液面 − 上游液面」的总位差；两侧各按自己的接管标高取值时，"
        "两段位差会在泵体处重复或漏算，H 与选型扬程随之后偏，且从界面上看不出原因。"
        "记录里的 ΔZ 仍是沿流向的「终点 − 起点」，代码不做基准面校验（也没有模型几何可校验），"
        "需在填表时自行统一；改基准面属于改输入，不是改算法。",
    )

    h(doc, "2.5　选型台账输入", 2)
    p(
        doc,
        "台账只存人工决定的型号、名称、台数、材质、配套电机、NPSHᵣ、余量与备注，"
        "不存流量、扬程、NPSHₐ 或三态——这些全部按当前工况实时算。",
    )
    table(
        doc,
        ["字段", "含义", "校验"],
        [
            ("model_no / pump_name / material / note", "型号 / 名称 / 材质 / 备注", "文字 ≤200 字"),
            ("qty", "台数", "整数 1–999"),
            ("motor_kw", "配套电机（人工决定值）", "数字 0–100000"),
            ("npshr_m", "必需汽蚀余量 NPSHᵣ", "数字 0–100000"),
            ("margin_m", "汽蚀余量（余量）", "数字 0–100000"),
        ],
        (6.4, 5.6, 4.0),
        caption="表 2-4　台账字段",
    )

    h(doc, "2.6　管道页参数", 2)
    p(
        doc,
        "两侧管径与阻力按该模型保存的管道页参数实时计算；下列为内置默认值，"
        "实际以管道页已保存的参数为准：流速 1.5 / 15 / 8 / 0.8 m/s、"
        "设计压力倍率 1.15、附加 0.15 MPa、下限 1.0 MPa、最大 DN1000、默认材质 S30408。"
        "泵面板与管道页读同一套参数，否则同一根管子会算出两个 DN。",
    )

    h(doc, "3　进口基准 NPSHₐ（未计位差与管损）", 1)
    note(
        doc,
        "**压力边界的假定**：本册把 Aspen 泵进口流股压力当作「上游容器操作压力」，"
        "泵出口流股压力当作「下游容器操作压力」，两者都不含管路位差与阻力。"
        "若 Aspen 模型已在上游/下游计入管损（例如上游设备至泵进口之间，"
        "或泵出口至下游设备之间已建有管路/阀门压降模块），"
        "就不能再在第 2.4 节的管段记录里重复填 ΔZ 与 Σhf，否则扬程与 NPSHₐ 会被扣两遍。"
        "判别办法：确认模型里泵进出口流股的压力是否已按「设备管口法兰」取值，"
        "或设计资料是否已扣除泵出入口管道阻力。",
    )
    formula(
        doc,
        "NPSHₐ(进口基准) = (p_进口 − p_v) × 10⁵ / (ρ · g)　，　g = 9.80665 m/s²",
        "p 取 bar 绝压，ρ 取 kg/m³，结果 m；尚未扣吸入段位差与管损",
    )
    p(
        doc,
        "**速度头项的处理**：泵入口 NPSHₐ 按法兰静压定义为 "
        "`NPSHₐ = p_s/(ρg) + u_s²/(2g) − p_v/(ρg)`。"
        "由上游液面（速度 u₁ ≈ 0）到泵入口列伯努利方程，有 "
        "`p_s/(ρg) = p₁/(ρg) − ΔZ_吸入 − Σhf_吸入 − u_s²/(2g)`；"
        "两式相加，`u_s²/(2g)` 恰好抵消，得到 "
        "`NPSHₐ = p₁/(ρg) − ΔZ_吸入 − Σhf_吸入 − p_v/(ρg)`。"
        "因此以液面压力展开时，吸入管动压头已并入 NPSHₐ 定义项本身并在推导中抵消，"
        "公式里不再单列速度头项：进口基准式只算 `(p_进口 − p_v)/(ρg)`，"
        "装置有效式再扣吸入段 ΔZ 与 Σhf。"
        "若上游边界是来料管段，被忽略的是该边界速度头，按泵入口推荐流速 0.8 m/s 计仅约 0.03 m。"
        "**非法值拦截**：p_in ≤ 0、p_v ≤ 0、ρ ≤ 0，或任一为非有限值（NaN/Inf），均拦截判空，"
        "返回空而不是 0；p_in < p_v 时给出负值，表示会汽蚀，不吞成 0。|NPSHₐ|<0.005 m 时归零，"
        "避免表里出现 −0.00。",
    )
    p(
        doc,
        "工具不使用 Aspen 自带的 NPSH-AVAIL 字段：实测一份模型逐值吻合、另一份量级到 6–1030 m，"
        "因此统一以自算值为准。",
    )

    h(doc, "4　装置有效 NPSHₐ（修正值）", 1)
    formula(
        doc,
        "NPSHₐ(装置有效) = NPSHₐ(进口基准) − ΔZ_吸入 − Σhf_吸入",
        "只扣吸入侧；ΔZ 为沿流向标高差（终点 − 起点），泵在吸入起点之下时为负，修正值增大",
    )
    p(
        doc,
        "修正项只有吸入段自己的两项都非空才算；任一缺失，修正值判空并写明"
        "缺「吸入段阻力 / 位压头」，"
        "三态随之判「无法判定」。进口基准值始终与修正值并列显示，但选型判据只用修正值（装置有效值）。",
    )
    p(
        doc,
        "**汽化率缺失的风险提示**：汽化率缺失时不阻断计算，NPSHₐ 与设计轴功率仍按"
        "液相暂估；但整泵计算的 `warnings`、选型台账三态理由、页面提示，"
        "以及 Excel 整本/分类与 AI 包泵表的「提示」列都会写"
        "「缺汽化率，暂按液相估算，请复核」。这是估算状态，不是已确认单相。"
        "若上游实际是漏读汽化率的两相流股，液相 NPSHₐ 与设计轴功率会偏乐观，"
        "可能误判为「满足」；必须结合管路页的汽化率/两相告警与抽检复核，不能只看泵页结论。",
    )
    p(
        doc,
        "**含气/两相进口（汽化率 >1e-4）** 不计算 NPSHₐ，与《管路选型计算说明书》3.1 节同一常量 "
        "`TWO_PHASE_VAP_EPS = 1×10⁻⁴`；泵侧别名 TWO_PHASE_VAP_LIMIT 直接指向该常量。"
        "这类进口已含气相，单相汽蚀余量公式的前提（净正吸入压头全部由液相提供）不成立，"
        "不是换一个密度值就能修正。"
        "门禁在核心函数 npsh_available_m（参数 vap_frac），"
        "页面、Excel、AI 包与 Word 报告四条出口同值为空，"
        "引用导出结果时不会再拿到失效数值。",
    )
    p(
        doc,
        "**轴功率照会同样受这一门禁**：汽化率 >1e-4 时，压升扬程口径与设计扬程口径的 "
        "`shaft_kw_estimate.check_kw`、`design_kw` 都返回空；完整原因与边界见第 7 章。",
    )

    h(doc, "5　设计扬程与选型扬程", 1)
    formula(
        doc,
        "H = H_pr + (ΔZ_吸入 + ΔZ_排出) + (Σhf_吸入 + Σhf_排出)",
        "H_pr 为 HEAD_CAL（压升扬程），可为负；ΔZ 与 Σhf 来自两侧管段",
    )
    p(
        doc,
        "`HEAD_CAL` 是按泵块密度反算的压升项；若泵块密度与流股 `m/V` 偏差较大，"
        "应按 2.3 节用同一密度交叉核对，不能只凭 HEAD_CAL 与 Aspen 压差下结论。",
    )
    p(
        doc,
        "**速度头假设**：本式默认上下游液面速度头差约等于 0，因此只叠加压升项、位差与阻力，"
        "不含 `(u_out² − u_in²)/(2g)`。若两端不是大液面而是管道接口，或两端流速差异显著，"
        "需按实际管口流速另加速度头项复核；HEAD_CAL 本身只是压升，不含速度头。",
    )
    formula(doc, "H_选型 = ceil(1.1 × H)", "先乘 1.1，再向上取整到 1 m")
    p(
        doc,
        "实现按十进制高精度（`Decimal`）计算后再向上取整，避免二进制浮点误差导致 "
        "`H = 50 / 90 / 100 m` 等边界值多进 1 m。",
    )
    p(
        doc,
        "缺任一侧、该侧某个 ΔZ / Σhf 为空，或 `HEAD_CAL` 缺失时，H 与 H_选型都不给，只用"
        "「已计位压头」「已计阻力」给出中间量，并注明两侧未齐或压升扬程缺失。"
        "**页面顶部余量倍率（1 / 1.05 / 1.10 / 1.15 / 自定义）仅作用于页面前端展示层的"
        "流量、压升扬程与「复制选型参数」行**；"
        "它不改写四出口共享的底层计算结果，也不进入 Excel / AI 包 / Word 报告或数据库，"
        "不作为 API 参数；"
        "不乘轴功率、建议电机、NPSHₐ、效率、p_v、粘度，"
        "更不作用于设计扬程 H 与选型扬程 H_sel——后者已内置 1.1 裕量。",
    )
    p(
        doc,
        "H ≤ 0 属反向/自流工况：只给设计扬程并标「反向/自流」，不给选型扬程，"
        "不拿负数取整冒充结论。",
    )
    p(
        doc,
        "**两相管段仍按单相公式计入 H**：两相吸入/排出管段的 Σhf 按单相达西公式"
        "（混合密度 + 液相粘度）计算并计入设计扬程 H，无两相拦截或告警；"
        "模型边界与压降低估风险见 6.3 节与表 11-1。",
    )

    h(doc, "6　沿程与局部阻力", 1)
    h(doc, "6.1　内径与达西公式", 2)
    formula(
        doc,
        "d = D_o − 2·S　[mm]　；　d_m = d / 1000　[m]　；　u = (Q/3600) / (π·d_m²/4)",
        "全章统一：D_o、S、d 用 mm；d_m 为 m，只用于流速、Re 与 Σhf；u 单位 m/s",
    )
    formula(
        doc,
        "Σhf = ( λ·L/d_m + Σζ ) · u² / (2g)　，　g = 9.80665 m/s²",
        "λ 为达西摩擦因数；L 取 m、d_m 取 m，Σhf 为 m",
    )
    p(
        doc,
        "保护规则：**L 与 Σζ 任一为空**，该侧 Σhf 都直接给空、状态判「未填」——"
        "只填 L 不填 Σζ 不会得到「仅沿程」的数，因为少算的正是全部局部阻力。"
        "`d ≤ 0`、`L < 0`、`u < 0` 同样返回空；缺项列表分别写「管长 L」「管件系数 Σζ」。"
        "显式填 0（确实没有管件）与留空是两种语义：库里前者存 0、后者存 NULL。",
    )
    p(
        doc,
        "**`u < 0` 这层保护在底层函数里**：`d ≤ 0`、`L < 0`、`u_ms < 0` 三条拦截写在 "
        "`hydraulics.head_loss_m()` 内部（任一命中即返回 `None`），不是上层逐管段另加的判断。"
        "`u = 0` 不在拦截范围——函数本身对 `u = 0` 返回 `0.0`；"
        "但上层 `solve()` 用 `if (u and lam)` 决定是否调用它，"
        "而 `Q ≤ 0` 时 `velocity_ms()` 先返回空，所以界面与导出里的 Σhf 仍是空，"
        "不会出现「有管长、有 Σζ 却显示 0 m」的假结论。"
        "`u < 0` 只是防御性守卫：`velocity_ms()` 只在 `Q > 0` 时给正值，正常数据走不到这条分支。",
    )
    p(
        doc,
        "**零值边界**：`L = 0` 为合法输入，Σhf 只余局部阻力项 `Σζ·u²/(2g)`；若 Σζ 也为 0，"
        "则 Σhf = 0.0。体积流量 `Q = 0` 时流速计算直接返回空（不是 0），"
        "Re 与 Σhf 均为空；自动 λ 与手填 λ 两种口径都判未填，缺项包含「体积流量」。",
    )
    p(
        doc,
        "**管道页只给几何 DN、`wall_mm` 为空时**：泵侧仍保留该侧 `dn` 与 `od_mm`，"
        "但 `wall_mm` 留空，由 `d = D_o − 2S` 得不到内径，`id_mm` 为空；"
        "该侧状态判 `unlinkable`（界面「无法关联」），原因固定写「没有可用的管内径」，"
        "并在进入缺项列表前提前返回。缺压力、缺温度、缺许用应力或厚壁越界这几种根因"
        "不会在这里逐条透传，需回管道页看该行的缺项与备注；对应退化口径见"
        "《管路选型计算说明书》3.6 节与表 7-2。",
    )

    h(doc, "6.2　雷诺数", 2)
    formula(
        doc,
        "Re = d_m·u·ρ / (μ/1000)",
        "d_m 为 m（与 6.1 同一符号）；μ 由 cP 换成 Pa·s；任一项为空或 ≤0 则返回空",
    )
    p(
        doc,
        "两侧 `ρ` 各自取本侧流股的 `mass/vol`，互不共用；`mass` 或 `vol` 缺失/非正时直接保持"
        "为空，不回退泵块密度。自动 λ 口径把「密度」列入缺项，手填 λ 口径不阻断 Σhf。"
        "完整规则见 2.3 节。",
    )

    h(doc, "6.3　摩擦因数 λ 的三种口径", 2)
    p(
        doc,
        "自动口径先判层流：`Re < 2300` 时取 `λ = 64/Re`，不调用 Colebrook/顾毓珍；"
        "`2300 ≤ Re < 3000` 过渡区回退 Colebrook，属工程近似。手填 λ 不套用该分支。",
    )
    table(
        doc,
        ["口径", "公式", "适用条件与实现"],
        [
            (
                "自动口径（层流分支）",
                "λ = 64/Re",
                "Re < 2300 时优先取层流式，不调用 Colebrook/顾毓珍；"
                "2300–3000 过渡区回退 Colebrook，属工程近似；手填 λ 不覆盖",
            ),
            (
                "Colebrook（默认）",
                "1/√λ = −2·log₁₀( ε/(3.7d) + 2.51/(Re·√λ) )",
                "隐式迭代：初值 0.02，最多 60 次，容差 1e-10；不收敛返回最后有限值。"
                "相对粗糙度 ε/(3.7d) 无量纲：ε 与 d 同取 mm",
            ),
            (
                "顾毓珍",
                "λ = 0.01227 + 0.7543 / Re^0.38",
                "清洁/一般工业钢管湍流经验式（隐含一般钢管粗糙度，非理想光滑管公式），"
                "3e3 ≤ Re ≤ 3e6；2300–3000 过渡区与 Re > 3e6 两端回退 Colebrook",
            ),
            (
                "手填",
                "直接取输入的 λ",
                "优先级最高：只需 u（由 Q、d 算得）、L、Σζ 与手填 λ；"
                "密度/粘度仅用于 Re 复核，缺了也不阻断 Σhf；"
                "不被 Re < 2300 的 64/Re 覆盖",
            ),
        ],
        (3.0, 6.6, 6.4),
        caption="表 6-1　三种 λ 口径",
        font_pt=8,
    )
    p(
        doc,
        "**手填 λ 优先级最高**：manual 分支只要求 u（由 Q、d 算得）、L、Σζ 与手填 λ；"
        "密度、粘度不列为阻塞缺项，缺它们时 Σhf 仍照算，Re 留空。"
        "Re < 2300 的 64/Re 只对自动口径生效，**不会覆盖手填 λ**。"
        "`method_note` 写「手填 λ:缺密度或粘度,Re 未计算」；有 Re 时写"
        "「手填 λ:λ 由人工给定,Re 仅作复核」。"
        "仅自动口径（Colebrook / 顾毓珍）执行「层流优先于口径」与「缺粘度判空」。"
        "手填值字段为 `lambda_manual`：保存层只接受 `0.0001 ≤ λ ≤ 1.0` 的有限数；"
        "`λ ≤ 0`、空值、NaN/Inf 在计算层一律按「手填 λ」缺项处理，不会当作 0 代入。"
        "不同手册或项目可能采用不同 λ 口径。页面管段提示（tooltip）会标出本次所用口径与备注；"
        "当前 Excel 泵表与 AI 分析包泵表未单列 λ 口径字段。",
    )
    p(
        doc,
        "**λ/Re 的粘度来源**：按 6.6 节，管段手填 `mu_cp` 优先；未手填时两侧共用泵进口流股的 "
        "Aspen `MUMX` 粘度，排出侧不会改读出口流股的 `MUMX`。因此同一台泵两侧的默认粘度相同，"
        "只有逐管段手填才会不同。",
    )
    p(
        doc,
        "**两相管段没有专用阻力模型**：单侧管段计算不检查 `vap_frac`，"
        "也没有两相拦截或告警；两相吸入/排出管段仍按单相达西公式计算 `Σhf`——"
        "混合密度取 `m/Q`，粘度按 MUMX 下钻结果：有 `TOTAL` 取混合总粘度，"
        "无 `TOTAL` 且仅有 `LIQUID` 时取纯液相粘度（或采用管段手填值）。"
        "单相模型通常会显著低估两相管路压降，"
        "算出的设计扬程 H 仅作参考，必须另做两相专项核算（见表 11-1）。",
    )

    h(doc, "6.4　绝对粗糙度", 2)
    p(
        doc,
        "默认 ε = 0.05 mm（手册常见取值 0.03–0.1 之间），只在 Colebrook 公式里以 ε/d 进入，"
        "**ε 与 d 同取 mm**（比值无量纲，不随单位制变化）。"
        "当前版本没有把粗糙度写进管段记录，实际计算恒用默认值。",
    )

    h(doc, "6.5　局部阻力系数 Σζ", 2)
    table(
        doc,
        ["管件", "ζ", "管件", "ζ"],
        [
            ("90° 弯头", "0.75", "止回阀（旋启式）", "2.0"),
            ("闸阀（全开）", "0.17", "渐缩管（标准大小头）", "0.04"),
            ("截止阀（全开）", "9.5", "—", "—"),
        ],
        (4.2, 3.0, 4.2, 3.0),
        caption="表 6-2　常用管件 ζ（设计手册常用值，非标准原文）",
        center_cols=(1, 3),
    )
    p(
        doc,
        "Σζ = Σ(数量 × ζ)，在前端求和后写入 zeta_sum，仍可手改。"
        "**前端管件勾选合计为 0 时回写 null，不是 0**：这表示「没填」，维持 6.1 节的未填保护，"
        "该侧 Σhf 为空；只有人工显式输入 0 才落库为 0，并按「确实没有管件」计算。"
        "没勾选不预置数量，也不把 0 当默认值。"
        "后端不重算该和，只存 Σζ 与管件明细两个字段，便于回显。"
        "**表内不含容器/设备进出口的局部阻力**（接管、封头开孔、内伸管等），"
        "这类损失需按设备条件另计后手工并入 Σζ。常见工程参考值："
        "容器出口至吸入管 ζ ≈ 0.5，排出管进入容器出口损失 ζ ≈ 1.0，供手工并入参考。"
        "渐扩管、三通、吸入过滤器等常见管件也未列入本快捷表，需按工程手册另计后手工并入 Σζ。",
    )
    p(
        doc,
        "人工修改 `zeta_sum` 后，允许它与 `fittings_json` 中各管件数量 × ζ 的求和不一致；"
        "例如手工叠加了容器进出口局部阻力。后端只存 `zeta_sum` 与 `fittings_json` 两个字段，"
        "不重算该和，回显时也不强制配平。",
    )

    h(doc, "6.6　粘度的来源与手填", 2)
    p(
        doc,
        "每侧粘度优先级：管段记录手填 μ > 泵块进口流股 Aspen 粘度 > 空。"
        "手填时标「手填」，否则标「Aspen」。注意两侧共用同一个 Aspen 粘度（都是进口流股的），"
        "排出侧不会改读排出流股的粘度；需要区分时逐侧手填。",
    )
    p(
        doc,
        "**仅自动口径（Colebrook / 顾毓珍）**执行「层流优先于口径」与「缺粘度判空」："
        "Re < 2300 时改取 λ = 64/Re；粘度缺失时该侧 Re、λ、Σhf 全部为空，"
        "状态为未填，缺项列「粘度」；此时应重读带 MUMX 物性集的模型，或直接手填。"
        "重读工况不会覆盖手填值。"
        "**手填 λ 不属于本节**：其优先级、缺密度/粘度不阻断 Σhf、不被 64/Re 覆盖，"
        "统一见 6.3 节；本节只说明粘度来源与自动口径的缺粘度判空。",
    )

    h(doc, "7　轴功率、效率与建议电机", 1)
    h(doc, "7.1　建议电机与分段裕量", 2)
    p(
        doc,
        "**建议电机的基准功率与裕量分开处理**：先确定用哪一口径的轴功率作基准，"
        "再按基准功率所处的功率段取 API 610 / GB/T 3215 分段裕量。"
        "效率 CEFF 参与设计扬程口径的 `design_kw` 计算，因而参与电机选档；"
        "CEFF 缺失或 ≤0 时 `design_kw` 为空；除反向守卫已命中外，电机回退 Aspen 口径。",
    )
    formula(
        doc,
        "P_need = P_基准 / η_传动 × motor_margin(P_基准)"
        "　；　建议电机 = 档位表中第一个 ≥ P_need 的值",
        "η_传动：直联 1.0，皮带约 0.95；P_基准 为设计轴功率或 Aspen 折回轴功率；"
        "分段裕量按基准功率判断，不按乘完裕量后的需求功率",
    )
    p(
        doc,
        "**三段电机裕量**：基准功率 `< 22 kW` 取 `1.25`；`22–55 kW`（含两端）取 `1.15`；"
        "`> 55 kW` 取 `1.10`。分段点用**基准功率**判断，避免「先乘 1.25 再跨段」的边界跳变。"
        "内部函数显式传 `margin` 时仍按传入值，保留旧项目口径与单测兼容；"
        "正常页面与导出路径不传 `margin`，统一走分段函数。"
        "标准原文（API 610 / GB/T 3215）按电机额定功率分档，"
        "本工具按基准轴功率分档作为工程简化口径，"
        "以避免先乘裕量再跨段的跳变。",
    )
    p(
        doc,
        "档位共 27 档：0.55、0.75、1.1、1.5、2.2、3.0、4.0、5.5、7.5、11、15、18.5、22、30、"
        "37、45、55、75、90、110、132、160、200、250、315、355、400 kW。"
        "超出上限返回空并提示所需功率。",
    )
    h(doc, "7.2　基准功率优先级与回退规则", 2)
    p(
        doc,
        "**基准优先级**（由电机建议判定一处给出，页面、台账、设备表与导出均读取 `totals.motor`）：",
    )
    bullets(
        doc,
        [
            "**前置守卫 · 反向/自流**：两侧管段齐全且设计扬程 H ≤ 0 时，直接不选电机，"
            "`power_source` 写「反向/自流工况」，并说明「设计扬程 … m ≤ 0」；"
            "此时**不回退 Aspen 轴功率**，不拿 Aspen 的正功率把一个反向工况选成电机。",
            "① **设计扬程口径**：前置守卫未命中、两侧管段齐全、进口非两相、"
            "`design_kw > 0` 时，用设计扬程 H（**未乘 1.1** 的 `design_head_m`）、"
            "吸入流股体积流量、2.3 节密度与 CEFF 算出的 `design_kw` 作基准。"
            "H 的 1.1 只用于选型扬程 `H_sel`，不进入电机功率，避免与分段电机裕量重复叠加。",
            "② **回退 Aspen 口径**：管段未齐、缺效率 CEFF、两相进口、缺流量/密度或 "
            "`design_kw` 不可用时，改用 Aspen 轴功率。优先 `BRAKE_POWER`；"
            "缺值时才回退 `ELEC_POWER × 0.95`。`power_source` 会标注"
            "「Aspen压升口径（管段未齐/缺效率/两相进口…）」等具体原因，不静默切换。"
            "若 `BRAKE_POWER` 存在且 ≤ 0，直接在本回退分支判反向并说明，"
            "**不会转用 `ELEC_POWER`**；只有 `BRAKE_POWER` 为空才走电功率折算。"
            "`BRAKE_POWER` 为空时先取 `ELEC_POWER × 0.95`；折算后仍 ≤0 同样不选电机，"
            "`note` 仍写「Aspen 轴功率 … kW ≤ 0」；两者都为空才报「缺 Aspen 轴功率,不选电机」。",
        ],
    )
    p(
        doc,
        "`design_kw` 使用吸入段流股流量，`check_kw` 使用泵块 VFLOW 与压升扬程，"
        "前者参与电机选档，后者仍只作 Aspen 压升口径的照会。"
        "偏差判据为 `abs(VFLOW − q_in) > 0.01 × q_in`（前提是两者均非空且 `q_in > 0`；"
        "分母固定取吸入流股流量 `q_in`，严格大于 1%，恰好 1% 不报），"
        "命中时页面提示「VFLOW 与进口流股流量不一致」，"
        "不静默二选一。",
    )
    p(
        doc,
        "**Aspen 轴功率的折回口径**：`BRAKE_POWER` 本身就是轴功率，直接作基准；"
        "只有回退读到 `ELEC_POWER` 时才先乘电机效率 0.95 折回轴功率。"
        "电机铭牌标的是轴输出功率，因此轴功率到电机功率只除传动效率再乘裕量，"
        "不除电机效率；混用会把档位系统性放大 1/0.95。"
        "`transmission_eff` 仅是内部函数参数，前端没有输入入口，界面路径固定按直联 1.0。",
    )
    p(
        doc,
        "上述 `ELEC_POWER × 0.95` 隐含一个前提：Aspen 的 ELEC_POWER 已按约 95% 的电机效率计入"
        "（电功率 ≈ 轴功率 ÷ 0.95）。0.95 是本仓库常量（`app/values.py` 的 `MOTOR_EFF`），"
        "不是从模型里读到的效率值；模型的电机效率若不同，这一折算需要复核。"
        "Aspen Plus 泵模块未设驱动机效率时默认按 1.0 计（此时 ELEC_POWER = BRAKE_POWER）；"
        "若回退至该分支且模型未设电机效率，乘 0.95 会使折算轴功率偏低约 5%。",
    )
    p(
        doc,
        "**输出一致性**：页面参数卡、选型台账、设备表与 Excel/Word 报告/AI 导出统一读取 "
        "整泵计算结果的 `totals.motor` 的 "
        "`kw / basis_kw / power_source / margin / basis / note`；"
        "Excel 整本/分类泵表与 AI 包 `pumps.csv` 均含「电机功率口径」列（取 `power_source`），"
        "各出口按该字段直出，不自行重算。",
    )
    h(doc, "7.3　照会轴功率 P_估 与对账边界", 2)
    p(
        doc,
        "另给一个**照会值**（`check_kw` 不参与电机选档）：",
    )
    formula(
        doc,
        "P_估 = ρ·Q·H·g ÷ (3.6×10⁶·η)",
        "P_估 kW；ρ kg/m³；Q m³/h；H m；g m/s²；η 为 0–1 的小数"
        "（3.6×10⁶ 是把 m³/h 与 kW 对齐的换算系数）",
    )
    p(
        doc,
        "同式有两组互不混用的输入：`check_kw` 用 **Q = 泵块 VFLOW、H = 压升扬程 HEAD_CAL**；"
        "`design_kw` 用 **Q = 吸入段流股体积流量、H = 设计扬程 H**（仅两侧管段齐全时给）。"
        "ρ 同取 2.3 节密度，η 取 CEFF。`design_kw` 用于电机设计口径，"
        "`check_kw` 只用于与 Aspen 轴功率互校；两者都不含粘度修正与泵型效率曲线。"
        "`check_kw` 的 Q 固定取泵块 VFLOW，不读进口流股 `vol_flow`；"
        "后者缺失只影响 `design_kw` 的 Q 与两侧水力，不影响 `check_kw`。"
        "不要把 `check_kw` 的 H 与 `design_kw` 的 Q 交叉组合，那既不是泵块口径，也不是管段口径。",
    )
    p(
        doc,
        "**与 Aspen 轴功率对账时先统一密度**：Aspen 的 HEAD_CAL 按泵块密度反算，"
        "而 P_估 沿用 2.3 节密度（单相优先取流股 m/V）。两套密度偏差较大时，"
        "`check_kw` 会相对 Aspen 轴功率产生约 `ρ_stream / ρ_pump` 的系统性偏差，"
        "不可直接拿 `check_kw` 否定 `BRAKE_POWER`；应先按 2.3 节用同一密度复核压升扬程。",
    )
    p(
        doc,
        "**H ≤ 0 时的照会值**：轴功率公式不拦截负扬程，H ≤ 0 时 `design_kw` 会按原式保留负号"
        "（0 或负值，表示反向/自流工况参考）；而 `check_kw` 独立按压升扬程 HEAD_CAL 计算，"
        "两者正负号可能不同。不要用一个的符号去反推另一个。",
    )
    p(
        doc,
        "**含气/两相进口（汽化率 >1e-4）的照会值一律为空**：`check_kw` 与 `design_kw` 都返回空，"
        "并给 `note = 含气/两相进口不计算轴功率照会`；电机因此走 Aspen 回退口径。"
        "含气/两相流体具有可压缩性，不能套用不可压缩单相液体的扬程功公式；"
        "气液滑移与气缚会导致泵水力效率严重衰减，若改用纯液相密度乘两相总体积流量，"
        "也会系统性高估 P_估。因此照会值无工程意义，宁可留空；"
        "单相与边界 1e-4 仍照常计算。",
    )
    p(
        doc,
        "反向工况下 HEAD_CAL 与压升为负都属正常，原样显示，不取绝对值、不判异常。",
    )

    h(doc, "8　选型台账与三态判定", 1)
    h(doc, "8.1　判据", 2)
    formula(
        doc,
        "NPSHᵣ + 余量 ≤ NPSHₐ(修正)　→　满足；否则不满足",
        "等号成立算满足；比较的是修正值，进口基准值只作对照",
    )
    p(
        doc,
        "判定结果只有三态：满足、不满足、无法判定。台账同时给出理由文本，"
        "形如「NPSHᵣ 3 + 余量 0.5 = 3.5 m ≤ NPSHₐ(修正) 4.2 m（进口基准值 6.7 m）」。"
        "若进口 `vap_frac` 为空，三态照样给，但理由末尾追加"
        "「缺汽化率，暂按液相估算，请复核」；这表示结论建立在液相假设上，"
        "不能当作已确认的单相选型结论。",
    )
    note(
        doc,
        "术语区分：进口基准值 = 未扣吸入段 ΔZ 与 Σhf 的 NPSHₐ；"
        "装置有效 NPSHₐ（修正值）= 扣完后的值。三态判据只用修正值，基准值只作对照。",
    )

    h(doc, "8.2　余量的默认值与可调范围", 2)
    p(
        doc,
        "未填时按建议值 0.5 m 参与判定，并置「余量为默认值」标记；人填了就用填的值。"
        "建议区间 0.5–1.0 m，但接口不强制落在区间内。整份提交、全空即删行。",
    )

    h(doc, "8.3　无法判定的分支顺序", 2)
    mono(
        doc,
        [
            "1 含气/两相进口（汽化率 >1e-4）→ unknown，"
            "理由「两相进口不计算 NPSHₐ，无法判定」（实现文案）",
            "2 收集缺项：p_v、进口压力、密度、"
            "NPSHₐ(读数不完整:非有限值拦截或基础 NPSHₐ 判空)、吸入段阻力/位压头",
            "3 缺项非空 → unknown，理由「缺 X、Y，无法判定」",
            "4 否则若 NPSHᵣ 为空 → unknown，理由「缺 NPSHᵣ，无法判定」",
            "5 否则按 8.1 判 ok / bad",
        ],
    )
    p(
        doc,
        "p_v 缺失不会被进口压力顶替，也不会默认「不汽蚀」；NPSHᵣ 为空时不拿建议值或同类泵值替代。",
    )

    h(doc, "8.4　台账能说明什么、不能说明什么", 2)
    p(
        doc,
        "台账记录的是「选了什么」：型号、台数、NPSHᵣ、余量与电机。它不宣称「选得对不对」——"
        "工具没有 Q-H 曲线与型谱库，能核对的只有 NPSH 这一条。换工况时自动带出上次的选型，"
        "并用当前工况的修正 NPSHₐ 重算三态。",
    )

    h(doc, "9　两侧管段的数据流", 1)
    h(doc, "9.1　一份共享记录", 2)
    p(
        doc,
        "泵面板的「设计扬程 / 管路阻力」与管道页共享同一条流股的"
        "管段记录（按模型 + 流股唯一），不另存一份 L、Σζ、内径。管道页看到的人工 DN、"
        "手填粘度与管件，就是泵面板读到的同一份。",
    )
    p(
        doc,
        "**人工 DN/自定义规格的边界**：`pipe_run` 的 `dn_override` 与自定义 `od/wall` "
        "在界面/接口上二选一，同时提交会被 400 拒绝。仅当历史库或直接写库使两者同时存在时，"
        "公共合并层以自定义 `od/wall` 优先于 `dn_override`。"
        "生效后既服务泵侧水力计算，也同步进入管道页返回行、"
        "Excel/Word 报告/AI 管道导出表与校核；自动原值保留在 `auto_dn` 等字段。"
        "因此泵侧看到的 `line.dn` 与管道导出、V2/V3 校核使用同一份生效规格。",
    )
    mono(
        doc,
        [
            "管道页参数 ─┐",
            "流股/设备读数 ─┼─> 管道构建 ─> DN / 内径 / 流速",
            "pipe_run(流股输入) ─┘        │",
            "                              v",
            "              单侧管段计算（每侧） ─> u / Re / λ / Σhf / 状态",
            "                              v",
            "              pump_line.compute(整台泵) ─> totals / NPSH / 三态",
            "                              v",
            "              页面 · Excel · AI 包 · Word 报告（同一函数，同值）",
        ],
    )

    h(doc, "9.2　存什么、不存什么", 2)
    p(
        doc,
        "pipe_run 只存输入：L、Σζ、管件明细、λ 口径与手填值、ΔZ、人工 DN 或自定义外径壁厚、"
        "手填粘度、备注、版本号。流速、雷诺数、λ 数值、Σhf、自动 DN、内径、设计扬程、选型扬程、"
        "NPSHₐ 与三态全部实时算，不落库。",
    )

    h(doc, "9.3　乐观锁", 2)
    p(
        doc,
        "保存管段时带「读到的版本号」，与库内版本不一致返回 409 并提示刷新重试；"
        "每次成功保存版本加 1。全空（无管件、无备注、无数值、无口径）时删行。"
        "从同模型旧工况沿用过来的记录只回显、不回写，版本号在响应里置 0，避免每次保存都冲突。",
    )

    h(doc, "9.4　人工 DN 与自定义规格", 2)
    p(
        doc,
        "选定人工 DN，或同时填了自定义外径与壁厚后，"
        "该管段以人工规格覆盖管道构建的自动选档结果，直到点「恢复自动」。"
        "人工 DN 的壁厚不沿用自动档位，而按该外径与管道页的设计压力、"
        "许用应力重算，并与管道页共用同一套壁厚路由（材质表 1/表 4、PN 保护、R1E、真空外压）；"
        "其中 R1E 仍受 `P_d ≤ 10.0 MPa` 守卫，超过时回到通用公式加 PN 整档保护。"
        "该人工规格同时进入管道页、管道导出表与 V2/V3："
        "自定义规格按 C1=0、按新外径重算 `t_required_mm`，"
        "V3 若发现薄壁不足仍会报 error；两路径的负偏差语义统一见"
        "《管路选型计算说明书》5.4.2 节。"
        "保存层要求 `2×wall < od`，不满足返回 400；历史库已有的脏行不阻断读取，"
        "运行时取不到有效内径就判「无法关联」，原因固定「没有可用的管内径」。"
        "缺设计压力、缺操作温度或查不到许用应力时不给壁厚，提示改填自定义规格。",
    )
    p(
        doc,
        "若管道构建因 R1E 查不到可用 `[σ]^t` 等原因早退进 missing（管段记录缺失），"
        "仅配 `dn_override` 仍会因缺设计压力/许用应力落入「无法关联」，"
        "并提示改填自定义 `od/wall`；只有同时填写自定义 `od/wall` 才绕过缺行继续计算。",
    )
    p(
        doc,
        "人工 DN 不受自动选档跳过规则限制：前端下拉从 DN15 起，可直接选 DN65 / DN125；"
        "API/库层面接受 `DN_TO_OD` 中任意档位（含 DN10）。页面不提供 DN10，"
        "但直接经接口或库写入 `dn_override=10` 仍会被接受。",
    )
    p(
        doc,
        "改管道页参数会先算影响：涉及几台泵、设计扬程最大变化多少、哪些 DN 会变；"
        "预览确认后才写入，避免「拖一下参数悄悄改掉一批结论」。",
    )

    h(doc, "9.5　缺一侧与多流股", 2)
    p(
        doc,
        "**状态列的口径**：下表「状态」指该侧管段自己的关联状态，不是整泵状态。"
        "一侧已关联、另一侧缺失时，泵仍可能整泵不给设计扬程与选型扬程。"
        "L/Σζ 缺失会让该侧 Σhf 为空；仅排出侧 ΔZ 缺失时该侧虽标「未填」，"
        "修正 NPSHₐ 仍可算（只扣吸入侧），但设计扬程 H 与选型扬程为空。",
    )
    table(
        doc,
        ["情况", "状态", "表现"],
        [
            ("恰好一条候选流股", "已关联", "自动使用"),
            ("人工指定且在候选内", "已关联", "使用指定流股"),
            ("没有候选", "缺失", "提示泵缺进口/出口流股，无法关联管路"),
            ("≥2 条且未指定", "待指定", "列出候选，要求指定，绝不按名字猜"),
            (
                "已关联但 L/Σζ 未填",
                "未填",
                "L 或 Σζ 任缺：该侧 Σhf 即为空（只填 L 不够），整台泵不给设计扬程与选型扬程",
            ),
            (
                "已关联、L/Σζ 已填但吸入侧 ΔZ 缺失",
                "未填",
                "Σhf 可算出，但修正 NPSHₐ、设计扬程 H 与选型扬程均为空",
            ),
            (
                "已关联、L/Σζ 已填但仅排出侧 ΔZ 缺失",
                "未填",
                "修正 NPSHₐ 仍正常计算（只扣吸入侧），但设计扬程 H 与选型扬程为空",
            ),
            (
                "仅缺某外径壁厚序列（几何退化，wall_mm 为空）",
                "无法关联",
                "保留 dn/od，wall 与内径为空；原因固定「没有可用的管内径」，"
                "不进入缺项列表。与《管路选型计算说明书》2.4 节、表 10-1"
                "「仅缺壁厚序列」是同一条退化路径；缺压力/温度/许用应力或厚壁越界"
                "这些根因回管道页看，见 6.1 节与《管路选型计算说明书》表 7-2",
            ),
            (
                "标准表缺失（管道选型抛 TablesNotReady）",
                "无法关联",
                "管道页接口返回 409 并附配置指引，不返回空表（《管路选型计算说明书》2.4 节、"
                "表 10-1「标准表缺失」）；泵面板不单独报 409："
                "`pump_line_context._pipe_rows` 捕获后候选流股全部没有表行，"
                "各侧标「无法关联」、原因原文透传异常文本（「标准表未录入:… 为空」），"
                "响应另带 `tables_error`。零流量、超档、R1E 查不到 `[σ]^t` 早退进 missing "
                "属于另一类「管道页给不出规格」，原因同样原文透传；"
                "极低流量（0 < Q ≤ 0.01 m³/h）单列待确认。"
                "不要把「标准表未录入」并入「管道页算出的空规格」",
            ),
        ],
        (5.0, 3.4, 7.6),
        caption="表 9-1　两侧流股关联状态（状态列为单侧）",
    )

    h(doc, "9.6　旧单一 Σhf 的迁移", 2)
    p(
        doc,
        "旧数据中每台泵只有一组 L/Σζ/Σhf。检测到旧数据时提供迁移向导：归吸入、归排出、或标为"
        "「两侧合计」。归某一侧时旧 ΔZ 置空，要求两侧分别补标高差；标为合计时修正 NPSHₐ 仍判"
        "无法判定，不拿合计值冒充吸入侧。旧手填内径不沿用，管径改由管道页统一选型。",
    )

    h(doc, "10　四条出口的一致性", 1)
    p(
        doc,
        "泵页、Excel 泵表、AI 分析包的 pumps.csv、Word 报告中的泵段，调用的都是同一条计算链；"
        "自测对四个出口做了一致性断言。引用时注意三点：",
    )
    bullets(
        doc,
        [
            "含气/两相进口的 NPSHₐ 由核心函数统一拒算，页面与三个导出同为空（见第 4 章）；",
            "页面顶部余量倍率仅影响页面前端展示层，不进入四出口共享结果；完整边界见第 5 章；",
            "重力加速度统一为 9.80665；顾毓珍公式在 2300–3000 过渡区与 Re>3e6 两端"
            "都回退 Colebrook 并标注口径。",
        ],
    )

    h(doc, "11　边界与未实现", 1)
    table(
        doc,
        ["边界", "实际行为"],
        [
            ("不做型谱库", "无 Q-H 曲线、无并联/备用台数优化、不做设计泵"),
            ("不做全厂管网", "只算泵吸入/排出两条管段，不做管网水力平衡"),
            ("不验收选型", "只核对 NPSH 一条，台账不宣称「已验过选型」"),
            ("电机档位", "工程常用序列，非标准原文；超出 400 kW 返回空"),
            ("ζ 表与粗糙度", "手册常用值，非标准原文"),
            ("NPSHᵣ", "只能人工从型谱录入；缺则无法判定"),
            (
                "极低蒸气压 / 非挥发重组分",
                "若 Aspen 输出 PBUB = 0，按缺 p_v 处理，NPSHₐ 与三态无法判定；"
                "需人工确认蒸气压数据或改用可靠物性来源",
            ),
            ("含气/两相进口 NPSH", "核心统一拒算，四出口同为空；无真实两相样本验证"),
            (
                "含气/两相进口轴功率照会",
                "汽化率 >1e-4 时 check_kw 与 design_kw 都为空；完整口径见 7.3 节",
            ),
            (
                "两相管段阻力与设计扬程",
                "单侧计算不检查汽化率，无两相拦截或告警；仍按单相达西公式计入 H。"
                "单相模型通常显著低估压降，结果仅作参考、需专项核算；模型边界见 6.3 节",
            ),
            ("手填 λ", "缺密度或粘度不阻断 Σhf、Re 留空；口径见 6.3 节与表 6-1"),
            (
                "电机与管路附加",
                "设计口径优先、Aspen 回退与分段裕量见 7.1–7.2 节；仅回退 Aspen 压升口径时"
                "不含管路附加扬程，最终仍以所选泵工况点轴功率为准",
            ),
            (
                "电机功率口径",
                "页面参数卡、台账、设备表与 Excel/Word 报告/AI 导出统一读 totals.motor；"
                "Excel 整本/分类与 AI 包 pumps.csv 均含「电机功率口径」列，"
                "各出口按该字段直出",
            ),
            (
                "缺进口汽化率",
                "汽化率缺失时 NPSHₐ、check_kw 与 design_kw 均按液相暂估；"
                "warnings、台账三态理由、"
                "页面与导出「提示」列均报「缺汽化率，暂按液相估算，请复核」。"
                "若实际为漏读的两相流股，可能偏乐观，需结合管路页告警复核",
            ),
            ("ENG 单位集", "换算按定义推导，尚无真实 ENG 模型实测"),
            (
                "排出侧粘度",
                "未手填时两侧共用泵进口流股 Aspen MUMX；排出侧不读出口流股 MUMX（见 6.3/6.6 节）",
            ),
            ("兼容路径", "旧单一 Σhf 路径仅服务迁移，新界面一律走两侧管段"),
        ],
        (3.6, 12.4),
        caption="表 11-1　边界与未实现事项",
    )

    h(doc, "12　复算示例", 1)
    p(
        doc,
        "以下数据由本仓库函数实算（合成输入，非他人模型），故意拆成两段："
        "算例 A 只复算「一段管」的沿程与局部阻力；算例 B 复算「一整台泵」的 NPSH 与设计扬程，"
        "其中的 Σhf 由管段记录直接给出。两段输入彼此独立，**A 的 Σhf 不进 B 的算式**，"
        "避免把「按 L/Σζ 算出的 8.97 m」与「记录里的 1.5 / 3.0 m」混成一笔账。"
        "表内数值为四舍五入后的展示值，实际计算按全精度传递；"
        "复核时以公式复算值为准，不用表内尾差反推实现。",
    )

    h(doc, "12.1　单管段阻力复算（算例 A）", 2)
    p(
        doc,
        "输入：Q = 100 m³/h，ρ = 900 kg/m³，μ = 1.0 cP，d = 100 mm，L = 50 m，Σζ = 5，"
        "ε = 0.05 mm；λ 按 Colebrook 与顾毓珍公式各算一次，用于说明两种口径的差别。",
    )
    table(
        doc,
        ["量", "公式与取值", "结果"],
        [
            ("u", "(100/3600)/(π×d_m²/4)，d_m = 0.1 m", "3.537 m/s"),
            ("Re", "0.1×3.537×900/(1.0/1000)", "3.18×10⁵"),
            ("λ（Colebrook）", "1/√λ = −2log₁₀(0.05/(3.7×100) + 2.51/(Re√λ))", "0.01813"),
            ("λ（顾毓珍）", "0.01227 + 0.7543/Re^0.38", "0.01839"),
            ("Σhf（Colebrook）", "(0.01813×50/0.1 + 5)×3.537²/(2×9.80665)", "8.97 m"),
            (
                "Σhf（顾毓珍）",
                "(0.01839×50/0.1 + 5)×3.537²/(2×9.80665)",
                "9.05 m（全精度实算）",
            ),
        ],
        (3.6, 8.8, 3.6),
        caption="表 12-1　算例 A：单管段阻力（Q=100 m³/h，d=100 mm，L=50 m，Σζ=5）",
        font_pt=8,
    )

    p(
        doc,
        "同一几何下，顾毓珍 λ 使 Σhf 由 8.97 m 变为 9.05 m，仅高 0.08 m（约 0.9%）；"
        "说明在本例的 Re 与相对粗糙度范围内，λ 口径对最终阻力的影响有限，"
        "但不能据此把两种口径混用。",
    )

    h(doc, "12.2　整台泵 NPSH 与设计扬程复算（算例 B）", 2)
    p(
        doc,
        "输入：压升扬程 H_pr = 20 m；进口流股操作压力 2.0 bar"
        "（按第 3 章假定视为上游容器操作压力）、p_v = 0.4 bar、ρ = 900 kg/m³；"
        "吸入段 ΔZ = +2 m（吸上工况：泵进口高于吸入液面 2 m，沿流向抬升）、Σhf = 1.5 m；"
        "排出段 ΔZ = 5 m、Σhf = 3.0 m；"
        "NPSHᵣ = 3.5 m、余量 0.5 m。Σhf 两项是记录值，不由算例 A 推出。",
    )
    table(
        doc,
        ["量", "公式与取值", "结果"],
        [
            ("NPSHₐ(进口基准)", "(2.0−0.4)×10⁵/(900×9.80665)", "18.13 m"),
            ("NPSHₐ(装置有效)", "18.13 − 2.0 − 1.5", "14.63 m"),
            ("设计扬程 H", "20 + (2+5) + (1.5+3.0)", "31.5 m"),
            ("选型扬程", "ceil(1.1×31.5)", "35 m"),
            ("三态", "3.5 + 0.5 = 4.0 ≤ 14.63", "满足"),
        ],
        (3.6, 8.8, 3.6),
        caption="表 12-2　算例 B：整台泵 NPSH 与设计扬程（吸上工况，泵进口高于吸入液面 2 m）",
        font_pt=8,
    )
    p(
        doc,
        "**接上算例 B 的 H = 31.5 m，补一遍 `design_kw` 照会轴功率**：取吸入流股体积流量 "
        "Q = 1200 m³/h、ρ = 900 kg/m³、η = 0.705、g = 9.80665 m/s²，代入 "
        "P_估 = ρQH g / (3.6×10⁶ η)："
        "900×1200×31.5×9.80665 / (3.6×10⁶×0.705) = 131.45 kW。"
        "这里的 H 是未乘 1.1 的设计扬程，故该值就是设计扬程口径的基准轴功率 `design_kw`；"
        "H = 31.5 m > 0，不触发反向/自流前置守卫，走①设计扬程口径。"
        "它落在 `> 55 kW` 段，取裕量 1.10：131.45×1.10 = 144.60 kW，"
        "档位表中第一个 ≥ 144.60 kW 的是 160 kW，于是电机选 160 kW。"
        "直联传动效率取 1.0，不再另乘；若用皮带（约 0.95），应先除传动效率再乘分段裕量。",
    )
    p(
        doc,
        "**两个“≤0/未齐”分支必须分开**："
        "① 两侧管段齐全且设计扬程 H ≤ 0 时，由**前置守卫**直接不选电机，"
        "`power_source` 写「反向/自流工况」，不回退 Aspen；"
        "② 管段未齐（或设计口径不可用）时进入**回退 Aspen 口径**，"
        "若此时 `BRAKE_POWER` 为 −2 kW，则在该回退分支内判 "
        "「Aspen 轴功率 −2.00 kW ≤ 0,反向工况不选电机」，"
        "`power_source` 为「Aspen压升口径（管段未齐）」，同样不转用 ELEC_POWER。",
    )
    p(
        doc,
        "其余口径对照：小功率基准 20 kW → 20×1.25 = 25 kW → 取 30 kW 档（<22 kW，1.25 档）；"
        "若回退 ELEC_POWER 140 kW，则 140×0.95×1.10 = 146.3 kW → 取 160 kW 档。",
    )

    h(doc, "13　常数速查", 1)
    table(
        doc,
        ["常数", "值", "用途"],
        [
            ("重力加速度", "9.80665 m/s²", "NPSH、压升与阻力统一"),
            ("设计扬程裕量", "1.1", "H_选型 = ceil(1.1H)"),
            ("建议余量", "0.5 m（建议 0.5–1.0）", "台账判据"),
            (
                "传动效率",
                "1.0（界面路径固定直联；0.95 仅内部参数/手算参考）",
                "建议电机；前端无传动效率输入入口",
            ),
            (
                "电机分段裕量",
                "<22 kW 1.25；22–55 kW 1.15；>55 kW 1.10",
                "按基准功率分档，不按乘完裕量后的需求功率",
            ),
            (
                "电机基准优先级",
                "反向/自流守卫 → 设计扬程口径 → Aspen 回退；H ≤ 0 且两侧齐全时不回退",
                "设计口径用未乘 1.1 的 H；完整顺序与回退见 7.2 节",
            ),
            ("电机效率", "0.95", "只用于 ELEC_POWER → 轴功率"),
            ("电机档位", "0.55–400 kW，27 档", "向上取档"),
            ("默认绝对粗糙度", "0.05 mm", "Colebrook"),
            ("层流上限", "Re < 2300", "仅 Colebrook / 顾毓珍取 λ = 64/Re；手填不覆盖"),
            (
                "顾毓珍区间",
                "3e3 ≤ Re ≤ 3e6",
                "顾毓珍公式（一般工业钢管湍流经验式）；2300–3000 与 Re>3e6 两端回退 Colebrook",
            ),
            ("Colebrook 迭代", "初值 0.02，60 次，1e-10", "隐式求解"),
            (
                "手填 λ 门槛",
                "0.0001 ≤ λ ≤ 1.0，且 Q、d、L、Σζ 齐",
                "空值/≤0 按未填；密度/粘度只复核 Re，不阻断 Σhf",
            ),
            ("NPSH 归零阈值", "0.005 m", "显示精度"),
            (
                "两相判定阈值",
                "TWO_PHASE_VAP_EPS = 1e-4（汽化率 > 1e-4）",
                "四出口不计算 NPSHₐ 与轴功率照会；与管道相态同一常量",
            ),
            ("默认流速", "1.5 / 15 / 8 / 0.8 m/s", "液 / 气 / 两相 / 泵入口"),
            (
                "VFLOW 与进口流股流量偏差告警阈值",
                "1%",
                "超过时页面提示「VFLOW 与进口流股流量不一致」，不静默二选一",
            ),
        ],
        (4.2, 6.4, 5.4),
        caption="表 13-1　常用常数",
        font_pt=8,
    )
    note(
        doc,
        "常数集中写在 app/design/pump.py、pump_line.py、hydraulics.py、values.py 与 "
        "pipe/constants.py；ζ 表、电机档位、粗糙度属工程惯例，不是标准原文。",
    )

    h(doc, "附录　核心代码函数映射表", 1)
    p(
        doc,
        "下表仅供代码核对，正文中的规则以章节表述为准。函数名带下划线者为模块内部实现，"
        "不构成对外接口。",
    )
    note(
        doc,
        "**表号说明**：本册附表 B-1 与《管路选型计算说明书》附录表 A-1 成套编号——"
        "A-1 列管路侧核心函数，B-1 列泵侧核心函数；两册各自完整，不跨册重复条目。",
    )
    table(
        doc,
        ["文档对象", "代码位置", "说明"],
        [
            ("泵进出口压差扬程", "app/design/pump.py::head_from_dp", "由 p_out − p_in 与密度换算"),
            ("选型扬程取整", "pump.py::selection_head_m", "设计扬程乘 1.1 后向上取整"),
            (
                "进口基准 NPSHₐ",
                "pump.py::npsh_available_m",
                "进口基准 NPSHₐ（速度头已在液面推导中抵消）与两相拦截",
            ),
            ("两侧流股识别", "app/design/pump_line.py::stream_side", "按拓扑与人工指定取吸入/排出"),
            ("单侧管段阻力", "pump_line.py::compute_line", "u、Re、λ、Σhf 与缺项状态"),
            ("整泵汇总", "pump_line.py::compute", "两侧合成 H、NPSHₐ、轴功率与 warnings"),
            ("选型台账", "app/design/pump_selection.py::compute_view", "三态判定、余量与理由"),
            (
                "管段记录读取",
                "app/design/pump_line_context.py::lines_for",
                "合并 pipe_run 与管道页参数",
            ),
            (
                "历史参数兼容",
                "pump_line_context.py::_merge_options",
                "旧库 pressure_factor 抬到 1.0",
            ),
            ("流速", "app/design/hydraulics.py::velocity_ms", "Q 与内径换算"),
            ("雷诺数", "hydraulics.py::reynolds", "d、u、ρ、μ"),
            ("Colebrook", "hydraulics.py::colebrook", "湍流区迭代解 λ"),
            (
                "顾毓珍",
                "hydraulics.py::gu_yuzhen",
                "清洁/一般工业钢管湍流经验式（3e3≤Re≤3e6）",
            ),
            ("λ 口径分发", "hydraulics.py::friction_factor", "层流、手填与自动三条分支"),
            ("沿程与局部阻力", "hydraulics.py::head_loss_m", "达西式加 Σζ"),
            ("手填 λ 求解", "hydraulics.py::solve", "空值与非正值按未填处理"),
            ("电机建议", "pump_line.py::_motor_suggestion", "分段裕量、效率与功率口径"),
        ],
        (3.4, 7.2, 5.4),
        caption="附表 B-1　泵计算核心函数",
        font_pt=8,
    )


def main():
    """正文已迁到 docs/*.md：这里只转调 md 渲染器，避免两套正文各改一份。

    build_pipe / build_pump 保留作排版层的历史参照，不再执行；新增或修改说明
    书内容请改 md，再运行 `python -m tools.make_calc_manuals_from_md`。
    """
    from tools.make_calc_manuals_from_md import main as render_from_md

    return render_from_md()


if __name__ == "__main__":
    main()
