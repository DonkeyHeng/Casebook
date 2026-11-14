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
from app.design import stream_table
from app.store import db

TITLE = "物料与能量衡算报告"

# 一册一主题色:物料册冷蓝、能量册暖砖红。黑白打印时靠封面「册别」与章节标题
# 的文字区分,颜色只是辅助 —— 评委多半会打印出来看。
KINDS: dict[str, dict] = {
    "mass": {"title": "物料衡算报告", "book": "物料衡算册",
             "accent": "1F4E79", "light": "DCE6F1", "zebra": "F7FAFD"},
    "energy": {"title": "能量衡算报告", "book": "能量衡算册",
               "accent": "9C3B23", "light": "F7E3DD", "zebra": "FDF8F6"},
    "all": {"title": "物料与能量衡算报告", "book": "衡算报告",
            "accent": "1F4E79", "light": "DCE6F1", "zebra": "F7FAFD"},
}
KIND_KEYS = tuple(KINDS)
_ACTIVE_KIND = KINDS["all"]


def kind_meta(kind: str) -> dict:
    return KINDS.get(kind) or KINDS["all"]

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
    """按位数取整;None / 非数字一律留空(与 family_export 同口径)。"""
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

def _setup(doc, case: dict, kind: str = "all") -> None:
    """A4 版面 + 页眉(册别/工况)+ 页脚(页码)。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    meta = kind_meta(kind)
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
    left.set(qn("w:sz"), str(eighth_pt))     # 1/8 pt 为单位:18 ≈ 2.25 pt
    left.set(qn("w:color"), color)
    borders.append(left)
    # tcBorders 必须排在 shd 之前,否则 Word 宽容、WPS/旧版会丢
    tc_pr.insert_element_before(borders, "w:shd", "w:noWrap", "w:tcMar",
                                "w:textDirection", "w:tcFitText", "w:vAlign",
                                "w:hideMark")


def _shade(cell, hex_fill: str) -> None:
    """给单元格铺底色(超差行标红用)。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_pr.insert_element_before(shd, "w:noWrap", "w:tcMar", "w:textDirection",
                                "w:tcFitText", "w:vAlign", "w:hideMark")


