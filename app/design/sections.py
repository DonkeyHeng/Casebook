"""工段(界区)判定:纯函数,不碰 Aspen、不读库。

要解决的问题
------------
一个工段不是一个「标签」,而是**一块界区**:有进界的流股、有出界的流股,
工段内部自己的物流在界区里绕。所以工段的物料衡算是

    Σ(进界流股) = Σ(出界流股) + 界区内累积(稳态取 0)

而不是「按编号把设备归堆再加总」。界区边界必须由人确认:同一个模型里,
某股料到底算不算「进第一工段」,只有画流程图的人知道。

草稿怎么来(实测依据)
---------------------
设备名通常带段号(`E0101` / `P0201` / `VA0301` / `EX0101` / `T0202-A`),
实测 9 个工况、629 台有段号信息的设备,594 / 629 = 94.4% 的进出口流股与
设备名段号自洽。于是可以自动推一版草稿:

* 流股 s 的**源段** = 产出它的设备的段号,没有源(外部进料)记 `None`;
* 流股 s 的**去向段** = 接收它的设备的段号,没有去向(外排)记 `None`;
* 工段 P 的进界流股 = 去向段是 P、源段不是 P 的流股;
* 工段 P 的出界流股 = 源段是 P、去向段不是 P 的流股。

本文件只产出**草稿**,真正参与衡算的是用户确认后的边界(`merge()`),
草稿仅作为起点。实测草稿在 9 个工况上已经闭合(见 docs/balance-report-plan.md),
但跨段设备(35 台接口设备)会让草稿出现「同段进出」的怪项,所以必须人工过一遍。

三条约定
--------
* 纯函数:配置以 dict 传入,本模块**不读文件**(读写 `data/config/sections.json`
  是 API / 工具层的事);
* 结果确定:同一输入永远同一输出(流股名排序),自测才能断言;
* 不丢工段:草稿推出来的工段与你确认的工段取并集,确认过的以你为准。
"""
from __future__ import annotations

import re

# 判不出工段时的桶(只用于「逐设备表」的分组;界区表里没有这个桶)
UTILITY_KEY = "util"
UTILITY_LABEL = "公用工程 / 未分组"

# 设备名里的 4 位编码:E0101 / EX0101 / VA0301 / T0202-A / C-0501
_BLOCK_CODE = re.compile(r"(\d{4})")
# 流股名的段号:0101 → 01(至少以 4 位数字开头)
_STREAM_CODE = re.compile(r"^(\d{2})\d{2}")


def _norm(value) -> str:
    """归一化成字符串;None / 空串返回空串(表示「没配」)。"""
    if value is None:
        return ""
    return str(value).strip()


def section_of_block(name) -> str | None:
    """设备名 → 段号;取不到返回 None。

    >>> section_of_block("E0101")
    '01'
    >>> section_of_block("T0202-A")
    '02'
    >>> section_of_block("B3")
    """
    if not name:
        return None
    match = _BLOCK_CODE.search(str(name))
    return match.group(1)[:2] if match else None


def section_of_stream(name) -> str | None:
    """流股名 → 段号;取不到返回 None(公用工程流股就是这一类)。

    >>> section_of_stream("0101")
    '01'
    >>> section_of_stream("CW1")
    """
    if not name:
        return None
    match = _STREAM_CODE.match(str(name).strip())
    return match.group(1) if match else None


def label_for(key: str) -> str:
    """工段 key → 报告里的标题(逐设备表用)。"""
    if not key or key == UTILITY_KEY:
        return UTILITY_LABEL
    return f"{key} 工段"


def _majority(values: list[str]) -> str | None:
    """出现次数最多的段号;并列时取字典序最小,保证结果确定。"""
    if not values:
        return None
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    best = max(counts.values())
    return sorted(k for k, v in counts.items() if v == best)[0]


def resolve(block_name, in_streams=(), out_streams=(), overrides=None) -> str:
    """一台设备归到哪个工段(只给「逐设备表」用,不是界区)。

    顺序:配置覆盖 → 设备名段号 → 流股段号众数 → 公用工程。
    """
    if overrides:
        forced = _norm(overrides.get(block_name))
        if forced:
            return forced

    by_block = section_of_block(block_name)
    if by_block:
        return by_block

    in_keys = [k for k in (section_of_stream(s) for s in in_streams or ()) if k]
    out_keys = [k for k in (section_of_stream(s) for s in out_streams or ()) if k]
    # 进口优先:进口是「这股料属于哪个工段」的确凿证据,出口常带下一段的编号
    return _majority(in_keys) or _majority(out_keys) or UTILITY_KEY


