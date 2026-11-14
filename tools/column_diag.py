"""塔剖面查看器:拉逐板剖面 + 标记异常。

定位:**只读、只标记,不给修复建议。**
工具的职责是告诉你"这份数据能不能用、哪里不对劲",
至于怎么改模型,那是 Aspen 建模的问题,不属于本工具的范围。

用法:
    python -m tools.column_diag              # 自动找所有塔,逐个看
    python -m tools.column_diag T0202        # 只看某个塔
    python -m tools.column_diag T0202 --csv out.csv   # 顺便导出剖面

标记规则(基于实测数据):
  1. 温度是否超出物理可能范围(低压下 >400℃ 基本就是发散了)
  2. 相邻级温差是否跳变过大
  3. 级间流量是否剧烈震荡(最大/最小 >50 倍)
  4. 塔顶液体流量是否与"回流比 × 馏出量"相符(不符 = 没收敛到规格)
  5. 是否存在"干板"(液体流量趋近 0)
  6. 馏出量相对进料是否小到可疑
"""

from __future__ import annotations

import argparse
import csv
import sys

from app.aspen import connect, reader

PROFILE_VARS = [
    ("B_TEMP", "温度", "C"),
    ("B_PRES", "压力", "bar"),
    ("LIQ_FLOW", "液相流量", "kmol/hr"),
    ("VAP_FLOW", "气相流量", "kmol/hr"),
    ("DUTY", "热负荷", "cal/sec"),
]


# ---------------------------------------------------------------- 读取


def _num(session, path):
    v, u = reader.fetch(session, path)
    try:
        return float(v), u
    except (TypeError, ValueError):
        return None, u


def read_profile(session, block: str) -> dict:
    """读一列塔的逐板剖面。"""
    base = rf"\Data\Blocks\{block}\Output"
    stages_node = session.find(rf"{base}\B_TEMP")
    stages = reader.child_names(stages_node)
    data = {"stages": stages, "vars": {}}
    for var, _label, _unit in PROFILE_VARS:
        vals = []
        for st in stages:
            v, u = _num(session, rf"{base}\{var}\{st}")
            vals.append(v)
        data["vars"][var] = vals
    _, unit = _num(session, rf"{base}\LIQ_FLOW\{stages[0]}") if stages else (None, "")
    data["flow_unit"] = unit
    return data


def read_setup(session, block: str) -> dict:
    """读塔的关键设置与进料信息。"""
    base = rf"\Data\Blocks\{block}"
    setup = {}
    for key in (
        "NSTAGE",
        "FEED_STAGE",
        "CONDENSER",
        "COND",
        "BASIS_RR",
        "RR_BASIS",
        "D:F",
        "D:F_BASIS",
        "PRES1",
        "OPT_PRES",
        "ABSORBER",
        "SPC_RR",
        "SPC_D",
    ):
        setup[key] = session.value(rf"{base}\Input\{key}")

    # 进料流股
    feeds = []
    node = session.find(rf"{base}\Output\IN_STRMID")
    if node is not None:
        try:
            for i in range(node.Elements.Count):
                name = node.Elements.Item(i).Value
                if name:
                    feeds.append(str(name))
        except Exception:
            pass
    setup["feeds"] = feeds

    feed_info = []
    for f in feeds:
        feed_info.append(
            {
                "name": f,
                "temperature_c": session.value(rf"\Data\Streams\{f}\Output\RES_TEMP"),
                "pressure_bar": session.value(rf"\Data\Streams\{f}\Output\RES_PRES"),
                "mole_flow": session.value(rf"\Data\Streams\{f}\Output\RES_MOLEFLOW"),
                "vap_frac": session.value(rf"\Data\Streams\{f}\Output\RES_VFRAC"),
            }
        )
    setup["feed_info"] = feed_info
    return setup


# ---------------------------------------------------------------- 检查


