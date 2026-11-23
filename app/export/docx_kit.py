"""Word 版式骨架 —— 衡算报告与管道选型一览表共用的一套排版原语。

为什么单独一个模块
------------------
两份 .docx 的封面、页眉页脚、章节标题、三线表出自同一套骨架。早先它们都写在
`report.py` 里,管道册去 `report` 里取私有的 `_para` / `_three_line` 等函数 ——
两册并排打印时颜色、页边距、页眉高度对不上,比多一个模块难看得多。

本模块的 `_` 前缀函数**是给 `report` 与 `pipe_schedule` 共用的接口**,不是
模块私藏;改名或改签名要同时改这两个调用方。

册别主题(`KINDS`)也在这里:一册一主题色,合订本与分册共用同一份配色。
`activate()` 改的是本模块的 `_ACTIVE_KIND`,调用方要用当前主题请读
`docx_kit._ACTIVE_KIND` 或 `kind_meta()` 的返回值,不要 `from ... import`
把值拷走 —— 那是快照,`activate()` 之后就过期了。
"""

from __future__ import annotations

from datetime import datetime

# 一册一主题色:物料册冷蓝、能量册暖砖红。黑白打印时靠封面「册别」与章节标题
# 的文字区分,颜色只是辅助 —— 评委多半会打印出来看。
KINDS: dict[str, dict] = {
    "mass": {
        "title": "物料衡算报告",
        "book": "物料衡算册",
        "accent": "1F4E79",
        "light": "DCE6F1",
        "zebra": "F7FAFD",
    },
    "energy": {
        "title": "能量衡算报告",
        "book": "能量衡算册",
        "accent": "9C3B23",
        "light": "F7E3DD",
        "zebra": "FDF8F6",
    },
    "all": {
        "title": "物料与能量衡算报告",
        "book": "衡算报告",
        "accent": "1F4E79",
        "light": "DCE6F1",
        "zebra": "F7FAFD",
    },
    # 管道册:墨绿。与物料册的冷蓝、能量册的暖砖红都能分开 —— 三册并排打印时
    # 靠封面色带就能认出哪本是哪本,不必翻到封面标题。
    "pipe": {
        "title": "管道选型一览表",
        "book": "管道专业 · 选型册",
        "accent": "2E5E4E",
        "light": "DFEAE5",
        "zebra": "F6FAF8",
    },
    # 换热册:暖铜。与管道墨绿并排打印时靠色带区分,避免装订后混册。
    "hx": {
        "title": "换热器选型一览表",
        "book": "换热专业 · 选型册",
        "accent": "8A5A2B",
        "light": "F3E7D8",
        "zebra": "FCF8F3",
    },
}


KIND_KEYS = tuple(KINDS)


_ACTIVE_KIND = KINDS["all"]


def kind_meta(kind: str) -> dict:
    return KINDS.get(kind) or KINDS["all"]


def activate(kind: str) -> dict:
    """把全局主题切到某一册。

    表格 / 标题 / 封面的主色都读这个全局值(它们是被各章反复调用的短函数,
    逐个传 meta 会把签名铺得很长)。外部导出(如管道表)复用这套版式时,
    先调它一次即可,不用自己拼颜色。
    """
    global _ACTIVE_KIND
    _ACTIVE_KIND = kind_meta(kind)
    return _ACTIVE_KIND


def _setup(doc, case: dict, kind: str = "all") -> None:
    """A4 版面 + 页眉(册别/工况)+ 页脚(页码)。"""
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(2.5)
    section.right_margin = Cm(2.5)
    section.top_margin = Cm(2.4)
    section.bottom_margin = Cm(2.2)

    style = doc.styles["Normal"]
    style.font.size = Pt(10.5)
    style.font.name = "宋体"
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    style.paragraph_format.line_spacing = 1.22


def _font(run, size=None, bold=None, name="宋体"):
    from docx.oxml.ns import qn

    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if size is not None:
        run.font.size = size
    if bold is not None:
        run.font.bold = bold
    return run


def _para(doc, text="", size=None, bold=False, align=None, space_after=None, name="宋体"):
    from docx.shared import Pt

    para = doc.add_paragraph()
    if align is not None:
        para.alignment = align
    if space_after is not None:
        para.paragraph_format.space_after = Pt(space_after)
    if text:
        _font(para.add_run(text), size, bold, name)
    return para


