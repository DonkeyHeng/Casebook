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
    unit_gaps     INTEGER,       -- 有原始值但单位不认识、被迫留空的字段数
    unit_gaps_json TEXT,         -- 上述字段的明细(JSON:单位清单 + 前 20 条样本)
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
    enthalpy_kjkg   REAL,   -- 质量焓(HMX_MASS 的 MIXED 子节点)
    enthalpy_kjkmol REAL,   -- 摩尔焓(HMX 的 MIXED 子节点)
    enthalpy_flow_kw REAL,  -- 能量流 m·h(HMX_FLOW 的 MIXED 子节点),可直接相加做 ΔH
    raw_json       TEXT,
    PRIMARY KEY (case_id, name)
);

-- 设备 ↔ 流股的连接关系(第 1 步:拓扑)
-- 主来源是流股侧的 SOURCE / DESTINATION;设备 IN_STRMID 只在塔上存在,
-- 换热器另有分侧字段(COLDIN/COLDOUT/HOTIN/HOTOUT),合并成 side。
CREATE TABLE IF NOT EXISTS connections (
    case_id   INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    block     TEXT    NOT NULL,
    direction TEXT    NOT NULL,          -- in / out
    stream    TEXT    NOT NULL,
    side      TEXT    DEFAULT '',        -- cold / hot(换热器分侧),其余为空
    port      TEXT    DEFAULT '',        -- 端口号(塔的 IN_STRMID 元素名)
    stage     INTEGER,                   -- 塔进料板号(来自 Input 的 FEED_STAGE)
    source    TEXT,                      -- stream / strmid / hx / feed_stage
    seq       INTEGER,
    PRIMARY KEY (case_id, block, direction, stream)
);

