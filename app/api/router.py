"""路由表原语:一条路由 = 方法 + 整串正则 + Handler 上的方法名。

为什么用表
----------
以前 GET / POST / DELETE 各是一条 `if` 链:加一个接口要改两三处、还要自己判断该
落在哪条链里;接口清单只在模块 docstring 里手工维护,必然漂。改成表之后:

* 一个接口一行,三种方法一眼看全,增删是加删一行;
* 用整串匹配(`fullmatch`),不依赖分支顺序 —— 以前 `columns` 与 `columns/{块名}`
  靠 `len(parts)` 区分,顺序一换就可能互相遮住;
* 表里写了不存在的方法名会立刻暴露:`selftest` 的 `test_routes()` 逐条核对了
  每个 handler 在 `Handler` 上都存在。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    """一条路由。

    `handler` 是 `Handler` 上的方法名,不是函数本身 —— 这样表可以在 Handler
    定义之前就写好(表在 `routes/__init__.py`,Handler 在 `server.py`)。
    """

    method: str
    pattern: re.Pattern[str]
    handler: str
    note: str = ""


def route(method: str, pattern: str, handler: str, note: str = "") -> Route:
    """建一条路由;`pattern` 写正则源码,匹配时对整串生效。"""
    return Route(method, re.compile(pattern), handler, note)


def match(table: list[Route], method: str, path: str) -> tuple[Route | None, re.Match[str] | None]:
    """在表里找第一条方法与整串都命中的路由。"""
    for item in table:
        if item.method != method:
            continue
        found = item.pattern.fullmatch(path)
        if found is not None:
            return item, found
    return None, None
