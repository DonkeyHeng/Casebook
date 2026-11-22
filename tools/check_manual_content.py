"""计算说明书的内容级自检：抓"描述与代码行为不符"。

`check_manual_refs` 只证明编号存在，常量测试只证明几个数字没漂。真正的
错误多数是"句子合法、但描述的代码行为不对"，例如少了 `Output` 一层、
单位系数写错、把某条分支的结论安到另一条上。这个检查器把能机械判定的
部分逐条比对源码事实：

1. 路径   节点路径每段都必须是源码里出现过的段，且 Streams/Blocks 后必须紧跟 Output
2. 单位   "单位×系数" 的声明必须等于 units 表 / 粘度换算的实际系数
3. 符号   反引号里的技术标识符必须在 app/ 里真实出现
4. 边界   R1E 的 5.67 / 8.80 MPa 必须与默认参数反解结果一致
5. 旧口径 已修过的错误说法进黑名单，防止"改回去"
6. 载体   docx 的关键路径与目录必须跟 md 同步

    python -m tools.check_manual_content        # 有问题返回 1

只读文件，不修改文档。
"""

from __future__ import annotations

import difflib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from tools import manual_facts as facts

ROOT = Path(__file__).resolve().parents[1]
DOCS = {
    "pipe": ROOT / "docs" / "casebook-pipe-calc-manual.md",
    "pump": ROOT / "docs" / "casebook-pump-calc-manual.md",
}
DOCX = {
    "pipe": ROOT / "docs" / "casebook-pipe-calc-manual.docx",
    "pump": ROOT / "docs" / "casebook-pump-calc-manual.docx",
}

FULL_PATH = re.compile(r"\\Data\\(?:Streams|Blocks)\\[^\s，。；、（）()|`]+")
BARE_PATH = re.compile(
    r"\\?(?:STRM_UPP|PBUB|PL|MUMX|VISC|VISCOSITY|MU_LIQ|MU_VAP|LIQUID-VISC|Output)"
    r"\\[^\s，。；、（）()|`]+"
)
UNIT_PAIR = re.compile(
    r"([A-Za-z][A-Za-z0-9°³²·/()\-]{0,20})(?:×|\s)\s*"
    r"([0-9]+(?:\.[0-9]+)?(?:[eE][-−]?[0-9]+)?)"
)
BACKTICK = re.compile(r"`([^`\n]+)`")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")

# 已经修过、又被改回来的说法。每修完一类错误就往这里加一条：这是零成本的回归网。
STALE = (
    ("泵块VFLOW与泵块密度", "check_kw 的密度走 2.3 节口径，不是纯泵块密度"),
    ("管段行已有vol_flow_m3h", "应为「管道页构建结果行」，pipe_run 不存流量"),
    ("20#只在碳钢式命中", "20# 在预置表 explicit 短路返回 B，碳钢式里只是冗余项"),
    ("不继续扩到8.80MPa", "5.67–8.80 MPa 本就自然落 R，不需要扩强制带"),
    ("Cr前是两位数字且Cr后不接数字", "12Cr1MoV 的 Cr 后接 1，卡点是前导非 0 的两位数"),
    ("（见9.1节）", "表 10-1 的空 grade_code 行应引 9.1–9.2 节"),
)

# 同一事实在两册都要出现；缺一册说明只改了一处。
SHARED = (
    ("没有可用的管内径", "退化分支的原因文案"),
    ("标准表缺失", "409 与泵面板透传的同一事实"),
    ("待确认", "空 grade_code 的页面口径"),
    ("C1=0", "自定义规格的负偏差语义"),
)


@dataclass(frozen=True)
class Issue:
    """一条可定位的问题。file 用相对仓库的路径，方便直接跳转。"""

    file: str
    line: int
    rule: str
    message: str

    def render(self) -> str:
        return f"{self.file}:{self.line} [{self.rule}] {self.message}"


def _norm(text: str) -> str:
    """去掉空白，便于比对（全角空格、普通空格都不算差异）。"""
    return re.sub(r"\s+", "", text.replace("\u3000", " "))


def _clean_path(raw: str) -> str:
    """去掉句末省略号与包裹符号，避免把人类写法当成非法段。"""
    text = raw.strip("`").rstrip()
    for tail in ("…", "...", "\\"):
        if text.endswith(tail):
            text = text[: -len(tail)]
    return text


def _closest(norm: str) -> str:
    candidates = [rel.lower() for rel in facts.stream_rel_paths()]
    match = difflib.get_close_matches(norm.lstrip("\\"), candidates, n=1)
    return match[0] if match else "（无候选）"


