"""tools/dump_fixture.py -- 把当前打开模型的节点树导出成脱敏 JSON 夹具。

为什么要有这一步
----------------
`app/aspen/reader.py` 的路径都是按实测写死的。版本换结构(V11 的 `PBUB` 没有
`TOTAL` 层)、或某个字段从目录变成叶子,读出来只是**静默变空** —— 泵页 p_v
整列空掉、粘度全空,都是这样发生的。单条路径的桩测不出跨字段的连锁影响,
`tools/selftest.py` 用整棵树回放才守得住,而回放要有夹具。

只采"读取面"
------------
流股 `Output`、设备 `Output` + `Input`、收敛状态、撕裂流、组分字典。
**不采整棵 `\\Data`**:那有上万节点、几 MB,既过不了 `check_public_safe` 的 2 MB
扫描阈值(超了直接跳过,等于脱敏门禁失效),也把无关数据带进仓库。

脱敏口径(与 `samples/` 一致)
----------------------------
**只能用自己(或有明确授权)的模型。** 夹具保留的是真实读数 —— 温度、压力、流量、
焓、热负荷、组分清单;改名只去掉了位号,数字本身仍是工艺流程信息,能反推产能与
操作窗口。他人的模型 / 比赛模型一律不采(见 `AGENTS.md` §4)。

* 流股名 → `S01…`、设备名 → `B01…`,引用它们的字段值(SOURCE / DESTINATION /
  COLDIN / IN_STRMID / FEED_STAGE …)一并改写;原名**不写进夹具**,没有反查表。
* 指向**没采的**流股/设备的位号 → `R01…`。
* `RUNID` → 0,`USER` → `user`,`MACHINE` / `NODENAME` → 空。
* 模型路径 → `model/<case>.apw`。
* 组分名保留(化学组分不涉及个人信息);要连组分一起换,采集后自行替换。

用法(真实桌面会话,Aspen 已打开且模型已 Run;DSH 沙箱里 ROT 为空)
----
    python tools\\dump_fixture.py --case v15-typical
    python tools\\dump_fixture.py --case v15-typical --streams 8 --blocks 6
    python tools\\dump_fixture.py --case v11-pump --streams 2 --blocks 1 --aspen-version V11

采集完跑两道门禁:

    python -m tools.selftest
    python -m tools.check_public_safe --all

夹具是**观测记录,不是真值**:它能证明"结构没漂、换算没断",证明不了"这个读数对"。
读数对不对仍以 `probe_*.py` 的真机实测为准。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.probe_aspen import Report, attach, find  # noqa: E402
from tools.probe_pump import children, name_of, unit_of, value_of  # noqa: E402

SCHEMA = 1

# 采集根:只取 reader 真正会走到的子树
BLOCK_ROOTS = ("Output", "Input")
RESULT_ROOTS = (
    r"\Data\Results Summary\Run-Status\Output",
    r"\Data\Results Summary\Conv-Sum\TEAR-SUMMARY\Output",
)
COMPONENT_ROOTS = (
    r"\Data\Components\Attr-Comps",
    r"\Data\Components\Comp-Lists",
    r"\Data\Components\Pseudocomponents",
    r"\Data\Components\Input",
)

# 这些字段是"运行环境",不是工艺数据:采到就改写,别把机器名与用户名带出去
RUN_STATUS_SCRUB = {
    "USER": "user",
    "MACHINE": "",
    "NODENAME": "",
    "RUNID": 0,
}

# ---- 读取面白名单 ----------------------------------------------------------
# 实测(2026-11-18,121 流股全流程模型):单条流股 `Output` 有 120 个子节点、469 个
# 后代,其中 `STR_MAIN`(119)、`X1`/`X2`、各种 `*_CYCLE` / `*_PRICE` / `COST`
# 都是 Aspen 给界面用的,`reader.py` 一个都不读。不裁剪的话,8 条流股就把 4000
# 节点上限吃满,设备反而采不进来。
# ⚠️ `reader.py` 新增要读的字段时,这个白名单要同步扩,再重采夹具。
STREAM_OUTPUT_KEEP = {
    "SOURCE",
    "DESTINATION",
    "X",
    "Y",
    "MASSFLOW3",
    "PL",
    "STRM_UPP",
    "PBUB",
    "VISC",
    "VISCOSITY",
    "MUMX",
    "MU_LIQ",
    "MU_VAP",
}
STREAM_OUTPUT_PREFIX = ("RES_", "HMX")

BLOCK_OUTPUT_KEEP = {
    # 热负荷 / 功率 / 面积
    "QCALC",
    "QNET",
    "HX_DUTY",
    "BRAKE_POWER",
    "ELEC_POWER",
    "HX_AREAC",
    "HX_AREAC2",
    "HX_AREAP",
    # 塔
    "COND_DUTY",
    "REB_DUTY",
    "B_TEMP",
    "B_PRES",
    "LIQ_FLOW",
    "VAP_FLOW",
    "DUTY",
    # 泵
    "HEAD_CAL",
    "VFLOW",
    "IN_PRES",
    "CEFF",
    "RHO_LIQ",
    "MW_LIQ",
    # 状态 / 连接
    "BLKSTAT",
    "BLKMSG",
    "COLDIN",
    "COLDOUT",
    "HOTIN",
    "HOTOUT",
    "IN_STRMID",
    "OUT_STRMID",
}

BLOCK_INPUT_KEEP = {"NSTAGE", "FEED_STAGE", "FCOLD", "FHOT", "PRES"}

# 这些节点的"值"是流股名或设备名。采到的流股/设备已在 mapping 里,但真实模型里
# 还有指向**没采的**设备/流股的引用(F0101 / T0102 …)—— 那些位号同样是模型信息,
# 一律改成引用编号 R01…,既不留真名,又保住"连接可以指向夹具外"这条语义。
REF_LEAVES = {
    "SOURCE",
    "DESTINATION",
    "COLDIN",
    "COLDOUT",
    "HOTIN",
    "HOTOUT",
    "IN_STRMID",
    "OUT_STRMID",
    "STREAMID",
}


def external_refs(nodes: dict, mapping: dict[str, str]) -> dict[str, str]:
    """给"指向夹具外的引用"编一组新名字,返回 {原名: R01}。

    注意数组元素:值挂在 `...\\STREAMID\\2` 这种路径上,叶子名是下标,
    所以要看**整条路径**里有没有 REF_LEAVES 里的段,不能只看最后一段。
    """
    extra: dict[str, str] = {}
    for path, entry in nodes.items():
        segs = {seg.upper() for seg in path.split("\\")}
        if not segs & REF_LEAVES:
            continue
        value = entry.get("v")
        if not isinstance(value, str):
            continue
        name = value.strip()
        if name and name not in mapping and name not in extra:
            extra[name] = f"R{len(extra) + 1:02d}"
    return extra

_STREAM_OUTPUT = re.compile(r"^\\Data\\Streams\\[^\\]+\\Output$")
_BLOCK_OUTPUT = re.compile(r"^\\Data\\Blocks\\[^\\]+\\Output$")
_BLOCK_INPUT = re.compile(r"^\\Data\\Blocks\\[^\\]+\\Input$")


def _keep_child(parent: str, name: str) -> bool:
    """只对 `流股\\Output`、`设备\\Output|Input` 的第一层过滤;更深的数组照收。"""
    up = name.upper()
    if _STREAM_OUTPUT.match(parent):
        return up in STREAM_OUTPUT_KEEP or up.startswith(STREAM_OUTPUT_PREFIX)
    if _BLOCK_OUTPUT.match(parent):
        return up in BLOCK_OUTPUT_KEEP
    if _BLOCK_INPUT.match(parent):
        return up in BLOCK_INPUT_KEEP
    return True


def _jsonable(value):
    """COM 的 VARIANT 转成 JSON 能存的东西;转不了就按字符串存。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def _entry(node, kids: list[str]) -> dict:
    entry: dict = {}
    value = _jsonable(value_of(node))
    unit = unit_of(node)
    if value is not None:
        entry["v"] = value
    if unit:
        entry["u"] = unit
    if kids:
        entry["kids"] = kids
    return entry


