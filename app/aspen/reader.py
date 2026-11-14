"""从 Aspen 数据树里读取工况数据。

所有路径都来自实测(见 `docs/aspen-field-map.md`),不要改名字。

一次 `read_case()` 产出:
  - model:模型信息
  - convergence:收敛/运行状态 + 撕裂流 + Aspen 原始提示文本
  - streams:流股表(已换算到固定单位)
  - blocks:设备表(已换算到固定单位,含 `balance` 焓平衡自检结果)
  - connections:设备 ↔ 流股连接关系(拓扑)
  - components:组分清单

规模提示:1000 条流股约 6000 次 COM 调用,必须放后台线程并给 `progress` 回调。

两个进度回调(都是可选的):
  * `progress(done, total, label)` —— 每读完一条流股/一个设备调一次,
    用于"读取设备 45/77"这种细粒度进度。
  * `on_phase(阶段名)` —— 阶段切换时调一次(读流股 → 读设备 → 判收敛 …),
    给网页进度条一个"现在在干什么"的粗粒度说明。
"""
from __future__ import annotations

import re
import time
from typing import Callable

from . import balance as balance_rules
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

# 流股焓。实测(2026-11-01,见 data/probe_topology.txt):
#   * `\Data\Streams\<流>\Output\HMX` 是**目录**,值在 `HMX\MIXED`(J/kmol);
#     直接读 `HMX` 拿到的是 `1` 这种标志位,所以必须先解析到叶子。
#   * `HMX_MASS\MIXED` = J/kg、`HMX_FLOW\MIXED` = MW(现成的 m·h,可直接相加)。
#   * `ENTH_CYCLE` / `L1_ENTH` / `LIQ_ENTH` 这些是自定义/分相/统计量,不能当流股焓。
# 三个量纲都按 UnitString 换算,不假设单位集(MET 与 ENG 都验证过)。
ENTHALPY_FIELDS = (
    ("enthalpy_kjkg", "HMX_MASS", "enthalpy"),
    ("enthalpy_kjkmol", "HMX", "enthalpy_molar"),
    ("enthalpy_flow_kw", "HMX_FLOW", "energy_flow"),
)

# 设备上的标量结果字段(不同设备类型用不同字段)
BLOCK_DUTY_FIELDS = ("QCALC", "QNET", "HX_DUTY")      # cal/sec
BLOCK_POWER_FIELDS = ("BRAKE_POWER", "ELEC_POWER")     # kW
BLOCK_AREA_FIELDS = ("HX_AREAC", "HX_AREAC2", "HX_AREAP")  # sqm;实测前者常空,后两个有值
# 塔的冷凝器 / 再沸器负荷(实测 2026-11-14:1 个塔一对,单位 cal/sec,冷凝为负)
BLOCK_TOWER_DUTY_FIELDS = (("cond_duty_kw", ("COND_DUTY",)), ("reb_duty_kw", ("REB_DUTY",)))