def _three_line(doc, headers, rows, widths, caption="", font_pt=None,
                header_extra=None, status_col=None, span_rows=()):
    """三线表:顶线 1.5 pt、表头下线 0.75 pt、底线 1.5 pt,无竖线无内部横线。

    python-docx 没有内置三线表样式,只能直接写 `w:tblBorders`。
    `w:sz` 的单位是 1/8 pt,所以 1.5 pt = 12、0.75 pt = 6。
    """
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    size = Pt(font_pt) if font_pt else Pt(9.5)
    meta = _ACTIVE_KIND
    if caption:
        cap = doc.add_paragraph()
        cap.paragraph_format.space_before = Pt(8)
        cap.paragraph_format.space_after = Pt(2)
        cap.paragraph_format.keep_with_next = True   # 表题不单独留在页底
        # 表题前的小色块:黑白打印时是灰块,仍能分出"这是表题"
        _font(cap.add_run("■ "), Pt(7), True).font.color.rgb = _rgb(meta["accent"])
        _font(cap.add_run(caption), Pt(10), True)

    head_rows = 2 if header_extra else 1
    table = doc.add_table(rows=head_rows + len(rows), cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    borders = OxmlElement("w:tblBorders")
    # 注意:这里别用 size 当循环变量 —— 它会把上面算好的字号覆盖掉,
    # 表现是"设了 8pt 却还是默认字号"(踩过一次)
    # 8pt 表体配 1.5pt 线太重;顶/底 1.0pt(sz=8)、表头下 0.5pt(sz=4)
    for edge, sz in (("top", 8), ("bottom", 8), ("left", 0), ("right", 0),
                     ("insideH", 0), ("insideV", 0)):
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
        borders, "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook",
        "w:tblCaption", "w:tblDescription")

    def _row_flag(row, tag: str) -> None:
        """给整行加一个 w:trPr 开关(tblHeader / cantSplit)。"""
        tr_pr = row._tr.get_or_add_trPr()
        node = OxmlElement(f"w:{tag}")
        node.set(qn("w:val"), "true")
        tr_pr.append(node)

    def fill(cell, text, bold=False, align=WD_ALIGN_PARAGRAPH.CENTER,
             color=None):
        para = cell.paragraphs[0]
        para.alignment = align
        para.paragraph_format.space_after = Pt(0)
        para.paragraph_format.line_spacing = 1.0   # 继承 1.22 会把长表撑高一倍
        run = _font(para.add_run("" if text is None else str(text)), size, bold)
        if color:
            run.font.color.rgb = _rgb(color)

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
        fill(header_row.cells[index], head, True)
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

    stripe = 0
    for row_index, row in enumerate(rows, start=head_rows):
        _row_flag(table.rows[row_index], "cantSplit")
        cells = table.rows[row_index].cells
        cells_data, bold, fill_hex = (row if isinstance(row, tuple)
                                      else (row, False, None))
        # 只有第一格有字 = 组分分段行(整行浅色带,把两个数据块分开)
        is_group = bool(cells_data) and not any(
            str(v).strip() for v in cells_data[1:])
        for index, value in enumerate(cells_data):
            text = "" if value is None else str(value)
            # 首列(位号/流股/项目)左对齐;纯文字格(相态、状态)居中;
            # 其余数值右对齐 —— 否则"液相"贴着右边,看着像数据错位
            if index == 0:
                align = WD_ALIGN_PARAGRAPH.LEFT
            elif text and not any(ch.isdigit() for ch in text):
                align = WD_ALIGN_PARAGRAPH.CENTER
            else:
                align = WD_ALIGN_PARAGRAPH.RIGHT
            # 成片的 0.000 灰化:数字不动,只让眼睛跳过
            tone = "A6A6A6" if text in ("0.00", "0.0", "0.000", "0.0000") else None
            fill(cells[index], text, bold, align, tone)

        if fill_hex:                      # 超差行:灰底 + 首列黑竖边
            for cell in cells:
                _shade(cell, "E8E8E8")
            _left_bar(cells[0], 18)
        elif is_group:                    # 分组行:主题浅色带
            for cell in cells:
                _shade(cell, meta["light"])
        elif stripe % 2 == 1:             # 斑马纹:宽表不串行
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

    # 分组行横着合并(组分 · 摩尔流量 / 组分 · 质量流量):合并后要把宽度一起
    # 改成整行宽,否则文字仍在首列那一格里折行。索引是「rows 列表里的下标」,
    # 表格行号要加上表头行数(head_rows):有分组表头时是 2,没有时是 1。
    for index in span_rows or ():
        row = table.rows[head_rows + index]
        cells = row.cells
        if len({id(c._tc) for c in cells}) != len(headers):
            continue                       # 已经合并过,跳过
        merged = cells[0].merge(cells[len(headers) - 1])
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
    return table


# ---------------------------------------------------------------- 各章内容

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
        pg, "w:cols", "w:formProt", "w:vAlign", "w:noEndnote", "w:titlePg",
        "w:textDirection", "w:bidi", "w:rtlGutter", "w:docGrid",
        "w:printerSettings", "w:sectPrChange")

    header = section.header
    header.is_linked_to_previous = False
    head = header.paragraphs[0]
    head.alignment = WD_ALIGN_PARAGRAPH.LEFT
    stops = head.paragraph_format.tab_stops
    stops.add_tab_stop(Cm(16.0), WD_TAB_ALIGNMENT.RIGHT)
    _font(head.add_run(f"Casebook · {meta['book']}"), Pt(8.5), False)
    _font(head.add_run(f"\t#{case.get('id')}　{case.get('model_name') or ''}"),
          Pt(8.5), False).font.color.rgb = _rgb("808080")
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


