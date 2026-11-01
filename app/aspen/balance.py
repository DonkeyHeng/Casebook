"""焓平衡自检:用流股能量流(ΔH)对每台设备的热负荷 / 功率。

纯函数,不碰 COM —— 输入是已经从 Aspen 读出来的三张表(流股 / 设备 / 连接),
所以既能跟着「读取」一起算,也能用 `tools/repair_cases --apply` 在库里回算,
改口径不用回 Aspen 重读。

口径(2026-11-01,三份真实模型验证,见 docs/aspen-field-map.md 12.x)
-----------------------------------------------------------------
* ΔH = Σ出(HMX_FLOW) − Σ入(HMX_FLOW),单位 kW(`HMX_FLOW` 本身就是 m·h)。
* **Heater / Flash2**:ΔH 应等于设备热负荷 `duty_kw`(`QCALC` / `QNET`)。
* **HeatX(双流股换热器)**:整体 ΔH 恒等于 0(热侧给、冷侧收),
  所以只校验**冷侧**:ΔH_cold 应等于 `HX_DUTY`。分侧信息来自 connections 的 `side`。
* **Pump / Compr / MCompr**:ΔH 是流体得到的机械功,和 `power_kw`(`BRAKE_POWER`)差一个效率。
  实测:泵的 ΔH/功率 = 1.000;压缩机 = 0.950(效率 0.95)。
  所以这里按**比值区间**判,不按 1% 判 —— 否则每台压缩机都会误报。
* **Mixer / FSplit / Valve / Pipe**:绝热,ΔH 应 ≈ 0;容差用"相对最大焓流的 0.5%",
  不用绝对 0.5 kW —— 实测 M0301 的 4.7 kW / V0504 的 3.7 kW 都在 20 万 kW 焓流面前是噪声。
* **RadFrac / 反应器**:不校验(塔有冷凝器 / 再沸器 / 中间回流;反应器的 QCALC 不是换热负荷)。
* 有流股缺焓值(没 Run 过、或能量流 —— 实测天大模型的 0136)→ 记「无法校验」,不是失败。
"""
from __future__ import annotations

# 判定状态
OK = "ok"            # 通过
BAD = "bad"          # 超差
NO_DATA = "no_data"  # 无法校验(缺数据)
SKIP = "skip"        # 按约定不校验

STATE_TEXT = {
    OK: "通过",
    BAD: "超差",
    NO_DATA: "无法校验",
    SKIP: "不校验",
}

TOL_REL = 0.01        # 热负荷类:相对 1%
TOL_ABS = 0.5         # 热负荷类:绝对 0.5 kW
ZERO_TOL_REL = 0.005  # 绝热类:相对最大焓流的 0.5%
POWER_LO, POWER_HI = 0.85, 1.05   # 机械功:ΔH/功率 的合理区间(差的是效率)

SKIP_TYPES = {"RadFrac", "Absorber", "RPlug", "RStoic", "RCSTR", "RGibbs", "REquil"}
ZERO_TYPES = {"Mixer", "FSplit", "Splitter", "Valve", "Pipe", "Mult"}
POWER_TYPES = {"Pump", "Compr", "MCompr", "Turbine"}
# 这些类型必须有热负荷字段,取不到就记「无法校验」,不能按绝热去猜
DUTY_TYPES = {"Heater", "Flash2", "Flash3", "HeaterX"}


def _result(state: str, dh=None, ref=None, ref_kind: str = "", note: str = "") -> dict:
    return {"dh_kw": dh, "ref_kw": ref, "ref_kind": ref_kind,
            "balance_state": state, "balance_note": note}


