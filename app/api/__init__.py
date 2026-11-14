"""HTTP 接口层:把本地工况库暴露给网页。

零依赖,只用标准库(方便以后打包 exe)。
"""

from .server import serve

__all__ = ["serve"]
