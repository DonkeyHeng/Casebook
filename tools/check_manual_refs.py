"""校验计算说明书 Markdown 的章节/表格交叉引用是否全部落地。

只检查正文与代码映射附录，不检查文末「修订对照清单」中作为历史文本引用的旧编号。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = {
    "pipe": ROOT / "docs" / "casebook-pipe-calc-manual.md",
    "pump": ROOT / "docs" / "casebook-pump-calc-manual.md",
}
TITLES = {"泵计算说明书": "pump", "管路选型计算说明书": "pipe"}

SECTION_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+){0,2})\s*节")
CHAPTER_RE = re.compile(r"第\s*(\d+)\s*章")
TABLE_RE = re.compile(r"(?<!附)表\s*(\d+-\d+)")
APPENDIX_TABLE_RE = re.compile(r"附表\s*([A-Z]-\d+)")
CROSS_DOC_RE = re.compile(r"《([^》]+)》\s*((?:\d+(?:\.\d+){0,2}[\s、/]*)+)节")
CROSS_DOC_TABLE_RE = re.compile(
    r"《([^》]+)》\s*(?:(\d+(?:\.\d+){0,2})\s*节\s*[与和及、/]?\s*)?表\s*(\d+-\d+)"
)
HEADING_RE = re.compile(r"^#{2,4}\s+(\d+(?:\.\d+){0,2})(?:\s|　)")
CAPTION_RE = re.compile(r"^\*\*(表|附表)\s+([0-9A-Z]+-\d+)")


def body_only(text: str) -> list[str]:
    """截掉文末修订对照清单，保留正文与代码映射附录。"""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("## 附录 B") or line.startswith("## 附录 C"):
            return lines[:index]
    return lines


def inventory(lines: list[str]) -> tuple[set[str], set[str]]:
    sections: set[str] = set()
    tables: set[str] = set()
    for line in lines:
        if match := HEADING_RE.match(line):
            sections.add(match.group(1))
        if match := CAPTION_RE.match(line):
            tables.add(match.group(2))
    return sections, tables


def references(lines: list[str]) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    in_fence = False
    for number, line in enumerate(lines, 1):
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        local_line = line
        for match in CROSS_DOC_TABLE_RE.finditer(local_line):
            target = TITLES.get(match.group(1))
            if target is None:
                found.append((number, "unknown_doc", match.group(1)))
            else:
                if match.group(2):
                    found.append((number, f"section@{target}", match.group(2)))
                found.append((number, f"table@{target}", match.group(3)))
            local_line = local_line.replace(match.group(0), "")
        for match in CROSS_DOC_RE.finditer(local_line):
            target = TITLES.get(match.group(1))
            if target is None:
                found.append((number, "unknown_doc", match.group(1)))
            else:
                for section in re.findall(r"\d+(?:\.\d+){0,2}", match.group(2)):
                    found.append((number, f"section@{target}", section))
            local_line = local_line.replace(match.group(0), "")
        for match in SECTION_RE.finditer(local_line):
            found.append((number, "section", match.group(1)))
        for match in CHAPTER_RE.finditer(local_line):
            found.append((number, "chapter", match.group(1)))
        for match in TABLE_RE.finditer(local_line):
            found.append((number, "table", match.group(1)))
        for match in APPENDIX_TABLE_RE.finditer(local_line):
            found.append((number, "app_table", match.group(1)))
    return found


def main() -> int:
    failed = 0
    cache: dict[str, tuple[set[str], set[str]]] = {}
    for key, path in DOCS.items():
        cache[key] = inventory(body_only(path.read_text(encoding="utf-8")))

    for key, path in DOCS.items():
        lines = body_only(path.read_text(encoding="utf-8"))
        sections, tables = cache[key]
        refs = references(lines)
        broken: list[str] = []
        for number, kind, value in refs:
            if kind == "section":
                ok = value in sections
            elif kind == "section@pipe":
                ok = value in cache["pipe"][0]
            elif kind == "section@pump":
                ok = value in cache["pump"][0]
            elif kind == "table@pipe":
                ok = value in cache["pipe"][1]
            elif kind == "table@pump":
                ok = value in cache["pump"][1]
            elif kind == "chapter":
                ok = value in sections
            elif kind in ("table", "app_table"):
                ok = value in tables
            else:
                ok = False
            if not ok:
                broken.append(f"L{number} {kind}={value}")

        print(
            f"{path.name}: 章节 {len(sections)}、表格 {len(tables)}、"
            f"引用 {len(refs)}、断链 {len(broken)}"
        )
        for item in broken:
            print(f"  - {item}")
        failed += len(broken)

    if failed:
        print(f"FAILED: {failed} 处断链")
        return 1
    print("PASS: 两份说明书交叉引用 100% 命中")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
