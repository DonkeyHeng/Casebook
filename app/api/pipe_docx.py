"""管道选型一览表 .docx —— 「管道」页的 Word 导出。

版式与衡算报告**共用一套骨架**:封面、页眉页脚、章节标题、三线表全部来自
`report_export`,这里只负责"这张表有几列、每列多宽、装什么数"。

为什么要复用而不是各写一份
--------------------------
早先这个文件是手写 `zip + document.xml` 的,为的是少一个运行时依赖。但
`python-docx` 本来就是 `requirements.txt` 里的既有依赖、衡算报告天天在用,
省不掉;留着两套渲染器的真实代价是"封面要照着抄一遍、改一处忘一处"。
两册并排打印时颜色、页边距、页眉高度对不上,比少一个依赖难看得多。

表本身的规范没变
----------------
九列与设计院附录二那张表逐列对齐(管路编号 / 温度 / 压力 / 状态 / 体积流量 /
公称直径 / 壁厚 / 管道材料 / 管道类型),三线表、无竖线 —— 只是把字号从五号
降到小五(9 pt),列宽按内容重新分过:五号字塞进 16 cm 的九列,每一列都会折行。
"""

from __future__ import annotations

import io
import time

from app.api import report_export as rx
from app.design import pipe as pipe_calc
from app.store import db

TITLE = "管道选型一览表"

#: 表头写成「名称|单位」,`_three_line` 会把单位拆到第二行(7.5 pt 灰)。
#: 单位挤在名称后面时,"体积流量m3/h" 这种 7 字表头在 2 cm 列里必然折行。
HEADERS = [
    "管路编号",
    "温度|℃",
    "压力|kPa",
    "状态",
    "体积流量|m³/h",
    "公称直径|mm",
    "壁厚|mm",
    "管道材料",
    "管道类型",
]

#: 九列合计 16.00 cm(= A4 页宽 21 − 左右各 2.5)。
#: 按 9 pt 实测最宽内容分配:管路编号要装下 18 个字符的 `PGL-0101-100-L1E-C`
#: (18 × 1.59 ≈ 28.6 mm,加内边距 3 mm),其余按"表头不折行"倒推。
WIDTHS = (4.20, 1.15, 1.25, 1.70, 1.80, 1.65, 1.00, 1.65, 1.60)

PHASE_TEXT = {"liquid": "液体", "gas": "气体", "two": "气液共存"}

#: 相态用颜色做第二信号(第一信号永远是文字本身):与流程图、塔剖面的口径一致,
#: 液=蓝、气=橙、共存=紫。灰度打印时全退成灰,不影响判读。
PHASE_TONE = {"液体": "1F5C8B", "气体": "B45309", "气液共存": "6B4C9A"}


def _num(value, digits: int) -> str:
    """数值 → 文本:去掉尾零,空值留空。

    空值留空不补 0 —— 库里 None 是"没读到",写 0 会被当成真值(与衡算报告同口径)。
    """
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        text = f"{float(value):.{digits}f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def _pipe_tag(row: dict) -> str:
    """管路编号 = 介质代号-流股号-DN-等级号(等级号自带绝热代号,如 -H / -C)。"""
    return f'{row["medium_code"]}-{row["stream"]}-{_num(row["dn"], 0)}-{row["grade_code"]}'


def _notes(missing_count: int) -> list[str]:
    """表下的口径注。数字与 `app/design/pipe.py` 的默认口径一一对应。

    只分三条:每条回答一个问题(壁厚怎么来的 / 两相怎么办 / 口径与类型怎么定)。
    拆成六条时整块注读起来像密集条款,反而没人看。
    """
    notes = [
        "注 1:设计压力按 max(操作表压×1.15,操作表压+0.15 MPa,1.0 MPa) 取;壁厚依 "
        "GB/T 20801.3 计算,计入壁厚负偏差与腐蚀裕量后圆整到该外径的标准壁厚序列,"
        "并按管道的公称压力(PN)复核最小壁厚。",
        "注 2:气液两相流按均相动压头 ρv² ≤ 2000 Pa 限速,且内径不小于上游纯液相进料 —— "
        "闪蒸后骤缩是段塞与振动的直接来源。",
        "注 3:常规物流公称直径不小于 DN15(DN10 仅作仪表 / 采样预留);公称直径大于 DN600 时"
        "管道类型由「无缝」改为「焊接」,壁厚按常用焊接管序列圆整,需另按 GB/T 3091 / "
        "GB/T 9711 复核;管号末尾 -H 为保温(设计温度 ≥ 50 ℃)、-C 为保冷(≤ 0 ℃)。",
    ]
    if missing_count:
        notes.append(
            f"注 4:另有 {missing_count} 条流股因内径超出现有档位未选型,见表后清单。"
        )
    return notes


