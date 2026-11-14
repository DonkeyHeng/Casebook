"""物料平衡表(宽表):流股做列、属性做行 —— 纯函数。

为什么是宽表
------------
竞赛设计文档里的「物料平衡书」不是逐设备小表,而是一张张**宽表**:一个工段
(或全流程)一行列出一组进出口流股,行是温度 / 压力 / 流量 / 各组分流量,
表格底部给出合计。这样读者一眼能看完一个工段的总进总出,而不是翻几十张
两行小表。本模块产出这种表要用的数据,渲染在 `app/api/report_export.py`。

三张表,三种列
--------------
1. **工段表**:列 = 该工段确认的进界流股 + 出界流股(边界来自
   `data/config/sections.json`,与界区衡算同一套);
2. **全流程表**:列 = 没有上游的流股(外部进料) + 没有下游的流股(出界产物)。
   这两类流股就是全流程的进出口,不依赖工段划分;
3. **总结表**:原料 / 出口的年化量(按年运行小时数折算)。

组分怎么算
----------
* 摩尔流量(组分):`总摩尔流量 ×[(1−汽化率)·x_liq + 汽化率·y_vap]`,纯算,老库也能出;
* 质量流量(组分):直接取库里 `stream_components.mass_flow_kgh`
  (来自 Aspen 的 `MASSFLOW3`,见 docs/aspen-field-map.md §4.2)。
  老工况这一列是空的(要重读一次工况才有),此时**整段不出**,并让报告注明,
  不用分子量反算 —— 分子量反算等于替用户猜。
"""

from __future__ import annotations

from app.design import mass_balance

# 相态文字
PHASE_TEXT = {"liq": "液相", "vap": "气相", "two": "两相", "": "—"}


def _r(value, digits: int = 2):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def phase_text(vap_frac) -> str:
    """汽化率 → 相态文字。取不到的留「—」,不当液体。"""
    if vap_frac is None:
        return PHASE_TEXT[""]
    try:
        value = float(vap_frac)
    except (TypeError, ValueError):
        return PHASE_TEXT[""]
    if value <= 1e-9:
        return PHASE_TEXT["liq"]
    if value >= 1 - 1e-9:
        return PHASE_TEXT["vap"]
    return f"{PHASE_TEXT['two']}({value:.3f})"


def _mole_fraction(comp_row, vap_frac) -> float | None:
    """按汽化率把 x_liq / y_vap 合成该流股的摩尔分率。"""
    x = comp_row.get("x_liq")
    y = comp_row.get("y_vap")
    if x is None and y is None:
        return None
    if vap_frac is None:
        return x if x is not None else y
    try:
        f = float(vap_frac)
    except (TypeError, ValueError):
        f = 0.0
    if f <= 1e-9:
        return x if x is not None else y
    if f >= 1 - 1e-9:
        return y if y is not None else x
    if x is None or y is None:
        return None
    return (1.0 - f) * float(x) + f * float(y)


def _component_names(composition: dict[str, list[dict]]) -> list[str]:
    """全部流股出现过的组分,按出现顺序去重(与 Aspen 里定义顺序一致)。"""
    seen: list[str] = []
    for rows in composition.values():
        for row in rows or []:
            name = row.get("component")
            if name and name not in seen:
                seen.append(name)
    return seen


def _column(stream: dict, composition: dict[str, list[dict]], names: list[str]) -> dict:
    """一条流股 → 一列(属性 + 组分)。"""
    name = stream["name"]
    vap = stream.get("vap_frac")
    comp_rows = composition.get(name) or []
    by_comp = {r.get("component"): r for r in comp_rows}
    mole = stream.get("mole_flow_kmolh")

    mole_comp: dict[str, float | None] = {}
    mass_comp: dict[str, float | None] = {}
    for comp in names:
        row = by_comp.get(comp)
        if row is None:
            mole_comp[comp] = None
            mass_comp[comp] = None
            continue
        frac = _mole_fraction(row, vap)
        mole_comp[comp] = None if (frac is None or mole is None) else frac * float(mole)
        mass_comp[comp] = row.get("mass_flow_kgh")

    return {
        "stream": name,
        "phase": phase_text(vap),
        "temperature_c": stream.get("temperature_c"),
        "pressure_bar": stream.get("pressure_bar"),
        "mass_flow_kgh": stream.get("mass_flow_kgh"),
        "mole_flow_kmolh": stream.get("mole_flow_kmolh"),
        "vol_flow_m3h": stream.get("vol_flow_m3h"),
        "vap_frac": vap,
        "mole_comp": mole_comp,
        "mass_comp": mass_comp,
    }


