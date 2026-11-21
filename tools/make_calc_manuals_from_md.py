"""从 docs/*.md 渲染两份《计算说明书》.docx。

md 是这两份说明书的唯一正文源；本脚本只做版式渲染，不再维护第二套正文。
样式函数复用 make_calc_manuals 的排版层（封面、目录域、页眉页脚、标题、表格）。

运行: python -m tools.make_calc_manuals_from_md
输出: docs/casebook-pipe-calc-manual.docx、docs/casebook-pump-calc-manual.docx
目录是 Word 域，生成后需在 Word 里更新域（或由交付流程刷新）。
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document

from tools import make_calc_manuals as mcm

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

BOOKS = {
    "pipe": {
        "title": "管路选型计算说明书",
        "book": "管路专业 · 计算册",
        "rows": [
            ("适用功能", "管道页 · 每条流股的管径、壁厚与等级号"),
            ("计算依据", "GB/T 20801.1—2025 承压公式 · GB/T 17395-2024 尺寸与偏差"),
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
        "note": "本说明书解释算法口径，不替代标准原文与工程设计审核",
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
        return floors
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
    mcm.table(doc, headers, fixed, widths, caption=caption)


def render(kind: str, md_path: Path) -> Path:
    book = BOOKS[kind]
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    doc = Document()
    mcm._page_setup(doc)
    mcm._cover(doc, kind, book["title"], book["book"], book["rows"], note=book["note"])
    mcm._toc(doc, kind)
    mcm._body(doc, kind, f"v{mcm.__version__}　{book['title']}")

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
            joined = [row for row in quote if row]
            if not joined:
                continue
            body = " ".join(joined)
            if body.lstrip("*").startswith("注：") or body.startswith("**注："):
                mcm.note(doc, body)
            elif re.search(r"[=≈]", body) and "：" not in body.split("=")[0][-6:]:
                mcm.formula(doc, "\n".join(joined))
            else:
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