# `w:pBdr` 在 CT_PPr 里排在表格样式之后、`w:shd` 之前。python-docx 只能
# `append`,排在 `w:spacing` / `w:rPr` 后面就是非法顺序 —— Word 宽容、WPS 会丢,
# 所以插边框一律走 `insert_element_before`,后面这些兄弟都要点名。
_PPR_AFTER_PBDR = (
    "w:shd",
    "w:tabs",
    "w:suppressAutoHyphens",
    "w:kinsoku",
    "w:wordWrap",
    "w:overflowPunct",
    "w:topLinePunct",
    "w:autoSpaceDE",
    "w:autoSpaceDN",
    "w:bidi",
    "w:adjustRightInd",
    "w:snapToGrid",
    "w:spacing",
    "w:ind",
    "w:contextualSpacing",
    "w:mirrorIndents",
    "w:suppressOverlap",
    "w:jc",
    "w:textDirection",
    "w:textAlignment",
    "w:textboxTightWrap",
    "w:outlineLvl",
    "w:divId",
    "w:cnfStyle",
    "w:rPr",
    "w:sectPr",
    "w:pPrChange",
)


def _heading(doc, text, level=1):
    """章节标题:黑体,不依赖 docx 内置样式(样式名各版本不一致)。

    层级靠三种记号分开,不靠字号堆叠:一级带主题色下横线、二级前加主题色竖块、
    三级只加粗。灰度打印时横线与竖块仍在,"这是第几层"不依赖读者比字号。
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt

    meta = _ACTIVE_KIND
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

    if level == 2:
        _font(para.add_run("▍"), sizes[2], True).font.color.rgb = _rgb(meta["accent"])
    run = _font(para.add_run(text), sizes.get(level, Pt(11)), True, "黑体")
    if level == 1:
        run.font.color.rgb = _rgb(meta["accent"])
        borders = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "6")  # 1/8 pt → 0.75 pt
        bottom.set(qn("w:space"), "3")
        bottom.set(qn("w:color"), meta["accent"])
        borders.append(bottom)
        para._p.get_or_add_pPr().insert_element_before(borders, *_PPR_AFTER_PBDR)
    return para


def _rgb(hex_color: str):
    from docx.shared import RGBColor

    return RGBColor.from_string(hex_color)


def _tint(cell, color=None, bold=None, italic=None) -> None:
    """给单元格里的文字上色 / 加粗 / 斜体(状态列用)。"""
    for para in cell.paragraphs:
        for run in para.runs:
            if color:
                run.font.color.rgb = _rgb(color)
            if bold is not None:
                run.font.bold = bold
            if italic is not None:
                run.font.italic = italic


def _left_bar(cell, eighth_pt: int = 18, color: str = "000000") -> None:
    """给单元格加一条左侧粗边(超差行用):黑白打印下也一眼看出这一行有问题。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), str(eighth_pt))  # 1/8 pt 为单位:18 ≈ 2.25 pt
    left.set(qn("w:color"), color)
    borders.append(left)
    # tcBorders 必须排在 shd 之前,否则 Word 宽容、WPS/旧版会丢
    tc_pr.insert_element_before(
        borders,
        "w:shd",
        "w:noWrap",
        "w:tcMar",
        "w:textDirection",
        "w:tcFitText",
        "w:vAlign",
        "w:hideMark",
    )


def _shade(cell, hex_fill: str) -> None:
    """给单元格铺底色(超差行标红用)。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_pr.insert_element_before(
        shd, "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark"
    )


def _three_line_caption(doc, caption, font_pt):
    """表题:小色块 + 加粗标题;表题不单独留在页底。"""
    from docx.shared import Pt

    meta = _ACTIVE_KIND
    if caption:
        cap = doc.add_paragraph()
        cap.paragraph_format.space_before = Pt(8)
        cap.paragraph_format.space_after = Pt(2)
        cap.paragraph_format.keep_with_next = True  # 表题不单独留在页底
        # 表题前的小色块:黑白打印时是灰块,仍能分出"这是表题"
        _font(cap.add_run("■ "), Pt(7), True).font.color.rgb = _rgb(meta["accent"])
        _font(cap.add_run(caption), Pt(10), True)


def _row_flag(row, tag: str) -> None:
    """给整行加一个 w:trPr 开关(tblHeader / cantSplit)。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tr_pr = row._tr.get_or_add_trPr()
    node = OxmlElement(f"w:{tag}")
    node.set(qn("w:val"), "true")
    tr_pr.append(node)


