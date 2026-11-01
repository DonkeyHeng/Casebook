"""本地工况库(SQLite)。

一张 `cases` 表存"某次读取"的快照(模型/时间/收敛状态),
`streams` / `blocks` / `stream_components` 存明细,全部用 `case_id` 挂在快照下。

设计要点:
  * 每次读取都新增一条 `cases`,不覆盖 —— 方便做多工况对比(二期)
  * 明细表存**已归一的数值**(℃/bar/kg·h⁻¹/kW),原始值和单位存在 `raw_json` 里备查
  * `converged=0` 的工况照样入库,但应用层应禁止其进入统计对比
"""
from __future__ import annotations

import json
import os
import sqlite3

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS cases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    model_path    TEXT NOT NULL,
    model_name    TEXT,
    read_at       TEXT NOT NULL,
    run_id        TEXT,
    run_date      TEXT,
    run_user      TEXT,
    aspen_version TEXT,
    severity      TEXT,          -- ok / warning / error
    converged     INTEGER,       -- 1 才能进统计对比
    reasons_json  TEXT,          -- 判定理由(列表)
    errtxt        TEXT,          -- Aspen 原始提示文本
    status_json   TEXT,          -- RSTAT/CVSTAT/... 原始码
    tear_json     TEXT,          -- 撕裂流误差
    block_errors  TEXT,          -- 报错模块名(JSON 列表)
    block_warnings TEXT,         -- 告警模块名(JSON 列表)
    stream_count  INTEGER,
    block_count   INTEGER,
    elapsed_sec   REAL
);

CREATE TABLE IF NOT EXISTS streams (
    case_id        INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    name           TEXT    NOT NULL,
    temperature_c  REAL,
    pressure_bar   REAL,
    mass_flow_kgh  REAL,
    mole_flow_kmolh REAL,
    vap_frac       REAL,
    vol_flow_m3h   REAL,
    raw_json       TEXT,
    PRIMARY KEY (case_id, name)
);

CREATE TABLE IF NOT EXISTS stream_components (
    case_id   INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    stream    TEXT    NOT NULL,
    component TEXT    NOT NULL,
    x_liq     REAL,
    y_vap     REAL,
    PRIMARY KEY (case_id, stream, component)
);

CREATE TABLE IF NOT EXISTS blocks (
    case_id       INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    name          TEXT    NOT NULL,
    type          TEXT,
    duty_kw       REAL,
    power_kw      REAL,
    area_m2       REAL,
    nstage        INTEGER,
    top_temp_c    REAL,
    bottom_temp_c REAL,
    status        INTEGER,
    message       TEXT,
    raw_json      TEXT,
    PRIMARY KEY (case_id, name)
);

CREATE TABLE IF NOT EXISTS column_stages (
    case_id       INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    block         TEXT    NOT NULL,
    stage         INTEGER NOT NULL,
    temperature_c REAL,
    pressure_bar  REAL,
    liq_flow      REAL,
    vap_flow      REAL,
    duty_kw       REAL,
    PRIMARY KEY (case_id, block, stage)
);

