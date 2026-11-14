"""物料衡算(质量守恒校验):纯函数,不碰 COM、不读写库。

两层结论,共用同一套容差与状态
------------------------------
1. **界区衡算**(主表,`boundary_build`):每个工段 Σ(进界流股) = Σ(出界流股)。
   边界流股由人确认(`app/design/sections.py` 出草稿),因为「算不算进第一工段」
   只有画流程图的人知道。
2. **逐设备衡算**(辅助表,`build`):每台设备 Σ(进口) = Σ(出口)。
   单进单出设备恒等、误差恒为 0,信息量主要在 Mixer / FSplit / 塔 / 换热器。

两层都是**新结论**,和 `app/aspen/balance.py` 的焓平衡是两码事:
焓平衡判「ΔH 与热负荷/功率对不对」,这里判「质量对不对」。
两者不共用字段、不共用状态,也不互相覆盖 —— 质量结论永远不写进
`blocks.balance_state`。

四个状态

| 状态 | 界区衡算 | 设备衡算 |
| --- | --- | --- |
| `pass` 通过 | 相对误差 ≤ 0.5% | 同 |
| `fail` 超差 | > 0.5%,疑似漏流股 | 同 |
| `no_data` 无法校验 | 没有进界或出界流股 / 流股缺质量流量 / 配置里的流股库里没有 | 缺连接 / 缺流量 |
| `too_small` 流量过小 | 进出合计都 < 1 kg/h | 同 |

为什么容差是纯相对 0.5%
-----------------------
实测 9 个工况:逐设备最大相对误差 0.005058%(Mixer M0207)、界区最大 0.0040%
(case 32 的 01 段),都比 0.5% 小两个数量级 —— 真漏一股流股时误差量级是
10%–100%,阈值不会误伤。但纯相对口径在**极小流量**上会把求解器残差放大成
100% 误差,所以留了「进、出都 < 1 kg/h 就记流量过小」的豁免线。

为什么不猜原因
--------------
只给相对误差,不写「守恒失败」这种超出证据的话。误差大 = 疑似漏流股,
需要人判断;实测数据里没有真漏流股的样本,「标红」路径只由自测夹具验证。
"""
from __future__ import annotations

from app.design import sections as section_rules

PASS = "pass"            # 通过
FAIL = "fail"            # 超差
NO_DATA = "no_data"      # 无法校验(缺流股 / 缺流量)
TOO_SMALL = "too_small"  # 流量过小,相对误差无意义

STATE_TEXT = {
    PASS: "通过",
    FAIL: "超差",
    NO_DATA: "无法校验",
    TOO_SMALL: "流量过小",
}
# 报告里标红的只有 FAIL
BAD_STATES = (FAIL,)

TOL_REL = 0.005                      # 相对容差 0.5%
MIN_FLOW_KGH = 1.0                   # 小流量豁免线:进、出都低于它就不判定

# 两层共用的字段名(报告列头按这个取)
ROW_FIELDS = ("mass_flow_kgh", "mole_flow_kmolh", "temperature_c", "pressure_bar")


def _row(stream, stream_map) -> dict:
    """一条流股 → 报告用的行(空值保持 None,不补 0)。"""
    info = stream_map.get(stream) or {}
    row = {"stream": stream}
    for field in ROW_FIELDS:
        row[field] = info.get(field)
    return row


def _sum(rows, field) -> float | None:
    """加总某一列;只要有 None 就整体 None(缺数据不能当 0 加)。"""
    if not rows:
        return None
    total = 0.0
    for row in rows:
        value = row.get(field)
        if value is None:
            return None
        total += float(value)
    return total


def _judge(mass_in, mass_out) -> tuple[str, float | None]:
    """两层共用的判定:只回状态与相对误差,不拼人话。"""
    if mass_in is None or mass_out is None:
        return NO_DATA, None
    if mass_in <= 0:
        return NO_DATA, None
    if mass_in < MIN_FLOW_KGH and mass_out < MIN_FLOW_KGH:
        return TOO_SMALL, None
    rel = abs(mass_in - mass_out) / mass_in
    return (PASS if rel <= TOL_REL else FAIL), rel