def check(profile: dict, setup: dict) -> list[dict]:
    """返回异常标记列表,每条含 level / title / detail。"""
    issues: list[dict] = []
    temps = profile["vars"].get("B_TEMP") or []
    liq = profile["vars"].get("LIQ_FLOW") or []
    vap = profile["vars"].get("VAP_FLOW") or []

    temps_ok = [t for t in temps if t is not None]
    liq_ok = [x for x in liq if x is not None]
    if not temps_ok:
        return issues

    # 1. 温度越界
    hot = max(temps_ok)
    cold = min(temps_ok)
    if hot > 400 or cold < -100:
        # 级号与温度都取越界的那一侧:以前两处都格式化 hot,低温越界会显示成最高温
        bad = hot if hot > 400 else cold
        worst = temps_ok.index(bad) + 1
        issues.append(
            {
                "level": "error",
                "title": f"温度超出物理可能范围(第 {worst} 级 {bad:.0f}℃)",
                "detail": "低压塔里出现这种温度,说明迭代已经发散,当前剖面数值不可用。",
            }
        )

    # 2. 相邻级温度跳变
    jumps = []
    for i in range(1, len(temps)):
        if temps[i] is None or temps[i - 1] is None:
            continue
        d = abs(temps[i] - temps[i - 1])
        if d > 80:
            jumps.append((i + 1, d))
    if jumps:
        i, d = max(jumps, key=lambda x: x[1])
        issues.append(
            {
                "level": "error",
                "title": f"相邻级温度跳变 {d:.0f}℃(第 {i} 级附近)",
                "detail": f"共 {len(jumps)} 处跳变 >80℃,正常精馏塔逐板温差通常只有几度。",
            }
        )

    # 3. 流量震荡
    positive = [x for x in liq_ok if x > 1e-9]
    if positive and min(positive) > 0:
        ratio = max(positive) / min(positive)
        if ratio > 50:
            issues.append(
                {
                    "level": "error",
                    "title": f"级间液流量震荡 {ratio:.0f} 倍",
                    "detail": f"最大 {max(positive):.0f}、最小 {min(positive):.2f} kmol/hr,不是收敛解。",
                }
            )

    # 4. 塔顶回流 vs 规格
    rr = setup.get("BASIS_RR")
    d = setup.get("D:F")
    if isinstance(rr, (int, float)) and isinstance(d, (int, float)) and d > 0 and liq and liq[0]:
        expect = rr * d
        if expect > 0 and abs(liq[0] - expect) / expect > 0.3:
            issues.append(
                {
                    "level": "error",
                    "title": f"塔顶液流量与规格不符(实际 {liq[0]:.1f} vs 规格推算 {expect:.1f} kmol/hr)",
                    "detail": f"回流比 {rr:g} × 馏出量 {d:g} = {expect:.2f} kmol/hr,实际却读出 {liq[0]:.1f}。"
                    "说明这次运行没有收敛到规格,读到的是迭代中途值。",
                }
            )

    # 5. 干板
    if liq_ok:
        scale = max(liq_ok) or 1.0
        dry = [i + 1 for i, x in enumerate(liq) if x is not None and x < scale * 1e-3]
        if dry:
            issues.append(
                {
                    "level": "warning",
                    "title": f"{len(dry)} 块板液体流量接近 0(第 {dry[0]} 级起)",
                    "detail": '对应 Aspen 的 "COLUMN DRIES UP" 一类提示。',
                }
            )

    # 6. 馏出量相对进料过小
    feed_total = sum(
        f["mole_flow"] for f in setup["feed_info"] if isinstance(f.get("mole_flow"), (int, float))
    )
    if feed_total and isinstance(d, (int, float)) and 0 < d < feed_total * 0.01:
        issues.append(
            {
                "level": "warning",
                "title": f"馏出量只有进料的 {d / feed_total * 100:.2f}%",
                "detail": f"进料 {feed_total:.1f} kmol/hr,馏出 {d:g} kmol/hr(仅供参考)。",
            }
        )

    return issues


