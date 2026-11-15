"""组分中文名:CAS 号 → 中文名(纯查表,无 IO)。

为什么以 CAS 为主键
-------------------
同一个物质在不同模型里 ID 不一样(实测:同一个 1,3-丙二醇 在 case46 里叫 `PDO`,
在 case47 里叫 `1,3-PDO`;同一个丙烯酸 一处叫 `ACOOH`、另一处叫 `CAA`)。
按 ID 查必然串,按 CAS 查才稳定,所以表键是 CAS。

CAS 从哪来
----------
读 Aspen 时从 `\\Data\\Components\\<组分>` 取(见 app/aspen/reader.py 的
read_component_meta),跟工况一起存进 components 表。本模块只负责"CAS 认得谁"。

没有 CAS 的组分
--------------
自定义/伪组分在 Aspen 里没有 CAS(实测 case46 的 `DIMHPA`、`3-HPA`,
case47 的 `ACETAL`、`C6H14O3`、`C6H10O3`),它们查不到中文名,
页面按约定只显示 Aspen 原名、不给悬浮。

怎么加新组分
------------
直接往 CN_BY_CAS 里加一行。加之前建议先确认中文名(DATABANK 名可以对照
Aspen 的 DBNAME1,换算成中文时别把异构体混了)。
"""

from __future__ import annotations

import re

# CAS 的标准形状:2–7 位数字 - 2 位 - 1 位校验码
_CAS_RE = re.compile(r"\d{2,7}-\d{2}-\d")

# CAS 号 → 中文名。只用已验证过的条目;拿不准就不加(宁缺勿错)。
CN_BY_CAS: dict[str, str] = {
    # ---- 主产物 / 中间体 ----
    "504-63-2": "1,3-丙二醇",
    "57-55-6": "1,2-丙二醇",
    "56-81-5": "甘油",
    "107-21-1": "乙二醇",
    "79-10-7": "丙烯酸",
    "107-02-8": "丙烯醛",
    "100-73-2": "2-甲酰基-3,4-二氢-2H-吡喃",
    "868-77-9": "甲基丙烯酸羟乙酯",
    "116-09-6": "羟基丙酮",
    "105-57-7": "乙缩醛",
    # ---- 醇 / 酮 / 酸 ----
    "67-64-1": "丙酮",
    "67-63-0": "异丙醇",
    "71-23-8": "正丙醇",
    "107-18-6": "烯丙醇",
    "64-19-7": "乙酸",
    "108-21-4": "乙酸异丙酯",
    # ---- 轻烃 / 芳烃 ----
    "74-98-6": "丙烷",
    "115-07-1": "丙烯",
    "108-88-3": "甲苯",
    "291-64-5": "环庚烷",
    # ---- 无机 / 公用工程 ----
    "7732-18-5": "水",
    "1333-74-0": "氢气",
    "7782-44-7": "氧气",
    "7727-37-9": "氮气",
    "124-38-9": "二氧化碳",
    "630-08-0": "一氧化碳",
    "7440-37-1": "氩气",
    "7757-79-1": "硝酸钾",
    "7632-00-0": "亚硝酸钠",
}


def extract_cas(value) -> str:
    """从一段文本里抠出 CAS 号;没有就返回空串。

    为什么需要它:Aspen 的别名/属性字段常把 CAS 写成 `CAS 64-19-7`、
    `CAS-64-19-7` 或带括号,直接整体当 CAS 用会一概判成"不是 CAS"。
    """
    match = _CAS_RE.search(str(value or ""))
    return match.group(0) if match else ""


def clean_cas(value) -> str:
    """把读到的 CAS 统一成 `64-19-7` 这种形态;不成立就返回空串。

    先按整体判断(去空白/引号后正好是 CAS),再退回"从文本里抠一个"。
    两条都不成立就当没有 —— 猜出来的 CAS 比没有更坏。
    """
    text = str(value or "").strip().strip('"').strip("'").strip()
    if not text:
        return ""
    if _CAS_RE.fullmatch(text):
        return text
    return extract_cas(text)


def cn_name(cas) -> str:
    """CAS → 中文名;查不到返回空串(调用方自己决定怎么兜底)。"""
    return CN_BY_CAS.get(clean_cas(cas), "")


def label(cas, fallback: str) -> str:
    """页面/导出用的单行标签:CAS 认得就给中文名,不认得退回 Aspen 原名。"""
    return cn_name(cas) or str(fallback or "").strip()


def cas_or_name(cas, fallback: str) -> str:
    """AI 包用的标识:有 CAS 用 CAS,没有才用 Aspen 原名。"""
    good = clean_cas(cas)
    return good or str(fallback or "").strip()
