"""摩尔流量守恒 —— 物料自洽的第二条判据。纯函数,不碰 COM、不读写库。

为什么需要这一条
----------------
`mass_balance.py` 只判质量守恒。质量在**任何**设备上都守恒(反应也不改质量),
所以它对「摩尔流量字段读错」是瞎的:质量 100 → 100 对得上,摩尔 5.0 → 2.5
也照样 pass,报告上看不出任何异常。

摩尔只在**无反应**的设备上守恒,于是它成了独立判据:同一台无反应设备进出摩尔流量
对不上,说明 `RES_MOLEFLOW` 读到的不是摩尔流量(单位、字段、组分口径之一出了问题),
而不是模型不守恒。这正是「质量流量 / 摩尔流量 = 分子量」这条自洽性的可判定形式 ——
比值变了,就是流量口径变了。

表观分子量
----------
每条流股的 `mass / mole` 就是表观分子量 `MW_app`(kg/h ÷ kmol/h = kg/kmol,
数值上等于 g/mol)。摩尔守恒等价于进出 `MW_app` 相等,所以超差说明里直接报两端
`MW_app`:数字砍半或翻倍时,读的人一眼能认出是单位口径错,不用去猜模型。

范围与豁免
----------
* 只判 `NON_REACTIVE_TYPES` 里的块类型。反应器(摩尔数本来就会变)与塔
  (RadFrac 可以挂反应)**整台不判**,进 `skipped` 并写明原因 —— 判不了就不判,
  不拿"看起来更严格"的规则去误报。
* `MULT` / `DUP` 这类倍增块也不判:出口是进口的缩放副本,进出本就不相等。
* 容差与状态词表全部取自 `mass_balance`(同 0.5%、同小流量豁免),
  不另立一套口径;两条结论各存各的字段,互不覆盖。
* 小流量豁免按**质量**判(< 1 kg/h),不拿 kg/h 的线去比 kmol/h。

结论只回状态、误差与 `MW_app`,不写「模型有问题」这种超出证据的话。

实测记录(2026-11-19,2 个库 7 个工况)
------------------------------------
`samples/case-23-error.db` 与 `data/cases.db` 共 470 台可判设备:通过 469、
超差 1(V0201,0.646%)、无法校验 10(其中 8 台是一侧连着没有流量的热流/公用工程流股)。
唯一那台超差是质量 20000.000 → 20000.000 kg/h 完全对得上、摩尔却少 6.242 kmol/h,
说明这条判据补的正是质量守恒看不见的那一块;具体成因(单位 / 字段 / Aspen 统计口径)
未验证,说明里只提示"需人工确认"。
"""

from __future__ import annotations

from .mass_balance import FAIL, MIN_FLOW_KGH, NO_DATA, PASS, TOL_REL, TOO_SMALL

# 质量与摩尔都守恒的块类型(小写,与 Aspen 的块类型同名)。
# 只收"无反应 + 进出口流股全部相连时摩尔数不变"的类型;拿不准的一律不收。
NON_REACTIVE_TYPES: tuple[str, ...] = (
    "heater",
    "heaterx",
    "heatx",
    "mheatx",
    "flash2",
    "flash3",
    "decanter",
    "sep",
    "sep2",
    "pump",
    "turbine",
    "compr",
    "mcompr",
    "valve",
    "pipe",
    "mixer",
    "fsplit",
    "splitter",
)

# 明确不判的类型(连着原因一起暴露,免得被当成"漏了")。
SKIP_REASONS: dict[str, str] = {
    "radfrac": "塔可以挂反应,摩尔数不一定守恒",
    "absorber": "塔可以挂反应,摩尔数不一定守恒",
    "extract": "塔可以挂反应,摩尔数不一定守恒",
    "rstoic": "反应器,摩尔数按化学计量变化",
    "requil": "反应器,摩尔数按平衡变化",
    "rgibbs": "反应器,摩尔数按平衡变化",
    "ryield": "反应器,收率由输入给定",
    "rcstr": "反应器,摩尔数按动力学变化",
    "rplug": "反应器,摩尔数按动力学变化",
    "rbatch": "反应器,摩尔数随时间变化",
    "mult": "倍增块,出口是进口的缩放副本",
    "dup": "复制块,出口是进口的副本",
}


def _sum(rows, field) -> float | None:
    """加总某一列;只要有一个 None 就整体 None(缺数据不能当 0 加)。"""
    if not rows:
        return None
    total = 0.0
    for row in rows:
        value = row.get(field)
        if value is None:
            return None
        total += float(value)
    return total


def mw_apparent(mass_kgh, mole_kmolh) -> float | None:
    """表观分子量 kg/kmol;缺值或摩尔为 0 时返回 None。"""
    if mass_kgh is None or mole_kmolh is None:
        return None
    try:
        mass, mole = float(mass_kgh), float(mole_kmolh)
    except (TypeError, ValueError):
        return None
    if mole <= 0:
        return None
    return mass / mole


def _judge(mole_in, mole_out, mass_in, mass_out) -> tuple[str, float | None]:
    """判据:相对误差 ≤ 0.5% 通过;小流量按质量算。"""
    if mole_in is None or mole_out is None or mole_in <= 0:
        return NO_DATA, None
    if (
        mass_in is not None
        and mass_out is not None
        and mass_in < MIN_FLOW_KGH
        and mass_out < MIN_FLOW_KGH
    ):
        return TOO_SMALL, None
    rel = abs(mole_in - mole_out) / mole_in
    return (PASS if rel <= TOL_REL else FAIL), rel


