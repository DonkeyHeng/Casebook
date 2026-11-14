"""probe_blocks.py -- 塔的冷凝器/再沸器负荷、换热器面积节点叫什么(只读)

要回答的问题
------------
设备页按分类出列,有两类值现在读不到:

  1. 塔(RadFrac)的**冷凝器 / 再沸器负荷** —— 塔没有单一 `QCALC`,要看 `COND_*` / `REB_*`
     这类节点是否存在、叫什么;
  2. 换热(HeatX)的**面积** —— `HX_AREAC` 在这些模型上读空,可能是 `AREA` / `HX_AREA`,
     也可能是 `Input\\AREA`(用户输入值,不是计算值)。

原则
----
只读。不调用 Run / Reinit / Save。读不到就写「没有」,不猜。

用法(真实桌面会话、Aspen 已打开且模型已 Run;DSH 沙箱里 ROT 是空的)
----
    python tools\\probe_blocks.py            # 只打印
    python tools\\probe_blocks.py --save     # 另存 probe_out/probe_blocks.txt|json
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

from app.design import block_family  # noqa: E402
from tools.probe_aspen import Report, attach, find  # noqa: E402
from tools.probe_pump import children, name_of, unit_of, value_of  # noqa: E402

# 塔:负荷类候选(名字里出现这些子串的都列出来)
TOWER_KEYS = ("COND", "REB", "DUTY", "QCALC", "QNET", "HEAT")
# 换热:面积/传热系数候选
HEAT_KEYS = ("AREA", "UA", "UTOT", "U", "DUTY", "TEMP", "SPEC", "MODE")


def dump(rep: Report, prefix: str, tree, base: str, keys: tuple[str, ...],
         show_all: bool = False) -> None:
    """把 base 下名字命中 keys 的子节点列出来;show_all=True 时全部列名字。"""
    node = find(tree, base)
    if node is None:
        rep.item(prefix, "节点不存在")
        return
    rows = []
    for k in children(node):
        nm = name_of(k)
        up = nm.upper()
        if show_all or any(key in up for key in keys):
            rows.append([nm, value_of(k), unit_of(k),
                         len(children(k)) or ""])
    rep.say(f"  [{prefix}] 命中 {len(rows)} 个")
    if rows:
        rep.table(["节点", "值", "单位", "子元素"], rows)


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

    blocks = []
    for node in children(find(tree, r"\Data\Blocks")):
        btype = ""
        try:
            btype = str(node.AttributeValue(6))
        except Exception:
            pass
        blocks.append((name_of(node), btype))
    rep.item("设备数量", len(blocks))

    towers = [n for n, t in blocks if block_family.family_of(t) == "column"]
    heatx = [n for n, t in blocks if t.strip().lower() == "heatx"]
    rep.item("塔", towers)
    rep.item("HeatX", heatx)

    # ---- 塔:负荷类节点
    rep.head("塔的负荷节点(COND / REB / DUTY)")
    for t in towers:
        dump(rep, f"{t}\\Output", tree, rf"\Data\Blocks\{t}\Output", TOWER_KEYS)
    if towers:
        dump(rep, f"{towers[0]}\\Output 全部", tree, rf"\Data\Blocks\{towers[0]}\Output",
             TOWER_KEYS, show_all=True)

    # ---- 换热:面积 / UA / 规格
    rep.head("换热器的面积与传热节点")
    for h in heatx:
        dump(rep, f"{h}\\Output", tree, rf"\Data\Blocks\{h}\Output", HEAT_KEYS)
        dump(rep, f"{h}\\Input", tree, rf"\Data\Blocks\{h}\Input", HEAT_KEYS)
    if heatx:
        dump(rep, f"{heatx[0]}\\Output 全部", tree, rf"\Data\Blocks\{heatx[0]}\Output",
             HEAT_KEYS, show_all=True)
        dump(rep, f"{heatx[0]}\\Input 全部", tree, rf"\Data\Blocks\{heatx[0]}\Input",
             HEAT_KEYS, show_all=True)

    rep.head("结论提示")
    rep.say("  塔:看上面有没有 COND_DUTY / REB_DUTY 一类节点(带值)。")
    rep.say("  换热:区分 Output(计算值)与 Input(用户输入值);Input 的面积可以展示,但要标注是输入值。")

    if a.save:
        _save(rep, a.out)
    return 0


def _save(rep: Report, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    txt = os.path.join(out_dir, "probe_blocks.txt")
    js = os.path.join(out_dir, "probe_blocks.json")
    with open(txt, "w", encoding="utf-8") as f:
        f.write("\n".join(rep.lines) + "\n")
    with open(js, "w", encoding="utf-8") as f:
        json.dump(rep.data, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[报告] {txt}")
    print(f"[报告] {js}")


if __name__ == "__main__":
    raise SystemExit(main())