# 泵块上的标量结果(输出键, Aspen 字段, 量纲)。单位认不出来时 head_m / rho_kgm3
# 会是 None —— 所以 unit_gaps 要拿这张表一起查,别让它静默变空。
PUMP_BLOCK_FIELDS = (
    ("head_m", "HEAD_CAL", "length"),
    ("flow_m3h", "VFLOW", "volume_flow"),
    ("dp_bar", "IN_PRES", "pressure"),
    ("rho_kgm3", "RHO_LIQ", "molar_density"),
)

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
    soft: list[str] = []      # 值得提示,但数据仍可用

    text = str(errtxt or "")
    if "completed with errors" in text:
        hard.append("Aspen 报告有模块/收敛块报错")

    # CVSTAT = 收敛块状态:它非 0 才是真出错(CVSTAT=1 时 .sum 里会出现 $OLVER 报错)
    if codes.get("CVSTAT") not in (0, None):
        hard.append(f"CVSTAT={codes.get('CVSTAT')}(收敛块出错)")

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

    # 有模块状态非零,却一个都没定位到 —— 这种"有情况但没细节"才值得提示
    if codes.get("UOSSTAT") not in (0, None) and not block_warnings:
        soft.append(f"UOSSTAT={codes.get('UOSSTAT')} —— 有模块状态非零,但没读到具体是哪个模块")

    # 注意:RSTAT / PER_ERROR **只记录、不参与判定**。
    # 实测(2026-11-01):一个 CVSTAT=0、UOSSTAT=0、78 个模块全 status=0 的样本模型,
    # PER_ERROR 仍然是 1;而真有模块报错的模型 PER_ERROR 也是 1 —— 完全没有区分度。

    # 撕裂流:只作提示,**不判 error**。
    # 实测(2026-11-01,某 128 流股样本):
    # T3 的 INFO-VAR 误差 1.656 > 容差 0.0393,而 Aspen 控制面板只报
    # "The following Unit Operation blocks were completed with warnings: M0302 E0204 R0301",
    # CVSTAT=0 —— **Aspen 自己不认为这条回路没收敛**。
    # 原因大概率是 ERR/ERR_TOL 的可比性随变量类型变化(INFO-VAR 这种"信息变量"尤其),
    # 所以只能列出原始数值让人自己判断,不能替 Aspen 下结论。
    for tear in tears:
        err, tol = tear.get("error"), tear.get("tolerance")
        if isinstance(err, (int, float)) and tol and err > tol:
            soft.append(
                f"撕裂流 {tear.get('stream')}({tear.get('variable')})误差 {err} > 容差 {tol}"
                " —— Aspen 控制面板没有报错,请自行核对这条回路的收敛设置")

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
            value, unit = fetch(session, rf"{base}\{field}")
            rec[key] = units.convert(quantity, value, unit)
            raw[field] = {"value": value, "unit": unit}

        # 焓:HMX 是目录,值在 HMX\MIXED,所以统一走 _enthalpy_leaf 解析到叶子
        for key, field, quantity in ENTHALPY_FIELDS:
            value, unit, leaf = _enthalpy_leaf(session, base, field)
            rec[key] = units.convert(quantity, value, unit)
            if value is not None:
                raw[leaf] = {"value": value, "unit": unit}
        # 没有 HMX_FLOW、但有质量焓时,用 m·h 现算一个能量流(kW),让 ΔH 口径统一
        if rec.get("enthalpy_flow_kw") is None:
            rec["enthalpy_flow_kw"] = _flow_kw_from_mass(rec.get("enthalpy_kjkg"),
                                                        rec.get("mass_flow_kgh"))

        # 上下游:连接关系的通用来源(设备侧 IN_STRMID 常常不存在)
        upstream, _ = fetch(session, rf"{base}\SOURCE")
        downstream, _ = fetch(session, rf"{base}\DESTINATION")
        rec["source"] = str(upstream).strip() if upstream not in (None, "") else ""
        rec["destination"] = str(downstream).strip() if downstream not in (None, "") else ""

        rec["raw"] = raw
        if include_composition:
            rec["composition"] = read_composition(session, sid)
        out.append(rec)
        if progress:
            progress(n, total, sid)
    return out


def _enthalpy_leaf(session, base: str, field: str) -> tuple[object, str, str]:
    """读一个焓字段,并把目录解析到叶子(HMX → HMX\\MIXED)。

    实测:直接读 `HMX` 拿到的是 `1` 这种标志位,真正的值(和单位)在 `HMX\\MIXED`。

    Returns:
        (值, 单位, 叶子路径后缀);读不到时 (None, "", field)。
    """
    node = session.find(rf"{base}\{field}")
    if node is None:
        return None, "", field
    children = child_names(node)
    if not children:
        return (*fetch(session, rf"{base}\{field}"), field)
    child = "MIXED" if "MIXED" in children else (children[0] if len(children) == 1 else None)
    if child is None:      # 分相目录(LIQ/VAP 多个子节点),没有"混合"值,不猜
        return None, "", field
    leaf = f"{field}\\{child}"
    return (*fetch(session, rf"{base}\{leaf}"), leaf)


def _flow_kw_from_mass(enthalpy_kjkg, mass_flow_kgh):
    """质量焓(kJ/kg)× 质量流量(kg/h)→ kW。"""
    if enthalpy_kjkg is None or mass_flow_kgh is None:
        return None
    return enthalpy_kjkg * mass_flow_kgh / 3600.0


def _looks_index(text: str) -> bool:
    return bool(re.fullmatch(r"\d+(\.\d+)?", str(text or "").strip()))


