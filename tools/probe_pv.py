"""probe_pv.py -- 流股蒸气压 p_v 只读探测(v2:查 PL 为什么是 0,并试 PBUB)

第一版结果(2026-11-14,华东理工模型,20 台泵)
------------------------------------------------
`\\Data\\Streams\\<流>\\Output\\PL` 节点**存在**,但 20/20 台泵进口流股全是 `0`、
且单位字符串为空 —— 这不是一个真实的蒸气压读数(真实读数会带 bar)。
`PBUB`(泡点压力)不在输出里,因为物性集里没加。

这一版要回答
------------
  1. `PL` 是不是目录节点(值在 `PL\\MIXED` / `PL\\LIQUID` 这类叶子);
  2. 加了 `PBUB` 之后能不能取到带单位的真实值;
  3. 用带单位换算的密度重算 NPSHₐ,量级对不对。

原则
----
只读。不调用 Run / Reinit / Save。读不到就写「没有」,不猜。

用法(真实桌面会话、Aspen 已打开且模型已 Run;DSH 沙箱里 ROT 是空的)
----
    python tools\\probe_pv.py            # 只打印
    python tools\\probe_pv.py --save     # 另存 probe_out/probe_pv.txt|json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.aspen import units  # noqa: E402
from tools.probe_aspen import Report, attach, find  # noqa: E402
from tools.probe_pump import children, name_of, unit_of, value_of  # noqa: E402

G = 9.80665

# 蒸气压候选:先标量,再试"目录 + 相"的写法(PL 可能是目录节点,像 HMX 一样)
# 结论(2026-11-14 实测):真正可用的是 `STRM_UPP\PBUB\MIXED\TOTAL`(混合物泡点压力);
# `PL` 是目录,分压在 `PL\MIXED\LIQUID\<组分>` 上,没有总压叶子。
PV_CANDIDATES = (
    "STRM_UPP\\PBUB\\MIXED\\TOTAL",
    "STRM_UPP\\PL\\MIXED\\TOTAL",
    "PL",
    "PL\\MIXED",
    "PL\\LIQUID",
    "PL\\VAPOR",
    "PBUB",
    "PBUB\\MIXED",
    "PBUB\\LIQUID",
    "PSAT",
    "VAP_PRES",
    "PVAP",
    "P_VAP",
)

PV_KEYWORDS = ("VAP", "BUB", "PSAT")


def conv(tree, path: str, quantity: str):
    """读一个节点并按量纲换算;单位不认识/读不到/目录节点 → None。

    目录节点必须跳过:`PL` 这种读出来是标志位 1 并且带 bar 单位,混进来会把 NPSHₐ 算成负数。
    """
    node = find(tree, path)
    if node is None:
        return None
    if children(node):  # 目录,不是读数
        return None
    return units.convert(quantity, value_of(node), unit_of(node))


def val_unit(tree, path: str) -> tuple[object, str]:
    node = find(tree, path)
    if node is None:
        return None, ""
    return value_of(node), unit_of(node)


def describe(tree, path: str) -> dict:
    """一个节点的样子:是否存在 / 值 / 单位 / 子节点名。"""
    node = find(tree, path)
    if node is None:
        return {"path": path, "exists": False}
    subs_all = [name_of(k) for k in children(node)]
    return {
        "path": path,
        "exists": True,
        "value": value_of(node),
        "unit": unit_of(node),
        "child_count": len(subs_all),
        "children": subs_all[:12],
    }


def walk(
    node,
    path: str = "",
    depth: int = 0,
    max_depth: int = 3,
    out: list | None = None,
    limit: int = 4000,
) -> list:
    """递归遍历子树,收集 (路径, 值, 单位, 子节点数)。目录节点的 Value 常是 0。"""
    if out is None:
        out = []
    for k in children(node):
        if len(out) >= limit:
            return out
        name = name_of(k)
        p = f"{path}\\{name}" if path else name
        out.append((p, value_of(k), unit_of(k), len(children(k))))
        if depth < max_depth:
            walk(k, p, depth + 1, max_depth, out, limit)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="把报告写到 probe_out/")
    ap.add_argument("--out", default="probe_out", help="报告输出目录")
    a = ap.parse_args()

    rep = Report()
    moniker, _doc, tree = attach(rep, ["Apwn.Document"])
    if tree is None:
        rep.say("\n[结论] 没附着上。检查: Aspen 是否开着、是否在真实桌面会话跑。")
        return 3
    rep.item("附着到", moniker)

    streams = [name_of(n) for n in children(find(tree, r"\Data\Streams"))]
    rep.head("3. 流股清单")
    rep.item("流股数量", len(streams))
    if not streams:
        rep.say("\n  没有流股,换一个已 Run 过的模型。")
        if a.save:
            _save(rep, a.out)
        return 0

    # ---- 4) PL / PBUB 节点逐个解剖
    first = streams[0]
    base0 = rf"\Data\Streams\{first}\Output"
    rep.head("4. 蒸气压节点解剖(第一个流股)")
    names = [name_of(k) for k in children(find(tree, base0))]
    rep.item("Output 子节点数", len(names))
    rep.item(
        "名字像蒸气压的",
        [
            n
            for n in names
            if n.upper() in ("PL", "PBUB") or any(w in n.upper() for w in PV_KEYWORDS)
        ]
        or "(无)",
    )
    for path in PV_CANDIDATES:
        d = describe(tree, rf"{base0}\{path}")
        if not d["exists"]:
            rep.item(path, "不存在")
            continue
        rep.item(
            path,
            f"值={d['value']}  单位={d['unit']!r}  "
            f"子节点={d['child_count']}个 {d['children'] if d['children'] else ''}",
        )

    # ---- 4b) 按单位/数值扫子树:泡点压力到底藏在哪个节点
    rep.head("4b. 扫 P0101 进口流股 0103 的 Output 子树")
    scan_stream = "0103"
    rows = walk(find(tree, rf"\Data\Streams\{scan_stream}\Output"))
    rep.item("子树节点数(最多 4000)", len(rows))
    bar_rows = [r for r in rows if "bar" in str(r[2]).lower() and r[1] is not None]
    rep.say(f"  带 bar 单位的节点 {len(bar_rows)} 个(前 40 个):")
    for p, v, u, n in bar_rows[:40]:
        rep.say(f"    {p}  = {v} {u}" + (f"   ({n} 子节点)" if n else ""))
    # 用户截图里 0103 的「泡点压力」= 0.533831 bar、WATER 分压 = 0.6529 bar
    for t in (0.533831, 0.6529):
        hit = [
            r
            for r in rows
            if isinstance(r[1], (int, float)) and abs(float(r[1]) - t) <= abs(t) * 2e-3 + 1e-9
        ]
        rep.item(f"数值 ≈ {t} 命中", hit[:6] or "(无)")
    prop_data = find(tree, rf"\Data\Streams\{scan_stream}\Output\Prop Data")
    if prop_data is None:
        prop_data = find(tree, rf"\Data\Streams\{scan_stream}\Output\PROP_DATA")
    if prop_data is not None:
        subs = [name_of(k) for k in children(prop_data)]
        rep.item("Prop Data 子节点", subs[:40] or "(空目录)")

    # ---- 5) 单位确认(密度算错会把 NPSHₐ 算歪)
    rep.head("5. 单位确认(第一个流股,带换算)")
    for field, quantity in (
        ("RES_TEMP", "temperature"),
        ("RES_PRES", "pressure"),
        ("RES_MASSFLOW", "mass_flow"),
        ("RES_VOLFLOW", "volume_flow"),
        ("RES_VFRAC", "fraction"),
    ):
        raw_v, raw_u = val_unit(tree, rf"{base0}\{field}")
        rep.item(
            field, f"值={raw_v}  单位={raw_u!r}  → {conv(tree, rf'{base0}\{field}', quantity)}"
        )

    # ---- 6) 泵与进口流股
    pumps: list[str] = []
    for node in children(find(tree, r"\Data\Blocks")):
        btype = ""
        try:
            btype = str(node.AttributeValue(6))
        except Exception:
            pass
        if "pump" in btype.lower():
            pumps.append(name_of(node))

    src_dst: dict[str, tuple[str, str]] = {}
    for sname in streams:
        src = val_unit(tree, rf"\Data\Streams\{sname}\Output\SOURCE")[0] or ""
        dst = val_unit(tree, rf"\Data\Streams\{sname}\Output\DESTINATION")[0] or ""
        src_dst[sname] = (str(src).strip(), str(dst).strip())

    pump_inlet = {
        p: [s for s, (_src, dst) in src_dst.items() if dst.lower() == p.lower()] for p in pumps
    }

    rep.head("6. 泵进口流股的 T / p_in / ρ / 蒸气压候选")
    coverage: dict[str, int] = {}
    pairs = 0
    for p in pumps:
        for sname in pump_inlet[p]:
            pairs += 1
            base = rf"\Data\Streams\{sname}\Output"
            temp = conv(tree, rf"{base}\RES_TEMP", "temperature")
            p_in = conv(tree, rf"{base}\RES_PRES", "pressure")
            mass = conv(tree, rf"{base}\RES_MASSFLOW", "mass_flow")
            vol = conv(tree, rf"{base}\RES_VOLFLOW", "volume_flow")
            vap = conv(tree, rf"{base}\RES_VFRAC", "fraction")
            rho = mass / vol if mass and vol else None
            parts = []
            for cand in PV_CANDIDATES:
                pv = conv(tree, rf"{base}\{cand}", "pressure")
                if pv is not None:
                    parts.append(f"{cand}={pv:g}bar")
                    coverage[cand] = coverage.get(cand, 0) + 1
            rep.item(
                f"{p} 进口 {sname}",
                f"T={temp}  p_in={p_in} bar  ρ={None if rho is None else round(rho)} kg/m³  "
                f"vap={vap}  " + ("  ".join(parts) if parts else "❌ 无可用 p_v"),
            )

    # ---- 7) NPSHₐ 交叉验算
    rep.head("7. NPSHₐ 交叉验算(同一套换算;[Aspen NPSH-AVAIL] 仅参考)")
    for p in pumps:
        avail, avail_u = val_unit(tree, rf"\Data\Blocks\{p}\Output\NPSH-AVAIL")
        for sname in pump_inlet[p]:
            base = rf"\Data\Streams\{sname}\Output"
            p_in = conv(tree, rf"{base}\RES_PRES", "pressure")
            mass = conv(tree, rf"{base}\RES_MASSFLOW", "mass_flow")
            vol = conv(tree, rf"{base}\RES_VOLFLOW", "volume_flow")
            if p_in is None or not mass or not vol:
                continue
            rho = mass / vol
            parts = []
            for cand in PV_CANDIDATES:
                pv = conv(tree, rf"{base}\{cand}", "pressure")
                if pv is not None:
                    parts.append(f"{cand}: NPSHₐ={(p_in - pv) * 1e5 / (rho * G):.2f} m")
            rep.item(
                f"{p} {sname}",
                "  ".join(parts) + (f"   [Aspen={avail} {avail_u}]" if avail is not None else ""),
            )

    # ---- 8) 结论
    rep.head("8. 结论")
    if coverage:
        for cand, n in sorted(coverage.items(), key=lambda kv: -kv[1]):
            rep.item(f"{cand} 带单位可取", f"{n}/{pairs} 台泵进口流股")
    else:
        rep.item("蒸气压", "❌ 一个带单位的都没有")
    rep.say("")
    rep.say("  口径:p_v 换算成 bar 后算 NPSHₐ;单相液相时 PL 与 PBUB 应相等,")
    rep.say("  若两者都有值且不等,把数值贴回来再定用哪个。")

    if a.save:
        _save(rep, a.out)
    return 0


def _save(rep: Report, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    txt = os.path.join(out_dir, "probe_pv.txt")
    js = os.path.join(out_dir, "probe_pv.json")
    with open(txt, "w", encoding="utf-8") as f:
        f.write("\n".join(rep.lines) + "\n")
    with open(js, "w", encoding="utf-8") as f:
        json.dump(rep.data, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[报告] {txt}")
    print(f"[报告] {js}")


if __name__ == "__main__":
    raise SystemExit(main())