CREATE TABLE IF NOT EXISTS stream_components (
    case_id   INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    stream    TEXT    NOT NULL,
    component TEXT    NOT NULL,
    x_liq     REAL,
    y_vap     REAL,
    -- 该组分在该流股里的质量流量 kg/h(来自 Aspen Output 下的 MASSFLOW3/<组分>)。
    -- 老库补列后是 NULL:重读一次工况或单独读一次组成才有值,报告里留空不补 0
    mass_flow_kgh REAL,
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
    -- 泵参数(第二期):来自泵块 Output,见 docs/aspen-field-map.md §13。
    -- 只有 Pump 块才有值;其余设备这些列是 NULL,不是 0。
    head_m        REAL,   -- HEAD_CAL,扬程 m(负数=反向工况)
    flow_m3h      REAL,   -- VFLOW,体积流量 m³/h
    pump_eff      REAL,   -- CEFF,泵效率 0~1
    p_in_bar      REAL,   -- 进口流股压力 bar(NPSHₐ 用的就是它)
    p_out_bar     REAL,   -- 出口流股压力 bar
    dp_bar        REAL,   -- ⚠️ 来自 ASPEN 的 IN_PRES:实测是**压升 ΔP**,不是进口压力
    rho_kgm3      REAL,   -- RHO_LIQ × MW_LIQ,质量密度 kg/m³
    pump_vap_frac REAL,   -- 进口汽化率;>0 视为两相,NPSHₐ 不计算
    motor_kw      REAL,   -- 建议电机功率(按默认裕量取的档位值)
    pv_bar        REAL,   -- 饱和蒸气压 bar:读工况时自动取进口流股泡点压力,取不到可由用户手填
    pv_source     TEXT,   -- p_v 来源:aspen(进口流股泡点压力)/ manual(用户手填)
    -- 设备进出口流股温度(所有设备通用;数据来自 connections + streams,不读块上的 B_TEMP):
    -- 换热器另有冷侧。塔用 top_temp_c / bottom_temp_c(逐级数组),不用这两列。
    inlet_temp_c      REAL,
    outlet_temp_c     REAL,
    cold_in_temp_c    REAL,
    cold_out_temp_c   REAL,
    outlet_vap_frac   REAL,   -- 出口流股汽化率(分离页用)
    cond_duty_kw      REAL,   -- 塔:冷凝器负荷 kW(COND_DUTY,放出为负)
    reb_duty_kw       REAL,   -- 塔:再沸器负荷 kW(REB_DUTY)
    dh_kw         REAL,   -- 焓平衡:ΔH = Σ出 − Σ入(流股能量流)
    ref_kw        REAL,   -- 对照值:热负荷 / 轴功率 / 0(绝热)
    ref_kind      TEXT,   -- duty / power / zero / hx_cold
    balance_state TEXT,   -- ok 通过 / bad 超差 / no_data 无法校验 / skip 不校验
    balance_note  TEXT,   -- 人话说明(含差值、容差、比值)
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
CREATE INDEX IF NOT EXISTS idx_conn_case   ON connections(case_id);
CREATE INDEX IF NOT EXISTS idx_conn_block  ON connections(case_id, block);
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
    _migrate(conn)
    conn.commit()


# 老库升级:CREATE TABLE IF NOT EXISTS 不会给已有表加列,只能逐个补
_ADDED_COLUMNS = (
    ("unit_gaps", "INTEGER"),
    ("unit_gaps_json", "TEXT"),
)

# streams 表后加的列(焓)
_STREAM_ADDED_COLUMNS = (
    ("enthalpy_kjkg", "REAL"),
    ("enthalpy_kjkmol", "REAL"),
    ("enthalpy_flow_kw", "REAL"),
)

# stream_components 表后加的列(组分质量流量,物料平衡表用)
_COMPONENT_ADDED_COLUMNS = (("mass_flow_kgh", "REAL"),)

# blocks 表后加的列(焓平衡自检 + 第二期泵参数)
_BLOCK_ADDED_COLUMNS = (
    ("dh_kw", "REAL"),
    ("ref_kw", "REAL"),
    ("ref_kind", "TEXT"),
    ("balance_state", "TEXT"),
    ("balance_note", "TEXT"),
    # 泵参数:老库补列后历史工况是 NULL,重新读一次才有值
    ("head_m", "REAL"),
    ("flow_m3h", "REAL"),
    ("pump_eff", "REAL"),
    ("p_in_bar", "REAL"),
    ("p_out_bar", "REAL"),
    ("dp_bar", "REAL"),
    ("rho_kgm3", "REAL"),
    ("pump_vap_frac", "REAL"),
    ("motor_kw", "REAL"),
    # 饱和蒸气压 bar:读取工况时自动取进口流股的泡点压力(Aspen 现算值);
    # 读不到才留空,页面可逐台手填 —— 程序自己不做估算
    ("pv_bar", "REAL"),
    # 来源标记:aspen / manual。老库补列后是 NULL(历史工况没有这个信息)
    ("pv_source", "TEXT"),
    # 设备进出口温度 / 冷侧温度 / 出口汽化率:老库补列后 NULL,
    # 可以用 repair_cases 从已存的流股 + 连接关系离线回填,不必回 Aspen
    ("inlet_temp_c", "REAL"),
    ("outlet_temp_c", "REAL"),
    ("cold_in_temp_c", "REAL"),
    ("cold_out_temp_c", "REAL"),
    ("outlet_vap_frac", "REAL"),
    # 塔的冷凝器 / 再沸器负荷:老库补列后 NULL,重读工况才有值
    ("cond_duty_kw", "REAL"),
    ("reb_duty_kw", "REAL"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    have = {row["name"] for row in conn.execute("PRAGMA table_info(cases)")}
    for name, ddl in _ADDED_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE cases ADD COLUMN {name} {ddl}")
    have_stream = {row["name"] for row in conn.execute("PRAGMA table_info(streams)")}
    for name, ddl in _STREAM_ADDED_COLUMNS:
        if name not in have_stream:
            conn.execute(f"ALTER TABLE streams ADD COLUMN {name} {ddl}")
    have_comp = {row["name"] for row in conn.execute("PRAGMA table_info(stream_components)")}
    for name, ddl in _COMPONENT_ADDED_COLUMNS:
        if name not in have_comp:
            conn.execute(f"ALTER TABLE stream_components ADD COLUMN {name} {ddl}")
    have_block = {row["name"] for row in conn.execute("PRAGMA table_info(blocks)")}
    for name, ddl in _BLOCK_ADDED_COLUMNS:
        if name not in have_block:
            conn.execute(f"ALTER TABLE blocks ADD COLUMN {name} {ddl}")


# ---------------------------------------------------------------- 写入


def save_case(conn: sqlite3.Connection, case: dict) -> int:
    """把 read_case() 的结果整份入库,返回 case_id。"""
    conv = case.get("convergence", {})
    model = case.get("model", {})
    run = conv.get("run", {})
    stats = case.get("stats", {})
    gaps = case.get("unit_gaps") or {}

    cur = conn.execute(
        """
        INSERT INTO cases (model_path, model_name, read_at, run_id, run_date, run_user,
                           aspen_version, severity, converged, reasons_json, errtxt,
                           status_json, tear_json, block_errors, block_warnings,
                           unit_gaps, unit_gaps_json,
                           stream_count, block_count, elapsed_sec)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            gaps.get("count"),
            json.dumps(gaps, ensure_ascii=False),
            stats.get("stream_count"),
            stats.get("block_count"),
            stats.get("elapsed_sec"),
        ),
    )
    case_id = int(cur.lastrowid)

    conn.executemany(
        """
        INSERT INTO streams (case_id, name, temperature_c, pressure_bar, mass_flow_kgh,
                             mole_flow_kmolh, vap_frac, vol_flow_m3h,
                             enthalpy_kjkg, enthalpy_kjkmol, enthalpy_flow_kw, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
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
                s.get("enthalpy_kjkg"),
                s.get("enthalpy_kjkmol"),
                s.get("enthalpy_flow_kw"),
                json.dumps(s.get("raw", {}), ensure_ascii=False),
            )
            for s in case.get("streams", [])
        ],
    )

    conn_rows = [
        (
            case_id,
            c.get("block"),
            c.get("direction"),
            c.get("stream"),
            c.get("side") or "",
            c.get("port") or "",
            c.get("stage"),
            c.get("source"),
            c.get("seq"),
        )
        for c in case.get("connections", [])
    ]
    if conn_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO connections
                (case_id, block, direction, stream, side, port, stage, source, seq)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            conn_rows,
        )

    comp_rows = []
    for stream in case.get("streams", []):
        for comp in stream.get("composition") or []:
            comp_rows.append(
                (
                    case_id,
                    stream["name"],
                    comp["component"],
                    comp.get("x_liq"),
                    comp.get("y_vap"),
                    comp.get("mass_flow_kgh"),
                )
            )
    if comp_rows:
        conn.executemany(
            """
            INSERT INTO stream_components
                (case_id, stream, component, x_liq, y_vap, mass_flow_kgh)
            VALUES (?,?,?,?,?,?)
            """,
            comp_rows,
        )

    conn.executemany(
        """
        INSERT INTO blocks (case_id, name, type, duty_kw, power_kw, area_m2, nstage,
                            top_temp_c, bottom_temp_c, status, message,
                            head_m, flow_m3h, pump_eff, p_in_bar, p_out_bar, dp_bar,
                            rho_kgm3, pump_vap_frac, motor_kw, pv_bar, pv_source,
                            inlet_temp_c, outlet_temp_c, cold_in_temp_c, cold_out_temp_c,
                            outlet_vap_frac, cond_duty_kw, reb_duty_kw,
                            dh_kw, ref_kw, ref_kind, balance_state, balance_note, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                b.get("head_m"),
                b.get("flow_m3h"),
                b.get("pump_eff"),
                b.get("p_in_bar"),
                b.get("p_out_bar"),
                b.get("dp_bar"),
                b.get("rho_kgm3"),
                b.get("pump_vap_frac"),
                b.get("motor_kw"),
                b.get("pv_bar"),
                b.get("pv_source"),
                b.get("inlet_temp_c"),
                b.get("outlet_temp_c"),
                b.get("cold_in_temp_c"),
                b.get("cold_out_temp_c"),
                b.get("outlet_vap_frac"),
                b.get("cond_duty_kw"),
                b.get("reb_duty_kw"),
                (b.get("balance") or {}).get("dh_kw"),
                (b.get("balance") or {}).get("ref_kw"),
                (b.get("balance") or {}).get("ref_kind"),
                (b.get("balance") or {}).get("balance_state"),
                (b.get("balance") or {}).get("balance_note"),
                json.dumps(b.get("raw", {}), ensure_ascii=False),
            )
            for b in case.get("blocks", [])
        ],
    )

    stage_rows = []
    for col in case.get("columns", []):
        for st in col.get("stages", []):
            stage_rows.append(
                (
                    case_id,
                    col["block"],
                    st.get("stage"),
                    st.get("temperature_c"),
                    st.get("pressure_bar"),
                    st.get("liq_flow"),
                    st.get("vap_flow"),
                    st.get("duty_kw"),
                )
            )
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


def inherit_pump_pv(conn: sqlite3.Connection, case_id: int) -> dict | None:
    """把**同一模型**更早工况里填过的 p_v 按位号搬到当前工况。

    只填空着的行(用户已经手填的不覆盖),只认同一个模型文件名 ——
    不同模型里同一个位号未必是同一种液体,不能瞎搬。
    返回 {source_case_id, source_read_at, copied, skipped};没有可沿用的返回 source_case_id=None。
    """
    target = conn.execute("SELECT id, model_name FROM cases WHERE id=?", (case_id,)).fetchone()
    if target is None:
        return None
    source = conn.execute(
        """
        SELECT c.id AS id, c.read_at AS read_at, COUNT(*) AS n
          FROM cases c JOIN blocks b ON b.case_id = c.id
         WHERE c.id != ?
           AND b.pv_bar IS NOT NULL
           AND COALESCE(c.model_name, '') = COALESCE(?, '')
         GROUP BY c.id
         ORDER BY c.id DESC
         LIMIT 1
        """,
        (case_id, target["model_name"]),
    ).fetchone()
    if source is None:
        return {"source_case_id": None, "source_read_at": None, "copied": 0, "skipped": 0}

    rows = conn.execute(
        "SELECT name, pv_bar, pv_source FROM blocks WHERE case_id=? AND pv_bar IS NOT NULL",
        (source["id"],),
    ).fetchall()
    copied = skipped = 0
    for row in rows:
        cur = conn.execute(
            "UPDATE blocks SET pv_bar=?, pv_source=? WHERE case_id=? AND name=? AND pv_bar IS NULL",
            (row["pv_bar"], row["pv_source"], case_id, row["name"]),
        )
        if cur.rowcount:
            copied += 1
        else:
            skipped += 1
    conn.commit()
    return {
        "source_case_id": source["id"],
        "source_read_at": source["read_at"],
        "copied": copied,
        "skipped": skipped,
    }


# ---------------------------------------------------------------- 查询


def set_pump_pv(
    conn: sqlite3.Connection, case_id: int, block: str, pv_bar, source: str | None = "manual"
) -> bool:
    """写入一台泵的饱和蒸气压(bar)与来源。传 None 表示清空(来源一并清空)。

    口径:p_v 优先取 Aspen 进口流股的泡点压力(见 docs/aspen-field-map.md §13.2);
    这里只处理**手填补位**——Aspen 没给出读数、或用户要改用厂家实测值时才写,
    来源记为 manual,页面参数卡上会标出来。重新读取工况会用 Aspen 的值覆盖手填值。
    NPSHₐ 直接拿它与 Aspen 的进口压力相减,所以两者必须同基准。
    返回是否真的更新到了行(设备名写错时返回 False,不静默成功)。
    """
    src = source if pv_bar is not None else None
    cur = conn.execute(
        "UPDATE blocks SET pv_bar=?, pv_source=? WHERE case_id=? AND name=?",
        (pv_bar, src, case_id, block),
    )
    conn.commit()
    return cur.rowcount > 0


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


def read_connections(conn: sqlite3.Connection, case_id: int) -> list[sqlite3.Row]:
    """某个工况的设备 ↔ 流股连接关系(按写入顺序)。"""
    return conn.execute(
        "SELECT block, direction, stream, side, port, stage, source, seq "
        "FROM connections WHERE case_id = ? ORDER BY seq, block, direction, stream",
        (case_id,),
    ).fetchall()


def read_column_stages(conn: sqlite3.Connection, case_id: int, block: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM column_stages WHERE case_id = ? AND block = ? ORDER BY stage",
        (case_id, block),
    ).fetchall()


def balance_summary(conn: sqlite3.Connection, case_id: int) -> dict:
    """某个工况的焓平衡四态台数(给首页仪表盘用)。"""
    counts = {"ok": 0, "bad": 0, "no_data": 0, "skip": 0}
    for row in conn.execute(
        "SELECT balance_state, COUNT(*) AS n FROM blocks WHERE case_id = ? GROUP BY balance_state",
        (case_id,),
    ):
        key = row["balance_state"]
        if key in counts:
            counts[key] = row["n"]
    return counts


def list_columns(conn: sqlite3.Connection, case_id: int) -> list[sqlite3.Row]:
    """某个工况里存了剖面的塔。"""
    return conn.execute(
        "SELECT block, COUNT(*) AS stage_count FROM column_stages "
        "WHERE case_id = ? GROUP BY block ORDER BY block",
        (case_id,),
    ).fetchall()


# ---------------------------------------------------------------- 流股组成(按需读)


def read_stream_composition(
    conn: sqlite3.Connection, case_id: int, stream: str
) -> list[sqlite3.Row]:
    """某条流股已存的组成;没读过返回空列表(不区分"0 组分")。"""
    return conn.execute(
        "SELECT component, x_liq, y_vap, mass_flow_kgh FROM stream_components "
        "WHERE case_id = ? AND stream = ? ORDER BY component",
        (case_id, stream),
    ).fetchall()


def save_stream_composition(
    conn: sqlite3.Connection, case_id: int, stream: str, rows: list[dict]
) -> int:
    """把一条流股的组成整条替换进工况库,返回写入条数。

    只有"点开看过"的流股才会走到这里 —— 组成默认不读,全量读会让耗时从 9 s 涨到 41 s。

    先删旧记录再插入,并且同一个事务:只做 INSERT OR REPLACE 时,若这次读出的
    组分比上次少(fill_composition.py --force 重读就是这样),多出来的旧组分
    会留在库里,页面和 AI 导出会把新旧两版混在一起。空列表也要清 —— 那表示
    "这次确实没有组分",不是"别动库里那份"。
    """
    try:
        conn.execute(
            "DELETE FROM stream_components WHERE case_id = ? AND stream = ?",
            (case_id, stream),
        )
        if rows:
            conn.executemany(
                """
                INSERT INTO stream_components
                    (case_id, stream, component, x_liq, y_vap, mass_flow_kgh)
                VALUES (?,?,?,?,?,?)
                """,
                [
                    (
                        case_id,
                        stream,
                        r["component"],
                        r.get("x_liq"),
                        r.get("y_vap"),
                        r.get("mass_flow_kgh"),
                    )
                    for r in rows
                ],
            )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return len(rows)


def list_composed_streams(conn: sqlite3.Connection, case_id: int) -> list[str]:
    """这个工况里已经读过组成的流股名。"""
    return [
        r["stream"]
        for r in conn.execute(
            "SELECT DISTINCT stream FROM stream_components WHERE case_id = ? ORDER BY stream",
            (case_id,),
        )
    ]


def read_all_composition(conn: sqlite3.Connection, case_id: int) -> list[sqlite3.Row]:
    """整个工况已存的组成(按流股、组分排序),给 AI 分析包用。"""
    return conn.execute(
        "SELECT stream, component, x_liq, y_vap, mass_flow_kgh FROM stream_components "
        "WHERE case_id = ? ORDER BY stream, component",
        (case_id,),
    ).fetchall()


def delete_case(conn: sqlite3.Connection, case_id: int) -> dict:
    """删掉一个工况及其全部明细,返回各表删除的行数。

    明细表显式删除,不依赖 `ON DELETE CASCADE`(外键 pragma 在不同连接上未必生效)。
    """
    counts: dict[str, int] = {}
    for table in ("connections", "stream_components", "column_stages", "blocks", "streams"):
        cur = conn.execute(f"DELETE FROM {table} WHERE case_id = ?", (case_id,))
        counts[table] = cur.rowcount
    conn.execute("DELETE FROM cases WHERE id = ?", (case_id,))
    conn.commit()
    return counts