def element_pairs(session, path: str) -> list[tuple[str, object]]:
    """读数组节点的 (元素名, 元素值)。节点不存在或不是数组返回 []。"""
    node = session.find(path)
    if node is None:
        return []
    pairs = []
    try:
        els = node.Elements
        for i in range(int(els.Count)):
            try:
                el = els.Item(i)
            except Exception:
                continue
            try:
                name = str(el.Name)
            except Exception:
                name = ""
            try:
                value = el.Value
            except Exception:
                value = None
            pairs.append((name, value))
    except Exception:
        return []
    return pairs


# ---------------------------------------------------------------- 连接关系

def read_connections(session, streams: list[dict],
                     blocks: list[dict] | None = None) -> list[dict]:
    """读"设备 ↔ 流股"的连接关系:一行 = (设备, 方向 in/out, 流股)。

    为什么不能只用 IN_STRMID
    -----------------------
    实测(2026-11-01,见 data/probe_topology.txt):67 台设备里**只有 8 台塔**有
    `Output\\IN_STRMID`,其余 59 台根本没有这个字段。真正通用的来源是流股侧的
    `\\Data\\Streams\\<流>\\Output\\SOURCE / DESTINATION`(值是块名,94/98 与 89/98 有值)。

    所以按三级来源合并,同一 (设备, 方向, 流股) 只留一行,并记下来自哪里:
      1. source="stream"   —— 流股 SOURCE / DESTINATION(主来源,最通用)
      2. source="strmid"   —— 设备 IN_STRMID / OUT_STRMID(塔上有,用它与流股侧互校)
      3. source="hx"       —— HeatX 分侧字段(Input FCOLD/FHOT,Output COLDIN/COLDOUT/HOTIN/HOTOUT)
    塔的 `Input\\FEED_STAGE`(Name=流股名、Value=板号)合并成行里的 stage。
    """
    known = {str(s["name"]).lower(): s["name"] for s in streams}
    rows: list[dict] = []
    index: dict[tuple[str, str, str], dict] = {}

    def add(block, direction: str, stream, *, side: str = "", port: str = "",
            stage=None, source: str = "stream") -> None:
        block = str(block or "").strip()
        stream = str(stream or "").strip()
        if not block or not stream:
            return
        key = (block, direction, stream)
        row = index.get(key)
        if row is None:
            row = {"block": block, "direction": direction, "stream": stream,
                   "side": "", "port": "", "stage": None, "source": source, "seq": 0}
            index[key] = row
            rows.append(row)
        if side and not row["side"]:
            row["side"] = side
        if port and not row["port"]:
            row["port"] = str(port)
        if stage is not None and row["stage"] is None:
            row["stage"] = stage

    # ---- 1) 流股侧 SOURCE / DESTINATION(通用来源)
    for stream in streams:
        sid = stream["name"]
        upstream = (stream.get("source") or "").strip()
        downstream = (stream.get("destination") or "").strip()
        if downstream:
            add(downstream, "in", sid, source="stream")
        if upstream:
            add(upstream, "out", sid, source="stream")

    block_names = [b["name"] for b in (blocks or [])]
    if not block_names:
        block_names = child_names(session.find(r"\Data\Blocks"))

    # ---- 2) 设备 IN_STRMID / OUT_STRMID(存在的设备合并进来)
    for bid in block_names:
        base = rf"\Data\Blocks\{bid}\Output"
        for direction, field in (("in", "IN_STRMID"), ("out", "OUT_STRMID")):
            for port, value in element_pairs(session, rf"{base}\{field}"):
                text = str(value or "").strip()
                stream = known.get(text.lower()) or known.get(port.strip().lower())
                if not stream:
                    continue
                add(bid, direction, stream,
                    port=port if _looks_index(port) else "", source="strmid")

    # ---- 3) HeatX 分侧字段 + 塔进料板号
    for bid in block_names:
        base = rf"\Data\Blocks\{bid}"
        for direction, side, paths in (
            ("in", "cold", ("Output\\COLDIN", "Input\\FCOLD")),
            ("out", "cold", ("Output\\COLDOUT", "Input\\FCOLD")),
            ("in", "hot", ("Output\\HOTIN", "Input\\FHOT")),
            ("out", "hot", ("Output\\HOTOUT", "Input\\FHOT")),
        ):
            for path in paths:
                value, _unit = fetch(session, rf"{base}\{path}")
                stream = known.get(str(value or "").strip().lower())
                if stream:
                    add(bid, direction, stream, side=side, source="hx")
                    break
        for stream_name, stage in element_pairs(session, rf"{base}\Input\FEED_STAGE"):
            target = known.get(str(stream_name).strip().lower())
            if not target:
                continue
            try:
                stage_no = int(float(stage))
            except (TypeError, ValueError):
                continue
            add(bid, "in", target, stage=stage_no, source="feed_stage")

    for i, row in enumerate(rows):
        row["seq"] = i
    return rows

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
    # 塔没有单一 qcalc:冷凝器 / 再沸器各一块负荷(冷凝为负值,原样保留)
    for key, fields in BLOCK_TOWER_DUTY_FIELDS:
        rec[key] = _first_converted(session, base, fields, "power", rec["raw"])

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


