"""app/export —— 把一次工况导出成文件(.docx / .xlsx / zip)。

与 `app/api/` 的分工:本包只负责"数据 → 文件字节",不认识 HTTP、不读请求、
不碰窗口。网页导出接口与命令行工具(`tools/balance_report.py`、
`tools/make_pipe_schedule.py`)都从这里取,两边共用同一套代码,所以命令行
不需要为了生成一份 Word 而把整个 HTTP 服务拖进来。

版式骨架(`_para` / `_heading` / `_three_line` 等)在 `report` 里,管道一览表
(`pipe_schedule`)复用同一套,两者的封面、页眉页脚、三线表口径不会走偏。
"""

from __future__ import annotations

__all__ = ["aipack", "family_xlsx", "pipe_schedule", "report"]