def _table_borders(table):
    """三线表的表格边框:顶/底 1.0 pt,无竖线、无内部横线。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    borders = OxmlElement("w:tblBorders")
    # 注意:这里别用 size 当循环变量 —— 它会把上面算好的字号覆盖掉,
    # 表现是"设了 8pt 却还是默认字号"(踩过一次)
    # 8pt 表体配 1.5pt 线太重;顶/底 1.0pt(sz=8)、表头下 0.5pt(sz=4)
    for edge, sz in (
        ("top", 8),
        ("bottom", 8),
        ("left", 0),
        ("right", 0),
        ("insideH", 0),
        ("insideV", 0),
    ):
        node = OxmlElement(f"w:{edge}")
        if sz:
            node.set(qn("w:val"), "single")
            node.set(qn("w:sz"), str(sz))
            node.set(qn("w:color"), "000000")
        else:
            node.set(qn("w:val"), "none")
            node.set(qn("w:sz"), "0")
        borders.append(node)
    table._tbl.tblPr.insert_element_before(
        borders,
        "w:shd",
        "w:tblLayout",
        "w:tblCellMar",
        "w:tblLook",
        "w:tblCaption",
        "w:tblDescription",
    )


def _header_block(table, headers, size, header_extra, meta, fill):
    """表头:可选的合并分组行 + 列头 + 表头下方的线;跨页重复。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt

    if header_extra:
        # 分组行(进口 / 出口):整行合并,和列头一起跨页重复 —— 否则续表只看到
        # 一堆流股名,读者不知道这是进口块还是出口块
        first_row = table.rows[0]
        merged = first_row.cells[0].merge(first_row.cells[len(headers) - 1])
        para = merged.paragraphs[0]
        para.alignment = WD_ALIGN_PARAGRAPH.LEFT
        para.paragraph_format.space_after = Pt(0)
        _font(para.add_run(str(header_extra)), size, True)
        for extra in merged.paragraphs[1:]:
            extra._element.getparent().remove(extra._element)
        for cell in first_row.cells:
            _shade(cell, meta["light"])
        _row_flag(first_row, "tblHeader")
        for cell in first_row.cells:
            cell.paragraphs[0].paragraph_format.keep_with_next = True
        header_row = table.rows[1]
    else:
        header_row = table.rows[0]

    for index, head in enumerate(headers):
        fill(header_row.cells[index], head, True, split_unit=True)
        _shade(header_row.cells[index], meta["light"])
        if index == 0:
            header_row.cells[index].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.LEFT
    # 表头跨页重复:表格被分页时,下一页顶部仍有列头(否则读者看不到单位)
    _row_flag(header_row, "tblHeader")
    # 列头不要单独留在页底(中文报告惯例:题 + 表头 + 至少一行数据同页)
    for cell in header_row.cells:
        for para in cell.paragraphs:
            para.paragraph_format.keep_with_next = True
    # 表头下方的线(三线表的第二条)
    for cell in header_row.cells:
        tc_pr = cell._tc.get_or_add_tcPr()
        borders_cell = OxmlElement("w:tcBorders")
        node = OxmlElement("w:bottom")
        node.set(qn("w:val"), "single")
        node.set(qn("w:sz"), "4")
        node.set(qn("w:color"), "000000")
        borders_cell.append(node)
        tc_pr.append(borders_cell)

    return header_row


