"""probe_pump.py -- 泵字段只读探测:扬程 / 汽蚀余量 / 蒸气压到底能不能从 Aspen 读到

要回答的问题
------------
第二期要给泵页补 流量 / 扬程 / 汽蚀余量,能不能**直接读** Aspen 的泵块结果,
决定这一块是"读"还是"算"。所以这里只查三件事:

  1. `\\Data\\Blocks\\<泵>\\Output` 到底有哪些节点(有没有 HEAD / NPSH / NET_WORK)
  2. 泵进出口流股里有没有 密度 / 蒸气压 节点(算 NPSHₐ 要用 p_v)
  3. 泵块 `Input` 里有没有 Head / Curve 输入(有曲线才可能有 NPSHᵣ)

原则
----
只读。不调用 Run / Reinit / Save。读不到就明确写"没有",不猜字段名。

用法(必须在**真实桌面会话**、Aspen 已打开模型时跑;DSH 沙箱里 ROT 是空的)
----
    python tools\\probe_pump.py --attach          # 只打印
    python tools\\probe_pump.py --attach --save   # 另存 probe_out/probe_pump.txt|json
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

from tools.probe_aspen import Report, attach, find  # noqa: E402

# 蒸气压候选名:Aspen 流股输出里可能叫这些,逐个试,不做假设
PV_CANDIDATES = (
    "VAP_PRES",
    "VAPP",
    "PVAP",
    "PV",
    "VPRES",
    "VAPOR_PRESSURE",
    "PSAT",
    "P_VAP",
    "VAPPRESS",
    "B_PRES",
    "BUBL_PRES",
    "PBUB",
    "PRES_VAP",
)

# 泵块输出里"我们希望有"的节点:只用于给结论,不用于取值。
# 用**子串**匹配,因为 Aspen 同类字段有多种写法。实测(V12.1,2026-11-08,21 台泵):
#   扬程 = HEAD_CAL(m)、流量 = VFLOW(cum/hr)、进口压力 = IN_PRES(bar)、
#   汽蚀 = NPSH-AVAIL(有,但 21 台里 12 台是 0)、NPSH_REQ(空:模型没定义泵曲线)。
WANTED_BLOCK = {
    "扬程": ("HEAD",),
    "流量": ("VFLOW",),
    "进口压力": ("IN_PRES",),
    "汽蚀": ("NPSH",),
    "效率": ("CEFF", "EFF"),
    "功率": ("BRAKE_POWER", "ELEC_POWER"),
}


def _norm(text: str) -> str:
    return str(text).upper().replace("-", "_")


INPUT_KEYWORDS = ("HEAD", "CURVE", "EFF", "NPSH", "PRES", "FLOW", "SPEED")


# ---------------------------------------------------------------- 树操作


def children(node) -> list:
    out = []
    if node is None:
        return out
    try:
        els = node.Elements
        for i in range(els.Count):
            try:
                out.append(els.Item(i))
            except Exception:
                pass
    except Exception:
        pass
    return out


def name_of(node) -> str:
    try:
        return str(node.Name)
    except Exception:
        return "?"


def value_of(node):
    try:
        return node.Value
    except Exception:
        return None


def unit_of(node) -> str:
    try:
        u = node.UnitString
        return str(u() if callable(u) else u)
    except Exception:
        return ""


def dump_children(rep: Report, node, limit: int = 200) -> list[list]:
    """一层子节点:名称 / 值 / 单位 / 子元素个数。"""
    rows = []
    for k in children(node)[:limit]:
        sub = children(k)
        kv = value_of(k)
        rows.append(
            [name_of(k), "" if kv is None else kv, unit_of(k), str(len(sub)) if sub else ""]
        )
    return rows


# ---------------------------------------------------------------- 主流程


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--attach", action="store_true", help="附着到已打开的 Aspen(必须)")
    ap.add_argument("--save", action="store_true", help="把报告写到 probe_out/")
    ap.add_argument("--out", default="probe_out", help="报告输出目录")
    a = ap.parse_args()

    if not a.attach:
        print("这个脚本要附着到已打开的 Aspen:")
        print("    python tools\\probe_pump.py --attach")
        return 2

    rep = Report()
    moniker, doc, tree = attach(rep, ["Apwn.Document"])
    if tree is None:
        rep.say("\n[结论] 没附着上,后面的探测跳过。检查窗口标题是否是 `<模型>.apw - Aspen Plus`。")
        return 3
    rep.item("附着到", moniker)

    # ---- 3) 泵块清单
    rep.head("3. 泵块清单")
    pumps: list[tuple[str, str]] = []
    for node in children(find(tree, r"\Data\Blocks")):
        btype = ""
        try:
            btype = str(node.AttributeValue(6))
        except Exception:
            pass
        if "pump" in btype.lower():
            pumps.append((name_of(node), btype))
    rep.item("泵块数量", len(pumps))
    for pname, ptype in pumps:
        rep.item(pname, ptype)
    if not pumps:
        rep.say("\n  这个模型里没有 Pump 块 —— 换一个带泵的模型再跑。")
        if a.save:
            _save(rep, a.out)
        return 0

    # ---- 4) 泵块 Output 全部子节点(核心)
    rep.head("4. 泵块 Output 全部子节点(能不能直接读扬程/汽蚀,就看这里)")
    block_out_names: dict[str, set[str]] = {}
    block_out_rows: dict[str, list[list]] = {}
    for pname, _ptype in pumps:
        node = find(tree, rf"\Data\Blocks\{pname}\Output")
        rows = dump_children(rep, node)
        block_out_names[pname] = {r[0] for r in rows}
        block_out_rows[pname] = rows
        rep.say(f"\n  [{pname}] 子节点 {len(rows)} 个")
        if rows:
            rep.table(["节点", "值", "单位", "子元素"], rows)
        else:
            rep.say("  (读不到 Output 子节点)")

    # ---- 5) 泵块 Input 里与扬程/曲线/效率有关的字段
    rep.head("5. 泵块 Input 与扬程/曲线/效率有关的字段")
    for pname, _ptype in pumps:
        hits = []
        for k in children(find(tree, rf"\Data\Blocks\{pname}\Input")):
            kn = name_of(k)
            if any(w in kn.upper() for w in INPUT_KEYWORDS):
                hits.append([kn, "" if value_of(k) is None else value_of(k), unit_of(k)])
        rep.say(f"\n  [{pname}] 命中 {len(hits)} 个")
        if hits:
            rep.table(["节点", "值", "单位"], hits)

    # ---- 6) 流股与泵的对应关系(用 SOURCE / DESTINATION,与 reader 一致)
    rep.head("6. 泵的进出口流股")
    streams: list[tuple[str, str, str]] = []
    for sn in children(find(tree, r"\Data\Streams")):
        sname = name_of(sn)
        src = value_of(find(tree, rf"\Data\Streams\{sname}\Output\SOURCE")) or ""
        dst = value_of(find(tree, rf"\Data\Streams\{sname}\Output\DESTINATION")) or ""
        streams.append((sname, str(src).strip(), str(dst).strip()))

    pump_streams: dict[str, dict[str, list[str]]] = {}
    for pname, _ptype in pumps:
        ins = [s for s, _src, dst in streams if dst.lower() == pname.lower()]
        outs = [s for s, src, _dst in streams if src.lower() == pname.lower()]
        pump_streams[pname] = {"in": ins, "out": outs}
        rep.item(pname, f"进 {ins or '(无)'}  出 {outs or '(无)'}")

    # ---- 7) 蒸气压 + 密度候选(算 NPSHₐ 的输入)
    rep.head("7. 泵进口流股的蒸气压 / 密度")
    pv_found: dict[str, list[tuple[str, object, str]]] = {}
    for pname, io in pump_streams.items():
        for sname in io["in"]:
            base = rf"\Data\Streams\{sname}\Output"
            rep.say(f"\n  [{pname}] 进口流股 {sname}")
            hits = []
            for cand in PV_CANDIDATES:
                v = value_of(find(tree, rf"{base}\{cand}"))
                if v is not None:
                    hits.append((cand, v, unit_of(find(tree, rf"{base}\{cand}"))))
            if hits:
                for c, v, u in hits:
                    rep.item(f"蒸气压 {c}", f"{v} {u}")
            else:
                rep.say("    蒸气压候选:全部 None")
            pv_found[pname] = hits

            # 密度:顺手把单位字符串记下来(0.0058 这种值不能靠猜单位)
            for dens in ("RHO_LIQ", "RHO_GAS", "RHOMX"):
                node = find(tree, rf"{base}\{dens}")
                if node is not None:
                    rep.item(f"密度 {dens}", f"{value_of(node)} {unit_of(node)}")

            # 兜底:流股 Output 里任何名字含 VAP / PRES / RHO 的节点都列出来
            names = [name_of(k) for k in children(find(tree, base))]
            related = [n for n in names if any(w in n.upper() for w in ("VAP", "PRES", "RHO"))]
            rep.item("相关节点候选", related or "(无)")

    # ---- 8) 结论
    rep.head("8. 结论:哪些能直接读")
    all_out = set().union(*block_out_names.values()) if block_out_names else set()
    first = pumps[0][0]
    sample_rows = {r[0]: (r[1], r[2]) for r in block_out_rows.get(first, [])}
    for label, cands in WANTED_BLOCK.items():
        got = sorted(n for n in all_out if any(_norm(c) in _norm(n) for c in cands))
        if not got:
            rep.item(f"{label} 可直接读", "❌ 没有(要走计算)")
            continue
        # 例值优先取"有值"的那个节点,别被空的 COEF/FAC 带偏
        pick = next((n for n in got if sample_rows.get(n, ("", ""))[0] not in ("", None)), got[0])
        value, unit = sample_rows.get(pick, ("", ""))
        rep.item(f"{label} 可直接读", f"{got}  例:{first} {pick} = {value} {unit}")
    got_pv = sorted({c for hits in pv_found.values() for c, _v, _u in hits})
    rep.item("蒸气压 可直接读", got_pv if got_pv else "❌ 没有(算 NPSHₐ 前要先定 p_v 从哪来)")

    rep.say("")
    rep.say("  说明:泵块 Output 的名字是 Aspen 内部名,不保证跨版本一致;")
    rep.say("  把这一节原样贴回对话,就能定 B 切片走「读」还是「算」。")

    if a.save:
        _save(rep, a.out)
    return 0


def _save(rep: Report, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    txt = os.path.join(out_dir, "probe_pump.txt")
    js = os.path.join(out_dir, "probe_pump.json")
    with open(txt, "w", encoding="utf-8") as f:
        f.write("\n".join(rep.lines) + "\n")
    with open(js, "w", encoding="utf-8") as f:
        json.dump(rep.data, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[报告] {txt}")
    print(f"[报告] {js}")


if __name__ == "__main__":
    raise SystemExit(main())
