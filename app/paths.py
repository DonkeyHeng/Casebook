# -*- coding: utf-8 -*-
"""app/paths.py —— 运行期的两个基准目录。

为什么要分开
------------
PyInstaller 单文件模式启动时会把 `app/` 解到临时目录(`sys._MEIPASS`):
那里**只读**,而且每次启动路径都变。而工况库、标准表、配置、窗口状态必须
留在 exe 旁边,否则一关程序数据就没了。

所以:

* `resource_root()` —— **只读**资源:`app/web` 静态文件、`assets` 图标
* `app_root()`      —— **可写**目录:exe 所在目录 / 仓库根(`data/` 在它下面)

源码运行时两者都是仓库根,行为与以前完全一致。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """是不是 PyInstaller 打出来的 exe。"""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """只读资源所在目录(exe 内临时解包目录 / 仓库根)。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def app_root() -> Path:
    """可写目录:exe 所在目录 / 仓库根。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def data_dir(*parts: str) -> Path:
    """`data/` 下的某个位置(exe 模式下就是 exe 旁边的 `data/`)。"""
    return app_root().joinpath("data", *parts)