def _body_block(table, rows, head_rows, status_col, meta, fill, center_cols=()):
    """表体:逐行填格、斑马纹 / 超差底纹、状态列标记。

    `center_cols` 点名"必须居中的列"(如管道表的材料 / 类型):默认规则是
    "含数字就右对齐",`S30408` 这种带数字的材料名会被判成数值,贴在右边。
    """
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    stripe = 0
    for row_index, row in enumerate(rows, start=head_rows):
        _row_flag(table.rows[row_index], "cantSplit")
        cells = table.rows[row_index].cells
        cells_data, bold, fill_hex = row if isinstance(row, tuple) else (row, False, None)
        # 只有第一格有字 = 组分分段行(整行浅色带,把两个数据块分开)
        is_group = bool(cells_data) and not any(str(v).strip() for v in cells_data[1:])
        for index, value in enumerate(cells_data):
            text = "" if value is None else str(value)
            # 首列(位号/流股/项目)左对齐;纯文字格(相态、状态)居中;
            # 其余数值右对齐 —— 否则"液相"贴着右边,看着像数据错位
            if index == 0:
                align = WD_ALIGN_PARAGRAPH.LEFT
            elif index in center_cols or text and not any(ch.isdigit() for ch in text):
                align = WD_ALIGN_PARAGRAPH.CENTER
            else:
                align = WD_ALIGN_PARAGRAPH.RIGHT
            # 成片的 0.000 灰化:数字不动,只让眼睛跳过
            # 全零值灰化:太浅(如 A6A6A6)在小字号下会被误当成空白,取 8C8C8C
            tone = "8C8C8C" if text in ("0.00", "0.0", "0.000", "0.0000") else None
            fill(cells[index], text, bold, align, tone)

        if fill_hex:  # 超差行:灰底 + 首列黑竖边
            for cell in cells:
                _shade(cell, "E8E8E8")
            _left_bar(cells[0], 18)
        elif is_group:  # 分组行:主题浅色带
            for cell in cells:
                _shade(cell, meta["light"])
        elif stripe % 2 == 1:  # 斑马纹:宽表不串行
            for cell in cells:
                _shade(cell, meta["zebra"])
        if not is_group:
            stripe += 1

        # 状态列不只靠颜色:超差前置实心点并加粗;无法校验变灰斜体
        if status_col is not None and status_col < len(cells_data):
            text = str(cells_data[status_col]).strip()
            para = cells[status_col].paragraphs[0]
            if text == "超差" and para.runs:
                para.runs[0].text = "● " + para.runs[0].text
                _tint(cells[status_col], color="B00020", bold=True)
            elif text in ("无法校验", "流量过小"):
                _tint(cells[status_col], color="8A6D3B", italic=True)
            elif text == "不校验":
                _tint(cells[status_col], color="7F7F7F")


def _span_widths(table, widths, span_rows, head_rows):
    """分组行横向合并 + 逐列宽度(合并行不能按 row.cells 设宽度)。"""
    from docx.shared import Cm

    for index in span_rows or ():
        row = table.rows[head_rows + index]
        cells = row.cells
        if len({id(c._tc) for c in cells}) != len(table.columns):
            continue  # 已经合并过,跳过
        merged = cells[0].merge(cells[len(table.columns) - 1])
        for extra in merged.paragraphs[1:]:
            extra._element.getparent().remove(extra._element)
        merged.width = Cm(sum(widths))

    for index, width in enumerate(widths):
        table.columns[index].width = Cm(width)
    for row in table.rows:
        # 分组行是横向合并的,row.cells 会把同一个单元格返回 N 次 —— 拿它设宽度
        # 会把最后一列的宽度写满整张表(踩过一次:流股列全变成 4cm)
        cells = row.cells
        # 合并行里 row.cells 依然返回 N 个引用,但它们指向同一个 tc
        if len(cells) != len(widths) or len({id(c._tc) for c in cells}) != len(widths):
            continue
        for index, width in enumerate(widths):
            cells[index].width = Cm(width)

    _set_table_width(table, sum(widths))