def _missing_streams(rows) -> str:
    """列出缺质量流量的流股名(最多 3 个,防止说明过长)。"""
    names = [r["stream"] for r in rows if r.get("mass_flow_kgh") is None]
    shown = "、".join(names[:3])
    return shown + (" 等" if len(names) > 3 else "")


def _tally(items) -> dict:
    """状态计数 + 最大相对误差。"""
    counts = {state: 0 for state in STATE_TEXT}
    worst = None
    for item in items:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
        rel = item.get("rel_err")
        if rel is not None and (worst is None or rel > worst["rel_err"]):
            worst = {"block": item.get("block") or item.get("key"), "rel_err": rel}
    return dict(counts, total=len(items), worst=worst)


# ---------------------------------------------------------------- 界区衡算

def boundary_build(streams, parts) -> dict:
    """按确认后的界区边界做质量守恒。

    Args:
        streams: [{"name", "mass_flow_kgh", "mole_flow_kmolh",
                   "temperature_c", "pressure_bar"}, …]
        parts: {工段: {"label", "in": [流股…], "out": [流股…], "blocks": [设备…]}}
               来自 `sections.merge(草稿, 确认结果)`

    Returns:
        {"parts": [{key, label, in_rows, out_rows, mass_in_kgh, mass_out_kgh,
                    mole_in_kmolh, mole_out_kmolh, rel_err, state, note,
                    blocks, missing}],
         "summary": {状态: 台数, "total", "worst"}}
    """
    stream_map = {s["name"]: s for s in streams or []}
    out: list[dict] = []

    for key, info in (parts or {}).items():
        in_names = list(info.get("in") or [])
        out_names = list(info.get("out") or [])
        rows_in = [_row(s, stream_map) for s in in_names]
        rows_out = [_row(s, stream_map) for s in out_names]
        missing = sorted({s for s in in_names + out_names if s not in stream_map})

        mass_in = _sum(rows_in, "mass_flow_kgh")
        mass_out = _sum(rows_out, "mass_flow_kgh")
        mole_in = _sum(rows_in, "mole_flow_kmolh")
        mole_out = _sum(rows_out, "mole_flow_kmolh")

        if missing:
            state, rel = NO_DATA, None
            note = "配置里的流股在这个工况里不存在:" + "、".join(missing[:3])
        elif not rows_in:
            state, rel = NO_DATA, None
            note = "没有确认进界流股"
        elif not rows_out:
            state, rel = NO_DATA, None
            note = "没有确认出界流股"
        else:
            state, rel = _judge(mass_in, mass_out)
            if state == NO_DATA:
                note = (f"流股 {_missing_streams(rows_in + rows_out)} 缺质量流量"
                        if _missing_streams(rows_in + rows_out)
                        else "进界合计为 0,相对误差无意义")
            elif state == TOO_SMALL:
                note = (f"进界 {mass_in:,.3f}、出界 {mass_out:,.3f} kg/h 都低于 "
                        f"{MIN_FLOW_KGH:g} kg/h,相对误差无意义")
            elif state == PASS:
                note = (f"进界 {mass_in:,.2f} = 出界 {mass_out:,.2f} kg/h,"
                        f"相对误差 {rel:.4%}(容差 {TOL_REL:.1%})")
            else:
                note = (f"相对误差 {rel:.4%} 超过 {TOL_REL:.1%},疑似漏流股或质量不守恒"
                        f"(进界 {mass_in:,.2f}、出界 {mass_out:,.2f} kg/h)")

        out.append({
            "key": key,
            "label": info.get("label") or section_rules.label_for(key),
            "in_rows": rows_in,
            "out_rows": rows_out,
            "mass_in_kgh": mass_in,
            "mass_out_kgh": mass_out,
            "mole_in_kmolh": mole_in,
            "mole_out_kmolh": mole_out,
            "rel_err": rel,
            "state": state,
            "note": note,
            "blocks": list(info.get("blocks") or []),
            "missing": missing,
        })

    return {"parts": out, "summary": _tally(out)}


