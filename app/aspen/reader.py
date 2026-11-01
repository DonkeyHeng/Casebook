"""从 Aspen 数据树里读取工况数据。

所有路径都来自实测(见 `docs/aspen-field-map.md`),不要改名字。

一次 `read_case()` 产出:
  - model:模型信息
  - convergence:收敛/运行状态 + 撕裂流 + Aspen 原始提示文本
  - streams:流股表(已换算到固定单位)
  - blocks:设备表(已换算到固定单位)
  - components:组分清单

规模提示:1000 条流股约 6000 次 COM 调用,必须放后台线程并给 `progress` 回调。
"""
from __future__ import annotations

import time
from typing import Callable

from . import units

# (输出键, Aspen 字段, 量纲)
STREAM_FIELDS = [
    ("temperature_c", "RES_TEMP", "temperature"),
    ("pressure_bar", "RES_PRES", "pressure"),
    ("mass_flow_kgh", "RES_MASSFLOW", "mass_flow"),
    ("mole_flow_kmolh", "RES_MOLEFLOW", "mole_flow"),
    ("vap_frac", "RES_VFRAC", "fraction"),
    ("vol_flow_m3h", "RES_VOLFLOW", "volume_flow"),
]

# 设备上的标量结果字段(不同设备类型用不同字段)
BLOCK_DUTY_FIELDS = ("QCALC", "QNET", "HX_DUTY")      # cal/sec
BLOCK_POWER_FIELDS = ("BRAKE_POWER", "ELEC_POWER")     # kW
BLOCK_AREA_FIELDS = ("HX_AREAC",)                      # sqm

RUN_STATUS_FIELDS = (
    "RSTAT",
    "CVSTAT",
    "CSSTAT",
    "UOSSTAT",
    "PPSTAT",
    "SENSSTAT",
    "ITSTAT",
    "PER_ERROR",
)

Progress = Callable[[int, int, str], None]


# ---------------------------------------------------------------- 底层取值

def _unit_of(node) -> str:
    """UnitString 有时是属性、有时是方法,两种都要兼容。"""
    if node is None:
        return ""
    try:
        u = node.UnitString
        return str(u() if callable(u) else u)
    except Exception:
        return ""


def fetch(session, path: str) -> tuple[object, str]:
    """返回 (值, 单位);节点不存在返回 (None, '')。"""
    node = session.find(path)
    if node is None:
        return None, ""
    try:
        value = node.Value
    except Exception:
        value = None
    return value, _unit_of(node)


def fetch_converted(session, path: str, quantity: str):
    """取值并按量纲换算;单位不认识时返回 None(宁缺勿错)。"""
    value, unit = fetch(session, path)
    if value is None:
        return None, None
    return units.convert(quantity, value, unit), unit


def child_names(node) -> list[str]:
    if node is None:
        return []
    out = []
    try:
        els = node.Elements
        for i in range(els.Count):
            try:
                out.append(str(els.Item(i).Name))
            except Exception:
                pass
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- 收敛状态

def read_convergence(session, blocks: list[dict] | None = None) -> dict:
    """读取运行状态(Aspen 的 Run-Status 页)、撕裂流误差、模块级状态。

    注意:Run-Status 里**没有** ERRTXT 节点(Aspen 把提示文本写在 .sum 文件里),
    真正有用的错误信息在模块的 `Output\\BLKMSG`。所以这里必须结合 blocks 才能给结论。
    """
    base = r"\Data\Results Summary\Run-Status\Output"

    codes = {}
    for key in RUN_STATUS_FIELDS:
        value, _ = fetch(session, f"{base}\\{key}")
        codes[key] = value

    errtxt, _ = fetch(session, f"{base}\\ERRTXT")  # 实测 V15 没有这个节点,读不到就是 None
    run = {}
    for key in ("RUNID", "ITDATE", "USER", "VERNO", "MACHINE", "NODENAME"):
        value, _ = fetch(session, f"{base}\\{key}")
        run[key] = value

    tears = _read_tears(session)
    verdict = judge(codes, errtxt, tears, blocks)

    return {
        "codes": codes,
        "errtxt": str(errtxt) if errtxt else "",
        "run": run,
        "tears": tears,
        **verdict,
    }


def _read_tears(session) -> list[dict]:
    """撕裂流误差表(按编号展开的数组)。"""
    base = r"\Data\Results Summary\Conv-Sum\TEAR-SUMMARY\Output"
    ids = child_names(session.find(f"{base}\\STREAMID"))
    out = []
    for i in ids:
        stream, _ = fetch(session, f"{base}\\STREAMID\\{i}")
        var, _ = fetch(session, f"{base}\\VARID\\{i}")
        err, _ = fetch(session, f"{base}\\ERR\\{i}")
        tol, _ = fetch(session, f"{base}\\ERR_TOL\\{i}")
        out.append({"stream": stream, "variable": var, "error": err, "tolerance": tol})
    return out