def group(devices) -> list[dict]:
    """把带 `section` 字段的设备按工段分组,工段升序、公用工程排最后。"""
    buckets: dict[str, list[dict]] = {}
    for device in devices or []:
        key = _norm(device.get("section")) or UTILITY_KEY
        buckets.setdefault(key, []).append(device)

    def order(key: str):
        return (key == UTILITY_KEY, key)

    return [{"key": key, "label": label_for(key), "devices": buckets[key]}
            for key in sorted(buckets, key=order)]


# ---------------------------------------------------------------- 界区草稿

def _ports(connections) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """connections → ({流股: [源设备]}, {流股: [去向设备]})。"""
    src: dict[str, list[str]] = {}
    dst: dict[str, list[str]] = {}
    for row in connections or []:
        bucket = dst if row.get("direction") == "in" else src
        bucket.setdefault(row["stream"], []).append(row["block"])
    return src, dst


def _first_section(block_names, cache: dict[str, str | None]):
    """一组设备 → 第一个能判出段号的段号(设备名)."""
    for name in block_names or ():
        if name not in cache:
            cache[name] = section_of_block(name)
        if cache[name]:
            return cache[name]
    return None


def draft(blocks, connections) -> dict:
    """按设备名段号推一版界区草稿。

    Returns:
        {"01": {"label": "01 工段", "in": [...], "out": [...], "blocks": [...]}, …}
        `in` / `out` 已排序,可直接展示给用户勾选与增删。
    """
    cache: dict[str, str | None] = {}
    parts: dict[str, dict] = {}
    for block in blocks or []:
        name = block["name"]
        part = cache.setdefault(name, section_of_block(name))
        if part:
            parts.setdefault(part, {"label": label_for(part), "in": [], "out": [],
                                    "blocks": []})["blocks"].append(name)

    src, dst = _ports(connections)
    for stream in set(src) | set(dst):
        part_in = _first_section(dst.get(stream, ()), cache)
        part_out = _first_section(src.get(stream, ()), cache)
        if part_in and part_in != part_out:
            parts.setdefault(part_in, {"label": label_for(part_in), "in": [], "out": [],
                                       "blocks": []})["in"].append(stream)
        if part_out and part_out != part_in:
            parts.setdefault(part_out, {"label": label_for(part_out), "in": [], "out": [],
                                        "blocks": []})["out"].append(stream)

    for info in parts.values():
        info["in"] = sorted(set(info["in"]))
        info["out"] = sorted(set(info["out"]))
        info["blocks"] = sorted(info["blocks"])
    return dict(sorted(parts.items()))


def parse_part(raw, key="") -> dict | None:
    """一条界区配置 → 归一化结构;坏数据返回 None(不抛异常)。"""
    if not isinstance(raw, dict):
        return None
    label = _norm(raw.get("label")) or label_for(key)

    def names(field) -> list[str]:
        value = raw.get(field)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return []
        return [_norm(v) for v in value if _norm(v)]

    return {"label": label, "in": names("in"), "out": names("out"),
            "blocks": names("blocks")}


def load(data, case_id) -> tuple[dict, dict]:
    """从 `sections.json` 的内容取该工况的确认结果。

    Returns:
        (parts, device_overrides)
        parts: {工段: {label, in, out, blocks}} —— 用户确认过的边界
        device_overrides: {设备名: 工段} —— 修正「逐设备表」的归堆
    """
    if not isinstance(data, dict):
        return {}, {}
    # 支持 {"cases": {"40": {...}}} 与直接 {"40": {...}} 两种写法
    cases = data.get("cases") if isinstance(data.get("cases"), dict) else data
    raw = cases.get(str(case_id))
    if not isinstance(raw, dict):
        return {}, {}

    parts: dict[str, dict] = {}
    device_overrides: dict[str, str] = {}
    for key, value in raw.items():
        if key == "device_sections" and isinstance(value, dict):
            for name, part in value.items():
                if _norm(name) and _norm(part):
                    device_overrides[_norm(name)] = _norm(part)
            continue
        part = parse_part(value, _norm(key))
        if part:
            parts[_norm(key)] = part
    return parts, device_overrides


def merge(draft_parts: dict, confirmed: dict) -> dict:
    """草稿与确认结果合并:确认过的以确认结果为准,草稿多出来的保留。

    确认时把 `in` / `out` 改成空列表是**有效决定**(表示「该工段没有跨界流股」),
    不当成「没配」而回退到草稿 —— 否则用户想把某段清空也做不到。
    """
    merged = {key: {"label": info["label"], "in": list(info["in"]),
                    "out": list(info["out"]), "blocks": list(info["blocks"])}
              for key, info in (draft_parts or {}).items()}
    for key, info in (confirmed or {}).items():
        merged[key] = {"label": info.get("label") or label_for(key),
                       "in": list(info.get("in") or []),
                       "out": list(info.get("out") or []),
                       "blocks": list(info.get("blocks") or [])}
    return dict(sorted(merged.items()))