def _three_line(
    doc,
    headers,
    rows,
    widths,
    caption="",
    font_pt=None,
    header_extra=None,
    status_col=None,
    span_rows=(),
    center_cols=(),
):
    """三线表:顶线 1.5 pt、表头下线 0.75 pt、底线 1.5 pt,无竖线无内部横线。

    python-docx 没有内置三线表样式,只能直接写 `w:tblBorders`。
    `w:sz` 的单位是 1/8 pt,所以 1.5 pt = 12、0.75 pt = 6。
    """
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    size = Pt(font_pt) if font_pt else Pt(9.0)
    meta = _ACTIVE_KIND
    _three_line_caption(doc, caption, font_pt)
    head_rows = 2 if header_extra else 1
    table = doc.add_table(rows=head_rows + len(rows), cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    _table_borders(table)

    def fill(
        cell,
        text,
        bold=False,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        color=None,
        split_unit=False,
    ):
        para = cell.paragraphs[0]
        para.alignment = align
        para.paragraph_format.space_after = Pt(0)
        para.paragraph_format.line_spacing = 1.0  # 继承 1.22 会把长表撑高一倍
        raw = "" if text is None else str(text)
        # 「ΣHin|kW」这种写法渲染成两行:名称在上、单位在下(7.5pt 灰),
        # 各列表头等高,不会出现有的折行有的不折。
        # 只对表头生效:表体里的竖线是内容(正则 `a|b`、绝对值 `|x|`),
        # 一律原样保留,不能在这里变成换行。
        if split_unit:
            name, _, unit = raw.partition("|")
        else:
            name, unit = raw, ""
        run = _font(para.add_run(name), size, bold)
        if color:
            run.font.color.rgb = _rgb(color)
        if unit:
            run.add_break()
            unit_run = _font(para.add_run(unit), Pt(7.5), False)
            unit_run.font.color.rgb = _rgb("595959")

    _header_block(table, headers, size, header_extra, meta, fill)
    _body_block(table, rows, head_rows, status_col, meta, fill, center_cols)

    _span_widths(table, widths, span_rows, head_rows)
    return table


class _Captions:
    """表号计数器:按章编号(表 2-1、表 3-4),与设计文档的习惯一致。"""

    def __init__(self, chapter: str = "1"):
        self.chapter = chapter
        self.n = 0

    def start(self, chapter: str) -> None:
        self.chapter = chapter
        self.n = 0

    def next(self, text: str = "") -> str:
        self.n += 1
        body = f"表 {self.chapter}-{self.n}"
        return f"{body}  {text}" if text else body


def _body_section(doc, case: dict, kind: str = "all"):
    """正文另起一节:页眉页脚只打在正文,页码从 1 开始(封面/目录不进页码)。"""
    from docx.enum.section import WD_SECTION
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    meta = kind_meta(kind)
    section = doc.add_section(WD_SECTION.NEW_PAGE)

    # 页码重排:pgNumType start=1(封面/目录那一节没有页码)
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
    stops = head.paragraph_format.tab_stops
    stops.add_tab_stop(Cm(16.0), WD_TAB_ALIGNMENT.RIGHT)
    _font(head.add_run(f"Casebook · {meta['book']}"), Pt(8.5), False)
    _font(
        head.add_run(f"\t#{case.get('id')}　{case.get('model_name') or ''}"), Pt(8.5), False
    ).font.color.rgb = _rgb("808080")
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
    _font(foot.add_run("— "), Pt(8.5), False).font.color.rgb = _rgb("808080")
    run = foot.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)
    run._r.append(instr)
    run._r.append(end)
    run.font.size = Pt(8.5)
    _font(foot.add_run(" —"), Pt(8.5), False).font.color.rgb = _rgb("808080")
    return section