def judge(codes: dict, errtxt, tears: list[dict], blocks: list[dict] | None = None) -> dict:
    """根据状态码 + 模块级 BLKSTAT/BLKMSG + 撕裂流误差,判定这次运行能不能用。

    实测经验(2026-10-31):
      * 正常收敛: RSTAT=0 CVSTAT=0 UOSSTAT=0 PER_ERROR=0,但 CSSTAT/PPSTAT 可能是 2 —— 2 不代表失败
      * 模块状态 BLKSTAT:0=正常、1=错误、2=告警;错误原文在 `Output\\BLKMSG`
      * 有模块报错时 CVSTAT/UOSSTAT 会变成 1,但**撕裂流误差可能依然很小**,
        所以"撕裂流收敛"不等于"整条流程可用",必须看模块级状态
    """
    reasons: list[str] = []
    text = str(errtxt or "")

    if "completed with errors" in text:
        reasons.append("Aspen 报告有模块/收敛块报错")

    for key in ("RSTAT", "CVSTAT", "UOSSTAT"):
        if codes.get(key) not in (0, None):
            reasons.append(f"{key}={codes.get(key)}(非 0)")

    per_error = codes.get("PER_ERROR")
    if isinstance(per_error, (int, float)) and per_error > 1e-6:
        reasons.append(f"PER_ERROR={per_error}")

    block_errors, block_warnings = [], []
    for b in blocks or []:
        status = b.get("status")
        if status == 1:
            block_errors.append(b)
        elif status == 2:
            block_warnings.append(b)

    for b in block_errors:
        reasons.append(f"{b['name']} 报错:{b.get('message') or '未知原因'}")
    for b in block_warnings:
        reasons.append(f"{b['name']} 告警:{b.get('message') or '未知原因'}")

    for tear in tears:
        err, tol = tear.get("error"), tear.get("tolerance")
        if isinstance(err, (int, float)) and tol and err > tol:
            reasons.append(f"撕裂流 {tear.get('stream')} 未收敛(误差 {err} > {tol})")

    if block_errors or any("非 0" in r or "报错" in r for r in reasons):
        severity = "error"
    elif reasons:
        severity = "warning"
    else:
        severity = "ok"

    return {
        "severity": severity,            # ok / warning / error
        "converged": severity == "ok",   # 只有 ok 才允许进统计对比
        "reasons": reasons,
        "block_errors": [b["name"] for b in block_errors],
        "block_warnings": [b["name"] for b in block_warnings],
    }


# ---------------------------------------------------------------- 流股

def read_streams(session, progress: Progress | None = None,
                 include_composition: bool = False) -> list[dict]:
    ids = child_names(session.find(r"\Data\Streams"))
    total = len(ids)
    out: list[dict] = []
    for n, sid in enumerate(ids, 1):
        base = rf"\Data\Streams\{sid}\Output"
        rec: dict = {"name": sid, "composition": None}
        raw = {}
        for key, field, quantity in STREAM_FIELDS:
            value, unit = fetch(session, f"{base}\\{field}")
            rec[key] = units.convert(quantity, value, unit)
            raw[field] = {"value": value, "unit": unit}
        rec["raw"] = raw
        if include_composition:
            rec["composition"] = read_composition(session, sid)
        out.append(rec)
        if progress:
            progress(n, total, sid)
    return out


def read_composition(session, stream: str) -> list[dict]:
    """液相 X / 气相 Y 摩尔分数(按组分展开)。"""
    base = rf"\Data\Streams\{stream}\Output"
    comps = child_names(session.find(f"{base}\\X")) or child_names(session.find(f"{base}\\Y"))
    rows = []
    for comp in comps:
        x, _ = fetch(session, f"{base}\\X\\{comp}")
        y, _ = fetch(session, f"{base}\\Y\\{comp}")
        rows.append({"component": comp, "x_liq": x, "y_vap": y})
    return rows


# ---------------------------------------------------------------- 设备

def read_blocks(session, progress: Progress | None = None) -> list[dict]:
    nodes = []
    root = session.find(r"\Data\Blocks")
    if root is not None:
        try:
            els = root.Elements
            for i in range(els.Count):
                nodes.append(els.Item(i))
        except Exception:
            pass

    total = len(nodes)
    out = []
    for n, node in enumerate(nodes, 1):
        try:
            bid = str(node.Name)
        except Exception:
            continue
        out.append(read_block(session, bid, node))
        if progress:
            progress(n, total, bid)
    return out


