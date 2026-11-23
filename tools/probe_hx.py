"""tools/probe_hx.py —— 换热器选型 P1-1 探针(**只读**)

要回答的问题
------------
1. `\\Data\\Utilities\\<U>\\Output` 里到底有哪些温度/用量字段?单位是什么?
2. Heater / HeaterX 的 `Input\\UTILITY_ID` 能不能读?值是不是公用工程名?
3. HeatX 的 `Output\\HX_UA` 有没有值?单位是什么?
4. 顺带看一眼侧流股的 `CP` / `THK` 候选节点(Phase 2 用,不影响本期结论)。

怎么用
------
    python -m tools.probe_hx                        # 附着当前打开的第一个模型
    python -m tools.probe_hx --model 关键字          # 只探路径里含关键字的模型
    python -m tools.probe_hx --out data/probe_hx.txt

前提:模型已在 Aspen Plus 里打开(最好 Run 过);**不调用 Run / Reinit / Save**,不写任何值。
产出:`data/probe_hx.txt`(自带结论的证据),把文件发回来即可。
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from app.aspen import reader
    from app.aspen.connect import AspenNotRunning, attach, list_open_models
except Exception as exc:  # pragma: no cover - 只在缺依赖时触发
    print(f"导入失败:{exc}")
    raise SystemExit(2) from None

UTIL_ROOT = r"\Data\Utilities"
BLOCK_ROOT = r"\Data\Blocks"
HEAT_TYPES = {"Heater", "HeaterX", "HeatX", "MHeatX"}
UTIL_TEMP_FIELDS = (
    "UTL_IN_TEMP",
    "UTL_OUT_TEMP",
    "UTL_B_TEMP",
    "UTL_TRATE",
    "UTL_DUTY",
    "UTL_ENTH_IN",
    "UTL_ENTH_OUT",
    "UTIL_TYPE",
)
PROP_HINTS = ("CP", "THK", "KTH", "VISC", "MUMX", "RHO")


def fmt(value, unit="") -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        text = f"{value:.6g}"
    else:
        text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return f"{text} {unit}".strip()


def node_snapshot(session, path: str) -> dict:
    value, unit = reader.fetch(session, path)
    node = session.find(path)
    return {
        "path": path,
        "exists": node is not None,
        "value": value,
        "unit": unit,
        "children": reader.child_names(node),
    }


def probe_utilities(session, lines: list[str]) -> dict:
    lines.append("## 1. 公用工程树")
    root = session.find(UTIL_ROOT)
    if root is None:
        lines.append("  * 找不到 \\Data\\Utilities")
        return {"names": [], "members": {}, "readable": {}}

    names = reader.child_names(root)
    lines.append(f"  * 成员({len(names)}):{', '.join(names) if names else '无'}")
    members = {}
    for util in names:
        base = f"{UTIL_ROOT}\\{util}"
        in_names = reader.child_names(session.find(base + r"\Input"))
        out_names = reader.child_names(session.find(base + r"\Output"))
        lines.append(f"  - {util}:Input {len(in_names)} 项,Output {len(out_names)} 项")
        fields = {}
        for field in UTIL_TEMP_FIELDS:
            snap = node_snapshot(session, f"{base}\\Output\\{field}")
            if snap["exists"]:
                fields[field] = snap
                lines.append(f"      {field:12s} = {fmt(snap['value'], snap['unit'])}")
        for arr in ("BLK_ID", "BLK_TYPE", "UTL_USAGE"):
            pairs = reader.element_pairs(session, f"{base}\\Output\\{arr}")
            if pairs:
                lines.append(f"      {arr:12s} = {len(pairs)} 项(前 6:{[p[0] for p in pairs[:6]]})")
        members[util] = {
            "fields": fields,
            "in_names": in_names,
            "out_names": out_names,
        }

    readable = {
        field: sum(
            1
            for u in members.values()
            if field in u["fields"] and u["fields"][field]["value"] is not None
        )
        for field in UTIL_TEMP_FIELDS
    }
    lines.append(
        "  * 字段可读台数:" + ", ".join(f"{k}={v}/{len(names)}" for k, v in readable.items())
    )
    return {"names": names, "members": members, "readable": readable}


def probe_heat_blocks(session, lines: list[str]) -> dict:
    lines.append("")
    lines.append("## 2. 换热类设备与 UTILITY_ID / HX_UA")
    blocks = reader.read_blocks(session)
    heat = [b for b in blocks if (b.get("type") or "") in HEAT_TYPES]
    lines.append(
        f"  * 设备总数 {len(blocks)},换热类 {len(heat)}:"
        + ", ".join(
            f"{b['type']}={sum(1 for x in heat if x['type'] == b['type'])}" for b in heat[:0]
        )
    )
    by_type: dict[str, int] = {}
    for b in heat:
        by_type[b["type"]] = by_type.get(b["type"], 0) + 1
    lines.append("  * 类型分布:" + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))

    rows = []
    for b in heat:
        base = f"{BLOCK_ROOT}\\{b['name']}"
        util_node = node_snapshot(session, base + r"\Input\UTILITY_ID")
        ua = node_snapshot(session, base + r"\Output\HX_UA")
        area = node_snapshot(session, base + r"\Output\HX_AREAC2")
        row = {
            "block": b["name"],
            "type": b["type"],
            "duty_kw": b.get("duty_kw"),
            "area_m2": b.get("area_m2"),
            "utility_id": util_node["value"],
            "utility_id_unit": util_node["unit"],
            "ua": ua["value"],
            "ua_unit": ua["unit"],
            "ua_exists": ua["exists"],
            "area_field": area["value"],
        }
        rows.append(row)

    for r in rows:
        lines.append(
            f"  - {r['block']:8s} {r['type']:8s} duty={fmt(r['duty_kw'], 'kW'):>12s} "
            f"area={fmt(r['area_m2'], 'm2'):>10s} UTILITY_ID={fmt(r['utility_id'], r['utility_id_unit']):>14s} "
            f"UA={fmt(r['ua'], r['ua_unit']):>18s}"
        )

    heators = [r for r in rows if r["type"] in ("Heater", "HeaterX")]
    with_id = [r for r in heators if r["utility_id"] not in (None, "")]
    with_ua = [r for r in rows if r["ua_exists"] and r["ua"] is not None]
    lines.append("")
    lines.append(f"  * Heater/HeaterX {len(heators)} 台,能读到 UTILITY_ID 字符串 {len(with_id)} 台")
    lines.append(
        f"  * 能读到 HX_UA 的设备 {len(with_ua)} 台:"
        + (
            ", ".join(f"{r['block']}({r['ua']} {r['ua_unit']})" for r in with_ua)
            if with_ua
            else "无"
        )
    )
    return {"rows": rows, "heators_with_id": with_id, "with_ua": with_ua}


def probe_side_props(session, heat: dict, lines: list[str]) -> None:
    lines.append("")
    lines.append("## 3. 侧流股物性候选(Phase 2 参考,不影响 P1 结论)")
    samples = []
    for r in heat["rows"]:
        if r["type"] != "HeatX":
            continue
        base = f"{BLOCK_ROOT}\\{r['block']}"
        for field in ("COLDIN", "HOTIN"):
            value, _ = reader.fetch(session, f"{base}\\Output\\{field}")
            if value:
                samples.append(str(value))
        if len(samples) >= 2:
            break
    if not samples:
        lines.append("  * 没有可用的 HeatX 侧流股样本,跳过")
        return
    for stream in samples[:2]:
        sbase = rf"\Data\Streams\{stream}"
        children = reader.child_names(session.find(sbase + r"\Output"))
        hits = [c for c in children if any(h in c.upper() for h in PROP_HINTS)]
        lines.append(f"  - 流股 {stream}:Output 下命中物性关键字的节点 {len(hits)} 个")
        for name in hits[:12]:
            value, unit = reader.fetch(session, f"{sbase}\\Output\\{name}")
            lines.append(
                f"      {name:24s} = {fmt(value, unit):>24s}  (子项 {reader.child_names(session.find(f'{sbase}\\Output\\{name}'))[:3]})"
            )
        upper = reader.child_names(session.find(sbase + r"\Output\STRM_UPP"))
        if upper:
            lines.append(f"      STRM_UPP 子项({len(upper)}):{', '.join(upper[:15])}")


def probe_names(session, heat: dict, lines: list[str]) -> None:
    lines.append("")
    lines.append("## 4. 节点名证据(便于核对路径)")
    if heat["rows"]:
        hx = next((r for r in heat["rows"] if r["type"] == "HeatX"), None)
        if hx:
            base = f"{BLOCK_ROOT}\\{hx['block']}"
            out_names = reader.child_names(session.find(base + r"\Output"))
            hits = [n for n in out_names if n.upper().startswith(("HX_", "COLD", "HOT"))]
            lines.append(
                f"  - HeatX {hx['block']} Output 里 HX_/COLD/HOT 节点({len(hits)}):{', '.join(hits)}"
            )
        heater = next((r for r in heat["rows"] if r["type"] in ("Heater", "HeaterX")), None)
        if heater:
            base = f"{BLOCK_ROOT}\\{heater['block']}"
            in_names = reader.child_names(session.find(base + r"\Input"))
            hits = [
                n
                for n in in_names
                if "UTIL" in n.upper() or "TEMP" in n.upper() or "PRES" in n.upper()
            ]
            lines.append(
                f"  - Heater {heater['block']} Input 里 UTIL/TEMP/PRES 节点({len(hits)}):{', '.join(hits)}"
            )
            out_names = reader.child_names(session.find(base + r"\Output"))
            hits = [n for n in out_names if "UTIL" in n.upper() or "Q" in n.upper()]
            lines.append(
                f"  - Heater {heater['block']} Output 里 UTIL/Q 节点({len(hits)}):{', '.join(hits[:40])}"
            )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="模型路径关键字;省略则取第一个打开的模型")
    ap.add_argument("--out", default="data/probe_hx.txt")
    args = ap.parse_args()

    try:
        models = list_open_models()
    except AspenNotRunning as exc:
        print(exc)
        return 2
    if not models:
        print("没有打开着的 Aspen 模型")
        return 2
    chosen = models[0]
    if args.model:
        match = [m for m in models if args.model.lower() in m.lower()]
        if not match:
            print(f"没有匹配的模型:{args.model}\n已打开:{models}")
            return 2
        chosen = match[0]

    try:
        session = attach(chosen)
    except AspenNotRunning as exc:
        print(exc)
        return 2

    lines: list[str] = [
        "换热器选型 P1-1 探针报告",
        f"时间:{dt.datetime.now():%Y-%m-%d %H:%M:%S}",
        f"模型:{session.model_path}",
        "",
    ]
    util = probe_utilities(session, lines)
    heat = probe_heat_blocks(session, lines)
    probe_side_props(session, heat, lines)
    probe_names(session, heat, lines)

    lines.append("")
    lines.append("## 5. 结论")
    if util["names"]:
        ok_t = [
            f"{f}={n}/{len(util['names'])}"
            for f, n in util["readable"].items()
            if f in ("UTL_IN_TEMP", "UTL_OUT_TEMP")
        ]
        lines.append(f"  * 公用工程 {len(util['names'])} 个;温度字段可读:{', '.join(ok_t) or '无'}")
    else:
        lines.append("  * 公用工程树为空")
    lines.append(
        f"  * Heater/HeaterX {len(heat['rows']) - sum(1 for r in heat['rows'] if r['type'] == 'HeatX')} 台,"
        f"UTILITY_ID 可读 {len(heat['heators_with_id'])} 台"
    )
    lines.append(f"  * HX_UA 有值 {len(heat['with_ua'])} 台")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[written] {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
