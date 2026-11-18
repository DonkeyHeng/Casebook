"""管道选型一览表 .docx —— 「管道」页的 Word 导出。

版式与衡算报告**共用一套骨架**:封面、页眉页脚、章节标题、三线表全部来自
`docx_kit`,这里只负责"这张表有几列、每列多宽、装什么数"。

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

from app.export import docx_kit as kit
from app.design import pipe as pipe_calc
from app.store import db

TITLE = "管道选型一览表"

#: 表头写成「名称|单位」,`_three_line` 会把单位拆到第二行(7.5 pt 灰)。
#: 单位挤在名称后面时,"体积流量m3/h" 这种 7 字表头在 2 cm 列里必然折行。
#: 校核备注(2026-11-18 第二轮):状态提示与风险预警集中放这一列,
#: 管路编号保持纯净,可直接作 BOM 材料统计 / PID 匹配的唯一主键。
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
    "校核备注",
]

#: 十列合计 16.00 cm(= A4 页宽 21 − 左右各 2.5)。管路编号要装下
#: `PGL-0204-1-125-L1E-H`(20 字符 × 1.59 ≈ 32 mm,加内边距);备注列留 2.45 cm,
#: 长提示自然折行 —— 宁可备注高一点,不让主键列折行。
WIDTHS = (3.55, 1.05, 1.10, 1.30, 1.55, 1.35, 0.95, 1.50, 1.20, 2.45)


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
    """管路编号 = 介质代号-流股号-DN-等级号(等级号自带绝热代号,如 -H / -C)。

    2026-11-18 第二轮:保持**纯净**,不拼任何校核标记 —— 这列是 BOM 材料统计与
    PID 匹配的唯一主键;状态提示全部移到「校核备注」列(`_check_remark`)。
    """
    return f'{row["medium_code"]}-{row["stream"]}-{_num(row["dn"], 0)}-{row["grade_code"]}'


def _check_remark(row: dict) -> str:
    """校核备注:状态提示与风险预警集中一列(§17.8),按轻重排序,分号隔开。"""
    marks: list[str] = []
    dn = row.get("dn") or 0
    if row.get("hot_vacuum") and dn >= 400:
        marks.append("高温真空:外压失稳复核")
    if row.get("vacuum_stiffener"):
        marks.append("需设外压加强圈,严禁全真空抽吸")
    elif row.get("vacuum"):
        marks.append("真空管:外压失稳复核")
    if row.get("hp_ss_upgraded"):
        marks.append("高压不锈钢升R1E(按[σ]^t公式壁厚)")
    if row.get("stratified_risk"):
        marks.append("DN≥800两相分层/波状流风险")
    if row.get("cavitation_limited"):
        marks.append("防汽蚀限速0.8 m/s")
    if row.get("nonstandard_no"):
        marks.append("非标准编号,待人工确认")
    # §19.2 深真空专项(≤10 kPa):防闪蒸 + 高温弹性模量折减
    if row.get("deep_vacuum"):
        marks.append("高真空(≤10kPa):严控吸入压降防闪蒸,建议全焊透/高真空法兰防漏气")
    if row.get("deep_hot_vacuum"):
        marks.append("高温深真空:弹性模量折减,严格核算外压失稳支撑跨距")
    # §19.3 DN1000 5% 容差:内径与超径百分比都给出,便于回查原流股
    if row.get("oversize_tolerated"):
        d_i = float(row.get("inner_d_mm") or 0.0)
        pct = pipe_calc.dn1000_oversize_pct(d_i)
        marks.append(f"流速略超上限(内径{d_i:.1f}mm),建议复核压降")
        marks.append(f"计算内径略超DN1000上限(约{pct:.1f}%),需复核实际压降")
    # §19.4 深冷 / 高温应力与韧性提示(按操作温度)。两组措辞都列出,避免漏读。
    if row.get("low_temp"):
        marks.append("低温工况:复核焊缝低温韧性及低温螺栓选型")
        marks.append("深冷工况:复核焊缝低温韧性及低温螺栓选型")
    if row.get("high_temp"):
        marks.append("高温管线:需做柔性应力分析(热膨胀补偿)")
        marks.append("高温管线(≥300℃):需做热膨胀柔性应力分析")
    return ";".join(marks)



def _notes(res: dict) -> list[str]:
    """表下的口径注。数字与 `app/design/pipe/calc.py` / `pipe_data.py` 默认口径一一对应。

    每条回答一个问题;2026-11-18 第二轮修订见 docs/pipe-selection-plan.md §17.8:
    校核标记移「校核备注」列、R1E 公式化壁厚、汽蚀/-H 触发收紧、真空专项校核。
    """
    note = [
        "注 1:设计压力按 max(操作表压×1.15,操作表压+0.15 MPa,1.0 MPa) 取;壁厚依 "
        "GB/T 20801.3 计算,计入壁厚负偏差与腐蚀裕量后圆整到该外径的标准壁厚序列,"
        "并按管道的公称压力(PN)复核最小壁厚。",
        "注 2:绝压 < 95.0 kPa(表压 < −6.3 kPa)归真空管,等级代号用 V(非 H,免与高压混淆);"
        "1 bar = 100 kPa 的常压微压降流股仍按常压,不判真空。"
        "内压按最小壁厚,并另按外压 0.1 MPa 失稳算最小壁厚,实际支撑跨距需专项复核;"
        "负压 / 真空液相吸入管线按防闪蒸限速(≤ 0.8 m/s)放大管径;"
        "操作 ≥ 250 ℃ 且 DN ≥ 400 的真空管壁厚下限 10 mm,DN ≥ 700 真空焊接管需设外压加强圈;"
        "绝压 ≤ 10 kPa 的高真空管线严控吸入压降防闪蒸,建议全焊透 / 高真空法兰防漏气,"
        "同时 ≥ 250 ℃ 时按弹性模量折减严格核算外压失稳支撑跨距。"
        "气液两相流按均相动压头 ρv² ≤ 2000 Pa 限速,内径不小于上游纯液相进料;"
        "DN ≥ 800 两相流另标分层 / 波状流风险,DN1000 焊接管壁厚不小于 10 mm。",
        "注 3:设计温度 = 操作温度 + 20 ℃,操作 ≥ 45 ℃(设计 ≥ 65 ℃)加保温代号 -H(≤ 0 ℃ 为 -C)。"
        "DN65 / DN125 为非优先口径,自动上圆整到 DN80 / DN150;常规物流不小于 DN15;"
        "DN > 600 为焊接管,另按 GB/T 3091 / GB/T 9711 复核。"
        "计算内径 996–1045 mm(DN1000 内径 996 mm 的 +5% 容差)仍选 DN1000 焊接管 × 10 mm,"
        "在备注标注流速略超上限并建议复核压降,不再踢入未选型清单。"
        "液相 80–105 ℃ 且绝压 95–115 kPa 的吸入侧按防汽蚀限速 0.8 m/s"
        "(泵排出口及升压管线不适用);绝压 4.7–5.6 MPa 的不锈钢统一升 R1E,"
        "壁厚按 t = PD/(2[σ]^t·E_j+P)+C 动态计算(小口径不小于 3 mm),防止高温薄壁倒挂。",
        "注 4:管路编号(介质代号-流股号-DN-等级号)为唯一主键,可直接用于 BOM 材料统计与"
        "PID 匹配;状态提示与风险预警集中在「校核备注」列。已按 Aspen 流股类型过滤非物流"
        "(热量流 Q1–Q4 / 功流),它们不进主表也不进表末未选型清单,故流股序号存在跳号;"
        "非四位数字的编号保留原名并在备注标注,待人工确认。"
        "操作 ≤ −20 ℃ 复核焊缝低温韧性及低温螺栓选型;操作 ≥ 300 ℃ 做热膨胀柔性应力分析。",
    ]
    if res["flagged"]:
        names = "、".join(str(m["stream"]) for m in res["flagged"])
        note.append(f"注 5:{names} 为零流量 / 备用或放空管线,管径需人工确认,不计入上述选型。")
    if res["missing"] and not res["flagged"]:
        note.append(f"注 5:另有 {len(res['missing'])} 条流股未选型,见表后清单。")
    return note



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

    kit.activate("pipe")
    doc = Document()
    kit._setup(doc, case, "pipe")

    rows = len(pres["rows"])
    welded = sum(1 for r in pres["rows"] if (r.get("pipe_type") or "无缝") == "焊接")
    cold = sum(1 for r in pres["rows"] if str(r.get("grade_code") or "").endswith("-C"))
    extra = [("管路条数", f"{rows} 条")]
    if welded:
        extra.append(("其中焊接管", f"{welded} 条(DN > 600)"))
    if cold:
        extra.append(("其中保冷管", f"{cold} 条(-C)"))
    kit._cover(doc, case, "pipe", extra_rows=extra)
    kit._body_section(doc, case, "pipe")

    kit._heading(doc, TITLE, 1)
    kit._para(
        doc,
        f"本表按流股逐条给出公称直径、壁厚与管路等级号,共 {rows} 条。"
        "口径以现行 GB/T 20801 与项目管道等级规定为准;表中数值由工况快照现算,"
        "供设计校核与材料统计使用,不作为施工图依据。",
        None,
        False,
        WD_ALIGN_PARAGRAPH.LEFT,
    )

    # 模拟收敛状态联动(§17.8):告警/失败的快照,物性仅供参考
    severity = str(case.get("severity") or "").strip().lower()
    if severity in ("warning", "error"):
        warn_text = (
            "警告：当前模拟快照存在收敛告警，物性数据仅供参考，"
            "正式选型前需消除收敛告警"
            if severity == "warning"
            else "警告：当前模拟快照收敛失败，物性数据不可信，"
            "严禁据此开展正式选型"
        )
        warn_para = kit._para(doc, warn_text, None, True, WD_ALIGN_PARAGRAPH.LEFT, space_after=6)
        for run in warn_para.runs:
            run.font.color.rgb = kit._rgb("C00000")

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
                _check_remark(r),
            ]
        )


    table = kit._three_line(
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
            kit._tint(row.cells[3], color=tone, bold=True)
        if row.cells[8].text.strip() == "焊接":
            kit._tint(row.cells[8], color=kit.KINDS["pipe"]["accent"], bold=True)

    kit._gap(doc, 10)
    for line in _notes(pres):
        para = kit._para(doc, line, Pt(7.5), False, WD_ALIGN_PARAGRAPH.LEFT, space_after=2)
        para.paragraph_format.line_spacing = 1.35
        # 悬挂缩进:换行后对齐到「注 N:」之后,而不是弹回左边界 ——
        # 否则一条注折成两行时,第二行看起来像另一条注
        para.paragraph_format.left_indent = Pt(24)
        para.paragraph_format.first_line_indent = Pt(-24)
        for run in para.runs:
            run.font.color.rgb = kit._rgb("595959")

    if pres["missing"]:
        kit._heading(doc, "未选型流股", 2)
        kit._para(
            doc,
            "以下流股不进上表 —— 表里每一行都必须是确定的选型结果:零流量 / 备用或"
            "放空管线需人工确认管径;计算内径超出标准管档位的,按相态分类给出出路 ——"
            "纯气相可按低压风管(矩形/圆形薄壁风道)专项设计,气液两相需多管并联设计。",
            None,
            False,
            WD_ALIGN_PARAGRAPH.LEFT,
        )
        kit._three_line(
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
