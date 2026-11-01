"""从 Aspen 数据树里读取工况数据。

所有路径都来自实测(见 `docs/aspen-field-map.md`),不要改名字。

一次 `read_case()` 产出:
  - model:模型信息
  - convergence:收敛/运行状态 + 撕裂流 + Aspen 原始提示文本
  - streams:流股表(已换算到固定单位)
  - blocks:设备表(已换算到固定单位)
  - components:组分清单

规模提示:1000 条流股约 6000 次 COM 调用,必须放后台线程并给 `progress` 回调。

两个进度回调(都是可选的):
  * `progress(done, total, label)` —— 每读完一条流股/一个设备调一次,
    用于"读取设备 45/77"这种细粒度进度。
  * `on_phase(阶段名)` —— 阶段切换时调一次(读流股 → 读设备 → 判收敛 …),
    给网页进度条一个"现在在干什么"的粗粒度说明。
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
PhaseHook = Callable[[str], None]


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

    实测经验(2026-10-31 起,2026-11-01 修正):
      * 正常收敛: RSTAT=0 CVSTAT=0 UOSSTAT=0 PER_ERROR=0,但 CSSTAT/PPSTAT 可能是 2 —— 2 不代表失败
      * 模块状态 BLKSTAT:0=正常、1=错误、2=告警;错误原文在 `Output\\BLKMSG`
      * 有模块报错时 CVSTAT/UOSSTAT 会变成 1,但**撕裂流误差可能依然很小**,
        所以"撕裂流收敛"不等于"整条流程可用",必须看模块级状态

    **UOSSTAT / PER_ERROR 不能单独当作"错误"**(2026-11-01 实测修正):
    读入一个样本模型,7 个模块只有 status=2 的告警(相稳定性检查)、**没有一个 status=1**,
    但 UOSSTAT=1、PER_ERROR=1 —— 旧规则据此判成"有错误",横幅直接说"不建议用它算收率",
    这是误报。对照之前那个真出错的模型(T0202/T0205 是 status=1),这两组状态码**都是 1**,
    说明 UOSSTAT/PER_ERROR 只表示"有东西非零",区分不了告警和错误。

    所以现在分两档:
      * **硬错误**(判 error):模块 status=1、CVSTAT 非 0(收敛块自己出错)、撕裂流超容差
      * **软信号**(最多判 warning):UOSSTAT / PER_ERROR / RSTAT 非 0、模块 status=2
    """
    hard: list[str] = []      # 真·错误
    soft: list[str] = []      # 只是"有东西非零"

    text = str(errtxt or "")
    if "completed with errors" in text:
        hard.append("Aspen 报告有模块/收敛块报错")

    # CVSTAT = 收敛块状态:它非 0 才是真出错(CVSTAT=1 时 .sum 里会出现 $OLVER 报错)
    if codes.get("CVSTAT") not in (0, None):
        hard.append(f"CVSTAT={codes.get('CVSTAT')}(收敛块出错)")

    for key in ("RSTAT", "UOSSTAT"):
        if codes.get(key) not in (0, None):
            soft.append(f"{key}={codes.get(key)}(非 0,含告警)")

    per_error = codes.get("PER_ERROR")
    if isinstance(per_error, (int, float)) and per_error > 1e-6:
        soft.append(f"PER_ERROR={per_error}(有非零状态项)")

    block_errors, block_warnings = [], []
    for b in blocks or []:
        status = b.get("status")
        if status == 1:
            block_errors.append(b)
        elif status == 2:
            block_warnings.append(b)

    for b in block_errors:
        hard.append(f"{b['name']} 报错:{b.get('message') or '未知原因'}")
    for b in block_warnings:
        soft.append(f"{b['name']} 告警:{b.get('message') or '未知原因'}")

    for tear in tears:
        err, tol = tear.get("error"), tear.get("tolerance")
        if isinstance(err, (int, float)) and tol and err > tol:
            hard.append(f"撕裂流 {tear.get('stream')} 未收敛(误差 {err} > {tol})")

    reasons = hard + soft
    if hard:
        severity = "error"
    elif reasons:
        severity = "warning"
    else:
        severity = "ok"

    return {
        "severity": severity,            # ok / warning / error
        # 有告警的数据仍然可用(只是引用时要注明),所以 converged 只排除 error。
        # 名字沿用 converged(库里已有列),语义是"能不能拿去分析"。
        "converged": severity != "error",
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


# ---------------------------------------------------------------- 单位诊断

def unit_gaps(streams: list[dict], blocks: list[dict]) -> dict:
    """统计"有原始值、但单位不认识导致换算不出来"的字段。

    为什么单独统计:老模型的单位集未必是 `METCBAR`(可能是 ENG:℉ / psia / lb·h⁻¹ / Btu·h⁻¹)。
    `units.convert` 遇到不认识的单位一律返回 None(宁缺勿错),表格上就是一列 `—`。
    用户看到大片空值时,必须能分清是"模型没结果"还是"工具不认识这个单位" ——
    这两件事的处置完全不同。
    """
    gaps: list[dict] = []

    for s in streams:
        raw = s.get("raw") or {}
        for key, field, _quantity in STREAM_FIELDS:
            item = raw.get(field)
            if item and item.get("value") is not None and s.get(key) is None:
                gaps.append({"scope": "stream", "name": s["name"],
                             "field": field, "unit": item.get("unit") or ""})

    for b in blocks:
        raw = b.get("raw") or {}
        for key, fields in (("duty_kw", BLOCK_DUTY_FIELDS),
                            ("power_kw", BLOCK_POWER_FIELDS),
                            ("area_m2", BLOCK_AREA_FIELDS)):
            if b.get(key) is not None:
                continue
            for field in fields:
                item = raw.get(field)
                if item and item.get("value") is not None:
                    gaps.append({"scope": "block", "name": b["name"],
                                 "field": field, "unit": item.get("unit") or ""})
                    break

    return {
        "count": len(gaps),
        "units": sorted({g["unit"] for g in gaps if g["unit"]}),
        "samples": gaps[:20],
    }


# ---------------------------------------------------------------- 总入口

def read_case(session, include_composition: bool = False,
              include_profiles: bool = True,
              progress: Progress | None = None,
              on_phase: PhaseHook | None = None) -> dict:
    """读一整个工况,返回可直接入库的 dict。"""
    t0 = time.time()

    def phase(name: str) -> None:
        if on_phase:
            try:
                on_phase(name)
            except Exception:
                pass  # 进度回调出错不能连累读取

    phase("读取流股")
    streams = read_streams(session, progress=progress,
                           include_composition=include_composition)
    phase("读取设备")
    blocks = read_blocks(session, progress=progress)
    # 收敛判定要用到模块级状态,所以必须放在 blocks 之后
    phase("判定收敛状态")
    convergence = read_convergence(session, blocks)
    phase("读取塔板剖面")
    profiles = read_column_profiles(session, blocks) if include_profiles else []

    phase("整理结果")
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
        "unit_gaps": unit_gaps(streams, blocks),
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