CREATE INDEX IF NOT EXISTS idx_streams_case ON streams(case_id);
CREATE INDEX IF NOT EXISTS idx_blocks_case  ON blocks(case_id);
CREATE INDEX IF NOT EXISTS idx_stages_case  ON column_stages(case_id);
"""


def open_db(path: str) -> sqlite3.Connection:
    """打开(必要时创建)工况库。"""
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# ---------------------------------------------------------------- 写入

def save_case(conn: sqlite3.Connection, case: dict) -> int:
    """把 read_case() 的结果整份入库,返回 case_id。"""
    conv = case.get("convergence", {})
    model = case.get("model", {})
    run = conv.get("run", {})
    stats = case.get("stats", {})

    cur = conn.execute(
        """
        INSERT INTO cases (model_path, model_name, read_at, run_id, run_date, run_user,
                           aspen_version, severity, converged, reasons_json, errtxt,
                           status_json, tear_json, block_errors, block_warnings,
                           stream_count, block_count, elapsed_sec)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            model.get("path"),
            model.get("name"),
            model.get("read_at"),
            run.get("RUNID"),
            run.get("ITDATE"),
            run.get("USER"),
            run.get("VERNO"),
            conv.get("severity"),
            1 if conv.get("converged") else 0,
            json.dumps(conv.get("reasons", []), ensure_ascii=False),
            conv.get("errtxt"),
            json.dumps(conv.get("codes", {}), ensure_ascii=False),
            json.dumps(conv.get("tears", []), ensure_ascii=False),
            json.dumps(conv.get("block_errors", []), ensure_ascii=False),
            json.dumps(conv.get("block_warnings", []), ensure_ascii=False),
            stats.get("stream_count"),
            stats.get("block_count"),
            stats.get("elapsed_sec"),
        ),
    )
    case_id = int(cur.lastrowid)

    conn.executemany(
        """
        INSERT INTO streams (case_id, name, temperature_c, pressure_bar, mass_flow_kgh,
                             mole_flow_kmolh, vap_frac, vol_flow_m3h, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                case_id,
                s.get("name"),
                s.get("temperature_c"),
                s.get("pressure_bar"),
                s.get("mass_flow_kgh"),
                s.get("mole_flow_kmolh"),
                s.get("vap_frac"),
                s.get("vol_flow_m3h"),
                json.dumps(s.get("raw", {}), ensure_ascii=False),
            )
            for s in case.get("streams", [])
        ],
    )

    comp_rows = []
    for stream in case.get("streams", []):
        for comp in stream.get("composition") or []:
            comp_rows.append(
                (case_id, stream["name"], comp["component"], comp.get("x_liq"), comp.get("y_vap"))
            )
    if comp_rows:
        conn.executemany(
            """
            INSERT INTO stream_components (case_id, stream, component, x_liq, y_vap)
            VALUES (?,?,?,?,?)
            """,
            comp_rows,
        )

    conn.executemany(
        """
        INSERT INTO blocks (case_id, name, type, duty_kw, power_kw, area_m2, nstage,
                            top_temp_c, bottom_temp_c, status, message, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                case_id,
                b.get("name"),
                b.get("type"),
                b.get("duty_kw"),
                b.get("power_kw"),
                b.get("area_m2"),
                b.get("nstage"),
                b.get("top_temp_c"),
                b.get("bottom_temp_c"),
                b.get("status"),
                b.get("message"),
                json.dumps(b.get("raw", {}), ensure_ascii=False),
            )
            for b in case.get("blocks", [])
        ],
    )

    stage_rows = []
    for col in case.get("columns", []):
        for st in col.get("stages", []):
            stage_rows.append((
                case_id, col["block"], st.get("stage"),
                st.get("temperature_c"), st.get("pressure_bar"),
                st.get("liq_flow"), st.get("vap_flow"), st.get("duty_kw"),
            ))
    if stage_rows:
        conn.executemany(
            """
            INSERT INTO column_stages (case_id, block, stage, temperature_c, pressure_bar,
                                       liq_flow, vap_flow, duty_kw)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            stage_rows,
        )

    conn.commit()
    return case_id


# ---------------------------------------------------------------- 查询

def list_cases(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, read_at, model_name, severity, converged, stream_count, block_count "
        "FROM cases ORDER BY id DESC"
    ).fetchall()


def get_case(conn: sqlite3.Connection, case_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()


def latest_case(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM cases ORDER BY id DESC LIMIT 1").fetchone()


def read_streams(conn: sqlite3.Connection, case_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM streams WHERE case_id = ? ORDER BY name", (case_id,)
    ).fetchall()


def read_blocks(conn: sqlite3.Connection, case_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM blocks WHERE case_id = ? ORDER BY name", (case_id,)
    ).fetchall()


def read_column_stages(conn: sqlite3.Connection, case_id: int, block: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM column_stages WHERE case_id = ? AND block = ? ORDER BY stage",
        (case_id, block),
    ).fetchall()


def list_columns(conn: sqlite3.Connection, case_id: int) -> list[sqlite3.Row]:
    """某个工况里存了剖面的塔。"""
    return conn.execute(
        "SELECT block, COUNT(*) AS stage_count FROM column_stages "
        "WHERE case_id = ? GROUP BY block ORDER BY block",
        (case_id,),
    ).fetchall()


# ---------------------------------------------------------------- 流股组成(按需读)

def read_stream_composition(conn: sqlite3.Connection, case_id: int,
                            stream: str) -> list[sqlite3.Row]:
    """某条流股已存的组成;没读过返回空列表(不区分"0 组分")。"""
    return conn.execute(
        "SELECT component, x_liq, y_vap FROM stream_components "
        "WHERE case_id = ? AND stream = ? ORDER BY component",
        (case_id, stream),
    ).fetchall()


def save_stream_composition(conn: sqlite3.Connection, case_id: int, stream: str,
                            rows: list[dict]) -> int:
    """把一条流股的组成写进工况库(重复写就覆盖),返回写入条数。

    只有"点开看过"的流股才会走到这里 —— 组成默认不读,全量读会让耗时从 9 s 涨到 41 s。
    """
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT OR REPLACE INTO stream_components (case_id, stream, component, x_liq, y_vap)
        VALUES (?,?,?,?,?)
        """,
        [(case_id, stream, r["component"], r.get("x_liq"), r.get("y_vap")) for r in rows],
    )
    conn.commit()
    return len(rows)


def list_composed_streams(conn: sqlite3.Connection, case_id: int) -> list[str]:
    """这个工况里已经读过组成的流股名。"""
    return [r["stream"] for r in conn.execute(
        "SELECT DISTINCT stream FROM stream_components WHERE case_id = ? ORDER BY stream",
        (case_id,),
    )]


def read_all_composition(conn: sqlite3.Connection, case_id: int) -> list[sqlite3.Row]:
    """整个工况已存的组成(按流股、组分排序),给 AI 分析包用。"""
    return conn.execute(
        "SELECT stream, component, x_liq, y_vap FROM stream_components "
        "WHERE case_id = ? ORDER BY stream, component",
        (case_id,),
    ).fetchall()


def delete_case(conn: sqlite3.Connection, case_id: int) -> None:
    conn.execute("DELETE FROM cases WHERE id = ?", (case_id,))
    conn.commit()