def _check_full_path(raw: str, line_no: int, name: str) -> list[Issue]:
    norm = facts.normalize_path(_clean_path(raw))
    segs = [seg for seg in norm.split("\\") if seg]
    problems: list[str] = []
    if segs[:1] != ["data"] or len(segs) < 4:
        problems.append("必须以 \\Data\\Streams 或 \\Data\\Blocks 开头")
    elif segs[1] not in ("streams", "blocks"):
        problems.append(f"第二段应为 Streams/Blocks，实际 {segs[1]}")
    elif segs[2] != facts.PLACEHOLDER:
        problems.append("流股/泵块名应写成 <流股> / <泵块名> 占位")
    elif segs[3] != "output":
        problems.append(f"占位符之后必须紧跟 Output，实际 {segs[3]}")
    else:
        nodes = facts.node_names()
        unknown = [seg for seg in segs[4:] if seg not in nodes]
        if unknown:
            problems.append(f"节点段不在源码里：{', '.join(unknown)}")
    if not problems:
        return []
    return [Issue(name, line_no, "path", f"{raw} —— {'；'.join(problems)}")]


def _check_bare_path(raw: str, line_no: int, name: str) -> list[Issue]:
    norm = facts.normalize_path(_clean_path(raw))
    segs = [seg for seg in norm.split("\\") if seg]
    if segs and segs[0] == "output":
        segs = segs[1:]
    if not segs:
        return []
    nodes = facts.node_names()
    unknown = [seg for seg in segs if seg not in nodes]
    if unknown:
        return [Issue(name, line_no, "path", f"{raw} —— 节点段不在源码里：{', '.join(unknown)}")]
    if len(segs) == 1:
        return []  # 只写容器名（如 `\Output\STRM_UPP\`），不构成完整路径
    joined = "\\".join(segs)
    rels = [rel.lower() for rel in facts.stream_rel_paths()]
    if any(rel == joined or rel.endswith("\\" + joined) or joined.endswith(rel) for rel in rels):
        return []
    return [
        Issue(
            name,
            line_no,
            "path",
            f"{raw} —— 不是任何候选路径的后缀，最近候选 {_closest(joined)}",
        )
    ]


def _unit_cells(body: str) -> list[tuple[int, str]]:
    """取表头含「单位」或「换算」的列里的单元格。

    只看这类列：正文散落的数字（如「(p_out − p_in)×10⁵」）不是换算声明，
    取值范围列（「0–100000」「0.0001–1e6」）也不是换算系数。
    """
    rows = list(enumerate(body.splitlines(), 1))
    separator = re.compile(r"\|(?:\s*:?-{2,}:?\s*\|)+")
    out: list[tuple[int, str]] = []
    header: list[str] | None = None
    for index, (line_no, line) in enumerate(rows):
        stripped = line.strip()
        if not stripped.startswith("|"):
            header = None
            continue
        nxt = rows[index + 1][1].strip() if index + 1 < len(rows) else ""
        if separator.fullmatch(nxt):
            header = [cell.strip() for cell in stripped.strip("|").split("|")]
            continue
        if separator.fullmatch(stripped) or header is None:
            continue
        values = [cell.strip() for cell in stripped.strip("|").split("|")]
        for column, cell in enumerate(values):
            if column < len(header) and ("单位" in header[column] or "换算" in header[column]):
                out.append((line_no, cell))
    return out


def _check_units(line: str, line_no: int, name: str) -> list[Issue]:
    issues: list[Issue] = []
    factors = facts.unit_factors()
    for match in UNIT_PAIR.finditer(line):
        raw_unit, raw_value = match.group(1), match.group(2)
        keys = facts.unit_alias(raw_unit.rstrip(")"))
        declared = float(raw_value.replace("−", "-"))
        hits = [value for key in keys for value in factors.get(key, ())]
        if not hits:
            continue  # 不在任何换算表里，无从判断，宁可放过
        if any(facts.same_factor(declared, actual) for actual in hits):
            continue
        issues.append(
            Issue(
                name,
                line_no,
                "unit",
                f"「{raw_unit} {raw_value}」与换算表不符：实际 {sorted(hits)[0]:g}",
            )
        )
    return issues