def _attrs(node) -> dict:
    """可读类型(属性 6)。reader 靠它区分 RadFrac / HeatX / Pump。"""
    try:
        if node is not None and node.HasAttribute(6):
            return {"6": str(node.AttributeValue(6))}
    except Exception:
        pass
    return {}


def walk(out: dict, node, path: str, *, depth: int, max_depth: int, cap: int) -> None:
    """收集 path 及其子树(深度 / 节点数都设上限)。

    只枚举一次子节点:每次 `Elements` 都是一轮 COM 调用,同一个节点数两次
    在 77 台设备、上百流股的模型上会成倍放大读取时间。
    """
    if node is None or len(out) >= cap:
        return
    pairs = []
    for child in children(node):
        name = name_of(child)
        if _keep_child(path, name):
            pairs.append((name, child))
    out[path] = _entry(node, [name for name, _ in pairs])
    if depth >= max_depth:
        return
    for name, child in pairs:
        walk(out, child, f"{path}\\{name}", depth=depth + 1, max_depth=max_depth, cap=cap)


def _pick(names: list[str], limit: int | None, explicit: list[str] | None) -> list[str]:
    if explicit:
        wanted = {n.strip().lower() for n in explicit}
        return [n for n in names if n.lower() in wanted]
    return names[:limit] if limit else names