def _tally(items) -> dict:
    """状态计数 + 最大相对误差(与 mass_balance 的报告形状一致)。"""
    counts = dict.fromkeys((PASS, FAIL, NO_DATA, TOO_SMALL), 0)
    worst = None
    for item in items:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
        rel = item.get("rel_err")
        if rel is not None and (worst is None or rel > worst["rel_err"]):
            worst = {"block": item["block"], "rel_err": rel}
    return dict(counts, total=len(items), worst=worst)


def _missing(rows) -> str:
    """列出缺摩尔流量的流股名(最多 3 个)。"""
    names = [r["name"] for r in rows if r.get("mole_flow_kmolh") is None]
    shown = "、".join(names[:3])
    return shown + (" 等" if len(names) > 3 else "")


def build(streams, blocks, connections, picked=None) -> dict:
    """逐设备判摩尔守恒。

    Args:
        streams: [{"name", "mass_flow_kgh", "mole_flow_kmolh"}, …](库里 streams 表)
        blocks: [{"name", "type"}, …]
        connections: [{"block", "direction", "stream"}, …]
        picked: 只看这些设备;None / 空 = 全部(与 mass_balance.build 同口径)

    Returns:
        {"devices": [{block, type, mole_in_kmolh, mole_out_kmolh,
                      mw_in_kgkmol, mw_out_kgkmol, rel_err, state, note}, …],
         "skipped": [{block, type, reason}, …],
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
    skipped: list[dict] = []

    for block in blocks or []:
        name = block["name"]
        if chosen is not None and name not in chosen:
            continue

        btype = str(block.get("type") or "").strip()
        key = btype.lower()
        if key not in NON_REACTIVE_TYPES:
            skipped.append(
                {
                    "block": name,
                    "type": btype,
                    "reason": SKIP_REASONS.get(key, "不在无反应块类型清单里,保守不判"),
                }
            )
            continue

        rows_in = [
            stream_map.get(s) or {"name": s, "mass_flow_kgh": None, "mole_flow_kmolh": None}
            for s in ins.get(name, [])
        ]
        rows_out = [
            stream_map.get(s) or {"name": s, "mass_flow_kgh": None, "mole_flow_kmolh": None}
            for s in outs.get(name, [])
        ]
        mole_in = _sum(rows_in, "mole_flow_kmolh")
        mole_out = _sum(rows_out, "mole_flow_kmolh")
        mass_in = _sum(rows_in, "mass_flow_kgh")
        mass_out = _sum(rows_out, "mass_flow_kgh")
        mw_in = mw_apparent(mass_in, mole_in)
        mw_out = mw_apparent(mass_out, mole_out)

        if not rows_in:
            state, rel = NO_DATA, None
            note = "缺进口流股连接(或这台设备没有进口)" if rows_out else "没有进出口连接"
        elif not rows_out:
            state, rel = NO_DATA, None
            note = "缺出口流股连接"
        else:
            state, rel = _judge(mole_in, mole_out, mass_in, mass_out)
            if state == NO_DATA:
                missing = _missing(rows_in + rows_out)
                missing_rows = [r for r in rows_in + rows_out if r.get("mole_flow_kmolh") is None]
                flowless = missing_rows and all(
                    r.get("mass_flow_kgh") is None for r in missing_rows
                )
                if flowless:
                    note = f"流股 {missing} 没有质量与摩尔流量(热流/公用工程),这条口径不做判定"
                else:
                    note = (
                        f"流股 {missing} 缺摩尔流量"
                        if missing
                        else "进口摩尔合计为 0,相对误差无意义"
                    )
            elif state == TOO_SMALL:
                note = (
                    f"进 {mass_in:,.3f}、出 {mass_out:,.3f} kg/h 都低于 "
                    f"{MIN_FLOW_KGH:g} kg/h,摩尔口径的误差无意义"
                )
            elif state == PASS:
                note = (
                    f"进 {mole_in:,.2f} = 出 {mole_out:,.2f} kmol/h,"
                    f"相对误差 {rel:.4%}(容差 {TOL_REL:.1%})"
                    + (f",表观分子量 {mw_in:,.2f} kg/kmol" if mw_in is not None else "")
                )
            else:
                note = (
                    f"进出摩尔流量差 {rel:.4%} 超过 {TOL_REL:.1%}"
                    f"(进 {mole_in:,.3f}、出 {mole_out:,.3f} kmol/h"
                )
                if mass_in is not None and mass_out is not None:
                    note += f",质量 {mass_in:,.3f} → {mass_out:,.3f} kg/h"
                note += ")"
                if mw_in is not None and mw_out is not None:
                    note += (
                        f",表观分子量 {mw_in:,.2f} → {mw_out:,.2f} kg/kmol"
                        " —— 疑似摩尔流量的单位、字段或统计口径不一致,需人工确认"
                    )

        devices.append(
            {
                "block": name,
                "type": btype,
                "mole_in_kmolh": mole_in,
                "mole_out_kmolh": mole_out,
                "mw_in_kgkmol": mw_in,
                "mw_out_kgkmol": mw_out,
                "rel_err": rel,
                "state": state,
                "note": note,
            }
        )

    return {
        "devices": devices,
        "skipped": skipped,
        "summary": _tally(devices),
        "unknown_picked": unknown,
    }