# ---------------------------------------------------------------- 结果完整性

# 流股上"有结果才会有值"的字段
STREAM_VALUE_KEYS = ("temperature_c", "pressure_bar", "mass_flow_kgh",
                     "mole_flow_kmolh", "vap_frac", "vol_flow_m3h")

# 低于这个覆盖率就当"模型没有结果"(实测未运行的模型是 0%)
EMPTY_COVERAGE = 0.1


def value_coverage(streams: list[dict]) -> float:
    """流股数值的填空率:0 = 一条值都没有(模型没跑过)。"""
    total = len(streams) * len(STREAM_VALUE_KEYS)
    if not total:
        return 1.0
    filled = sum(1 for s in streams for k in STREAM_VALUE_KEYS if s.get(k) is not None)
    return filled / total


def mark_empty(convergence: dict, coverage: float, run_id=None) -> dict:
    """没 Run 过的模型:单独判成 "empty",不能给绿勾。

    2026-11-01 实测:打开一个从没 Run 过的 .bkp 直接读 ——
    Run-Status 的 8 个状态码全 None、run_id 拿不到、630 个流股字段 **0% 有值**,
    但旧逻辑是"没有信号 = 没有问题",于是判成 ok / 可用于定量分析:
    一张全空的表配一个"收敛"的绿勾,比报错更误导人。
    """
    if convergence.get("severity") == "error":
        return convergence          # 已经有硬错误,不用再叠一层
    if coverage >= EMPTY_COVERAGE:
        return convergence

    hint = "Run-Status 为空" if not str(run_id or "").strip() else "Run-Status 有值但数值读不到"
    convergence["severity"] = "empty"
    convergence["converged"] = False
    convergence["reasons"] = [
        f"模型没有结果(流股数值只有 {coverage:.0%} 可读,{hint})"
        " —— 请先在 Aspen 里 Run 一次,再重新读取"
    ] + list(convergence.get("reasons") or [])
    return convergence


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
        # 焓的原始键是叶子路径(如 HMX\MIXED),所以按前缀找
        for key, field, _quantity in ENTHALPY_FIELDS:
            if s.get(key) is not None:
                continue
            for raw_key, item in raw.items():
                if raw_key == field or raw_key.startswith(field + "\\"):
                    if item and item.get("value") is not None:
                        gaps.append({"scope": "stream", "name": s["name"],
                                     "field": raw_key, "unit": item.get("unit") or ""})
                    break

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

        # 泵的标量字段同样要统计:单位集换了写法(meter → m-kgf/kg、kmol/cum → mol/cc)
        # 时,这里会把认不出的单位报出来,而不是让泵页静默变成一片「—」。
        if str(b.get("type") or "").strip().lower() == "pump":
            for key, field, _quantity in PUMP_BLOCK_FIELDS:
                if b.get(key) is not None:
                    continue
                item = raw.get(field)
                if item and item.get("value") is not None:
                    gaps.append({"scope": "block", "name": b["name"],
                                 "field": field, "unit": item.get("unit") or ""})

    return {
        "count": len(gaps),
        "units": sorted({g["unit"] for g in gaps if g["unit"]}),
        "samples": gaps[:20],
    }


# ---------------------------------------------------------------- 泵参数

# 进口流股 p_v(蒸气压)的读取路径,实测 2026-11-14(华东理工模型,Aspen Plus V12.1):
#   `Output\STRM_UPP\PBUB\MIXED\TOTAL` = 泡点压力(bar)。单相液相时它就是蒸气压,
#   正好是 NPSHₐ 公式里的 p_v;20/20 台泵进口流股都有值。
# ⚠️ 不要读 `Output\PL`:它是**目录节点**(值恒为 1,还带单位,极像读数);
#   真正的分压在 `PL\MIXED\LIQUID\<组分>`,没有混合物总压叶子。
#   所以读取时必须跳过有子节点的节点,再用 pump.sanitize_pv_bar 挡住 0/负值。
PV_STREAM_PATHS = (
    r"STRM_UPP\PBUB\MIXED\TOTAL",
    r"PBUB\MIXED\TOTAL",
    r"STRM_UPP\PL\MIXED\TOTAL",
)