def _name_maps(streams: list[str], blocks: list[str]) -> dict[str, str]:
    mapping = {}
    for i, name in enumerate(streams, 1):
        mapping[name] = f"S{i:02d}"
    for i, name in enumerate(blocks, 1):
        mapping[name] = f"B{i:02d}"
    return mapping


def _rename_path(path: str, mapping: dict[str, str]) -> str:
    segs = path.split("\\")
    if len(segs) > 3 and segs[1] == "Data" and segs[2] in ("Streams", "Blocks"):
        segs[3] = mapping.get(segs[3], segs[3])
    return "\\".join(segs)


def _rename_entry(entry: dict, mapping: dict[str, str]) -> dict:
    out = dict(entry)
    if "kids" in out:
        out["kids"] = [mapping.get(str(k), str(k)) for k in out["kids"]]
    if isinstance(out.get("v"), str) and out["v"] in mapping:
        out["v"] = mapping[out["v"]]
    return out


def anonymize(nodes: dict, mapping: dict[str, str]) -> dict:
    """路径、子节点名、以及引用设备/流股名的字段值一起改写。

    还要把 `\\Data\\Streams` / `\\Data\\Blocks` 的 kids 收敛到"确实采到的那些":
    真实模型有 121 条流股,只采 8 条时根目录若照抄 121 个名字,reader 会把没采的
    当成空记录读进来(112 条全空流股 + 74 台类型为 None 的设备)。
    """
    out = {}
    for path, entry in nodes.items():
        out[_rename_path(path, mapping)] = _rename_entry(entry, mapping)
    for root in (r"\Data\Streams", r"\Data\Blocks"):
        entry = out.get(root)
        if entry and "kids" in entry:
            entry["kids"] = [k for k in entry["kids"] if f"{root}\\{k}" in out]
    return out


def scrub(nodes: dict) -> None:
    """运行环境字段就地脱敏(用户名 / 机器名 / 运行号)。"""
    for path, entry in nodes.items():
        tail = path.rsplit("\\", 1)[-1].upper()
        if tail in RUN_STATUS_SCRUB:
            entry["v"] = RUN_STATUS_SCRUB[tail]


def collect(tree, *, streams: list[str], blocks: list[str], max_depth: int, cap: int) -> dict:
    nodes: dict = {}

    def step(label: str) -> None:
        print(f"  · {label} … 已采 {len(nodes)} 节点", file=sys.stderr, flush=True)

    # 两个根目录只取"有哪些子节点":reader 用它们列流股表 / 设备表,
    # 展开到深度 2 会把 77 台设备的全部子树扫一遍(几十万次 COM 调用)。
    for root in (r"\Data\Streams", r"\Data\Blocks"):
        step(root)
        walk(nodes, find(tree, root), root, depth=0, max_depth=0, cap=cap)

    for root in RESULT_ROOTS:
        step(root)
        walk(nodes, find(tree, root), root, depth=0, max_depth=2, cap=cap)

    for name in streams:
        root = rf"\Data\Streams\{name}\Output"
        step(f"流股 {name}")
        walk(nodes, find(tree, root), root, depth=0, max_depth=max_depth, cap=cap)

    for name in blocks:
        for sub in BLOCK_ROOTS:
            root = rf"\Data\Blocks\{name}\{sub}"
            step(f"设备 {name}\\{sub}")
            walk(nodes, find(tree, root), root, depth=0, max_depth=max_depth, cap=cap)

    for root in COMPONENT_ROOTS:
        step(root)
        walk(nodes, find(tree, root), root, depth=0, max_depth=2, cap=cap)

    # 流股 / 设备节点上的「可读类型」属性(6)必须显式记下来:reader 靠它分族,
    # 而属性不在 Elements 里,光靠子节点名采不到。
    for name, root in [
        (n, rf"\Data\Streams\{n}") for n in streams
    ] + [(n, rf"\Data\Blocks\{n}") for n in blocks]:
        node = find(tree, root)
        if node is None:
            continue
        attrs = _attrs(node)
        if attrs:
            nodes.setdefault(root, {})["attrs"] = attrs
    return nodes


