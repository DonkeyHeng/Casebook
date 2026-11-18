"""请求级异常:让分发层知道该回哪一类状态码。

单独一个模块,是因为 `server.py`(HTTP 外壳)与 `routes/*`(各域处理)都要用:
异常类留在任何一边,另一边都得反向 import,绕成环。
"""

from __future__ import annotations


class BadBody(Exception):
    """请求体不合法(Content-Length 不对 / 超大 / 不是 JSON 对象 / 读到一半断了)。

    单独一类异常,是为了让 do_POST 回 400。以前这里一律退回 `{}`,
    而空对象在某些保存接口里表示"清空配置" —— 于是坏请求能静默改配置。
    """

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class BadQuery(Exception):
    """查询参数不合法(不是数字、不是有限值、超范围)—— 回 400,不要静默换默认值。"""