def read_stream_vapor_pressure(session, stream_name: str):
    """进口流股的 p_v(bar)。读不到返回 None —— 绝不拿 0 或进口压力顶替。"""
    from app.design import pump as pump_rules

    base = rf"\Data\Streams\{stream_name}\Output"
    for rel in PV_STREAM_PATHS:
        node = session.find(rf"{base}\{rel}")
        if node is None or child_names(node):  # 目录节点(PL)的值是标志位,不是读数
            continue
        value, unit = fetch(session, rf"{base}\{rel}")
        pv = pump_rules.sanitize_pv_bar(units.convert("pressure", value, unit))
        if pv is not None:
            return pv
    return None


def enrich_side_temperatures(streams, blocks, connections) -> None:
    """给每台设备补 进口/出口流股温度,换热器另补冷侧;**纯数据、不碰 COM**。

    为什么不用设备自己的 `B_TEMP`:
      * 塔是逐级数组(顶/底温好用,保留在 top/bottom 两列);
      * 换热器(HeatX)没有 `B_TEMP`;
      * 反应器 RPlug 的 `B_TEMP` 读出来是标志位 `1.0`(和 `HMX` 读成 `1` 同一类坑)。
    流股温度是实测值,所有设备通用,所以统一取连接流股的 `temperature_c`。

    口径:
      * 主进口 / 主出口:换热器优先 `hot` 侧,其余设备取第一个进/出流股;
      * `cold_*` 两列只有 `side=cold` 的连接才有值;
      * 出口汽化率同样取主出口流股。
    """
    by_stream = {s["name"]: s for s in streams}
    inflow: dict[str, list] = {}
    outflow: dict[str, list] = {}
    for row in connections or []:
        bucket = inflow if row.get("direction") == "in" else outflow
        bucket.setdefault(row.get("block"), []).append(row)

    def pick(rows, side=None):
        for r in rows:
            sid = r.get("stream")
            if sid in by_stream and (side is None or (r.get("side") or "") == side):
                return by_stream[sid]
        return None

    for block in blocks:
        name = block["name"]
        ins, outs = inflow.get(name, []), outflow.get(name, [])
        main_in = pick(ins, "hot") or pick(ins)
        main_out = pick(outs, "hot") or pick(outs)
        cold_in, cold_out = pick(ins, "cold"), pick(outs, "cold")
        block["inlet_temp_c"] = main_in.get("temperature_c") if main_in else None
        block["outlet_temp_c"] = main_out.get("temperature_c") if main_out else None
        block["cold_in_temp_c"] = cold_in.get("temperature_c") if cold_in else None
        block["cold_out_temp_c"] = cold_out.get("temperature_c") if cold_out else None
        block["outlet_vap_frac"] = main_out.get("vap_frac") if main_out else None


