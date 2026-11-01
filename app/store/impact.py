"""报错设备的波及范围:哪些流股的数据**不能拿来用**(纯函数,不碰 COM)。

为什么能用现成数据算:第 0 步探测时已经确认,流股侧的 `SOURCE` / `DESTINATION`
(以及换热器的 `FCOLD`/`FHOT`、塔的 `FEED_STAGE`)足以还原"设备 ↔ 流股",
这些连接已经存在 `connections` 表里 —— 所以路线图 P0-3B 原本要"再摸一轮
`IN_STRMID` 字段"才能做的事,现在不用再探测 Aspen 了。

口径(和判定规则一致,只有硬错误才算"流股数据不可用")
  * 设备 `BLKSTAT=1`(报错)→ 它的进口和出口流股都标 **error**
    (进口流股的值是错的输入,出口流股的值是错的输出,两头都不能用)
  * 设备 `BLKSTAT=2`(告警)→ 标 **warning**(数据仍可用,引用时注明)
  * 同一条流股被多台设备碰:报错优先于告警;逐台设备都记下来(便于解释)
"""
from __future__ import annotations

ERROR = "error"
WARNING = "warning"

_RANK = {ERROR: 0, WARNING: 1}


def build(connections, blocks) -> dict:
    """从 connections + blocks 算出受影响流股清单。

    Args:
        connections: [{"block", "direction", "stream", "side", ...}, …]
        blocks:      [{"name", "status", …}, …]  status: 0 正常 / 1 错误 / 2 告警
    """
    status_of: dict[str, object] = {}
    for block in blocks or []:
        status_of[block["name"]] = block.get("status")

    error_blocks = sorted(n for n, s in status_of.items() if s == 1)
    warning_blocks = sorted(n for n, s in status_of.items() if s == 2)

    streams: dict[str, dict] = {}
    seen: set[tuple[str, str, str]] = set()
    for row in connections or []:
        block = row["block"]
        status = status_of.get(block)
        if status not in (1, 2):
            continue
        severity = ERROR if status == 1 else WARNING
        stream = row["stream"]
        key = (block, row.get("direction") or "", stream)
        if key in seen:                      # 同一台设备的两条重复记录,别写两遍
            continue
        seen.add(key)

        item = streams.setdefault(stream, {"stream": stream, "severity": severity, "blocks": []})
        # 报错优先:先标 error 的不会被后到的 warning 降级
        if _RANK[severity] < _RANK[item["severity"]]:
            item["severity"] = severity
        item["blocks"].append({
            "block": block,
            "direction": row.get("direction") or "",
            "severity": severity,
        })

    rows = sorted(streams.values(), key=lambda s: (_RANK[s["severity"]], s["stream"]))
    for item in rows:
        item["blocks"].sort(key=lambda b: (_RANK[b["severity"]], b["block"]))

    error_streams = sum(1 for s in rows if s["severity"] == ERROR)
    return {
        "error_blocks": error_blocks,
        "warning_blocks": warning_blocks,
        "streams": rows,
        "summary": {
            "total": len(rows),
            "error": error_streams,
            "warning": len(rows) - error_streams,
            "block_error": len(error_blocks),
            "block_warning": len(warning_blocks),
        },
    }