def compute(streams, blocks, connections) -> dict[str, dict]:
    """对每台设备算 ΔH 与参照值,返回 {设备名: 结果}。

    Args:
        streams: [{"name", "enthalpy_flow_kw"}, …]
        blocks:  [{"name", "type", "duty_kw", "power_kw"}, …]
        connections: [{"block", "direction", "stream", "side"}, …]
    """
    h = {s["name"]: s.get("enthalpy_flow_kw") for s in streams}
    ins: dict[str, list[dict]] = {}
    outs: dict[str, list[dict]] = {}
    for row in connections or []:
        bucket = ins if row.get("direction") == "in" else outs
        bucket.setdefault(row["block"], []).append(row)

    out: dict[str, dict] = {}
    for block in blocks or []:
        name = block["name"]
        btype = block.get("type") or ""
        in_rows = ins.get(name, [])
        out_rows = outs.get(name, [])
        d_in = [r["stream"] for r in in_rows]
        d_out = [r["stream"] for r in out_rows]

        if btype in SKIP_TYPES:
            out[name] = _result(SKIP, note="塔 / 反应器:进出口焓差不等于设备热负荷,按约定不校验")
            continue
        if not d_in or not d_out:
            out[name] = _result(NO_DATA, note="连接不全(缺进口或出口流股),无法校验")
            continue
        missing = [s for s in d_in + d_out if h.get(s) is None]
        if missing:
            shown = "、".join(missing[:3]) + (" 等" if len(missing) > 3 else "")
            out[name] = _result(NO_DATA, note=f"流股 {shown} 没有焓值(没 Run 过?或是能量流)")
            continue

        if btype == "HeatX":
            out[name] = _heatx(block, in_rows, out_rows, h)
            continue

        dh = sum(h[s] for s in d_out) - sum(h[s] for s in d_in)
        if btype in POWER_TYPES:
            out[name] = _power(block, dh)
            continue
        if btype in ZERO_TYPES:
            out[name] = _zero(block, dh, d_in, d_out, h)
            continue

        duty = block.get("duty_kw")
        if duty is None:
            if btype in DUTY_TYPES:
                out[name] = _result(NO_DATA, dh=round(dh, 3),
                                    note="没有热负荷字段(QCALC / QNET 都读不到)")
                continue
            out[name] = _zero(block, dh, d_in, d_out, h, auto=True)
            continue
        if abs(duty) < 1e-9:
            out[name] = _zero(block, dh, d_in, d_out, h)
            continue

        diff = dh - duty
        tol = max(abs(duty) * TOL_REL, TOL_ABS)
        ok = abs(diff) <= tol
        note = (f"ΔH 与 duty 差 {diff:+.2f} kW(容差 {tol:.2f})"
                if not ok else f"ΔH 与 duty 一致(差 {diff:+.2f} kW)")
        out[name] = _result(OK if ok else BAD, dh=round(dh, 3),
                            ref=round(duty, 3), ref_kind="duty", note=note)
    return out


def _heatx(block, in_rows, out_rows, h) -> dict:
    """双流股换热器:只校验冷侧(整体 ΔH 恒为 0)。"""
    duty = block.get("duty_kw")
    cold_in = [r["stream"] for r in in_rows if r.get("side") == "cold"]
    cold_out = [r["stream"] for r in out_rows if r.get("side") == "cold"]
    if duty is None:
        return _result(NO_DATA, note="没有 HX_DUTY,无法校验")
    if not cold_in or not cold_out:
        return _result(NO_DATA, note="缺少冷/热侧分侧信息(FCOLD/FHOT、COLDIN/HOTOUT 读不到)")
    dh = sum(h[s] for s in cold_out) - sum(h[s] for s in cold_in)
    diff = dh - duty
    tol = max(abs(duty) * TOL_REL, TOL_ABS)
    ok = abs(diff) <= tol
    note = (f"冷侧 ΔH 与 HX_DUTY 差 {diff:+.2f} kW(容差 {tol:.2f})"
            if not ok else f"冷侧 ΔH 与 HX_DUTY 一致(差 {diff:+.2f} kW)")
    return _result(OK if ok else BAD, dh=round(dh, 3), ref=round(duty, 3),
                   ref_kind="hx_cold", note=note)


def _power(block, dh: float) -> dict:
    """泵 / 压缩机:ΔH 应等于轴功率 × 效率,所以按比值区间判。"""
    power = block.get("power_kw")
    if power is None:
        return _result(NO_DATA, dh=round(dh, 3), note="没有轴功率字段(BRAKE_POWER / ELEC_POWER)")
    ratio = dh / power if power else None
    if ratio is None:
        return _result(NO_DATA, dh=round(dh, 3), note="轴功率为 0,比值无意义")
    ok = POWER_LO <= ratio <= POWER_HI
    note = (f"ΔH / 功率 = {ratio:.3f}(机械功,差的部分是效率)"
            if ok else f"ΔH / 功率 = {ratio:.3f},超出 {POWER_LO}–{POWER_HI} 区间")
    return _result(OK if ok else BAD, dh=round(dh, 3), ref=round(power, 3),
                   ref_kind="power", note=note)


def _zero(block, dh: float, d_in, d_out, h, auto: bool = False) -> dict:
    """绝热设备:ΔH 应 ≈ 0,容差按最大焓流的相对比例。"""
    scale = max(sum(abs(h[s]) for s in d_out), sum(abs(h[s]) for s in d_in)) or 0.0
    tol = max(scale * ZERO_TOL_REL, TOL_ABS)
    ok = abs(dh) <= tol
    prefix = "无热负荷字段,按绝热校验" if auto else "绝热设备"
    note = (f"{prefix}:ΔH = {dh:+.3f} kW(容差 {tol:.2f} = 最大焓流 {scale:,.0f} kW 的 {ZERO_TOL_REL:.1%})")
    return _result(OK if ok else BAD, dh=round(dh, 3), ref=0.0, ref_kind="zero", note=note)


def summarize(results: dict[str, dict]) -> dict:
    """统计各状态台数,给 UI / AI 包做摘要。"""
    counts = {state: 0 for state in STATE_TEXT}
    for item in results.values():
        counts[item["balance_state"]] = counts.get(item["balance_state"], 0) + 1
    return counts
