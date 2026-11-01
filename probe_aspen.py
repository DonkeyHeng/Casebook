"""probe_aspen.py -- Aspen Plus COM 只读探测 / 自检脚本

目的(按优先级):
  1. 确认本机 Aspen 的 COM 接口注册完整(ProgID / 服务器路径)
  2. 附着(attach)到**用户已经打开**的 Aspen 实例,不启动、不占用额外许可证
  3. 把真实数据读出来:流股表(T/P/流量/汽化率/组成)、设备表(类型/热负荷/塔板数)、
     收敛状态、塔板级剖面
  4. 报告里同时记录"真实字段路径",供后续适配层直接抄,不再靠猜

实测结论(V15 / 41.0,2026-10-31,见 docs/aspen-field-map.md):
  * 手工打开的 Aspen 只把文档注册为 **文件 moniker**(`...\\model.apw`),
    不注册 `Apwn.Document`,所以 GetActiveObject 会报 MK_E_UNAVAILABLE;
    必须用 `GetObject(apw路径)` 附着。
  * 流股结果字段是 `RES_TEMP / RES_PRES / RES_MASSFLOW / RES_MOLEFLOW /
    RES_VFRAC / RES_VOLFLOW`,不是 TEMP/PRES/MASSFLW。
  * 单位可直接读:`node.UnitString`(属性或方法,两种都要兼容)。
  * 模块可读类型:`node.AttributeValue(6)` -> 'Heater' / 'RadFrac' / 'Pump' ...
  * 级剖面:`\\Data\\Blocks\\<塔>\\Output\\B_TEMP\\<级>` 等。
  * 组成:`\\Data\\Streams\\<流>\\Output\\X\\<组分>`(液相摩尔分数)、`Y\\<组分>`(气相)。

设计原则:
  * 默认只读。绝不调用 Run2 / Reinit,除非显式加 --run
  * 不猜路径:读不到就明确记 None,并把真实存在的子节点名打出来
  * 失败也出报告,并说明失败在哪一步

用法:
  python probe_aspen.py                 # 只扫注册表,不碰 Aspen
  python probe_aspen.py --attach        # 附加到已打开的 Aspen 并出完整报告
  python probe_aspen.py --attach --deep # 额外转储字段全名(排错用)
  python probe_aspen.py --launch model.apw   # 自己开 Aspen(慢,占许可证)
  python probe_aspen.py --attach --run  # [谨慎] 附加后跑一次仿真
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

# ---------------------------------------------------------------- 关键字段表
# 这些名字全部是实测得到的,不是猜的(2026-10-31,Aspen Plus V15 / 41.0)

STREAM_RESULTS = [
    ("RES_TEMP", "温度"),
    ("RES_PRES", "压力"),
    ("RES_MASSFLOW", "质量流量"),
    ("RES_MOLEFLOW", "摩尔流量"),
    ("RES_VFRAC", "汽化率"),
    ("RES_VOLFLOW", "体积流量"),
]

BLOCK_CANDIDATES = [
    ("QCALC", "热负荷"),
    ("QNET", "净热负荷"),
    ("HX_DUTY", "换热负荷"),
    ("BRAKE_POWER", "轴功率"),
    ("ELEC_POWER", "电功率"),
    ("HX_AREAC", "换热面积"),
    ("CEFF", "泵效率"),
]


# ---------------------------------------------------------------- 报告收集

class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.data: dict = {"sections": [], "generated": time.strftime("%Y-%m-%d %H:%M:%S")}

    def head(self, title: str) -> None:
        self.lines.append("")
        self.lines.append("=" * 78)
        self.lines.append(title)
        self.lines.append("=" * 78)
        self.data["sections"].append({"title": title, "items": []})

    def say(self, msg: str = "") -> None:
        print(msg)
        self.lines.append(msg)

    def item(self, key: str, value) -> None:
        text = f"  {key}: {value}"
        print(text)
        self.lines.append(text)
        if self.data["sections"]:
            self.data["sections"][-1]["items"].append({"key": key, "value": _jsonable(value)})

    def table(self, headers: list[str], rows: list[list]) -> None:
        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(str(cell)))
        line = "  " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
        self.say(line)
        self.say("  " + "  ".join("-" * w for w in widths))
        for row in rows:
            self.say("  " + "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))
        if self.data["sections"]:
            self.data["sections"][-1]["items"].append(
                {"key": "table", "value": {"headers": headers, "rows": rows}}
            )

    def save(self, out_dir: str) -> None:
        os.makedirs(out_dir, exist_ok=True)
        txt = os.path.join(out_dir, "probe_report.txt")
        js = os.path.join(out_dir, "probe_report.json")
        with open(txt, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines) + "\n")
        with open(js, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n[报告] {txt}")
        print(f"[报告] {js}")


def _jsonable(v):
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


# ---------------------------------------------------------------- 注册表扫描

ASPEN_HINTS = ("apwn", "aspenplus", "aspen plus", "aspenone", "aspenplusdocument")


def scan_registry(rep: Report) -> list[tuple[str, str]]:
    rep.head("1. 注册表扫描:找出 Aspen 的 COM ProgID")
    found: list[tuple[str, str]] = []
    try:
        import winreg
    except Exception as e:  # pragma: no cover
        rep.say(f"  [失败] 无法导入 winreg: {e}")
        return found

    try:
        hkcr = winreg.HKEY_CLASSES_ROOT
        with winreg.OpenKey(hkcr, "") as root:
            i = 0
            total = 0
            while True:
                try:
                    name = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                total += 1
                low = name.lower()
                if "." not in name or not any(h in low for h in ASPEN_HINTS):
                    continue
                clsid = ""
                try:
                    with winreg.OpenKey(hkcr, name + r"\CLSID") as k:
                        clsid = str(winreg.QueryValueEx(k, "")[0])
                except OSError:
                    continue
                found.append((name, clsid))
            rep.say(f"  扫描 HKCR 共 {total} 个键,命中 {len(found)} 个 Aspen ProgID")
    except Exception as e:
        rep.say(f"  [失败] 扫描异常: {e}")

    for progid, clsid in sorted(found):
        if progid.count(".") <= 1 and "AspenPlus" not in progid:
            rep.item(progid, clsid)

    # 服务器可执行文件路径
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Apwn.Document.41.0\CLSID") as k:
            clsid = str(winreg.QueryValueEx(k, "")[0])
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32") as k:
            srv = str(winreg.QueryValueEx(k, "")[0])
        rep.item("COM 服务器", srv)
        exe = srv.split('"')[1] if srv.startswith('"') else srv.split(" -")[0]
        rep.item("可执行文件存在", os.path.isfile(exe.strip()))
    except Exception as e:
        rep.item("COM 服务器", f"(读不到: {type(e).__name__})")

    return found


# ---------------------------------------------------------------- 连接

def rot_apw_monikers() -> list[str]:
    """ROT 里所有 .apw/.bkp 文件 moniker。

    V15 手工启动的 Aspen 只注册这个,不注册 Apwn.Document。
    """
    import pythoncom

    ctx = pythoncom.CreateBindCtx(0)
    out: list[str] = []
    for moniker in pythoncom.GetRunningObjectTable():
        try:
            name = moniker.GetDisplayName(ctx, None)
        except Exception:
            continue
        if name.lower().endswith((".apw", ".bkp", ".apwz", ".bkpz")):
            out.append(name)
    return out


def attach(rep: Report, progids: list[str]):
    """附加策略:文件 moniker 优先,ProgID 兜底。"""
    rep.head("2. 附加到已在运行的 Aspen 实例")
    import win32com.client

    try:
        monikers = rot_apw_monikers()
    except Exception as e:
        rep.item("ROT 读取失败", f"{type(e).__name__}: {e}")
        monikers = []

    rep.item("ROT 里的模型文件", monikers if monikers else "(空)")

    for name in monikers:
        try:
            doc = win32com.client.GetObject(name)
            tree = doc.Tree
        except Exception as e:
            rep.say(f"  [x] {name}: {type(e).__name__}: {e}")
            continue
        rep.say(f"  [OK] 已附着: {name}")
        return name, doc, tree

    rep.say("  文件 moniker 不可用,改试 ProgID:")
    for progid in progids:
        try:
            doc = win32com.client.GetActiveObject(progid)
            tree = doc.Tree
        except Exception as e:
            rep.say(f"    [x] {progid}: {type(e).__name__}")
            continue
        rep.say(f"    [OK] {progid}")
        return progid, doc, tree

    rep.say("  [结论] 没有可附着的实例。检查:")
    rep.say("         1) Aspen 是否开着(窗口标题应为 `xxx.apw - Aspen Plus V15`)")
    rep.say("         2) 脚本是否在真实桌面会话跑(DSH 沙箱里 ROT 是空的)")
    rep.say("         3) Aspen 是否以管理员身份启动(权限不同则无法附加)")
    return None, None, None


# ---------------------------------------------------------------- 树操作

def find(tree, path: str):
    try:
        return tree.FindNode(path)
    except Exception:
        return None


def node_name(node) -> str:
    try:
        return str(node.Name)
    except Exception:
        return "?"


def node_value(node):
    if node is None:
        return None
    try:
        return node.Value
    except Exception:
        return None


def node_unit(node) -> str:
    """UnitString 有时是属性、有时是方法,统一处理。"""
    if node is None:
        return ""
    try:
        u = node.UnitString
        return str(u() if callable(u) else u)
    except Exception:
        return ""


def child_nodes(node) -> list:
    if node is None:
        return []
    out = []
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


def child_names(node) -> list[str]:
    return [node_name(c) for c in child_nodes(node)]


def read(tree, path: str):
    """返回 (值, 单位);读不到返回 (None, '')。"""
    nd = find(tree, path)
    return node_value(nd), node_unit(nd)


# ---------------------------------------------------------------- 主体探测

def probe_convergence(tree, rep: Report):
    rep.head("3. 收敛 / 运行状态")
    base = r"\Data\Results Summary\Run-Status\Output"
    fields = [
        ("RSTAT", "运行状态"),
        ("CVSTAT", "收敛状态"),
        ("CSSTAT", "计算状态"),
        ("UOSSTAT", "单元操作状态"),
        ("PPSTAT", "物性状态"),
        ("SENSSTAT", "灵敏度状态"),
        ("ITSTAT", "迭代次数"),
        ("PER_ERROR", "误差"),
        ("BLKSTAT", "模块状态"),
    ]
    vals = {}
    for key, label in fields:
        v, _ = read(tree, base + "\\" + key)
        vals[key] = v
        rep.item(f"{key}({label})", v)
    for key in ("ITDATE", "RUNID", "USER", "VERNO"):
        v, _ = read(tree, base + "\\" + key)
        rep.item(key, v)
    rep.item("Engine.IsRunning", _engine_is_running(tree))

    # 判定提示(保守规则,非收敛样本还没校准)
    bad = [k for k in ("RSTAT", "CVSTAT", "UOSSTAT") if vals.get(k) not in (0, None)]
    rep.item("判定", "疑似收敛" if not bad else f"疑似异常/未收敛({', '.join(bad)} 非 0)")

    # 撕裂流
    tear = r"\Data\Results Summary\Conv-Sum\TEAR-SUMMARY\Output"
    ids = child_names(find(tree, tear + r"\STREAMID"))
    errs = child_names(find(tree, tear + r"\ERR"))
    if ids:
        rows = []
        for i, sid in enumerate(ids):
            s, _ = read(tree, rf"{tear}\STREAMID\{sid}")
            e, _ = read(tree, rf"{tear}\ERR\{sid}")
            var, _ = read(tree, rf"{tear}\VARID\{sid}")
            rows.append([sid, s, var, e])
        rep.say("  撕裂流误差:")
        rep.table(["序号", "撕裂流", "变量", "误差"], rows)


def _engine_is_running(tree) -> object:
    try:
        return tree.Application.Engine.IsRunning
    except Exception as e:
        return f"(读不到: {type(e).__name__})"


def probe_streams(tree, rep: Report):
    rep.head("4. 流股表(全部)")
    root = find(tree, r"\Data\Streams")
    ids = child_names(root)
    rep.item("流股数", len(ids))
    if not ids:
        rep.say("  [失败] 没找到 \\Data\\Streams")
        return

    headers = ["流股"] + [lbl for _, lbl in STREAM_RESULTS] + ["单位"]
    rows = []
    detail = []
    for sid in ids:
        row = [sid]
        units = []
        rec = {"name": sid}
        for key, _lbl in STREAM_RESULTS:
            v, u = read(tree, rf"\Data\Streams\{sid}\Output\{key}")
            row.append(_fmt(v))
            units.append(u)
            rec[key] = v
            rec[key + "_unit"] = u
        row.append(" / ".join(dict.fromkeys(u for u in units if u)))
        rows.append(row)
        detail.append(rec)
    rep.table(headers, rows)
    rep.data["streams"] = detail

    # 组成(第一个流股)
    sid = ids[0]
    rep.say(f"\n  组成(流股 {sid},液相 X / 气相 Y 摩尔分数):")
    comps = []
    for phase in ("X", "Y"):
        comps.append(child_names(find(tree, rf"\Data\Streams\{sid}\Output\{phase}")))
    if comps[0]:
        rows = []
        for i, comp in enumerate(comps[0]):
            x, _ = read(tree, rf"\Data\Streams\{sid}\Output\X\{comp}")
            y, _ = read(tree, rf"\Data\Streams\{sid}\Output\Y\{comp}")
            rows.append([comp, _fmt(x), _fmt(y)])
        rep.table(["组分", "X(液相)", "Y(气相)"], rows)
    else:
        rep.say("  (没有 X/Y 组成节点)")


def probe_blocks(tree, rep: Report):
    rep.head("5. 设备表(全部)")
    root = find(tree, r"\Data\Blocks")
    nodes = child_nodes(root)
    if not nodes:
        rep.say("  [失败] 没找到 \\Data\\Blocks")
        return

    rep.item("模块数", len(nodes))
    rows = []
    detail = []
    for nd in nodes:
        bid = node_name(nd)
        try:
            btype = nd.AttributeValue(6)
        except Exception:
            btype = None
        rec = {"name": bid, "type": btype}
        extra = []

        # 标量型结果(数组节点跳过,避免把级数数组的 Value(恒为 1)当成结果)
        for key, _lbl in BLOCK_CANDIDATES:
            node = find(tree, rf"\Data\Blocks\{bid}\Output\{key}")
            if node is None or child_nodes(node):
                continue
            v, u = node_value(node), node_unit(node)
            if v is None:
                continue
            extra.append(f"{key}={_fmt(v)}{(' ' + u) if u else ''}")
            rec[key] = v
            rec[key + "_unit"] = u

        # 塔:理论级数 + 塔顶/塔底温度(级剖面数组的首末级)
        nstage, _ = read(tree, rf"\Data\Blocks\{bid}\Input\NSTAGE")
        if nstage:
            extra.insert(0, f"NSTAGE={_fmt(nstage)}")
            rec["NSTAGE"] = nstage
            btemp = find(tree, rf"\Data\Blocks\{bid}\Output\B_TEMP")
            stages = child_names(btemp)
            if stages:
                top, tu = read(tree, rf"\Data\Blocks\{bid}\Output\B_TEMP\{stages[0]}")
                bot, bu = read(tree, rf"\Data\Blocks\{bid}\Output\B_TEMP\{stages[-1]}")
                extra.append(f"顶温={_fmt(top)}{tu} 底温={_fmt(bot)}{bu}")
                rec["top_temp"], rec["bottom_temp"] = top, bot

        rows.append([bid, btype, " ; ".join(extra[:6])])
        detail.append(rec)
    rep.table(["设备", "类型", "关键结果"], rows)
    rep.data["blocks"] = detail


def probe_columns(tree, rep: Report):
    rep.head("6. 塔板级剖面(二期功能的可行性)")
    blocks = child_nodes(find(tree, r"\Data\Blocks"))
    found_any = False
    for nd in blocks:
        bid = node_name(nd)
        try:
            btype = str(nd.AttributeValue(6))
        except Exception:
            btype = ""
        if "RadFrac" not in btype and "Absorber" not in btype:
            continue
        nstage, _ = read(tree, rf"\Data\Blocks\{bid}\Input\NSTAGE")
        temp_node = find(tree, rf"\Data\Blocks\{bid}\Output\B_TEMP")
        stages = child_names(temp_node)
        if not stages:
            continue
        found_any = True
        rep.say(f"\n  [{bid}] 类型={btype} 理论级数={nstage} 剖面级数={len(stages)}")
        rows = []
        picked = stages[:5] + [s for s in stages[-2:] if s not in stages[:5]]
        for st in picked:
            t, tu = read(tree, rf"\Data\Blocks\{bid}\Output\B_TEMP\{st}")
            p, pu = read(tree, rf"\Data\Blocks\{bid}\Output\B_PRES\{st}")
            rows.append([st, _fmt(t), tu, _fmt(p), pu])
        rep.table(["级", "温度", "单位", "压力", "单位"], rows)
        rep.say("      (剖面变量还有:DUTY / LIQ_FLOW / VAP_FLOW / RHO_LIQ / RHO_GAS / MW_LIQ / MW_GAS / X / Y / B_K / MASS_CONC)")
    if not found_any:
        rep.say("  (没找到塔板剖面节点)")


def probe_deep(tree, rep: Report):
    rep.head("7. 深度转储(-deep):字段全名 + 值")
    streams = child_names(find(tree, r"\Data\Streams"))
    if not streams:
        return
    sid = streams[0]
    rep.say(f"  流股 {sid}\\Output 全部子节点:")
    for c in child_nodes(find(tree, rf"\Data\Streams\{sid}\Output")):
        rep.say(f"    {node_name(c):<22} = {_fmt(node_value(c))}  [{node_unit(c)}]")

    blocks = child_nodes(find(tree, r"\Data\Blocks"))
    if blocks:
        bid = node_name(blocks[0])
        rep.say(f"\n  设备 {bid}\\Input 前 40 个子节点:")
        for c in child_nodes(find(tree, rf"\Data\Blocks\{bid}\Input"))[:40]:
            rep.say(f"    {node_name(c):<22} = {_fmt(node_value(c))}  [{node_unit(c)}]")


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        if v != 0 and (abs(v) < 1e-3 or abs(v) >= 1e6):
            return f"{v:.6g}"
        return f"{v:.6g}" if abs(v) < 1000 else f"{v:.1f}"
    return str(v)


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="Aspen Plus COM 只读探测")
    ap.add_argument("--attach", action="store_true", help="附着到已打开的 Aspen")
    ap.add_argument("--launch", metavar="APW", help="自启 Aspen 并加载 apw(慢,占许可证)")
    ap.add_argument("--run", action="store_true", help="[危险] 附加后运行一次仿真")
    ap.add_argument("--deep", action="store_true", help="转储字段全名")
    ap.add_argument("--out", default=".", help="报告输出目录")
    args = ap.parse_args()

    rep = Report()
    rep.head("0. 环境")
    rep.item("Python", sys.version.replace("\n", " "))
    rep.item("平台", sys.platform)
    rep.item("时间", time.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        import win32com

        rep.item("pywin32", getattr(win32com, "__gen_path__", "ok"))
    except Exception as e:
        rep.say(f"  [致命] 没有 pywin32: {e}")
        rep.say("         先执行: python -m pip install pywin32")

    progids = [p for p, _ in scan_registry(rep)]

    doc = tree = None
    how = None
    if args.launch:
        rep.head("2b. 自启 Aspen 加载模型")
        import win32com.client

        if not os.path.isfile(args.launch):
            rep.say(f"  [失败] 文件不存在: {args.launch}")
        else:
            t0 = time.time()
            doc = win32com.client.Dispatch("Apwn.Document")
            doc.InitFromArchive2(os.path.abspath(args.launch))
            tree = doc.Tree
            how = args.launch
            rep.say(f"  加载完成,耗时 {time.time() - t0:.1f}s")
    elif args.attach:
        try:
            how, doc, tree = attach(rep, ["Apwn.Document"] + progids)
        except Exception:
            rep.say("  [失败] 附加异常:")
            rep.say(traceback.format_exc())

    if tree is None:
        rep.say("\n[总] 没有连上 Aspen,只完成了注册表扫描。")
        rep.save(args.out)
        return 0

    rep.head("2c. 文档信息")
    rep.item("附着方式", how)
    for attr in ("Name", "FullName", "ProcessId"):
        try:
            rep.item(attr, getattr(doc, attr))
        except Exception as e:
            rep.item(attr, f"(读不到: {type(e).__name__})")

    if args.run:
        rep.head("2d. 运行仿真(--run)")
        try:
            t0 = time.time()
            doc.Run2()
            rep.say(f"  运行完成,耗时 {time.time() - t0:.1f}s")
        except Exception:
            rep.say("  [失败] 运行异常:")
            rep.say(traceback.format_exc())

    try:
        probe_convergence(tree, rep)
        probe_streams(tree, rep)
        probe_blocks(tree, rep)
        probe_columns(tree, rep)
        if args.deep:
            probe_deep(tree, rep)
    except Exception:
        rep.head("探测中断")
        rep.say(traceback.format_exc())

    rep.head("8. 验收")
    streams = len(child_names(find(tree, r"\Data\Streams")))
    blocks = len(child_names(find(tree, r"\Data\Blocks")))
    rep.item("读到的流股数", streams)
    rep.item("读到的设备数", blocks)
    rep.item("结论", "技术路线跑通" if streams and blocks else "数据不完整,需要排查")

    rep.save(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