def main() -> int:
    ap = argparse.ArgumentParser(description="把当前 Aspen 模型的节点树导出成脱敏夹具")
    ap.add_argument("--case", required=True, help="夹具目录名(kebab-case,如 v15-typical)")
    ap.add_argument("--streams", type=int, default=8, help="最多采几条流股(默认 8)")
    ap.add_argument("--blocks", type=int, default=6, help="最多采几台设备(默认 6)")
    ap.add_argument("--only-streams", nargs="*", help="只采这些流股(按名字)")
    ap.add_argument("--only-blocks", nargs="*", help="只采这些设备(按名字)")
    ap.add_argument("--depth", type=int, default=6, help="子树最大深度(默认 6)")
    ap.add_argument("--cap", type=int, default=12000, help="节点数上限(默认 12000)")
    ap.add_argument("--aspen-version", default="unknown", help="版本标记,如 V15 / V11")
    ap.add_argument("--unit-set", default="", help="单位集标记,如 METCBAR / ENG")
    ap.add_argument("--out", default=str(ROOT / "tools" / "fixtures"), help="输出根目录")
    args = ap.parse_args()

    rep = Report()
    moniker, doc, tree = attach(rep, ["Apwn.Document"])
    if tree is None:
        rep.say("\n[结论] 没附着上。检查: Aspen 是否开着、是否在真实桌面会话跑。")
        return 3
    rep.item("附着到", moniker)
    print("  ⚠ 夹具含真实读数:只能用自己(或有明确授权)的模型,他人模型不要采", flush=True)

    stream_names = _pick(
        [name_of(n) for n in children(find(tree, r"\Data\Streams"))],
        args.streams,
        args.only_streams,
    )
    block_names = _pick(
        [name_of(n) for n in children(find(tree, r"\Data\Blocks"))],
        args.blocks,
        args.only_blocks,
    )
    if not stream_names or not block_names:
        rep.say("\n[结论] 没有流股或没有设备 —— 换一个已 Run 过的模型。")
        return 4

    nodes = collect(
        tree, streams=stream_names, blocks=block_names, max_depth=args.depth, cap=args.cap
    )
    truncated = len(nodes) >= args.cap
    mapping = _name_maps(stream_names, block_names)
    mapping.update(external_refs(nodes, mapping))
    nodes = anonymize(nodes, mapping)
    scrub(nodes)

    server = ""
    try:
        server = str(doc.Name)
    except Exception:
        pass

    fixture = {
        "case": args.case,
        "meta": {
            "schema": SCHEMA,
            "source": "collector",
            "aspen_version": args.aspen_version,
            "unit_set": args.unit_set,
            "collected_at": time.strftime("%Y-%m-%d %H:%M"),
            "model_path": f"model/{args.case}.apw",
            "server": server,
            "unread_ok": [],
            "truncated": truncated,
            "note": "真机采集:结构可信;数值只用于核对换算与判定口径,不作为设计依据。",
        },
        "nodes": dict(sorted(nodes.items(), key=lambda kv: (kv[0].count("\\"), kv[0]))),
    }

    out_dir = Path(args.out) / args.case
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "tree.json"
    out_path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    size_kb = out_path.stat().st_size / 1024
    print()
    print(f"[夹具] {out_path}")
    print(f"  流股 {len(stream_names)} 条 / 设备 {len(block_names)} 台 / 节点 {len(nodes)} 个")
    print(f"  体积 {size_kb:.1f} KB(超过 2000 KB 会被 check_public_safe 跳过)")
    print("  下一步:python -m tools.selftest && python -m tools.check_public_safe --all")
    if truncated:
        print(f"  ⚠ 撞到节点上限 {args.cap}:后采的设备/子树被截断,这份夹具不完整")
        print("     提高 --cap,或减少 --streams / --blocks 后重采")
        return 6
    if size_kb > 2000:
        print("  ⚠ 超 2 MB:减少 --streams / --blocks 或把夹具拆成按设备族的两份")
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