def _cover(doc, case: dict, kind: str = "all") -> None:
    """封面:主题色带 + 主标题 + 册别 + 工况信息块。"""
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    meta = kind_meta(kind)

    band = doc.add_table(rows=1, cols=1)
    band.autofit = False
    band.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = band.rows[0].cells[0]
    cell.width = Cm(16.0)
    _shade(cell, meta["accent"])
    band.rows[0].height = Cm(0.45)
    para = cell.paragraphs[0]
    para.paragraph_format.space_after = Pt(0)
    _font(para.add_run(""), Pt(1))

    _para(doc, "", Pt(10), space_after=60)
    _para(doc, meta["title"], Pt(26), True, WD_ALIGN_PARAGRAPH.CENTER,
          space_after=10, name="黑体").runs[0].font.color.rgb = _rgb(meta["accent"])
    _para(doc, meta["book"], Pt(13), False, WD_ALIGN_PARAGRAPH.CENTER,
          space_after=8)
    _para(doc, "（依据本工具工况快照生成 · 非工程设计依据）", Pt(9.5), False,
          WD_ALIGN_PARAGRAPH.CENTER, space_after=80)

    rows = [
        ("工况", case.get("model_name") or ""),
        ("工况号", f"#{case.get('id')}"),
        ("读取时间", case.get("read_at") or ""),
        ("Aspen 版本", case.get("aspen_version") or ""),
        ("收敛判定", {"ok": "通过", "warning": "告警", "error": "报错"}.get(
            case.get("severity") or "", case.get("severity") or "")),
        ("流股 / 设备", f"{case.get('stream_count') or 0} / {case.get('block_count') or 0}"),
    ]
    # 用制表位排信息块,不用表格 —— 表格在部分渲染器里会画出一圈莫名其妙的框线
    from docx.enum.text import WD_TAB_ALIGNMENT

    for label, value in rows:
        para = doc.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.LEFT
        para.paragraph_format.space_after = Pt(5)
        stops = para.paragraph_format.tab_stops
        stops.add_tab_stop(Cm(8.2), WD_TAB_ALIGNMENT.RIGHT)   # 标签右端
        stops.add_tab_stop(Cm(8.6), WD_TAB_ALIGNMENT.LEFT)    # 值左端
        _font(para.add_run(label + "\t"), Pt(10.5), False).font.color.rgb = _rgb("595959")
        # 模型名可能很长:让它自然折到第二行,不要撑出右边距(内容不截断)
        _font(para.add_run(value), Pt(9.5), True)

    _para(doc, "", Pt(10), space_after=44)
    _para(doc, f"生成时间:{datetime.now().strftime('%Y-%m-%d %H:%M')}", Pt(9.5),
          False, WD_ALIGN_PARAGRAPH.CENTER).runs[0].font.color.rgb = _rgb("808080")
    doc.add_page_break()