def _set_table_width(table, cm: float) -> None:
    """把表宽写死成 dxa。

    python-docx 建表时留 `w:tblW = 0 / auto`,而 `autofit = False` 只改
    `tblLayout`。两者并存时 Word 会按内容重算表宽 —— 窄表被拉满整页、
    九列宽表被压扁,上面辛苦分的列宽全成"建议值"。所以先删旧的再插新的。
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tbl_pr = table._tbl.tblPr
    for old in tbl_pr.findall(qn("w:tblW")):
        tbl_pr.remove(old)
    node = OxmlElement("w:tblW")
    node.set(qn("w:w"), str(int(round(cm * 567))))
    node.set(qn("w:type"), "dxa")
    tbl_pr.insert_element_before(
        node,
        "w:jc",
        "w:tblCellSpacing",
        "w:tblInd",
        "w:tblBorders",
        "w:shd",
        "w:tblLayout",
        "w:tblCellMar",
        "w:tblLook",
        "w:tblCaption",
        "w:tblDescription",
    )


def _gap(doc, points: float):
    """精确空档:行距设成固定值,不靠「几个空段落」凑高度。

    空段落撑间距在 Word / WPS / 网页转换器里的行高各不相同,封面会整体跑位;
    固定行距只有一个解释。
    """
    from docx.shared import Pt

    para = doc.add_paragraph()
    fmt = para.paragraph_format
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(0)
    fmt.line_spacing = Pt(max(points, 1.0))
    _font(para.add_run(""), Pt(1))
    return para


def _tracked(run, twentieths: int = 40) -> None:
    """给 run 加字距:`w:spacing` 以 1/20 pt 计,60 就是疏排 3 pt。

    只用于封面那行西文小标签 —— 疏排之后它读起来像"抬头",不像正文首句。
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    node = OxmlElement("w:spacing")
    node.set(qn("w:val"), str(twentieths))
    run._element.get_or_add_rPr().insert_element_before(
        node, "w:w", "w:kern", "w:position", "w:sz", "w:szCs", "w:highlight"
    )


def _band(doc, rows, width_cm: float = 16.0) -> None:
    """满宽色带:`rows` 是 `[(填充色, 高度 cm), ...]`。

    几行叠在同一张表里,而不是并排几张一行表 —— 相邻的 `w:tbl` 会被 Word
    并成一张,行高按后一张算,双色带就退化成一条实线。
    """
    from docx.enum.table import WD_ROW_HEIGHT_RULE, WD_TABLE_ALIGNMENT
    from docx.shared import Cm, Pt

    table = doc.add_table(rows=len(rows), cols=1)
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_width(table, width_cm)
    for row, (color, height_cm) in zip(table.rows, rows, strict=False):
        cell = row.cells[0]
        cell.width = Cm(width_cm)
        _shade(cell, color)
        row.height = Cm(height_cm)
        row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
        para = cell.paragraphs[0]
        para.paragraph_format.space_before = Pt(0)
        para.paragraph_format.space_after = Pt(0)
        para.paragraph_format.line_spacing = Pt(1)
        _font(para.add_run(""), Pt(1))


def _cover_meta(doc, rows, meta) -> None:
    """封面信息块:两列无框表 —— 标签右对齐贴住值,值左对齐并受宽度约束。

    原来用制表位排:模型名一长就顶出右边距,标签与值还会挤成一坨(制表位在
    部分渲染器里根本不生效)。表格是最笨也最稳的做法。
    """
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    label_w, value_w = 3.6, 12.4
    table = doc.add_table(rows=len(rows), cols=2)
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    _set_table_width(table, label_w + value_w)

    # 信息块只用上下两条细线框住,不画格线 —— 有格线就像一张数据表,
    # 而这里只是"这份报告属于哪个工况"。
    borders = OxmlElement("w:tblBorders")
    for edge, sz in (("top", 4), ("bottom", 4), ("left", 0), ("right", 0)):
        node = OxmlElement(f"w:{edge}")
        if sz:
            node.set(qn("w:val"), "single")
            node.set(qn("w:sz"), str(sz))
            node.set(qn("w:color"), meta["accent"])
        else:
            node.set(qn("w:val"), "none")
            node.set(qn("w:sz"), "0")
        borders.append(node)
    table._tbl.tblPr.insert_element_before(
        borders, "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook"
    )

    table.columns[0].width = Cm(label_w)
    table.columns[1].width = Cm(value_w)

    for row, (label, value) in zip(table.rows, rows, strict=False):
        lab_cell, val_cell = row.cells
        lab_cell.width = Cm(label_w)
        val_cell.width = Cm(value_w)
        for cell, text, bold, color, align in (
            (lab_cell, label, False, "7F7F7F", WD_ALIGN_PARAGRAPH.RIGHT),
            (val_cell, str(value or ""), True, "262626", WD_ALIGN_PARAGRAPH.LEFT),
        ):
            para = cell.paragraphs[0]
            para.alignment = align
            para.paragraph_format.space_before = Pt(0)
            para.paragraph_format.space_after = Pt(0)
            para.paragraph_format.line_spacing = 1.25
            _font(para.add_run(text), Pt(9.5), bold).font.color.rgb = _rgb(color)