def read_block(session, bid: str, node=None) -> dict:
    base = rf"\Data\Blocks\{bid}"

    btype = None
    if node is not None:
        try:
            btype = str(node.AttributeValue(6))  # 6 = 可读类型名,如 'Heater'
        except Exception:
            btype = None

    rec: dict = {"name": bid, "type": btype, "raw": {}}

    # 热负荷 / 功率 / 面积:按候选字段取第一个有值的
    rec["duty_kw"] = _first_converted(session, base, BLOCK_DUTY_FIELDS, "power", rec["raw"])
    rec["power_kw"] = _first_converted(session, base, BLOCK_POWER_FIELDS, "power", rec["raw"])
    rec["area_m2"] = _first_converted(session, base, BLOCK_AREA_FIELDS, "area", rec["raw"])

    # 理论级数
    nstage, _ = fetch(session, rf"{base}\Input\NSTAGE")
    rec["nstage"] = int(nstage) if isinstance(nstage, (int, float)) else None

    # 顶/底温度:塔是数组(B_TEMP\1 ... B_TEMP\n),其它设备是标量
    btemp = session.find(rf"{base}\Output\B_TEMP")
    stages = child_names(btemp)
    if stages:
        top, _ = fetch_converted(session, rf"{base}\Output\B_TEMP\{stages[0]}", "temperature")
        bottom, _ = fetch_converted(session, rf"{base}\Output\B_TEMP\{stages[-1]}", "temperature")
        rec["top_temp_c"], rec["bottom_temp_c"] = top, bottom
    elif btemp is not None:
        top, _ = fetch_converted(session, rf"{base}\Output\B_TEMP", "temperature")
        rec["top_temp_c"], rec["bottom_temp_c"] = top, None
    else:
        rec["top_temp_c"] = rec["bottom_temp_c"] = None

    # 模块自身状态(0=正常,1=错误,2=告警)与提示原文
    rec["status"], _ = fetch(session, rf"{base}\Output\BLKSTAT")
    rec["message"], _ = fetch(session, rf"{base}\Output\BLKMSG")
    return rec


def _first_converted(session, base: str, fields, quantity: str, raw: dict):
    for field in fields:
        value, unit = fetch(session, f"{base}\\Output\\{field}")
        if value is None:
            continue
        node = session.find(f"{base}\\Output\\{field}")
        if child_names(node):  # 数组节点(如塔的 DUTY\1..n),不是标量
            continue
        raw[field] = {"value": value, "unit": unit}
        converted = units.convert(quantity, value, unit)
        if converted is not None:
            return converted
    return None


# ---------------------------------------------------------------- 塔板剖面

def read_column_profiles(session, blocks: list[dict] | None = None) -> list[dict]:
    """读所有塔类的逐板剖面(二期功能,数据量很小)。

    路径:`\\Data\\Blocks\\<塔>\\Output\\{B_TEMP,B_PRES,LIQ_FLOW,VAP_FLOW,DUTY}\\<级>`
    """
    columns = [b for b in (blocks or []) if b.get("type") in ("RadFrac", "Absorber")]
    out = []
    for col in columns:
        bid = col["name"]
        base = rf"\Data\Blocks\{bid}\Output"
        stages = child_names(session.find(rf"{base}\B_TEMP"))
        if not stages:
            continue
        rows = []
        for st in stages:
            row = {"stage": int(st) if st.isdigit() else st}
            for key, field, quantity in (
                ("temperature_c", "B_TEMP", "temperature"),
                ("pressure_bar", "B_PRES", "pressure"),
                ("liq_flow", "LIQ_FLOW", None),
                ("vap_flow", "VAP_FLOW", None),
                ("duty_kw", "DUTY", "power"),
            ):
                value, unit = fetch(session, rf"{base}\{field}\{st}")
                if quantity:
                    row[key] = units.convert(quantity, value, unit)
                    row[key + "_unit"] = unit
                else:
                    try:
                        row[key] = float(value) if value is not None else None
                    except (TypeError, ValueError):
                        row[key] = None
                    row[key + "_unit"] = unit
            rows.append(row)
        out.append({"block": bid, "type": col.get("type"), "stages": rows})
    return out


# ---------------------------------------------------------------- 总入口

def read_case(session, include_composition: bool = False,
              include_profiles: bool = True,
              progress: Progress | None = None) -> dict:
    """读一整个工况,返回可直接入库的 dict。"""
    t0 = time.time()
    streams = read_streams(session, progress=None, include_composition=include_composition)
    blocks = read_blocks(session, progress=progress)
    # 收敛判定要用到模块级状态,所以必须放在 blocks 之后
    convergence = read_convergence(session, blocks)
    profiles = read_column_profiles(session, blocks) if include_profiles else []

    components = []
    if streams:
        components = child_names(session.find(rf"\Data\Streams\{streams[0]['name']}\Output\X"))

    return {
        "model": {
            "path": session.model_path,
            "name": _model_name(session.model_path),   # 文件名,不是 COM 服务名
            "server": _doc_name(session),               # 'Aspen Plus 41.0 OLE 服务'
            "process_id": session.process_id,
            "read_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "convergence": convergence,
        "streams": streams,
        "blocks": blocks,
        "columns": profiles,
        "components": components,
        "stats": {
            "stream_count": len(streams),
            "block_count": len(blocks),
            "column_count": len(profiles),
            "elapsed_sec": round(time.time() - t0, 1),
        },
    }


def _doc_name(session) -> str:
    try:
        return str(session.doc.Name)
    except Exception:
        return ""


def _model_name(path: str) -> str:
    """从路径取文件名(去掉目录),用作工况显示名。"""
    try:
        return str(path).replace("/", "\\").rsplit("\\", 1)[-1]
    except Exception:
        return str(path)