def _toc(doc, kind: str = "all", has_detail: bool = False) -> None:
    """目录页:按册列出条目(手写目录,不依赖 Word 的 TOC 域)。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    meta = kind_meta(kind)
    _para(doc, "", None, space_after=4)
    _para(doc, "目　录", Pt(16), True, WD_ALIGN_PARAGRAPH.CENTER, space_after=14,
          name="黑体").runs[0].font.color.rgb = _rgb(meta["accent"])
    _para(doc, meta["book"], Pt(9.5), False, WD_ALIGN_PARAGRAPH.CENTER,
          space_after=22).runs[0].font.color.rgb = _rgb("808080")

    entries: list[tuple[str, int]] = [("校核结论摘要", 0)]
    if kind in ("mass", "all"):
        entries += [("一、物料衡算", 0),
                    ("1.1 各工段物料平衡表", 1),
                    ("1.2 全流程物料平衡表", 1),
                    ("1.3 物料消耗与产出汇总", 1)]
    if kind in ("energy", "all"):
        ch = "2" if kind == "all" else "1"
        entries.append((("二" if ch == "2" else "一") + "、能量衡算", 0))
        entries.append((f"{ch}.1 逐设备能量衡算", 1))
        if has_detail:
            entries.append((f"{ch}.2 选中设备的进出口明细", 1))
        base = 3 if has_detail else 2
        entries += [(f"{ch}.{base} 全流程能量汇总", 1),
                    (f"{ch}.{base + 1} 塔的冷凝器 / 再沸器负荷", 1)]
    entries.append(("附注", 0))

    for text, level in entries:
        para = doc.add_paragraph()
        para.paragraph_format.space_after = Pt(8)
        para.paragraph_format.left_indent = Pt(28 * level)
        run = _font(para.add_run(text), Pt(12 if level == 0 else 11), level == 0)
        if level == 0:
            run.font.color.rgb = _rgb(meta["accent"])
    doc.add_page_break()


def _summary_chapter(doc, data: dict, cap: "_Captions", kind: str = "all") -> None:
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
        states = [mass_balance.PASS, mass_balance.FAIL, mass_balance.NO_DATA,
                  mass_balance.TOO_SMALL]
        headers = ["项目", "通过", "超差", "无法校验", "流量过小", "合计"]
        rows = [
            ["工段界区衡算"] + [zone_sum[s] for s in states] + [zone_sum["total"]],
            ["逐设备质量守恒"] + [dev_sum[s] for s in states] + [dev_sum["total"]],
        ]
        _three_line(doc, headers, rows, (3.6, 2.0, 2.0, 2.4, 2.0, 2.0),
                    cap.next("质量守恒校验结果"), status_col=2)

    if kind in ("energy", "all"):
        order = [energy_report.OK, energy_report.BAD, energy_report.NO_DATA]
        by_state = energy["by_state"]
        rows = [[energy_report.STATE_TEXT[s], by_state.get(s, 0)] for s in order]
        rows.append(["不校验（换热器热侧，不单独校验）",
                     by_state.get(energy_report.SKIP, 0)])
        _three_line(doc, ["焓平衡状态", "表内行数"], rows, (7.8, 3.2),
                    cap.next("焓平衡结论分布"), status_col=0)
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
    return [columns[i:i + size] for i in range(0, len(columns), size)]


def _widths(ncols: int):
    """项目 + 单位 + n 个流股列的宽度(cm),合计 16。"""
    rest = 16.0 - 4.0  # 项目 2.2 + 单位 1.8
    each = round(rest / max(ncols, 1), 2)
    return (2.2, 1.8) + (each,) * ncols


def _wide_table(doc, table: dict, cap: "_Captions", chapter: str,
                max_cols: int = 5, title: str = "") -> None:  # noqa: D401
    """一张物料平衡宽表:流股做列、属性与组分做行。

    列多的时候按 `max_cols` 拆成「表 x-1（1）（2）…」,进口与出口永不混在同一块,
    这样任何一块表格横着都读得下去(与设计文档里「全流程物料衡算表(1)(2)」同做法)。
    """
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    segments = [("进口", table.get("in_columns") or []),
                ("出口", table.get("out_columns") or [])]
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
            row("质量流量", "kg/h", [_num(c.get("mass_flow_kgh"), 2) for c in cols],
                True),
            row("摩尔流量", "kmol/h", [_num(c.get("mole_flow_kmolh"), 2) for c in cols]),
            row("体积流量", "m³/h", [_num(c.get("vol_flow_m3h"), 2) for c in cols]),
        ]

        components = table.get("components") or []
        span_rows: list[int] = []
        if components:
            span_rows.append(len(rows))
            rows.append(row("组分 · 摩尔流量 kmol/h", "", [""] * len(cols), True))
            for comp in components:
                rows.append(row(f"　{comp}", "kmol/h",
                                [_num((c.get("mole_comp") or {}).get(comp), 3)
                                 for c in cols]))
        if table.get("has_mass_comp"):
            span_rows.append(len(rows))
            rows.append(row("组分 · 质量流量 kg/h", "", [""] * len(cols), True))
            for comp in components:
                rows.append(row(f"　{comp}", "kg/h",
                                [_num((c.get("mass_comp") or {}).get(comp), 3)
                                 for c in cols]))

        headers = ["项目", "单位", *[c["stream"] for c in cols]]
        _three_line(doc, headers, rows, _widths(len(cols)), caption,
                    font_pt=8.0, header_extra=group, span_rows=span_rows)

    # 表下给该段的合计与误差(界区校验结论就写在这一行,不再单独出小表)
    state_text = mass_balance.STATE_TEXT.get(table.get("state"), "")
    note = (f"Σ进 {_num(table.get('mass_in_kgh'), 2)} kg/h、"
            f"Σ出 {_num(table.get('mass_out_kgh'), 2)} kg/h,"
            f"相对误差 {_percent(table.get('rel_err')) or '—'}"
            f"（容差 {mass_balance.TOL_REL:.1%}），判定：{state_text}")
    if not table.get("has_mass_comp"):
        note += "。组分质量流量未读取（重读一次工况后自动出现），表中只给摩尔流量"
    para = _para(doc, note, None, False, WD_ALIGN_PARAGRAPH.LEFT,
                 space_after=8)
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
    para.paragraph_format.space_before = Pt(36)
    para.paragraph_format.space_after = Pt(36)
    run = para.add_run(f"【{text}：图片插入处】")
    _font(run, Pt(18), True, "黑体")
    run.font.color.rgb = None

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


def _mass_chapter(doc, data: dict, cap: "_Captions", chapter: str = "1") -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    cap.start(chapter)
    label = "一" if chapter == "1" else "二"
    wide = data["wide"]
    _heading(doc, f"{label}、物料衡算", 1)
    _para(doc, ("表中流股做列、属性与组分做行：进口列组是该工段确认的进界流股，"
                "出口列组是出界流股。表下给该段 Σ进 / Σ出 与相对误差——"
                f"相对误差 ≤ {mass_balance.TOL_REL:.1%} 判通过，"
                f"进出合计都低于 {mass_balance.MIN_FLOW_KGH:g} kg/h 时不判定。"
                "组分行按 Aspen 里的组分 ID 原样列出，不做中文映射。"),
          None, False, WD_ALIGN_PARAGRAPH.LEFT)
    if data.get("auto_boundary"):
        _para(doc, "本报告未载入人工确认的工段边界，进 / 出界流股按设备名段号自动推导，"
                   "建议在「衡算报告」页确认后重新生成。", None, False,
              WD_ALIGN_PARAGRAPH.LEFT)

    _heading(doc, f"{chapter}.1 各工段物料平衡表", 2)
    if not wide["zones"]:
        _para(doc, "（本工况没有可出的工段：可能被人工删除，或设备位号里没有段号；"
                   "此时只出下面的全流程表）")
    for zone in wide["zones"]:
        _heading(doc, zone["label"], 3)
        _figure_slot(doc, f"{zone['label']}流程图")
        _wide_table(doc, zone, cap, chapter, title=f"{zone['label']}物料平衡表")

    _heading(doc, f"{chapter}.2 全流程物料平衡表", 2)
    _para(doc, ("进口列 = 没有上游的流股（外部进料 / 公用工程补水），"
                "出口列 = 没有下游的流股（产品、副产、三废）。"
                "公用工程流股（冷却水、蒸汽等）也在其中，是否算入原料见 1.3 的说明。"),
          None, False, WD_ALIGN_PARAGRAPH.LEFT)
    _figure_slot(doc, "全流程图")
    _wide_table(doc, wide["overall"], cap, chapter, title="全流程物料平衡表")

    _heading(doc, f"{chapter}.3 物料消耗与产出汇总", 2)
    summary = wide["summary"]
    hours = summary["hours_per_year"]
    _para(doc, f"按年运行 {hours:g} h 折算年量。原料与产品的归类由人确认；"
               "未确认时按「外部进料 / 出界流股」原样列出，不擅自贴标签。",
          None, False, WD_ALIGN_PARAGRAPH.LEFT)

    rows = [[item["stream"], item["phase"],
             _num(item["mass_flow_kgh"], 2),
             _num(item["t_per_year"], 2)] for item in summary["feed"]]
    _three_line(doc, ["流股", "相态", "质量流量 kg/h", "年量 t/a"], rows,
                (4.6, 2.4, 4.4, 4.4), cap.next("外部进料（原料 / 公用工程）"))

    rows = [[item["stream"], item["phase"],
             _num(item["mass_flow_kgh"], 2),
             _num(item["t_per_year"], 2)] for item in summary["outlet"]]
    _three_line(doc, ["流股", "相态", "质量流量 kg/h", "年量 t/a"], rows,
                (4.6, 2.4, 4.4, 4.4), cap.next("出界流股（产品 / 副产 / 三废）"))


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
    if h_in is not None and h_out is not None and \
            (energy_row or {}).get("state") in ("ok", "bad"):
        error = round((h_out - h_in) - ((q or 0.0) + (w or 0.0)), 3)
    return {"w_kw": w, "q_kw": q, "h_in_kw": h_in, "h_out_kw": h_out,
            "error_kw": error}


def _energy_chapter(doc, data: dict, cap: "_Captions", chapter: str = "2") -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    cap.start(chapter)
    label = "一" if chapter == "1" else "二"
    energy = data["energy"]
    _heading(doc, f"{label}、能量衡算", 1)
    _para(doc, ("以设备为基本单位，连续系统的能量平衡写作 Q + W = ΣHout − ΣHin："
                "Q 是设备热负荷、W 是输入系统的机械功（泵 / 压缩机的轴功率）、"
                "ΣHin、ΣHout 是进出口各物料焓之和，单位 kW。"
                "焓值与焓平衡判定取自工况库里已有的结论，本报告不重算；"
                "「偏差」列 = (ΣHout − ΣHin) − (Q + W)，即闭合误差。"
                "换热器只校冷侧（整体 ΔH 恒约等于 0），故冷、热两侧各一行；"
                "塔的对照值是首级（冷凝器）+ 末级（再沸器）负荷，反应器的对照值是反应热 QCALC；"
                "带换热伴流的多股反应器净 ΔH 会被伴流抵消，判「无法校验」而不是超差。"),
          None, False, WD_ALIGN_PARAGRAPH.LEFT)

    # 设备 → 能量行(换热器两侧各一行)
    by_block: dict[str, list[dict]] = {}
    for row in energy["devices"]:
        by_block.setdefault(row["block"], []).append(row)

    blocks = {b["name"]: b for b in data["blocks"]}

    _heading(doc, f"{chapter}.1 逐设备能量衡算", 2)
    for group in energy["groups"]:
        rows = []
        for row in group["devices"]:
            block = blocks.get(row["block"]) or {}
            bal = _balance_row(block, row, row)
            state_text = energy_report.STATE_TEXT.get(row["state"], row["state"])
            bad = row["state"] == energy_report.BAD
            rows.append(([row["block"], row["side"] or "—", row["type"],
                          _num(bal["h_in_kw"], 2), _num(bal["h_out_kw"], 2),
                          _num(bal["q_kw"], 2), _num(bal["w_kw"], 2),
                          _num(bal["error_kw"], 2), state_text],
                         False, "FDE8E8" if bad else None))
        _three_line(doc, ["设备", "侧", "类型", "ΣHin kW", "ΣHout kW",
                          "Q 热负荷 kW", "W 机械功 kW", "偏差 kW", "判定"],
                    rows, (2.1, 0.8, 1.9, 2.1, 2.1, 1.8, 1.8, 1.5, 1.3),
                    cap.next(f"{group['label']}能量衡算"), font_pt=8.0)

    picked = data.get("picked")
    if picked:
        _heading(doc, f"{chapter}.2 选中设备的进出口明细", 2)
        _para(doc, "只对本次勾选的设备出明细：先流股表（进出口流股的温压与流量、焓流），"
                   "再平衡表（W / Q / ΣHin / ΣHout / 偏差），与设计文档的写法一致。",
              None, False, WD_ALIGN_PARAGRAPH.LEFT)
        for name in picked:
            rows = by_block.get(name) or []
            if not rows:
                continue
            block = blocks.get(name) or {}
            _heading(doc, f"{name}（{block.get('type') or '未知类型'}）", 3)
            body = []
            for row in rows:
                body.append((["进口焓流 kW", row["side"] or "—", _num(row["h_in_kw"], 2)],
                             False, None))
                body.append((["出口焓流 kW", row["side"] or "—", _num(row["h_out_kw"], 2)],
                             False, None))
            _three_line(doc, ["项目", "侧", "数值"], body, (6.0, 3.0, 6.0),
                        cap.next(f"{name} 焓流"), font_pt=8.0)
            bal = _balance_row(block, rows[0], rows[0])
            _three_line(doc, ["W kW", "Q kW", "ΣHin kW", "ΣHout kW", "偏差 kW"],
                        [[_num(bal["w_kw"], 2), _num(bal["q_kw"], 2),
                          _num(bal["h_in_kw"], 2), _num(bal["h_out_kw"], 2),
                          _num(bal["error_kw"], 2)]],
                        (3.0, 3.0, 3.4, 3.4, 3.0), cap.next(f"{name} 能量平衡"),
                        font_pt=8.0)

    _heading(doc, f"{chapter}.{3 if picked else 2} 全流程能量汇总", 2)
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
        _heading(doc, f"{chapter}.{4 if picked else 3} 塔的冷凝器 / 再沸器负荷", 2)
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
        "能量衡算的 ΔH 与焓平衡状态取自库内已有结论，未重算。换热器只校验冷侧；"
        "塔按「首级冷凝器 + 末级再沸器」的净热负荷校验（93 座塔实测中间级 duty 全为 0，"
        "遇中间换热器时该口径会偏小）；反应器按反应热 QCALC 校验，只对单进单出成立，"
        "带换热伴流的多股反应器记「无法校验」。",
        "塔负荷来源：块字段优先，缺失时用逐级剖面首 / 末级回退，"
        "并在明细表的「来源」列标出。",
        "相对误差口径不区分「漏流股」与「求解器残差」，量级小时属于正常收敛误差，"
        "量级大（10% 以上）才值得查模型。",
        "本报告由 Casebook 自动生成，用于内部校核与存档，不能作为工程设计依据。",
    ]
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

    global _ACTIVE_KIND
    if kind not in KINDS:
        kind = "all"
    _ACTIVE_KIND = kind_meta(kind)

    doc = Document()
    cap = _Captions()
    _setup(doc, data["case"], kind)
    _cover(doc, data["case"], kind)
    _toc(doc, kind, bool(data.get("picked")))
    _body_section(doc, data["case"], kind)   # 正文另起一节:页眉页脚只打在正文
    _summary_chapter(doc, data, cap, kind)

    # 章号=表号:分册时那一册的正文从「一」起;合订本依次 一 / 二
    if kind in ("mass", "all"):
        _mass_chapter(doc, data, cap, "1")
        if kind == "all":
            doc.add_page_break()
    if kind in ("energy", "all"):
        _energy_chapter(doc, data, cap, "2" if kind == "all" else "1")
    doc.add_page_break()
    _notes_chapter(doc, data)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def build_report(conn, case_id: int, parts=None, picked=None,
                 kind: str = "all") -> tuple[bytes, str]:
    """一步生成 .docx,返回 `(字节, 文件名)`。

    缺 python-docx 时抛 `ImportError`;工况不存在时抛 `LookupError`。
    """
    data = build_data(conn, case_id, parts, picked)
    return render(data, kind), filename(case_id, kind)
