"""能量衡算表与全流程汇总:纯函数,不碰 COM、不读写库。

与 balance.py 的关系
--------------------
**ΔH、对照值、焓平衡状态、说明一律引用**,不重算。`balance.compute()` 的结果
由调用方传进来(`balance_results` 参数),理由有两条:

1. 口径只有一处:自测与历史工况回算(`repair_cases`)用的是同一份 `compute()`,
   报告再算一遍迟早会分叉;
2. 分层:`app/aspen/connect.py` 顶层 `import pythoncom`,
   `from app.aspen import balance` 会把 COM 拖进纯计算层。依赖倒置之后,
   `app/design/` 可以离线跑、可以在没有 pywin32 的机器上跑。

只补 `compute()` 不返回的东西:进口焓流合计、出口焓流合计。

两台设备的特殊口径
------------------
* **HeatX**:`compute()` 只校冷侧(整体 ΔH 恒约等于 0),所以表里出**两行**:
  冷侧行给合计与 ΔH,热侧行只给合计、ΔH 留空、状态标「不校验(热侧 = −冷侧)」;
* **塔 / 反应器**:焓平衡状态是「不校验」,照搬,不硬凑平衡。塔的冷凝器 /
  再沸器负荷单独一张明细表(数据源见 `tower_duties`)。

汇总不重复计数
--------------
实测单台塔的再沸器就能超过同工况全部换热器正 duty 的合计(一台 34.7 MW vs
13 台 23.6 MW),所以**总量必须含塔**,再用「其中:塔」子行说明构成,
而不是把塔排除在总量之外(那会把公用工程负荷算小一个量级)。

反应器的 QCALC 不计入总量
-------------------------
反应器的 `duty_kw` 是反应热,不是换热器给它的公用工程负荷,所以判为
「不校验」的块(塔 / 反应器)一律不进总加热 / 总冷却,单独记在
`summary.reaction_net_kw` 里供报告附注说明。实测 case 30 有 7 337.97 kW 属于
`R0501`(RPlug),正是这一类。
"""
from __future__ import annotations

from app.design import block_family

# 焓平衡状态(与 app/aspen/balance.py 的取值一致;不 import 那个模块)
OK, BAD, NO_DATA, SKIP = "ok", "bad", "no_data", "skip"
STATE_TEXT = {OK: "通过", BAD: "超差", NO_DATA: "无法校验", SKIP: "不校验"}

# 热侧行的说明
_HOT_NOTE = "热侧 = −冷侧,不单独校验(整体 ΔH 恒约等于 0)"


def _row_sum(rows, hmap) -> float | None:
    """一组流股的焓流合计;缺一个就整体 None(缺数据不能当 0 加)。"""
    if not rows:
        return None
    total = 0.0
    for stream in rows:
        value = hmap.get(stream)
        if value is None:
            return None
        total += float(value)
    return total


def tower_duties(blocks, stages) -> dict:
    """塔的冷凝器 / 再沸器负荷:块字段优先,逐级剖面回退。

    Args:
        blocks: [{"name", "type", "cond_duty_kw", "reb_duty_kw"}, …]
        stages: {设备名: [{"stage", "duty_kw"}, …]} —— 来自 `column_stages`

    Returns:
        {设备名: {"cond_kw", "reb_kw", "source"}},`source` 是 `block` / `stage` / `""`

    为什么能回退:实测 15 座有 `COND_DUTY/REB_DUTY` 的塔上,逐级剖面的
    **第 1 级 duty = 冷凝器负荷、末级 duty = 再沸器负荷**,中间级全为 0,
    与块字段对到小数点后两位完全一致。78 / 93 座塔只有剖面、没有块字段。
    """
    out: dict[str, dict] = {}
    for block in blocks or []:
        name = block["name"]
        if block_family.family_of(block.get("type")) != "column":
            continue
        cond = block.get("cond_duty_kw")
        reb = block.get("reb_duty_kw")
        if cond is not None or reb is not None:
            out[name] = {"cond_kw": cond, "reb_kw": reb, "source": "block"}
            continue

        rows = sorted(stages.get(name) or [], key=lambda r: r.get("stage") or 0)
        if rows:
            out[name] = {"cond_kw": rows[0].get("duty_kw"),
                         "reb_kw": rows[-1].get("duty_kw"),
                         "source": "stage"}
        else:
            out[name] = {"cond_kw": None, "reb_kw": None, "source": ""}
    return out


