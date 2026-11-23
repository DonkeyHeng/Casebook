"""换热器配置的读写(`data/config/hx-*.json`)。

分工(方案 §3.7):

* **内置默认值**在 `app/design/heat/data.py`,那里是冷启动与离线单测的兜底;
* 本模块只负责"用户改过之后"的覆盖文件:读、校验、原子写;
* 文件存在且合法 → 整体覆盖内置(不合并);非法/缺字段 → 回退内置并给 warning;
* 程序自身不主动生成这些文件,只有界面点保存才写盘。

坏文件不能把页面打挂,也不能悄悄混进一部分用户值 —— 校验按"整份文件"通过/不通过。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from app import paths

#: 文件名 ↔ 内置默认字典的键
CONFIG_FILES = {
    "fouling": "hx-fouling.json",
    "u-reference": "hx-u-reference.json",
    "utility-presets": "hx-utility-presets.json",
}
CONFIG_SOURCE_BUILTIN = "builtin"
CONFIG_SOURCE_USER = "user"


def config_dir(base: str | Path | None = None) -> Path:
    """配置目录;`base` 主要给单测用,生产环境走 `app.paths.data_dir()`。"""
    if base is not None:
        return Path(base)
    return paths.data_dir("config")


def load_config(
    name: str,
    defaults: dict,
    base: str | Path | None = None,
) -> tuple[dict, str, list[str]]:
    """读一份配置。

    Returns:
        (data, source, warnings)
        data      —— 可直接用的字典(要么是用户文件,要么是内置)
        source    —— 'builtin' / 'user'
        warnings  —— 中文提示;非空时界面应展示"已回退内置"
    """
    filename = CONFIG_FILES.get(name)
    if filename is None:
        return dict(defaults), CONFIG_SOURCE_BUILTIN, [f"未知配置名 {name!r}"]

    path = config_dir(base) / filename
    if not path.exists():
        return dict(defaults), CONFIG_SOURCE_BUILTIN, []

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return dict(defaults), CONFIG_SOURCE_BUILTIN, [f"{filename} 读取失败,已回退内置:{exc}"]

    problem = validate_config(name, raw)
    if problem:
        return (
            dict(defaults),
            CONFIG_SOURCE_BUILTIN,
            [f"{filename} 内容不合法,已回退内置:{problem}"],
        )

    return raw, CONFIG_SOURCE_USER, []


def validate_config(name: str, raw) -> str | None:
    """整份校验;合法返回 None,不合法返回一句中文原因。"""
    if not isinstance(raw, dict) or not raw:
        return "顶层必须是对象且不能为空"

    for key, row in raw.items():
        if not isinstance(key, str) or not isinstance(row, dict):
            return f"条目 {key!r} 必须是对象"

        if name == "fouling":
            if not _is_number(row.get("value")):
                return f"{key}: 缺少数值字段 value"

        elif name == "u-reference":
            for field in ("min", "max", "default"):
                if not _is_number(row.get(field)):
                    return f"{key}: 缺少数值字段 {field}"
            if row["min"] > row["default"] or row["default"] > row["max"]:
                return f"{key}: default 必须落在 min~max 之间"

        elif name == "utility-presets":
            if not _is_number(row.get("t_in_c")) or not _is_number(row.get("t_out_c")):
                return f"{key}: 缺少 t_in_c / t_out_c"
            if not isinstance(row.get("phase"), str):
                return f"{key}: 缺少 phase(字符串)"
            if row["phase"] not in ("condensing", "boiling", "liquid", "gas"):
                return f"{key}: phase 只能是 condensing/boiling/liquid/gas"

        else:
            return f"未知配置名 {name!r}"

    return None


def save_config(name: str, data: dict, base: str | Path | None = None) -> tuple[bool, str]:
    """原子写一份配置。返回 (是否成功, 提示)。

    先写 `.tmp` 再 `os.replace`;中途崩溃不会留下半截 JSON。
    """
    filename = CONFIG_FILES.get(name)
    if filename is None:
        return False, f"未知配置名 {name!r}"

    problem = validate_config(name, data)
    if problem:
        return False, f"内容不合法,未写盘:{problem}"

    path = config_dir(base) / filename
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{filename}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except OSError as exc:
        return False, f"写入失败:{exc}"

    return True, f"已保存 {filename}"


def reset_config(name: str, base: str | Path | None = None) -> tuple[bool, str]:
    """删除用户覆盖文件,回到内置默认。

    文件本来就不存在时也算成功 —— 目标状态就是"没有覆盖",不必报错。
    """
    filename = CONFIG_FILES.get(name)
    if filename is None:
        return False, f"未知配置名 {name!r}"
    path = config_dir(base) / filename
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        return False, f"删除失败:{exc}"
    return True, f"已恢复内置默认({filename} 已删除)"


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
