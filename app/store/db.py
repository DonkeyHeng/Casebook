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
import time


def _now() -> str:
    """本地时间戳(只用于"什么时候填的"这种展示,不参与任何计算)。"""
    return time.strftime("%Y-%m-%d %H:%M:%S")


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS cases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    model_path    TEXT NOT NULL,
    model_name    TEXT,
    model_hash    TEXT,          -- 模型文件内容 sha256;老库补列后是 NULL,按文件名兜底
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

-- 组分字典(模型级,一工况一套):CAS 号是跨模型对齐组分的稳定键。
-- 老库建库时这张表还不存在,CREATE TABLE IF NOT EXISTS 会直接补上;
-- 历史工况里 cas / dbname 是 NULL(当初没读过),重读一次 Aspen 才有值。
CREATE TABLE IF NOT EXISTS components (
    case_id   INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    component TEXT    NOT NULL,
    -- 饱和蒸气压/汽蚀余量都按组分算,CAS 用来查中文名、做跨工况对齐
    cas       TEXT,
    -- Aspen 数据库名(如 ACROLEIN / 1,3-PROPANEDIOL),人工核对中文名时对照用
    dbname    TEXT,
    PRIMARY KEY (case_id, component)
);

-- 手填的组分 CAS(按模型挂,不按工况):
-- 重读工况会整份重建 components 表,写那里会被冲掉;组分本质上是"模型"的属性。
CREATE TABLE IF NOT EXISTS component_cas (
    model_name TEXT    NOT NULL,
    component  TEXT    NOT NULL,
    cas        TEXT    NOT NULL,
    updated_at TEXT,
    PRIMARY KEY (model_name, component)
);

-- 手动给的"设计扬程"修正项(按模型+位号):位压头 ΔZ 与管路阻力 Σhf,单位 m。
-- Aspen 的泵扬程只有压升那一部分,这两项是几何/管路信息,只能人来填。
-- l_m(管长)与 zeta_json(管件数量 + 手填 Σζ)是算 Σhf 的人填输入,一并持久化,
-- 免得刷新页面后管件数量丢失、Σζ 却还留着(两处口径会对不上)。
-- 缺项一律 NULL:程序不替用户补 0,也不给默认数量。
CREATE TABLE IF NOT EXISTS pump_design (
    model_name TEXT    NOT NULL,
    block      TEXT    NOT NULL,
    dz_m       REAL,
    hf_m       REAL,
    l_m        REAL,
    zeta_json  TEXT,
    updated_at TEXT,
    PRIMARY KEY (model_name, block)
);

-- 泵选型台账(按模型身份 + 位号)。只存**人工决定**的字段。
-- 流量 / 扬程 / 选型扬程 / NPSHₐ / 三态判定一律按当前工况实时算,不落库:
-- 数值以工况为准,不从外部 Excel 导入、不做反推(见 docs/pump-selection-plan.md §7)。
CREATE TABLE IF NOT EXISTS pump_selection (
    model_key  TEXT    NOT NULL,   -- model_identity.key_of():内容哈希优先,文件名兜底
    block      TEXT    NOT NULL,
    model_no   TEXT,               -- 型号
    pump_name  TEXT,               -- 名称
    qty        INTEGER,            -- 台数
    material   TEXT,               -- 材质
    motor_kw   REAL,               -- 配套电机 kW(人工决定,不等于程序建议值)
    npshr_m    REAL,               -- NPSHᵣ m;暂缓,空着就是「无法判定」
    margin_m   REAL,               -- 汽蚀余量 m,§6.3 建议 0.5~1.0
    note       TEXT,
    updated_at TEXT,
    PRIMARY KEY (model_key, block)
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
    # 模型身份加固:老库补列后是 NULL,key_of() 会退回文件名,历史数据不失效
    ("model_hash", "TEXT"),
)

# streams 表后加的列(焓)
_STREAM_ADDED_COLUMNS = (
    ("enthalpy_kjkg", "REAL"),
    ("enthalpy_kjkmol", "REAL"),
    ("enthalpy_flow_kw", "REAL"),
    # Aspen 节点属性 6 的可读类型(MATERIAL / HEAT / WORK),§19.1。
    # 老库是 NULL:选型侧按保守的名字 + 空结果判据退化,不误删物料流股。
    ("stream_type", "TEXT"),
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
    # 泵进口流股粘度 cP:算 Re 用。模型物性集里没挂 VISC 时读不到(NULL),页面手填
    ("mu_cp", "REAL"),
    ("reb_duty_kw", "REAL"),
)