def _device_row(block, in_streams, out_streams, hmap, balance, side="",
                tower=None) -> dict:
    """一台设备(或换热器的一侧)→ 能量表的一行。"""
    result = balance or {}
    state = result.get("balance_state") or NO_DATA
    ref_kind = result.get("ref_kind") or ""
    dh = result.get("dh_kw")
    ref = result.get("ref_kw")
    btype = block.get("type") or ""

    if side == "hot":
        # 热侧不参与校验:ΔH 留空,状态照写不校验
        return {
            "block": block["name"], "type": btype, "side": "热",
            "h_in_kw": _row_sum(in_streams, hmap),
            "h_out_kw": _row_sum(out_streams, hmap),
            "dh_kw": None, "dev_kw": None, "dev_kind": "",
            "delta_kw": None, "state": SKIP, "note": _HOT_NOTE,
            "tower": tower,
        }

    if ref_kind == "power":
        dev_kw, dev_kind = block.get("power_kw"), "power"
    elif ref_kind == "duty":
        dev_kw, dev_kind = block.get("duty_kw"), "duty"
    else:
        dev_kw, dev_kind = None, ""

    delta = None
    if dh is not None and ref is not None:
        delta = round(dh - ref, 3)

    return {
        "block": block["name"], "type": btype, "side": "冷" if side == "cold" else "",
        "h_in_kw": _row_sum(in_streams, hmap),
        "h_out_kw": _row_sum(out_streams, hmap),
        "dh_kw": dh, "dev_kw": dev_kw, "dev_kind": dev_kind,
        "delta_kw": delta, "state": state,
        "note": result.get("balance_note") or "",
        "tower": tower,
    }