# ---------------------------------------------------------------- 输出


def print_profile(block: str, profile: dict, setup: dict) -> None:
    stages = profile["stages"]
    if not stages:
        print("  (没有逐板数据)")
        return
    temps = profile["vars"]["B_TEMP"]
    pres = profile["vars"]["B_PRES"]
    liq = profile["vars"]["LIQ_FLOW"]
    vap = profile["vars"]["VAP_FLOW"]
    duty = profile["vars"]["DUTY"]

    print(f"  {'级':>3} {'温度℃':>9} {'压力bar':>9} {'液流量':>11} {'气流量':>11} {'热负荷':>11}")
    for i, st in enumerate(stages):

        def f(v, w=9, p=2):
            return f"{v:{w}.{p}f}" if isinstance(v, (int, float)) else f"{'-':>{w}}"

        print(
            f"  {st:>3} {f(temps[i])} {f(pres[i], 9, 3)} {f(liq[i], 11, 2)} "
            f"{f(vap[i], 11, 2)} {f(duty[i], 11, 0)}"
        )
    print(f"  单位:液体/气体 {profile['flow_unit']}")


def diagnose(session, block: str) -> int:
    setup = read_setup(session, block)
    profile = read_profile(session, block)

    print(f"\n{'=' * 70}")
    print(
        f"塔 {block}  冷凝器={setup.get('CONDENSER')}  级数={setup.get('NSTAGE')}  "
        f"进料级={setup.get('FEED_STAGE')}  塔压={setup.get('PRES1')} bar"
    )
    print(f"  规格:回流比={setup.get('BASIS_RR')}  馏出量 D:F={setup.get('D:F')}")
    for f in setup["feed_info"]:
        print(
            f"  进料 {f['name']}: {f['mole_flow']} kmol/hr, {f['temperature_c']} ℃, "
            f"汽化率 {f['vap_frac']}"
        )

    print("\n  逐板剖面:")
    print_profile(block, profile, setup)

    issues = check(profile, setup)
    print("\n  标记:")
    if not issues:
        print("    ✅ 未发现明显异常")
        return 0

    for n, issue in enumerate(issues, 1):
        mark = "❌" if issue["level"] == "error" else "⚠️"
        print(f"    {mark} [{n}] {issue['title']}")
        print(f"         {issue['detail']}")

    errors = [i for i in issues if i["level"] == "error"]
    if errors:
        print("\n  结论:该塔未收敛,逐板数据不可用于分析/对比。")
    else:
        print("\n  结论:有告警,数据可用但需留意。")
    return 1


def find_columns(session) -> list[str]:
    """找出所有 RadFrac/Absorber 类模块。"""
    out = []
    root = session.find(r"\Data\Blocks")
    if root is None:
        return out
    try:
        for i in range(root.Elements.Count):
            node = root.Elements.Item(i)
            try:
                btype = str(node.AttributeValue(6))
            except Exception:
                continue
            if "RadFrac" in btype or "Absorber" in btype:
                out.append(str(node.Name))
    except Exception:
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="塔逐板剖面诊断")
    ap.add_argument("block", nargs="?", help="塔名,如 T0202;不填则诊断全部塔")
    ap.add_argument("--csv", help="把剖面导出到 CSV")
    args = ap.parse_args()

    session = connect.attach()
    blocks = [args.block] if args.block else find_columns(session)
    if not blocks:
        print("没有找到塔(RadFrac)。")
        return 1

    rc = 0
    for b in blocks:
        rc |= diagnose(session, b)

    if args.csv and args.block:
        profile = read_profile(session, args.block)
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["级", "温度C", "压力bar", "液流量", "气流量", "热负荷"])
            for i, st in enumerate(profile["stages"]):
                w.writerow([st] + [profile["vars"][v][i] for v, _, _ in PROFILE_VARS])
        print(f"\n剖面已导出:{args.csv}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