# pump_design 表后加的列(管路阻力半自动的人填输入)。
# 老库补列后历史记录是 NULL,页面按"没填过"显示,不回算、不猜值。
_PUMP_DESIGN_ADDED_COLUMNS = (
    ("l_m", "REAL"),
    ("zeta_json", "TEXT"),
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
    have_pump_design = {row["name"] for row in conn.execute("PRAGMA table_info(pump_design)")}
    for name, ddl in _PUMP_DESIGN_ADDED_COLUMNS:
        if name not in have_pump_design:
            conn.execute(f"ALTER TABLE pump_design ADD COLUMN {name} {ddl}")


# ---------------------------------------------------------------- 写入


def save_case(conn: sqlite3.Connection, case: dict) -> int:
    """把 read_case() 的结果整份入库,返回 case_id。"""
    conv = case.get("convergence", {})
    model = case.get("model", {})
    run = conv.get("run", {})
    stats = case.get("stats", {})
    gaps = case.get("unit_gaps") or {}

    # 局部导入:model_identity 只做本地文件读取,与 store 层没有循环依赖
    from app.design import model_identity

    cur = conn.execute(
        """
        INSERT INTO cases (model_path, model_name, model_hash, read_at, run_id,
                           run_date, run_user, aspen_version, severity, converged,
                           reasons_json, errtxt, status_json, tear_json,
                           block_errors, block_warnings, unit_gaps, unit_gaps_json,
                           stream_count, block_count, elapsed_sec)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            model.get("path"),
            model.get("name"),
            model_identity.hash_file(model.get("path")),
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
        INSERT INTO streams (
            case_id, name, temperature_c, pressure_bar, mass_flow_kgh,
            mole_flow_kmolh, vap_frac, vol_flow_m3h,
            enthalpy_kjkg, enthalpy_kjkmol, enthalpy_flow_kw, stream_type, raw_json
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                s.get("stream_type") or None,
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

    meta_rows = [
        (case_id, m.get("component"), m.get("cas"), m.get("dbname"))
        for m in (case.get("component_meta") or [])
        if m.get("component")
    ]
    # 组分字典是模型级、每次读取都是完整一份:先清后插,避免上一次的残留变成幽灵行
    conn.execute("DELETE FROM components WHERE case_id = ?", (case_id,))
    if meta_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO components (case_id, component, cas, dbname)
            VALUES (?,?,?,?)
            """,
            meta_rows,
        )

    conn.executemany(
        """
        INSERT INTO blocks (case_id, name, type, duty_kw, power_kw, area_m2, nstage,
                            top_temp_c, bottom_temp_c, status, message,
                            head_m, flow_m3h, pump_eff, p_in_bar, p_out_bar, dp_bar,
                            rho_kgm3, pump_vap_frac, mu_cp, motor_kw, pv_bar, pv_source,
                            inlet_temp_c, outlet_temp_c, cold_in_temp_c, cold_out_temp_c,
                            outlet_vap_frac, cond_duty_kw, reb_duty_kw,
                            dh_kw, ref_kw, ref_kind, balance_state, balance_note, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                b.get("mu_cp"),
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

    只填空着的行(用户已经手填的不覆盖),只认同一个模型身份键
    (内容哈希优先、老工况退回文件名) —— 不同模型里同一个位号未必是
    同一种液体,不能瞎搬。
    返回 {source_case_id, source_read_at, copied, skipped};没有可沿用的返回 source_case_id=None。
    """
    from app.design import model_identity

    target = conn.execute(
        "SELECT id, model_hash, model_name FROM cases WHERE id=?", (case_id,)
    ).fetchone()
    if target is None:
        return None
    key = model_identity.key_of(target["model_hash"], target["model_name"])
    if not key:
        return {"source_case_id": None, "source_read_at": None, "copied": 0, "skipped": 0}
    source = conn.execute(
        """
        SELECT c.id AS id, c.read_at AS read_at, COUNT(*) AS n
          FROM cases c JOIN blocks b ON b.case_id = c.id
         WHERE c.id != ?
           AND b.pv_bar IS NOT NULL
           AND (CASE WHEN c.model_hash IS NOT NULL AND c.model_hash <> ''
                     THEN 'sha256:' || c.model_hash
                     ELSE COALESCE(c.model_name, '') END) = ?
         GROUP BY c.id
         ORDER BY c.id DESC
         LIMIT 1
        """,
        (case_id, key),
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


def read_components(conn: sqlite3.Connection, case_id: int) -> list[sqlite3.Row]:
    """这个工况已存的组分字典;没读过返回空列表(调用方据此决定要不要去 Aspen 现读)。"""
    return conn.execute(
        "SELECT component, cas, dbname FROM components WHERE case_id = ? ORDER BY component",
        (case_id,),
    ).fetchall()


def save_components(conn: sqlite3.Connection, case_id: int, rows: list[dict]) -> int:
    """整份替换组分字典,返回写入条数。

    和组成一样整份替换:同一份模型重新读过之后,组分可能变少,
    留着旧行会让页面显示出上一版模型的组分。
    """
    try:
        conn.execute("DELETE FROM components WHERE case_id = ?", (case_id,))
        if rows:
            conn.executemany(
                """
                INSERT OR REPLACE INTO components (case_id, component, cas, dbname)
                VALUES (?,?,?,?)
                """,
                [
                    (case_id, r.get("component"), r.get("cas"), r.get("dbname"))
                    for r in rows
                    if r.get("component")
                ],
            )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return len(rows)


def read_component_cas(conn: sqlite3.Connection, model_name: str) -> dict[str, str]:
    """这个模型手填过的组分 CAS:{组分 → CAS};没填过返回空 dict。"""
    name = str(model_name or "").strip()
    if not name:
        return {}
    return {
        r["component"]: r["cas"]
        for r in conn.execute(
            "SELECT component, cas FROM component_cas WHERE model_name = ? ORDER BY component",
            (name,),
        )
    }


def save_component_cas(
    conn: sqlite3.Connection, model_name: str, component: str, cas: str | None
) -> bool:
    """写一条手填 CAS;cas 为空表示清除。返回是否写到了东西。"""
    name = str(model_name or "").strip()
    comp = str(component or "").strip()
    if not name or not comp:
        return False
    if not cas:
        cur = conn.execute(
            "DELETE FROM component_cas WHERE model_name = ? AND component = ?", (name, comp)
        )
        conn.commit()
        return cur.rowcount > 0
    conn.execute(
        """
        INSERT INTO component_cas (model_name, component, cas, updated_at)
        VALUES (?,?,?,?)
        ON CONFLICT(model_name, component)
        DO UPDATE SET cas = excluded.cas, updated_at = excluded.updated_at
        """,
        (name, comp, str(cas), _now()),
    )
    conn.commit()
    return True


def previous_case_same_model(conn: sqlite3.Connection, case_id: int) -> int | None:
    """同一模型里更早的一个工况 id;没有返回 None。

    按模型身份键匹配(内容哈希优先、老工况退回文件名),与
    `model_identity.key_of()` 口径一致 —— 别只改这一处。
    """
    from app.design import model_identity

    target = conn.execute(
        "SELECT model_hash, model_name FROM cases WHERE id = ?", (case_id,)
    ).fetchone()
    if target is None:
        return None
    key = model_identity.key_of(target["model_hash"], target["model_name"])
    if not key:
        return None
    row = conn.execute(
        """
        SELECT id FROM cases
        WHERE id < ?
          AND (CASE WHEN model_hash IS NOT NULL AND model_hash <> ''
                    THEN 'sha256:' || model_hash
                    ELSE COALESCE(model_name, '') END) = ?
        ORDER BY id DESC LIMIT 1
        """,
        (case_id, key),
    ).fetchone()
    return int(row["id"]) if row else None


def read_pump_design(conn: sqlite3.Connection, model_name: str, block: str) -> dict | None:
    """某台泵手填的设计参数;没填过返回 None。

    zeta_json 原样透传(不解析):库里万一存了坏 JSON,也不能让 ΔZ / Σhf 一起读不出来。
    """
    row = conn.execute(
        "SELECT dz_m, hf_m, l_m, zeta_json FROM pump_design WHERE model_name = ? AND block = ?",
        (str(model_name or "").strip(), str(block or "").strip()),
    ).fetchone()
    if row is None:
        return None
    return {
        "dz_m": row["dz_m"],
        "hf_m": row["hf_m"],
        "l_m": row["l_m"],
        "zeta_json": row["zeta_json"],
    }


def save_pump_design(
    conn: sqlite3.Connection,
    model_name: str,
    block: str,
    dz_m,
    hf_m,
    l_m=None,
    zeta_json: str | None = None,
) -> bool:
    """写一台泵的手填设计参数;所有项都空表示清除。返回是否写到东西。

    zeta_json 是 API 层校验过的 JSON 文本,库层只存不解析,也不补默认值。
    新增参数放在末尾且可缺省,老调用点 `save_pump_design(c, m, b, dz, hf)` 语义不变。
    """
    name = str(model_name or "").strip()
    key = str(block or "").strip()
    if not name or not key:
        return False
    if all(v is None for v in (dz_m, hf_m, l_m, zeta_json)):
        cur = conn.execute(
            "DELETE FROM pump_design WHERE model_name = ? AND block = ?", (name, key)
        )
        conn.commit()
        return cur.rowcount > 0
    conn.execute(
        """
        INSERT INTO pump_design
            (model_name, block, dz_m, hf_m, l_m, zeta_json, updated_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(model_name, block)
        DO UPDATE SET dz_m = excluded.dz_m, hf_m = excluded.hf_m,
                      l_m = excluded.l_m, zeta_json = excluded.zeta_json,
                      updated_at = excluded.updated_at
        """,
        (name, key, dz_m, hf_m, l_m, zeta_json, _now()),
    )
    conn.commit()
    return True


# ---------------------------------------------------------------- 选型台账

SELECTION_FIELDS = (
    "model_no",
    "pump_name",
    "qty",
    "material",
    "motor_kw",
    "npshr_m",
    "margin_m",
    "note",
)


def model_key(conn: sqlite3.Connection, case_id: int) -> str:
    """这个工况的模型身份键;没这个工况返回空串。

    所有「按模型挂」的读写都走这里,别再各自从 cases 里直接取 model_name,
    否则身份口径又会出现只改一处的分叉。
    """
    from app.design import model_identity

    row = conn.execute(
        "SELECT model_hash, model_name FROM cases WHERE id = ?", (case_id,)
    ).fetchone()
    if row is None:
        return ""
    return model_identity.key_of(row["model_hash"], row["model_name"])


def read_pump_selection(conn: sqlite3.Connection, key: str, block: str) -> dict | None:
    """某台泵手填的选型结果;没填过返回 None。"""
    key = str(key or "").strip()
    name = str(block or "").strip()
    if not key or not name:
        return None
    row = conn.execute(
        f"SELECT {', '.join(SELECTION_FIELDS)} FROM pump_selection "
        "WHERE model_key = ? AND block = ?",
        (key, name),
    ).fetchone()
    return dict(row) if row is not None else None


def save_pump_selection(conn: sqlite3.Connection, key: str, block: str, values: dict) -> bool:
    """写一台泵的选型结果;所有字段都空表示清除。返回是否写到东西。

    values 已由 API 层校验,这里只做持久化:不补默认值、不算任何数值。
    只更新传进来的字段,没传的保留原值(支持只改一个字段)。
    """
    key = str(key or "").strip()
    name = str(block or "").strip()
    if not key or not name:
        return False
    fields = [f for f in SELECTION_FIELDS if f in values]
    vals = [values[f] for f in fields]
    if not vals or all(v is None or v == "" for v in vals):
        cur = conn.execute(
            "DELETE FROM pump_selection WHERE model_key = ? AND block = ?", (key, name)
        )
        conn.commit()
        return cur.rowcount > 0
    cols = ", ".join(("model_key", "block", *fields, "updated_at"))
    marks = ", ".join("?" for _ in range(len(fields) + 3))
    updates = ", ".join(f"{f} = excluded.{f}" for f in fields)
    conn.execute(
        f"""
        INSERT INTO pump_selection ({cols}) VALUES ({marks})
        ON CONFLICT(model_key, block)
        DO UPDATE SET {updates}, updated_at = excluded.updated_at
        """,
        (key, name, *vals, _now()),
    )
    conn.commit()
    return True


def read_cas_map(
    conn: sqlite3.Connection, case_id: int, model_name: str | None = None
) -> dict[str, str]:
    """组分 → CAS 的合并视图:Aspen / 模型文件读到的优先,读不到才用手填的。

    手填只补空缺 —— 读得到的时候以模型为准,免得手误覆盖真实读数。
    手填表按**模型身份键**取(hash 优先、老工况退回文件名);第三参数只作
    库里查不到该工况时的兜底,调用方不必再自己拼 model_name。
    """
    out = {
        r["component"]: r["cas"]
        for r in conn.execute(
            "SELECT component, cas FROM components WHERE case_id = ? AND cas IS NOT NULL",
            (case_id,),
        )
    }
    key = model_key(conn, case_id) or str(model_name or "").strip()
    if key:
        for comp, cas in read_component_cas(conn, key).items():
            out.setdefault(comp, cas)
    return out


def delete_case(conn: sqlite3.Connection, case_id: int) -> dict:
    """删掉一个工况及其全部明细,返回各表删除的行数。

    明细表显式删除,不依赖 `ON DELETE CASCADE`(外键 pragma 在不同连接上未必生效)。
    """
    counts: dict[str, int] = {}
    for table in (
        "components",
        "connections",
        "stream_components",
        "column_stages",
        "blocks",
        "streams",
    ):
        cur = conn.execute(f"DELETE FROM {table} WHERE case_id = ?", (case_id,))
        counts[table] = cur.rowcount
    conn.execute("DELETE FROM cases WHERE id = ?", (case_id,))
    conn.commit()
    return counts
