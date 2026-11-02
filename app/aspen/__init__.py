"""COM 适配层:连接、读取、单位换算。

只做只读读取,不调用 Run2 / Reinit,不改模型。
"""
from .connect import AspenNotRunning, AspenSession, attach, list_open_models
from .reader import read_case

__all__ = [
    "AspenNotRunning",
    "AspenSession",
    "attach",
    "list_open_models",
    "read_case",
]
