"""从 Aspen 模型文件里解析组分字典(纯文本解析,不需要 Aspen 在跑)。

为什么要这条路
--------------
`\\Data\\Components` 的第一层是分组名而不是组分(实测 2026-11-15),CAS 挂在哪个父节点、
字段叫什么,至今没有实测答案。而模型文件(`.bkp` / `.apw`)里存着同样的信息,格式稳定:

```
COMPONENTS MAIN ? \\
  CID = H2O ANAME = H2O OUTNAME = H2O DBNAME1 = "WATER" ANAME1 = "H2O" /
  CID = PDO ANAME = C3H8O2-3 OUTNAME = PDO DBNAME1 = "1,3-PROPANEDIOL" ...
DSET CASN CASN @CASN ( "7732-18-5" "504-63-2" "" ... )
```

两处必须配对读:
  * `COMPONENTS MAIN` 段:每个组分的 `CID` / `DBNAME1` / `ANAME1`,按定义顺序排列;
  * `DSET CASN` 段:一个**按同顺序排列**的 CAS 列表,自定义组分留空 `""`。
第 i 个组分配第 i 个 CAS。

局限(写清楚,免得当成万能)
------------------------
* 只认这个文本布局;Aspen 换版本把段名改了就会解析失败 —— 失败返回空列表,调用方回退到
  "读不到 CAS",页面按约定只显示 Aspen 原名,**不会算错数**。
* 字段被拆行时(`DBNAME1` 与 `=` 之间换行)也能处理,因为这里按记录切分而不是逐行读。
"""

from __future__ import annotations

import re
from pathlib import Path

from ..design import component_names as names

# 一条组分记录:CID = X [ANAME = Y] OUTNAME = Z [TYPE = …] [DBNAME1 = "…"] [ANAME1 = "…"]
# 注意字段之间可能有换行,所以统一把 \r\n 折成空格再匹配。
_RECORD = re.compile(
    r'CID\s*=\s*("?[^"\s/]+"?)\s+'
    r"(?:ANAME\s*=\s*([^\s/]+)\s+)?"
    r'OUTNAME\s*=\s*("?[^"\s/]+"?)'
    r"(?:\s+TYPE\s*=\s*\S+)?"
    r'(?:\s+DBNAME1\s*=\s*"([^"]*)")?'
    r'(?:\s+ANAME1\s*=\s*"([^"]*)")?'
)

# 用正则而不是 find:实测某个 .bkp 里是 "COMPONENTS \r\nMAIN",折成空格后变成两个空格,
# 字符串 "COMPONENTS MAIN" 找不到。段尾同理。
_MAIN_RE = re.compile(r"COMPONENTS\s+MAIN")
_MAIN_END_RE = re.compile(r'COMPONENTS\s+"ADA/PCS"')
# 注意用完整记录名:文件里还有 "IDSET CASN (PROPERTIES)" 这种行,只写 "DSET CASN"
# 会被它的子串提前命中,于是把 "(PROPERTIES)" 当成 CAS 括号 → 一个 CAS 都取不到
_CAS_START = "DSET CASN CASN"


def _strip_quotes(text: str) -> str:
    return str(text or "").strip().strip('"')


def _cas_list(text: str, start: int) -> list[str]:
    """取 DSET CASN 后面括号里的 CAS 列表(位置与组分定义顺序一致)。"""
    open_at = text.find("(", start)
    if open_at < 0:
        return []
    close_at = text.find(")", open_at)
    chunk = text[open_at : close_at if close_at > 0 else open_at + 2000]
    return re.findall(r'"([^"]*)"', chunk)


def parse_text(text: str) -> list[dict]:
    """解析已经读进来的模型文本;返回 [{component, cas, dbname}](保持定义顺序)。"""
    if not text:
        return []
    # 段头和记录都可能被换行拆开(实测某个 .bkp 是 "COMPONENTS \nMAIN ?",直接搜
    # "COMPONENTS MAIN" 会失败),所以先把换行折成空格再找锚点与切记录。
    text = text.replace("\r\n", "\n").replace("\n", " ")
    start_m = _MAIN_RE.search(text)
    if not start_m:
        return []
    start = start_m.start()
    end_m = _MAIN_END_RE.search(text, start_m.end())
    section = text[start : end_m.start() if end_m else start + 40000]

    records = _RECORD.findall(section)
    if not records:
        return []

    # 属性段可能排在 COMPONENTS MAIN 之前,所以整篇找,不只从 start 往后找
    cas_at = text.find(_CAS_START)
    if cas_at < 0:
        cas_at = text.find(_CAS_START, start)
    cas_values = _cas_list(text, cas_at) if cas_at > 0 else []

    out = []
    for i, (cid, _aname, _outname, dbname, _aname1) in enumerate(records):
        raw_cas = cas_values[i] if i < len(cas_values) else ""
        out.append(
            {
                "component": _strip_quotes(cid),
                "cas": names.clean_cas(raw_cas) or None,
                "dbname": _strip_quotes(dbname) or None,
            }
        )
    return out


# 同一个文件可能被读很多次(每点开一条流股都要组分字典),按 (路径, 大小, 修改时间) 缓存;
# 模型改了就自动失效,不会读到上一版的名字。
_CACHE: dict[tuple[str, int, float], list[dict]] = {}


def parse_model_file(path: str) -> list[dict]:
    """读模型文件并解析;文件不存在 / 布局不认识时返回空列表(不抛异常)。

    结果按 (路径, 大小, mtime) 缓存 —— 一次工况读取里 `read_component_meta` 会被调用多次,
    每次都重新解 2–3 MB 文件纯属浪费。返回副本,调用方随便改。

    传入 `.apw` 读不到时,会自动试同名 `.bkp`(见 candidate_files)。
    """
    for candidate in candidate_files(path):
        rows = _parse_one(candidate)
        if rows:
            return rows
    return []


def _parse_one(file: Path) -> list[dict]:
    """解析单个文件;不存在 / 读不了 / 布局不认识都返回空列表。"""
    try:
        stat = file.stat()
        if not file.is_file() or stat.st_size <= 0:
            return []
    except OSError:
        return []
    key = (str(file), stat.st_size, stat.st_mtime)
    cached = _CACHE.get(key)
    if cached is not None:
        return [dict(row) for row in cached]
    try:
        text = file.read_bytes().decode("latin-1", "ignore")
    except OSError:
        return []
    rows = parse_text(text)
    if rows:
        _CACHE.clear()  # 只留最近一份:用户一般就开一个模型
        _CACHE[key] = [dict(row) for row in rows]
    return rows


def candidate_files(path: str) -> list[Path]:
    """要尝试的文件:先给的这个,再同名换扩展名。

    实测 `.apw`(运行工作簿)是纯二进制,里面没有文本式组分明细,`CID =` 一次都不出现;
    而同一份模型的 `.bkp` 有。很多人的目录里两个都在,所以 .apw 读不到时顺手试 .bkp。
    """
    if not path:
        return []
    base = Path(path)
    out = [base]
    for ext in (".bkp", ".apw"):
        alt = base.parent / (base.stem + ext)
        if alt != base:
            out.append(alt)
    return out