# ---------------------------------------------------------------- 逐设备衡算

def build(streams, blocks, connections, overrides=None, picked=None) -> dict:
    """逐设备质量守恒,并按工段分组。

    Args:
        streams / blocks / connections: 库里三张表(字段见 docs/balance-report-plan.md)
        overrides: {设备名: 工段},修正逐设备表的归堆;None 表示不覆盖
        picked: 要核算的设备名集合;None / 空 = 全部。不认识的名字记进 `unknown_picked`

    Returns:
        {"devices": [{block, type, section, in_rows, out_rows,
                      mass_in_kgh, mass_out_kgh, mole_in_kmolh, mole_out_kmolh,
                      rel_err, state, note}, …],
         "groups": [{key, label, devices: [...]}, …],
         "summary": {状态: 台数, "total", "worst"},
         "unknown_picked": [名字…]}
    """
    stream_map = {s["name"]: s for s in streams or []}
    ins: dict[str, list[str]] = {}
    outs: dict[str, list[str]] = {}
    for row in connections or []:
        bucket = ins if row.get("direction") == "in" else outs
        bucket.setdefault(row["block"], []).append(row["stream"])

    known = {b["name"] for b in blocks or []}
    unknown = sorted({str(p) for p in picked or ()} - known)
    chosen = set(picked) if picked else None

    devices: list[dict] = []
    for block in blocks or []:
        name = block["name"]
        if chosen is not None and name not in chosen:
            continue

        in_streams = ins.get(name, [])
        out_streams = outs.get(name, [])
        rows_in = [_row(s, stream_map) for s in in_streams]
        rows_out = [_row(s, stream_map) for s in out_streams]

        mass_in = _sum(rows_in, "mass_flow_kgh")
        mass_out = _sum(rows_out, "mass_flow_kgh")
        mole_in = _sum(rows_in, "mole_flow_kmolh")
        mole_out = _sum(rows_out, "mole_flow_kmolh")

        if not rows_in:
            state, rel = NO_DATA, None
            note = "缺进口流股连接(或这台设备没有进口)" if rows_out else "没有进出口连接"
        elif not rows_out:
            state, rel = NO_DATA, None
            note = "缺出口流股连接"
        else:
            state, rel = _judge(mass_in, mass_out)
            if state == NO_DATA:
                note = (f"流股 {_missing_streams(rows_in + rows_out)} 缺质量流量"
                        if _missing_streams(rows_in + rows_out)
                        else "进口合计为 0,相对误差无意义")
            elif state == TOO_SMALL:
                note = (f"进 {mass_in:,.3f}、出 {mass_out:,.3f} kg/h 都低于 "
                        f"{MIN_FLOW_KGH:g} kg/h,相对误差无意义")
            elif state == PASS:
                note = (f"进 {mass_in:,.2f} = 出 {mass_out:,.2f} kg/h,"
                        f"相对误差 {rel:.4%}(容差 {TOL_REL:.1%})")
            else:
                note = (f"相对误差 {rel:.4%} 超过 {TOL_REL:.1%},疑似漏流股或质量不守恒"
                        f"(进 {mass_in:,.2f}、出 {mass_out:,.2f} kg/h)")

        devices.append({
            "block": name,
            "type": block.get("type") or "",
            "section": section_rules.resolve(name, in_streams, out_streams, overrides),
            "in_rows": rows_in,
            "out_rows": rows_out,
            "mass_in_kgh": mass_in,
            "mass_out_kgh": mass_out,
            "mole_in_kmolh": mole_in,
            "mole_out_kmolh": mole_out,
            "rel_err": rel,
            "state": state,
            "note": note,
        })

    return {
        "devices": devices,
        "groups": section_rules.group(devices),
        "summary": _tally(devices),
        "unknown_picked": unknown,
    }
