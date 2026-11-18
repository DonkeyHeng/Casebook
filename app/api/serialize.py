"""HTTP 响应前的行转换与共享小工具。

放在单独模块,是因为 `server.py` 与 `routes/*` 都要用:放任何一边都会让另一边
反向 import,绕成环。
"""

from __future__ import annotations

import sqlite3

from app.design.pipe import data as pipe_data


def row_to_dict(row: sqlite3.Row) -> dict:
    """`sqlite3.Row` → dict(按列名)。

    注意:`sqlite3.Row` 直接迭代得到的是**值**,不是列名,所以这里必须走
    `row.keys()`(ruff SIM118 会建议写成 `for k in row`,那样就取错东西了)。
    """
    return {k: row[k] for k in row.keys()}  # noqa: SIM118


def pipe_table_status() -> dict:
    """管路选型依赖的三张标准表录到什么程度(不连 Aspen、不查库)。"""
    stress_grades = sorted(pipe_data.ALLOWABLE_STRESS)
    return {
        "dn_to_od": {
            "loaded": bool(pipe_data.DN_TO_OD),
            "count": len(pipe_data.DN_TO_OD),
            "verified": sorted(pipe_data.DN_OD_VERIFIED),
        },
        "od_to_walls": {"loaded": bool(pipe_data.OD_TO_WALLS), "count": len(pipe_data.OD_TO_WALLS)},
        "stress": {
            "loaded": bool(pipe_data.ALLOWABLE_STRESS),
            "grades": stress_grades,
            "count": sum(len(v) for v in pipe_data.ALLOWABLE_STRESS.values()),
        },
        # 管道等级表:不填也能跑 —— 全部流股按页面上的默认材质
        "piping_class": pipe_data.piping_class_status(),
        "ready": bool(pipe_data.DN_TO_OD and pipe_data.ALLOWABLE_STRESS and pipe_data.OD_TO_WALLS),
    }