def _check_symbols(line: str, line_no: int, name: str) -> list[Issue]:
    issues: list[Issue] = []
    known = facts.symbols()
    for span in BACKTICK.findall(line):
        if "\\" in span or "/" in span or span.endswith((".py", ".csv", ".md", ".json", ".db")):
            continue
        for ident in IDENT.findall(span):
            if ident[:1].isupper():
                continue
            if "_" not in ident and "." not in ident:
                continue
            leaf = ident.split(".")[-1]
            if leaf not in known:
                issues.append(Issue(name, line_no, "symbol", f"`{ident}` 在 app/ 源码里找不到"))
    return issues


# 修订记录（版本记录 / 修订对照清单）引用的是「修订前」的原话，旧口径规则要跳过它；
# 逐条对照已外置到 docs/calc-manual-changelog.md，正文里只剩「版本记录」小表。
CHANGELOG_TITLE = "版本记录"


def _presplit(text: str, title: str) -> str:
    """切掉标题含 `title` 的那个章节及其后全部内容。"""
    pattern = rf"^#{{2,3}}[^\n]*{re.escape(title)}"
    return re.split(pattern, text, maxsplit=1, flags=re.M)[0]


def _stale_scope(text: str) -> str:
    """旧口径规则的扫描范围：改稿记录之外。"""
    return _presplit(text, CHANGELOG_TITLE)


def check_text(kind: str, text: str) -> list[Issue]:
    """对一份 md 正文跑 1–5 组规则（不读 docx）。"""
    name = f"docs/{DOCS[kind].name}"
    issues: list[Issue] = []
    # 旧口径只查正文：修订对照清单本来就要引用"修订前"的原话，不能算残留。
    compact = _norm(_stale_scope(text))
    for line_no, line in enumerate(text.splitlines(), 1):
        masked = line
        for match in FULL_PATH.finditer(line):
            masked = masked.replace(match.group(0), " " * len(match.group(0)))
            issues.extend(_check_full_path(match.group(0), line_no, name))
        for match in BARE_PATH.finditer(masked):
            issues.extend(_check_bare_path(match.group(0), line_no, name))
        issues.extend(_check_symbols(line, line_no, name))
    for line_no, cell in _unit_cells(text):
        issues.extend(_check_units(cell, line_no, name))

    for phrase, why in STALE:
        if _norm(phrase) in compact:
            line_no = next(
                (i for i, row in enumerate(text.splitlines(), 1) if _norm(phrase) in _norm(row)),
                0,
            )
            issues.append(Issue(name, line_no, "stale", f"残留旧口径「{phrase}」：{why}"))

    if re.search(r"保持\s{2,}为空", text):
        line_no = next(
            i for i, row in enumerate(text.splitlines(), 1) if "保持" in row and "为空" in row
        )
        issues.append(Issue(name, line_no, "format", "「保持  为空」多了空格"))

    if kind == "pipe":
        low, high = facts.r1e_boundaries()
        for value in (low, high):
            text_value = f"{value:.2f}"
            if text_value not in text:
                issues.append(
                    Issue(name, 0, "boundary", f"缺少由默认参数反解的边界 {text_value} MPa")
                )
        band_low, band_high = facts.pressure_band()
        band = f"{band_low:g}–{band_high:g}"
        if band not in text:
            issues.append(Issue(name, 0, "boundary", f"缺少 R1E 压力带 {band} MPa"))
    return issues


def _docx_text(path: Path) -> list[str]:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    parts: list[str] = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            parts.append(Paragraph(child, doc).text)
        elif child.tag == qn("w:tbl"):
            for row in Table(child, doc).rows:
                parts.extend(cell.text for cell in row.cells)
    return parts


def check_docx(kind: str, text: str) -> list[Issue]:
    """docx 与 md 的载体一致性：关键路径逐条在、目录覆盖所有标题。"""
    path = DOCX[kind]
    if not path.exists():
        return []  # docx 是发布产物，不入库；CI 里没有就跳过
    name = f"docs/{path.name}"
    parts = _docx_text(path)
    compact = _norm(facts.normalize_path("".join(parts)))
    issues: list[Issue] = []

    for raw in FULL_PATH.findall(text):
        key = _norm(facts.normalize_path(_clean_path(raw)))
        if key and key not in compact:
            issues.append(Issue(name, 0, "docx", f"docx 缺少路径 {raw}"))

    # 目录是 TOC 域，条目由 Word 打开后更新（F9）生成；占位未消除即视为发布产物未完成。
    if any("目录将在 Word 中更新" in part for part in parts):
        issues.append(Issue(name, 0, "docx-toc", "目录仍为占位提示，未在 Word 中更新域"))
        return issues

    toc = [part.rsplit("\t", 1)[0] for part in parts if re.search(r"\t\d+$", part) and part.strip()]
    toc_titles = {_norm(title) for title in toc}
    headings = {
        _norm(row.lstrip("#").strip())
        for row in text.splitlines()
        if re.match(r"^(##|###)\s", row) and _norm(row.lstrip("#").strip()) != "目录"
    }
    missing = sorted(title for title in headings if title and title not in toc_titles)
    for title in missing:
        issues.append(Issue(name, 0, "docx-toc", f"目录未收录「{title}」"))
    return issues