def build(blocks, streams, connections, balance_results, tower=None) -> dict:
    """能量衡算表 + 全流程汇总。

    Args:
        blocks: 设备表(需要 name / type / duty_kw / power_kw / cond_duty_kw / reb_duty_kw)
        streams: 流股表(需要 name / enthalpy_flow_kw)
        connections: 连接表(需要 block / direction / stream / side)
        balance_results: `balance.compute()` 的返回值 —— **ΔH 与判定只认这个**
        tower: `tower_duties()` 的返回值,可选;没有就不出塔负荷明细

    Returns:
        {"devices": [行…],
         "groups": [{"key", "label", "devices": [...]}, …],
         "summary": {"heat_pos_kw", "tower_reb_kw", "heat_total_kw",
                     "cool_neg_kw", "tower_cond_kw", "cool_total_kw",
                     "shaft_kw", "reaction_net_kw",
                     "tower_count", "device_count",
                     "by_state": {状态: 台数}},
         "towers": [{block, type, cond_kw, reb_kw, source}, …]}
    """
    hmap = {s["name"]: s.get("enthalpy_flow_kw") for s in streams or []}
    ins: dict[str, list[dict]] = {}
    outs: dict[str, list[dict]] = {}
    for row in connections or []:
        bucket = ins if row.get("direction") == "in" else outs
        bucket.setdefault(row["block"], []).append(row)

    tower_map = tower or {}
    devices: list[dict] = []
    towers: list[dict] = []
    heat_pos = cool_neg = shaft = 0.0

    for block in blocks or []:
        name = block["name"]
        btype = block.get("type") or ""
        family = block_family.family_of(btype)
        in_rows = ins.get(name, [])
        out_rows = outs.get(name, [])
        tower_info = tower_map.get(name)

        if family == "column":
            rows = tower_info or {"cond_kw": block.get("cond_duty_kw"),
                                  "reb_kw": block.get("reb_duty_kw"), "source": ""}
            towers.append({"block": name, "type": btype,
                           "cond_kw": rows.get("cond_kw"),
                           "reb_kw": rows.get("reb_kw"),
                           "source": rows.get("source") or ""})

        if btype == "HeatX" or (family == "heat" and _has_sides(in_rows, out_rows)):
            cold_in = [r["stream"] for r in in_rows if r.get("side") == "cold"]
            cold_out = [r["stream"] for r in out_rows if r.get("side") == "cold"]
            hot_in = [r["stream"] for r in in_rows if r.get("side") == "hot"]
            hot_out = [r["stream"] for r in out_rows if r.get("side") == "hot"]
            if cold_in or cold_out:
                devices.append(_device_row(block, cold_in, cold_out, hmap,
                                           balance_results.get(name), "cold", tower_info))
            if hot_in or hot_out:
                devices.append(_device_row(block, hot_in, hot_out, hmap,
                                           balance_results.get(name), "hot", tower_info))
            if not (cold_in or cold_out or hot_in or hot_out):
                devices.append(_device_row(block, [], [], hmap,
                                           balance_results.get(name), "", tower_info))
        else:
            devices.append(_device_row(
                block, [r["stream"] for r in in_rows], [r["stream"] for r in out_rows],
                hmap, balance_results.get(name), "", tower_info))

        # ---- 汇总:塔以外的设备按热负荷正负与轴功率累加
        state = (balance_results.get(name) or {}).get("balance_state")
        if family != "column":
            duty = block.get("duty_kw")
            if duty is not None and state != SKIP:
                if duty > 0:
                    heat_pos += float(duty)
                elif duty < 0:
                    cool_neg += float(duty)
            power = block.get("power_kw")
            if power is not None:
                ref_kind = (balance_results.get(name) or {}).get("ref_kind")
                if ref_kind == "power" or family in ("pump", "move"):
                    shaft += float(power)

    reaction = sum(float(b.get("duty_kw") or 0.0) for b in blocks or []
                   if (balance_results.get(b["name"]) or {}).get("balance_state") == SKIP
                   and block_family.family_of(b.get("type")) != "column")
    tower_reb = sum(t["reb_kw"] for t in towers if t["reb_kw"] is not None)
    tower_cond = sum(t["cond_kw"] for t in towers if t["cond_kw"] is not None)

    by_state: dict[str, int] = {}
    for row in devices:
        by_state[row["state"]] = by_state.get(row["state"], 0) + 1

    groups: dict[str, list[dict]] = {}
    for row in devices:
        family = block_family.family_of(row["type"])
        groups.setdefault(family, []).append(row)
    order = [key for key, _label, _types in block_family.FAMILIES]

    return {
        "devices": devices,
        "groups": [{"key": key, "label": block_family.FAMILY_LABELS[key],
                    "devices": groups[key]}
                   for key in order if key in groups],
        "summary": {
            "heat_pos_kw": round(heat_pos, 3),
            "tower_reb_kw": round(tower_reb, 3),
            "heat_total_kw": round(heat_pos + tower_reb, 3),
            "cool_neg_kw": round(cool_neg, 3),
            "tower_cond_kw": round(tower_cond, 3),
            "cool_total_kw": round(cool_neg + tower_cond, 3),
            "shaft_kw": round(shaft, 3),
            "reaction_net_kw": round(reaction, 3),
            "tower_count": len(towers),
            "device_count": len(devices),
            "by_state": by_state,
        },
        "towers": towers,
    }


def _has_sides(in_rows, out_rows) -> bool:
    """换热器是否带冷/热分侧信息(部分模型读不到 COLDIN/HOTOUT)。"""
    return any(r.get("side") for r in list(in_rows) + list(out_rows))
