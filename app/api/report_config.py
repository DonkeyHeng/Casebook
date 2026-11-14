"""工段边界配置的读写 —— `data/config/sections.json`。

为什么单独一个文件
------------------
`app/design/sections.py` 是纯函数(不读文件、无副作用),写盘与校验留在 API 层:
一份配置写坏了,应该只在保存时报一句人话,不该让整个应用起不来。
这里也负责「配置里写了不存在的流股」这类结构性校验 —— 真正的衡算仍由
`app/design/mass_balance.py` 判,两边不重复。

存储形态(按工况 id,因为同一套流程改过之后工段划分会变):

    {"40": {"01": {"label": "第一工段", "in": [...], "out": [...]},
            "device_sections": {"E0211": "02"}}}
"""
from __future__ import annotations

import json
import os
import re

from app.design import sections as section_rules

CONFIG_PATH = os.path.join("data", "config", "sections.json")

# 工段 key:两个字母数字以内(01、02、A1 都行),避免把整句标题当 key
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,15}$")


def _norm(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def load(case_id: int) -> tuple[dict, dict, list, str]:
    """读该工况的确认结果。

    Returns:
        (parts, device_overrides, deleted, error)
        `error` 非空表示文件读不了 / 解析失败,此时 parts 为空(调用方改用草稿)。
    """
    if not os.path.exists(CONFIG_PATH):
        return {}, {}, [], ""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        return {}, {}, [], f"读不了 {CONFIG_PATH}:{exc}"
    parts, overrides, deleted = section_rules.load(data, case_id)
    return parts, overrides, deleted, ""


def _clean_names(value) -> tuple[list[str], list[str]]:
    """把 "A,B" / ["A","B"] 归一化成列表;返回 (名字列表, 错误列表)。"""
    errors: list[str] = []
    if value is None:
        return [], errors
    if isinstance(value, str):
        items = re.split(r"[,，\s]+", value)
    elif isinstance(value, (list, tuple)):
        items = [_norm(v) for v in value]
    else:
        return [], ["in / out 得是流股名数组或逗号分隔的字符串"]
    names: list[str] = []
    for item in items:
        text = _norm(item)
        if text:
            names.append(text)
    return names, errors


def validate(payload) -> tuple[dict, dict, list, list[str]]:
    """校验前端提交的内容,返回 (parts, overrides, deleted, errors)。"""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return {}, {}, [], ["提交内容得是 JSON 对象"]

    raw_parts = payload.get("parts")
    if raw_parts is None:
        raw_parts = payload
    if not isinstance(raw_parts, dict):
        return {}, {}, [], ["parts 得是按工段组织的一个对象"]

    parts: dict[str, dict] = {}
    for key, value in raw_parts.items():
        if key == "device_sections":
            continue
        name = _norm(key)
        if not name:
            continue
        if not _KEY_RE.match(name):
            errors.append(f"工段 key「{name}」只能用字母、数字与 ._- ,且不超过 16 字")
            continue
        if not isinstance(value, dict):
            errors.append(f"工段「{name}」的配置得是对象(含 label / in / out)")
            continue
        in_names, errs = _clean_names(value.get("in"))
        out_names, errs2 = _clean_names(value.get("out"))
        errors += errs + errs2
        if not in_names and not out_names:
            errors.append(f"工段「{name}」的进界与出界都空着,最少要填一边"
                          "(整段不参与衡算可以直接删掉这个工段)")
            continue
        parts[name] = {"label": _norm(value.get("label")) or section_rules.label_for(name),
                       "in": in_names, "out": out_names, "blocks": []}

    deleted: list[str] = []
    raw_deleted = payload.get("deleted")
    if isinstance(raw_deleted, (list, tuple)):
        deleted = [_norm(v) for v in raw_deleted if _norm(v)]
    elif isinstance(raw_deleted, str):
        deleted = [_norm(v) for v in re.split(r"[,，\s]+", raw_deleted) if _norm(v)]

    overrides: dict[str, str] = {}
    raw_over = payload.get("device_sections")
    if isinstance(raw_over, dict):
        for block, part in raw_over.items():
            if _norm(block) and _norm(part):
                overrides[_norm(block)] = _norm(part)

    if not parts and not deleted and not errors:
        errors.append("一个工段都没填,不保存")
    return parts, overrides, deleted, errors


def save(case_id: int, parts: dict, overrides: dict,
         deleted=None) -> tuple[bool, list[str]]:
    """把该工况的边界写进配置(其它工况的内容原样保留)。"""
    data: dict = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError) as exc:
            return False, [f"现有配置读不了,没有覆盖:{exc}"]

    entry: dict = {}
    for key, info in parts.items():
        entry[key] = {"label": info.get("label") or section_rules.label_for(key),
                      "in": list(info.get("in") or []),
                      "out": list(info.get("out") or [])}
    if overrides:
        entry["device_sections"] = dict(overrides)
    if deleted:
        # 记住"人工删掉了哪些工段":草稿每次都能把它们推回来,不显式记就删不掉
        entry["deleted"] = sorted(set(deleted))
    data[str(case_id)] = entry

    try:
        directory = os.path.dirname(CONFIG_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError as exc:
        return False, [f"写不了 {CONFIG_PATH}:{exc}"]
    return True, []


def status() -> dict:
    """配置文件的现状,给界面显示用。"""
    exists = os.path.exists(CONFIG_PATH)
    cases: list[str] = []
    if exists:
        try:
            with open(CONFIG_PATH, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                cases = sorted(k for k, v in data.items() if isinstance(v, dict))
        except (OSError, ValueError):
            cases = []
    return {"path": CONFIG_PATH, "exists": exists, "cases": cases}