def check_all() -> list[Issue]:
    issues: list[Issue] = []
    texts: dict[str, str] = {}
    for kind, path in DOCS.items():
        if not path.exists():
            issues.append(Issue(f"docs/{path.name}", 0, "missing", "找不到说明书正文"))
            continue
        texts[kind] = path.read_text(encoding="utf-8")
        issues.extend(check_text(kind, texts[kind]))
        issues.extend(check_docx(kind, texts[kind]))
    for kind, text in texts.items():
        compact = _norm(text)
        for phrase, why in SHARED:
            if _norm(phrase) not in compact:
                other = "泵" if kind == "pipe" else "管路"
                issues.append(
                    Issue(
                        f"docs/{DOCS[kind].name}",
                        0,
                        "shared",
                        f"两册共用事实「{phrase}」在本册缺失（{other}册有）：{why}",
                    )
                )
    return issues


# 检查器自己的回归网：每条规则喂一个"必然违规"的样本，必须报出来；再喂一个
# 干净样本，必须不报。规则写成永远通过的样子（比如正则匹配不到东西）时，
# 这里会立刻失败——这才是"自检真的在检"的唯一证据。
MUTATIONS = (
    ("path", "pipe", r"`\Data\Streams\<流股>\VFLOW` 读压降"),
    ("path", "pipe", r"`\Data\Streams\<流股>\Output\NOT_A_NODE\TOTAL` 读压降"),
    ("unit", "pipe", "| 单位 | 数值 |\n| --- | --- |\n| bar×1.5 | — |\n"),
    ("symbol", "pipe", "见 `not_a_real_func` 的实现"),
    ("stale", "pipe", "泵块VFLOW与泵块密度 口径"),
    ("boundary", "pipe", "R1E 边界另说。"),
)


def selftest() -> list[str]:
    """跑一遍必然违规的样本，返回"规则没响"的清单（空 = 检查器可信）。"""
    failures: list[str] = []
    for rule, kind, snippet in MUTATIONS:
        issues = check_text(kind, snippet)
        if not any(item.rule == rule for item in issues):
            failures.append(f"{rule} 规则对样本没有报错：{snippet!r}")
    # 反例：干净的样本不该报路径 / 单位 / 符号错。
    clean = "密度取 `\\Data\\Streams\\<流股>\\Output\\STRM_UPP\\RHO_LIQ\\MIXED\\TOTAL`。"
    clean_units = "| 单位 | 数值 |\n| --- | --- |\n| bar×1 | — |\n"
    for snippet in (clean, clean_units):
        loud = [item for item in check_text("pipe", snippet) if item.rule in ("path", "unit")]
        if loud:
            failures.append(f"干净样本被误报：{snippet!r} → {loud[0].render()}")
    # md-only 章节只是不参与 docx 比对，内容仍要查——否则等于给附录开了免检口。
    appendix = (
        "## 正文\n\n干净段落。\n\n"
        "## 附录 B　版本记录\n\n| 版本 | 说明 |\n| --- | --- |\n"
        "| v9.9.9 | 表里写了 `\\Data\\Streams\\<流股>\\VFLOW` |\n"
    )
    if not any(item.rule == "path" for item in check_text("pipe", appendix)):
        failures.append("md-only 章节里的路径错误没有被查出来")
    return failures


def main() -> int:
    if "--selftest" in sys.argv:
        failures = selftest()
        for line in failures:
            print(f"  - {line}")
        print("SELFTEST PASS" if not failures else "SELFTEST FAILED")
        return 0 if not failures else 1

    issues = check_all()
    if not issues:
        for path in DOCS.values():
            print(f"{path.name}: 路径 / 单位 / 符号 / 边界 / 旧口径 / 载体 全部通过")
        print("PASS: 说明书内容级自检 100% 命中")
        return 0
    print(f"FAILED: {len(issues)} 处内容问题")
    for item in issues:
        print(f"  - {item.render()}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
