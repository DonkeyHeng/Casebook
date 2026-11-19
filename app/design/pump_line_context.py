"""把「库里的管段记录 + 管道页选型结果 + 泵读数」拼成一份可渲染的泵管路视图。

纯计算在 `app/design/pump_line.py`;这里只负责取数与口径对齐,给三个地方共用:
页面接口、Excel / AI 包导出、自测。所有入口都走同一函数,避免"页面一个数、
导出另一个数"。

`pipe.build()` 必须带**该模型存下来的管道页参数**;旧实现没传 options,
泵面板用的是默认流速,和管道页顶栏参数不一致(同一根管子两个 DN)。
"""

from __future__ import annotations

import json

from app.design import pipe as pipe_calc
from app.design import pump_line


def default_pipe_options() -> dict:
    """管道页参数的内置默认值,与 `GET /pipes` 的默认口径一致。"""
    return {
        "velocity": dict(pipe_calc.DEFAULT_VELOCITY),
        "pressure_factor": 1.15,
        "pressure_add_mpa": 0.15,
        "pressure_floor_mpa": 1.0,
        "max_dn": 1000,
        "material": "S30408",
    }


def _merge_options(stored: dict | None) -> dict:
    base = default_pipe_options()
    if not isinstance(stored, dict):
        return base
    out = {**base, **{k: v for k, v in stored.items() if v is not None}}
    if isinstance(stored.get("velocity"), dict):
        out["velocity"] = {**base["velocity"], **stored["velocity"]}
    return out


def options_for(conn, case_id: int) -> dict:
    """该模型当前生效的管道页参数(存过就用存的,没存用默认)。"""
    from app.store import db

    key = db.model_key(conn, case_id)
    return _merge_options(db.read_pipe_options(conn, key))


def _pipe_rows(conn, case_id: int, *, blocks, streams, connections, options):
    """跑一次管道选型;标准表没录全时返回错误文本,不造空表。"""
    try:
        result = pipe_calc.build(streams, blocks, connections, options=options)
        return result.get("rows") or [], result.get("missing") or [], ""
    except pipe_calc.TablesNotReady as exc:
        return [], [], str(exc)
    except Exception as exc:  # 坏数据不该让整个泵面板打不开
        return [], [], f"管道选型失败:{exc}"


def lines_for(
    conn,
    case_id: int,
    block: dict,
    *,
    blocks=None,
    streams=None,
    connections=None,
    options: dict | None = None,
    saved: dict | None = None,
) -> dict:
    """一台泵的两侧管段视图(含合计、修正 NPSHₐ、三态)。"""
    from app.store import db

    block = dict(block)
    streams = [
        dict(s) for s in (streams if streams is not None else db.read_streams(conn, case_id))
    ]
    blocks = [dict(b) for b in (blocks if blocks is not None else db.read_blocks(conn, case_id))]
    connections = [
        dict(c)
        for c in (connections if connections is not None else db.read_connections(conn, case_id))
    ]
    options = _merge_options(options if options is not None else options_for(conn, case_id))

    key = db.model_key(conn, case_id)
    picks = db.read_pump_stream_pick(conn, key, block["name"])
    names = pump_line.stream_side(connections, block["name"], "in", picks.get("suction")).get(
        "streams", []
    ) + pump_line.stream_side(connections, block["name"], "out", picks.get("discharge")).get(
        "streams", []
    )
    runs, run_sources = db.pipe_runs_for(conn, case_id, names)

    rows, missing, tables_error = _pipe_rows(
        conn,
        case_id,
        blocks=blocks,
        streams=streams,
        connections=connections,
        options=options,
    )
    if tables_error:
        # 表格给不出规格时仍返回两侧流股与状态,只把原因说清楚
        missing = [{"stream": s, "reason": tables_error} for s in names]

    view = pump_line.compute(
        block,
        connections,
        streams,
        rows,
        missing,
        runs=runs,
        saved=saved,
        eps_mm=options.get("roughness_mm"),
        picks=picks,
    )
    view["options"] = options
    view["picks"] = picks
    view["run_sources"] = run_sources
    view["tables_error"] = tables_error
    view["has_runs"] = bool(runs)
    return view


FITTING_ZETA = {
    "el90": 0.75,
    "gate": 0.9,
    "globe": 9.5,
    "check": 2.0,
    "reducer": 0.04,
}


def legacy_of(conn, case_id: int, block: str) -> dict | None:
    """旧单一 Σhf 的迁移状态;没有可迁移内容返回 None。"""
    from app.store import db

    key = db.model_key(conn, case_id)
    row = db.read_pump_design_legacy(conn, key, block)
    if row is None:
        return None
    has_old = any(row.get(k) is not None for k in ("hf_m", "l_m", "zeta_json", "dz_m"))
    if not has_old and not row.get("legacy_split"):
        return None
    fittings = {}
    zeta_sum = None
    legacy_id = None
    raw = row.get("zeta_json")
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            fittings = parsed.get("fittings") if isinstance(parsed.get("fittings"), dict) else {}
            for key_name in ("zeta", "zeta_sum"):
                if isinstance(parsed.get(key_name), (int, float)):
                    zeta_sum = float(parsed[key_name])
                    break
            overrides = parsed.get("overrides")
            if isinstance(overrides, dict) and isinstance(overrides.get("id_mm"), (int, float)):
                legacy_id = float(overrides["id_mm"])
    if zeta_sum is None and fittings:
        zeta_sum = sum(float(qty or 0) * FITTING_ZETA.get(k, 0.0) for k, qty in fittings.items())
    return {
        "block": block,
        "hf_m": row.get("hf_m"),
        "dz_m": row.get("dz_m"),
        "l_m": row.get("l_m"),
        "zeta_sum": zeta_sum,
        "fittings": fittings,
        "legacy_id_mm": legacy_id,
        "legacy_split": row.get("legacy_split"),
        "legacy_total_hf": row.get("legacy_total_hf"),
        "needs_choice": not row.get("legacy_split"),
    }


def legacy_candidates(conn, case_id: int, blocks=None) -> list[dict]:
    """需要迁移向导的泵(旧 Σhf 还在、且没做过归属选择)。"""
    from app.store import db

    blocks = blocks if blocks is not None else db.read_blocks(conn, case_id)
    out = []
    for block in blocks:
        item = legacy_of(conn, case_id, str(block.get("name")))
        if item and item["needs_choice"]:
            out.append(item)
    return out


def freeze_state(conn, case_id: int) -> dict:
    """哪些管段被人工 DN 冻结了(改管道页参数不会再动它们)。

    冻结是显式选择,不是故障;改参数前要让人知道"这几根不会跟着变"。
    """
    from app.store import db

    key = db.model_key(conn, case_id)
    rows = conn.execute(
        "SELECT stream, dn_override, od_override_mm, wall_override_mm "
        "FROM pipe_run WHERE model_key = ? "
        "AND (dn_override IS NOT NULL OR od_override_mm IS NOT NULL)",
        (key,),
    ).fetchall()
    return {
        "count": len(rows),
        "streams": [
            {
                "stream": r["stream"],
                "dn": r["dn_override"],
                "custom": bool(r["od_override_mm"] and r["wall_override_mm"]),
            }
            for r in rows
        ],
    }