def build_pipe_docx(conn, case_id: int) -> tuple[bytes, str]:
    """生成管道选型一览表 .docx,返回 (字节, 建议文件名)。

    Raises:
        LookupError: 没有这个工况。
        ImportError: 没装 python-docx。
        pipe_calc.TablesNotReady: 标准表没录,DN / 壁厚算不出来 —— 不造数。
    """
    case = db.get_case(conn, case_id)
    if case is None:
        raise LookupError(case_id)
    case = dict(case)

    streams = db.read_streams(conn, case_id)
    blocks = db.read_blocks(conn, case_id)
    connections = db.read_connections(conn, case_id)
    pres = pipe_calc.build(
        [dict(s) for s in streams], [dict(b) for b in blocks], [dict(c) for c in connections]
    )

    by_name = {s["name"]: dict(s) for s in streams}

    def value_of(name: str, key: str, scale: float = 1.0):
        v = (by_name.get(name) or {}).get(key)
        return v * scale if isinstance(v, (int, float)) else None

    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt
    except ImportError as exc:  # pragma: no cover - 取决于运行环境
        raise ImportError("没有安装 python-docx,无法导出管道 Word") from exc

    rx.activate("pipe")
    doc = Document()
    rx._setup(doc, case, "pipe")

    rows = len(pres["rows"])
    welded = sum(1 for r in pres["rows"] if (r.get("pipe_type") or "无缝") == "焊接")
    cold = sum(1 for r in pres["rows"] if str(r.get("grade_code") or "").endswith("-C"))
    extra = [("管路条数", f"{rows} 条")]
    if welded:
        extra.append(("其中焊接管", f"{welded} 条(DN > 600)"))
    if cold:
        extra.append(("其中保冷管", f"{cold} 条(-C)"))
    rx._cover(doc, case, "pipe", extra_rows=extra)
    rx._body_section(doc, case, "pipe")

    rx._heading(doc, TITLE, 1)
    rx._para(
        doc,
        f"本表按流股逐条给出公称直径、壁厚与管路等级号,共 {rows} 条。"
        "口径以现行 GB/T 20801 与项目管道等级规定为准;表中数值由工况快照现算,"
        "供设计校核与材料统计使用,不作为施工图依据。",
        None,
        False,
        WD_ALIGN_PARAGRAPH.LEFT,
    )

    table_rows = []
    for r in pres["rows"]:
        name = r["stream"]
        table_rows.append(
            [
                _pipe_tag(r),
                _num(value_of(name, "temperature_c"), 1),
                _num(value_of(name, "pressure_bar", 100.0), 1),
                PHASE_TEXT.get(r["phase"], r["phase"]),
                _num(r["vol_flow_m3h"], 2),
                _num(r["dn"], 0),
                _num(r["wall_mm"], 1),
                r["material"] or "",
                r.get("pipe_type") or "无缝",
            ]
        )

    table = rx._three_line(
        doc,
        HEADERS,
        table_rows,
        WIDTHS,
        caption="表 1  管道选型明细(按流股)",
        font_pt=9.0,
        center_cols=(2, 3, 5, 6, 7, 8),
    )

    # 相态上色:文字已经写明"气体/液体",颜色只是让人一眼分出两相段
    for row in table.rows[1:]:
        text = row.cells[3].text.strip()
        tone = PHASE_TONE.get(text)
        if tone:
            rx._tint(row.cells[3], color=tone, bold=True)
        if row.cells[8].text.strip() == "焊接":
            rx._tint(row.cells[8], color=rx.KINDS["pipe"]["accent"], bold=True)

    rx._gap(doc, 10)
    for line in _notes(len(pres["missing"])):
        para = rx._para(doc, line, Pt(7.5), False, WD_ALIGN_PARAGRAPH.LEFT, space_after=2)
        para.paragraph_format.line_spacing = 1.35
        # 悬挂缩进:换行后对齐到「注 N:」之后,而不是弹回左边界 ——
        # 否则一条注折成两行时,第二行看起来像另一条注
        para.paragraph_format.left_indent = Pt(24)
        para.paragraph_format.first_line_indent = Pt(-24)
        for run in para.runs:
            run.font.color.rgb = rx._rgb("595959")

    if pres["missing"]:
        rx._heading(doc, "未选型流股", 2)
        rx._para(
            doc,
            "以下流股的计算内径超出现有标准管档位,需并联或专项设计;"
            "不进上表 —— 表里每一行都必须是确定的选型结果。",
            None,
            False,
            WD_ALIGN_PARAGRAPH.LEFT,
        )
        rx._three_line(
            doc,
            ["流股", "未选型原因"],
            [[m["stream"], m["reason"]] for m in pres["missing"]],
            (3.2, 12.8),
            font_pt=9.0,
        )

    buffer = io.BytesIO()
    doc.save(buffer)
    stamp = time.strftime("%Y%m%d_%H%M")
    return buffer.getvalue(), f"Casebook_pipes_case{case_id}_{stamp}.docx"