def _cover(
    doc,
    case: dict,
    kind: str = "all",
    title: str | None = None,
    book: str | None = None,
    extra_rows=None,
) -> None:
    """封面:双色带 + 抬头 + 主标题 + 主题色横线 + 册别 + 工况信息块 + 生成时间。

    `title` / `book` / `extra_rows` 让别的导出(如管道表)复用同一张封面 ——
    版式只有一处可改,不会两册长得不一样。
    """
    from docx.enum.table import WD_ROW_HEIGHT_RULE, WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    meta = kind_meta(kind)
    title = title or meta["title"]
    book = book or meta["book"]

    _band(doc, [(meta["accent"], 0.30), (meta["light"], 0.07)])

    kicker = doc.add_paragraph()
    kicker.alignment = WD_ALIGN_PARAGRAPH.CENTER
    kicker.paragraph_format.space_before = Pt(140)
    kicker.paragraph_format.space_after = Pt(0)
    head = _font(kicker.add_run("CASEBOOK · 流程模拟数据校核"), Pt(8.5), False)
    head.font.color.rgb = _rgb("8C8C8C")
    _tracked(head, 30)  # 1.5 pt 疏排:够拉开,又不至于读成"C A S E B O O K"

    _gap(doc, 8)
    _para(doc, title, Pt(27), True, WD_ALIGN_PARAGRAPH.CENTER, space_after=0, name="黑体").runs[
        0
    ].font.color.rgb = _rgb(meta["accent"])

    # 主标题下的短横线:宽度固定 4.2cm,不随内容伸缩,位置由表格居中决定
    _gap(doc, 30)
    line = doc.add_table(rows=1, cols=1)
    line.autofit = False
    line.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_width(line, 4.2)
    cell = line.rows[0].cells[0]
    cell.width = Cm(4.2)
    _shade(cell, meta["accent"])
    # 行高必须定死:不设的话行高跟着段落走(正文 1.22 倍行距),
    # 一条 1 mm 的装饰线会撑成 1 cm 的实心色块
    line.rows[0].height = Cm(0.10)
    line.rows[0].height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
    para = cell.paragraphs[0]
    para.paragraph_format.space_before = Pt(0)
    para.paragraph_format.space_after = Pt(0)
    para.paragraph_format.line_spacing = Pt(1)
    _font(para.add_run(""), Pt(1))

    _gap(doc, 16)
    # 合订本时 book 只是「衡算报告」,与主标题重复 —— 只在分册时才打在副标题位
    if book and book != title and kind != "all":
        _para(doc, book, Pt(12), False, WD_ALIGN_PARAGRAPH.CENTER, space_after=0).runs[
            0
        ].font.color.rgb = _rgb("595959")
        _gap(doc, 6)
    _para(
        doc,
        "（依据本工具工况快照生成 · 非工程设计依据）",
        Pt(9),
        False,
        WD_ALIGN_PARAGRAPH.CENTER,
        space_after=0,
    ).runs[0].font.color.rgb = _rgb("8C8C8C")

    _gap(doc, 78)
    rows = [
        ("工况", case.get("model_name") or ""),
        ("工况号", f"#{case.get('id')}"),
        ("读取时间", (case.get("read_at") or "").replace("T", " ")),
        ("Aspen 版本", case.get("aspen_version") or ""),
        (
            "收敛判定",
            {"ok": "通过", "warning": "告警", "error": "报错"}.get(
                case.get("severity") or "", case.get("severity") or ""
            ),
        ),
        ("流股 / 设备", f"{case.get('stream_count') or 0} / {case.get('block_count') or 0}"),
    ]
    rows.extend(extra_rows or ())
    _cover_meta(doc, rows, meta)

    _gap(doc, 56)
    stamp = _para(
        doc,
        f"生成时间:{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        Pt(8.5),
        False,
        WD_ALIGN_PARAGRAPH.CENTER,
        space_after=0,
    )
    stamp.runs[0].font.color.rgb = _rgb("A6A6A6")
    doc.add_page_break()
