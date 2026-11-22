"""从 docs/*.md 渲染两份《计算说明书》.docx。

md 是这两份说明书的唯一正文源；本脚本只做版式渲染，不再维护第二套正文。
样式函数复用 make_calc_manuals 的排版层（封面、目录域、页眉页脚、标题、表格）。

运行: python -m tools.make_calc_manuals_from_md
输出: docs/casebook-pipe-calc-manual.docx、docs/casebook-pump-calc-manual.docx
目录是 Word 域，生成后需在 Word 里更新域（或由交付流程刷新）。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from tools import calc_manual_math as cmm
from tools import make_calc_manuals as mcm
from tools import manual_appendix_b as mab

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

BOOKS = {
    "pipe": {
        "title": "管路选型计算说明书",
        "book": "管路专业 · 计算册",
        "rows": [
            ("适用功能", "管道页 · 每条流股的管径、壁厚与等级号"),
            ("计算依据", "GB/T 20801.1-2025 承压公式 · GB/T 17395-2024 尺寸与偏差"),
            ("数据来源", "Aspen 工况快照 + 用户导入的国标 CSV + 人工参数"),
            ("复算方式", "按本文公式、常数与顺序逐项复算"),
        ],
        "note": "本说明书解释算法口径，不替代标准原文与工程设计审核",
    },
    "pump": {
        "title": "泵计算说明书",
        "book": "泵专业 · 计算册",
        "rows": [
            ("适用功能", "泵面板 · NPSH、设计扬程、轴功率与建议电机"),
            (
                "计算依据",
                "达西公式 · Colebrook/顾毓珍 · GB/T 20801（管径与壁厚侧）"
                " · API 610 / GB/T 3215（电机分段裕量参考）",
            ),
            ("数据来源", "Aspen 工况快照 + pipe_run 管段记录 + 人工参数"),
            ("复算方式", "按本文公式、常数与顺序逐项复算"),
        ],
        "note": "本说明书解释算法口径，不替代厂家型谱与工程设计审核",
    },
}

WIDTH_TOTAL_CM = 15.6


_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def _cells(line: str) -> list[str]:
    """切分表格行；`\\|` 是单元格内的竖线，不参与切分，输出时还原成 `|`。

    正则式（如 `0[0-9]{2}Cr\\d|0?[0-9]Cr\\d`）与数学竖线（`|下偏差|`）都含竖线，
    直接 `split("|")` 会把一格拆成两格、后面的列整体串位。
    """
    body = line.strip().strip("|")
    return [
        cell.strip().replace("\\|", "|").replace("`", "") for cell in _CELL_SPLIT_RE.split(body)
    ]


def _widths(headers: list[str], rows: list[list[str]]) -> list[float]:
    """按各列最长内容分配宽度：CJK 记 2 个字符宽，最窄 2.2 cm。

    表里常出现 `temperature_c` 这类无空格标识符，列太窄会被 Word 从中间截断，
    所以除了按比例分配，还给每列设一个能容纳最长单词的下限。
    """
    raw: list[float] = []
    floors: list[float] = []
    for column in range(len(headers)):
        texts = [headers[column]] + [row[column] for row in rows]
        longest = max(sum(2 if ord(ch) > 127 else 1 for ch in text) for text in texts)
        raw.append(max(longest, 6))
        token = max(
            (len(part) for text in texts for part in re.split(r"[\s/，。；、（）]+", text)),
            default=1,
        )
        floors.append(max(2.3, token * 0.21))
    spare = WIDTH_TOTAL_CM - sum(floors)
    if spare <= 0:
        # 列数多时「每列至少 2.3 cm」会超过版心（如 10 列 ×2.3 = 23 cm），
        # 表格会越出右边距、末列被裁掉。此时按内容长度重新分配：
        # 长文本列多给，短数字列少给，总宽强制回到版心内。
        classes = []
        for column in range(len(headers)):
            texts = [headers[column]] + [row[column] for row in rows]
            longest_text = max(sum(2 if ord(ch) > 127 else 1 for ch in text) for text in texts)
            classes.append(
                3.2
                if longest_text >= 24
                else 2.4
                if longest_text >= 14
                else 1.5
                if longest_text >= 6
                else 1.1
            )
        scale = WIDTH_TOTAL_CM / sum(classes)
        return [value * scale for value in classes]
    weights = [max(0.0, value - floor) for value, floor in zip(raw, floors, strict=True)]
    total = sum(weights) or 1.0
    return [floor + spare * weight / total for floor, weight in zip(floors, weights, strict=True)]


def _render_table(doc, block: list[str], caption: str) -> None:
    headers = _cells(block[0])
    rows = [_cells(line) for line in block[2:]]
    rows = [row for row in rows if any(cell for cell in row)]
    fixed: list[list[str]] = []
    for source in rows:
        cells = source[: len(headers)]
        cells += [""] * (len(headers) - len(cells))
        fixed.append(cells)
    widths = _widths(headers, fixed)
    # 9 列以上的宽表降到 7.5 pt：10 列表均宽只有 1.5 cm 左右，8.5 pt 会把数字拆行
    font_pt = 7.5 if len(headers) >= 9 else 8.5
    mcm.table(doc, headers, fixed, widths, caption=caption, font_pt=font_pt)


_VERSION_RE = re.compile(r"^-\s*版本\s*v([0-9.]+)\s*·\s*(\d{4}-\d{2}-\d{2})")


def _cover_texts(lines: list[str]) -> tuple[str, str, str, str]:
    """从 md 头部取版本行、说明行与口径行，封面与页眉不再吃脚本里的旧版本号/旧文案。"""
    version, date = "", ""
    tagline = ""
    note = ""
    for line in lines[:15]:
        match = _VERSION_RE.match(line.strip())
        if match and not version:
            version, date = match.group(1), match.group(2)
            continue
        if not note and line.strip().startswith("- 说明："):
            note = line.strip().split("：", 1)[1].strip()
            continue
        if not tagline and line.strip().startswith(">"):
            body = line.strip().lstrip(">").strip()
            if body.startswith("面向需要复算"):
                tagline = body
    if not version:
        version = mcm.__version__
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    if not tagline:
        tagline = "面向需要复算与核对的使用者 · 口径以本工具当前版本的实际计算为准"
    return f"版本 v{version}　·　{date}　·　Casebook", tagline, note, version


def render(kind: str, md_path: Path) -> Path:
    book = BOOKS[kind]
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    stamp, tagline, note, version = _cover_texts(lines)

    doc = Document()
    # 打开文档时让 Word 自动更新 TOC 域，避免目录页只显示「按 F9」的提示
    update = OxmlElement("w:updateFields")
    update.set(qn("w:val"), "true")
    doc.settings.element.append(update)
    mcm._page_setup(doc)
    mcm._cover(
        doc,
        kind,
        book["title"],
        book["book"],
        book["rows"],
        note=note or book["note"],
        tagline=tagline,
        stamp=stamp,
    )
    mcm._toc(doc, kind)
    mcm._body(doc, kind, f"v{version}　{book['title']}")

    index = 0
    # 封面与目录由样式层生成：跳过 md 的标题块，直到「## 目录」及其条目结束。
    while index < len(lines) and not lines[index].startswith("## 目录"):
        index += 1
    caption = ""
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped.startswith("## 目录"):
            index += 1
            while index < len(lines) and not lines[index].startswith("## "):
                index += 1
            continue

        if stripped == mab.MARKER:
            # 附录 B 表由 tools/manual_appendix_b.py 统一下发：两册条目、顺序恒等。
            # 反引号在表格里按旧渲染口径去掉，与正文表格的 `代码` 处理保持一致。
            rows = [[cell.replace("`", "") for cell in row] for row in mab.ROWS]
            mcm.table(
                doc,
                list(mab.COLUMNS),
                rows,
                _widths(list(mab.COLUMNS), rows),
            )
            index += 1
            continue

        if stripped.startswith("```"):
            index += 1
            code: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            index += 1
            mcm.mono(doc, code)
            continue

        match = re.match(r"^(#{2,4})\s*(.+)$", stripped)
        if match:
            level = len(match.group(1)) - 1
            mcm.h(doc, match.group(2).replace(" ", "　", 1), level)
            index += 1
            continue

        if stripped.startswith("|"):
            block: list[str] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                block.append(lines[index].strip())
                index += 1
            _render_table(doc, block, caption)
            caption = ""
            continue

        if stripped.startswith(">"):
            quote: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote.append(lines[index].strip()[1:].strip())
                index += 1
            # `>` 空行是块内分段：公式与下方说明各成一段，不能拼成一行
            segments: list[list[str]] = []
            current: list[str] = []
            for row in quote:
                if row:
                    current.append(row)
                elif current:
                    segments.append(current)
                    current = []
            if current:
                segments.append(current)
            for segment in segments:
                body = " ".join(segment)
                if body.lstrip("*").startswith("注：") or body.startswith("**注："):
                    mcm.note(doc, body)
                    continue
                formula_line = next((row for row in segment if cmm.formula_parts(row)), None)
                if formula_line:
                    mcm.formula(doc, formula_line)
                    rest = [row for row in segment if row != formula_line]
                    if rest:
                        mcm.note(doc, " ".join(rest))
                else:
                    # 含等号但未登记为正式公式的，多为口径说明；按注文排，不强行居中当公式
                    mcm.note(doc, body)
            continue

        if stripped.startswith("- "):
            items: list[str] = []
            while index < len(lines) and lines[index].strip().startswith("- "):
                items.append(lines[index].strip()[2:].strip())
                index += 1
            mcm.bullets(doc, items)
            continue

        if not stripped:
            index += 1
            continue

        if stripped.startswith("**表 ") and stripped.endswith("**"):
            caption = stripped.strip("*")
            index += 1
            continue

        mcm.p(doc, stripped)
        index += 1

    target = DOCS / md_path.with_suffix(".docx").name
    doc.save(target)
    return target


def main() -> int:
    for kind, name in (
        ("pipe", "casebook-pipe-calc-manual.md"),
        ("pump", "casebook-pump-calc-manual.md"),
    ):
        print(render(kind, DOCS / name).name, "written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