def read_pump_fields(session, streams, blocks, connections) -> None:
    r"""给 Pump 块补 扬程/流量/效率/进口压力/密度/p_v/建议电机 —— **就地写进 blocks**。

    实测(2026-11-08 / 11-14,V12.1,见 docs/aspen-field-map.md §13):
      `HEAD_CAL`(meter) / `VFLOW`(cum/hr) / `CEFF` / `IN_PRES`(bar) /
      `RHO_LIQ`(kmol/cum) + `MW_LIQ`(kg/kmol);
      进口流股 `STRM_UPP\PBUB\MIXED\TOTAL` = 泡点压力(bar),当 p_v 用。

    读不到的一律留 None,不拿计算值顶替 —— 页面显示「—」比编一个数好。
    只对 Pump 块多花这几次 COM 调用,不扩大全表读取成本。
    """
    from app.design import pump as pump_rules

    inflow: dict[str, list[str]] = {}
    outflow: dict[str, list[str]] = {}
    for row in connections or []:
        bucket = inflow if row.get("direction") == "in" else outflow
        bucket.setdefault(row.get("block"), []).append(row.get("stream"))

    by_name = {s["name"]: s for s in streams}
    for block in blocks or []:
        if str(block.get("type") or "").strip().lower() != "pump":
            continue
        base = rf"\Data\Blocks\{block['name']}\Output"
        raw = block.setdefault("raw", {})

        def grab(field: str, quantity: str):
            """返回 (换算值, 原始值, 单位);原始值单独留一份进 raw,便于追溯。"""
            value, unit = fetch(session, f"{base}\\{field}")
            if value is None:
                return None, None, unit
            raw[field] = {"value": value, "unit": unit}
            if quantity in ("fraction", "number"):
                try:
                    return float(value), value, unit
                except (TypeError, ValueError):
                    return None, value, unit
            return units.convert(quantity, value, unit), value, unit

        head, _h_raw, _h_unit = grab("HEAD_CAL", "length")
        flow, _f_raw, _f_unit = grab("VFLOW", "volume_flow")
        # ⚠️ 这个节点叫 `IN_PRES`,但实测是**压升 ΔP**(bar),不是进口压力:
        #    dp ÷ (ρ·g) 与 `HEAD_CAL` 逐台吻合到 0.00 m(2026-11-08,21 台全对)。
        # 真正的进口压力在进口流股上,见下面的 p_in_bar。
        dp, _dp_raw, _dp_unit = grab("IN_PRES", "pressure")
        eff, _e_raw, _e_unit = grab("CEFF", "number")
        rho_molar, _rho_raw, _rho_unit = grab("RHO_LIQ", "molar_density")
        mw, _mw_raw, _mw_unit = grab("MW_LIQ", "number")

        inlet = next((by_name[s] for s in inflow.get(block["name"], []) if s in by_name), None)
        outlet = next((by_name[s] for s in outflow.get(block["name"], []) if s in by_name), None)

        block["head_m"] = head
        block["flow_m3h"] = flow
        block["pump_eff"] = eff
        block["dp_bar"] = dp
        block["p_in_bar"] = inlet.get("pressure_bar") if inlet else None
        block["p_out_bar"] = outlet.get("pressure_bar") if outlet else None
        # p_v:直接取进口流股的泡点压力(实测 20/20 有值);读不到才留 None,页面可手填。
        pv = read_stream_vapor_pressure(session, inlet["name"]) if inlet else None
        block["pv_bar"] = pv
        block["pv_source"] = "aspen" if pv is not None else None
        # 密度口径(2026-11-08 定):**优先用进口流股的质量流量÷体积流量** ——
        # 单相时它就是实测密度,与流量守恒自洽。实测有一台泵的 RHO_LIQ×MW 与它差 24%,
        # 说明泵块那个值不是按进口条件给的。两相时 m/V 是混合密度,不能用,退回泵块值。
        block["pump_vap_frac"] = inlet.get("vap_frac") if inlet else None
        rho_from_flow = None
        vap = block["pump_vap_frac"]
        if inlet and (vap is None or float(vap) <= 0.001):
            mass = inlet.get("mass_flow_kgh")
            vol = inlet.get("vol_flow_m3h")
            if mass and vol:
                rho_from_flow = float(mass) / float(vol)
        block["rho_kgm3"] = (rho_from_flow if rho_from_flow is not None
                             else pump_rules.mass_density_kgm3(rho_molar, mw))
        block["motor_kw"], _note = pump_rules.pick_motor(block.get("power_kw"))


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
    # 再补一刀:模型可能根本没运行过 —— 那种情况"没有任何信号",不能当 ok
    mark_empty(convergence, value_coverage(streams), convergence.get("run", {}).get("RUNID"))
    phase("读取连接关系")
    connections = read_connections(session, streams, blocks)
    phase("补设备进出温度")
    enrich_side_temperatures(streams, blocks, connections)
    phase("读取泵参数")
    read_pump_fields(session, streams, blocks, connections)
    phase("焓平衡自检")
    results = balance_rules.compute(streams, blocks, connections)
    for block in blocks:
        block["balance"] = results.get(block["name"])
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
        "connections": connections,
        "balance": results,
        "components": components,
        "unit_gaps": unit_gaps(streams, blocks),
        "stats": {
            "stream_count": len(streams),
            "block_count": len(blocks),
            "column_count": len(profiles),
            "connection_count": len(connections),
            "balance_summary": balance_rules.summarize(results),
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