def _mass_total(columns) -> float | None:
    total = 0.0
    for col in columns:
        value = col.get("mass_flow_kgh")
        if value is None:
            return None
        total += float(value)
    return total


def _mass_comp_available(columns) -> bool:
    """组分的质量流量是否读到了(任一条流股有值就算有)。"""
    for col in columns:
        for value in (col.get("mass_comp") or {}).values():
            if value is not None:
                return True
    return False


def _table(key, label, in_columns, out_columns, names) -> dict:
    """一张宽表:进口列 + 出口列,行是属性与组分。"""
    mass_in = _mass_total(in_columns)
    mass_out = _mass_total(out_columns)
    rel = None
    state = mass_balance.NO_DATA
    if mass_in is not None and mass_out is not None and mass_in > 0:
        rel = abs(mass_in - mass_out) / mass_in
        state = mass_balance.PASS if rel <= mass_balance.TOL_REL else mass_balance.FAIL
        if mass_in < mass_balance.MIN_FLOW_KGH and mass_out < mass_balance.MIN_FLOW_KGH:
            state = mass_balance.TOO_SMALL
            rel = None

    return {
        "key": key,
        "label": label,
        "in_columns": in_columns,
        "out_columns": out_columns,
        "components": names,
        "mass_in_kgh": mass_in,
        "mass_out_kgh": mass_out,
        "rel_err": rel,
        "state": state,
        "has_mass_comp": _mass_comp_available(in_columns + out_columns),
    }


def build(streams, connections, parts, composition=None, hours_per_year: float = 8000.0) -> dict:
    """产出物料平衡宽表(工段 + 全流程)与总结数据。

    Args:
        streams: 流股表(需要 name / temperature_c / pressure_bar / mass_flow_kgh /
                 mole_flow_kmolh / vol_flow_m3h / vap_frac)
        connections: 连接表 —— 用来判断「哪些流股没有上游 / 没有下游」
        parts: 确认后的工段边界 {工段: {"label", "in", "out", …}};
               None 或空表示不出工段表(只有全流程表)
        composition: {流股名: [{"component","x_liq","y_vap","mass_flow_kgh"}, …]}
        hours_per_year: 年运行小时数,用来折算 t/a(竞赛通行 8000 h)

    Returns:
        {"zones": [表…], "overall": 表, "components": [组分名…],
         "summary": {"feed": [...], "outlet": [...], "hours_per_year": 8000}}
    """
    stream_map = {s["name"]: s for s in streams or []}
    composition = composition or {}
    names = _component_names(composition)

    def columns(stream_names):
        return [
            _column(stream_map[name], composition, names)
            for name in stream_names
            if name in stream_map
        ]

    zones = []
    for key, info in (parts or {}).items():
        zones.append(
            _table(
                key,
                info.get("label") or key,
                columns(info.get("in") or []),
                columns(info.get("out") or []),
                names,
            )
        )

    # 全流程:没有上游 = 外部进料;没有下游 = 出界产物/三废
    src: dict[str, list[str]] = {}
    dst: dict[str, list[str]] = {}
    for row in connections or []:
        bucket = dst if row.get("direction") == "in" else src
        bucket.setdefault(row["stream"], []).append(row["block"])
    feed = [s["name"] for s in streams or [] if not src.get(s["name"])]
    outlet = [s["name"] for s in streams or [] if not dst.get(s["name"])]
    feed_columns = columns(feed)
    outlet_columns = columns(outlet)
    overall = _table("overall", "全流程", feed_columns, outlet_columns, names)

    def annual(columns_) -> list[dict]:
        rows = []
        for col in columns_:
            mass = col.get("mass_flow_kgh")
            rows.append(
                {
                    "stream": col["stream"],
                    "phase": col["phase"],
                    "mass_flow_kgh": mass,
                    "t_per_year": None
                    if mass is None
                    else float(mass) * float(hours_per_year) / 1000.0,
                }
            )
        return rows

    return {
        "zones": zones,
        "overall": overall,
        "components": names,
        "summary": {
            "feed": annual(feed_columns),
            "outlet": annual(outlet_columns),
            "hours_per_year": hours_per_year,
        },
    }
