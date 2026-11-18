"""COM 适配层:连接、读取、单位换算。

只做只读读取,不调用 Run2 / Reinit,不改模型。

包级导出走 PEP 562 惰性导入
--------------------------
`.connect` 在模块级 `import pythoncom`,所以只要包一初始化,连
`from app.aspen import units` 这种纯换算都会把 COM 扩展拖进来 —— 没有 pywin32 的
机器(离线自测、CI)连 `reader` 都 import 不进去。改成按需取:`attach` /
`AspenSession` 真正被用到时才连 COM,子模块(`reader` / `units` / `balance`)
照旧可以直接 `from app.aspen import reader`。
"""

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "AspenNotRunning": ".connect",
    "AspenSession": ".connect",
    "attach": ".connect",
    "list_open_models": ".connect",
    "read_case": ".reader",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module, __name__), name)


def __dir__() -> list[str]:
    return sorted(__all__)
