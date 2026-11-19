"""焓平衡与物料衡算、能量衡算的判定口径。

用例正文原样搬自 `tools/selftest.py`(迁移到 pytest 时只改了位置)。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.aspen import balance as balance_rules
from app.design import energy_report as energy_rules
from app.design import mass_balance as mass_rules
from app.design import mole_balance as mole_rules
from app.design import sections as section_rules
from app.store import db
from tests._harness import (
    _blocks,
    _conns,
    _energy,
    _mb_blocks,
    _mb_conns,
    _mb_streams,
    _streams,
    check,
    check_close,
    check_eq,
)


def test_balance() -> None:
    """balance.compute():按设备类型选参照值,不是一刀切 1%。"""
    # ① Heater:ΔH 应等于热负荷
    res = balance_rules.compute(
        _streams(feed=10.0, prod=110.0),
        _blocks({"name": "H1", "type": "Heater", "duty_kw": 100.0}),
        _conns(("H1", "in", "feed", None), ("H1", "out", "prod", None)),
    )
    check_eq("balance: Heater 一致 → ok", res["H1"]["balance_state"], balance_rules.OK)
    check_eq("balance: Heater 记录 ΔH", res["H1"]["dh_kw"], 100.0)
    check_eq("balance: Heater 参照类型", res["H1"]["ref_kind"], "duty")

    # ② 差 2 kW(超 1% 也超 0.5 kW)→ 超差
    res = balance_rules.compute(
        _streams(feed=10.0, prod=110.0),
        _blocks({"name": "H1", "type": "Heater", "duty_kw": 98.0}),
        _conns(("H1", "in", "feed", None), ("H1", "out", "prod", None)),
    )
    check_eq("balance: Heater 差 2 kW → 超差", res["H1"]["balance_state"], balance_rules.BAD)

    # ③ 差 0.4 kW:绝对容差 0.5 kW 内 → 通过(小负荷时不能按 1% 卡死)
    res = balance_rules.compute(
        _streams(feed=10.0, prod=110.0),
        _blocks({"name": "H1", "type": "Heater", "duty_kw": 99.6}),
        _conns(("H1", "in", "feed", None), ("H1", "out", "prod", None)),
    )
    check_eq("balance: Heater 差 0.4 kW → 通过", res["H1"]["balance_state"], balance_rules.OK)

    # ④ Heater 没有 QCALC → 无法校验,不许按绝热去猜
    res = balance_rules.compute(
        _streams(feed=10.0, prod=110.0),
        _blocks({"name": "H1", "type": "Heater"}),
        _conns(("H1", "in", "feed", None), ("H1", "out", "prod", None)),
    )
    check_eq(
        "balance: Heater 缺热负荷 → 无法校验", res["H1"]["balance_state"], balance_rules.NO_DATA
    )

    # ⑤ HeatX 只校验冷侧(整体 ΔH 恒为 0,拿整体比必然 0 对不上 duty)
    res = balance_rules.compute(
        _streams(cin=10.0, cout=60.0, hin=200.0, hout=150.0),
        _blocks({"name": "E1", "type": "HeatX", "duty_kw": 50.0}),
        _conns(
            ("E1", "in", "cin", "cold"),
            ("E1", "out", "cout", "cold"),
            ("E1", "in", "hin", "hot"),
            ("E1", "out", "hout", "hot"),
        ),
    )
    check_eq("balance: HeatX 冷侧一致 → ok", res["E1"]["balance_state"], balance_rules.OK)
    check_eq("balance: HeatX 参照类型", res["E1"]["ref_kind"], "hx_cold")

    # ⑥ HeatX 缺 duty 或缺分侧信息 → 无法校验
    res = balance_rules.compute(
        _streams(cin=10.0, cout=60.0),
        _blocks({"name": "E1", "type": "HeatX"}),
        _conns(("E1", "in", "cin", "cold"), ("E1", "out", "cout", "cold")),
    )
    check_eq(
        "balance: HeatX 缺 HX_DUTY → 无法校验", res["E1"]["balance_state"], balance_rules.NO_DATA
    )
    res = balance_rules.compute(
        _streams(cin=10.0, cout=60.0),
        _blocks({"name": "E1", "type": "HeatX", "duty_kw": 50.0}),
        _conns(("E1", "in", "cin", None), ("E1", "out", "cout", None)),
    )
    check_eq(
        "balance: HeatX 缺分侧信息 → 无法校验", res["E1"]["balance_state"], balance_rules.NO_DATA
    )

    # ⑦ 泵 / 压缩机:按比值区间判,差的是效率(压缩机实测 0.950)
    res = balance_rules.compute(
        _streams(feed=0.0, prod=95.0),
        _blocks({"name": "C1", "type": "Compr", "power_kw": 100.0}),
        _conns(("C1", "in", "feed", None), ("C1", "out", "prod", None)),
    )
    check_eq("balance: 压缩机 ΔH/功率 0.95 → ok", res["C1"]["balance_state"], balance_rules.OK)
    res = balance_rules.compute(
        _streams(feed=0.0, prod=60.0),
        _blocks({"name": "C1", "type": "Compr", "power_kw": 100.0}),
        _conns(("C1", "in", "feed", None), ("C1", "out", "prod", None)),
    )
    check_eq("balance: 压缩机 ΔH/功率 0.60 → 超差", res["C1"]["balance_state"], balance_rules.BAD)
    res = balance_rules.compute(
        _streams(feed=0.0, prod=95.0),
        _blocks({"name": "C1", "type": "Compr"}),
        _conns(("C1", "in", "feed", None), ("C1", "out", "prod", None)),
    )
    check_eq(
        "balance: 压缩机缺轴功率 → 无法校验", res["C1"]["balance_state"], balance_rules.NO_DATA
    )

    # ⑧ 绝热设备:ΔH ≈ 0,容差按最大焓流的 0.5%(不是绝对 0.5 kW)
    res = balance_rules.compute(
        _streams(a=1000.0, b=500.0, c=1500.4),
        _blocks({"name": "M1", "type": "Mixer"}),
        _conns(("M1", "in", "a", None), ("M1", "in", "b", None), ("M1", "out", "c", None)),
    )
    check_eq(
        "balance: Mixer 差 0.4 kW(20 万 kW 量级)→ 通过",
        res["M1"]["balance_state"],
        balance_rules.OK,
    )
    res = balance_rules.compute(
        _streams(a=1000.0, b=500.0, c=1600.0),
        _blocks({"name": "M1", "type": "Mixer"}),
        _conns(("M1", "in", "a", None), ("M1", "in", "b", None), ("M1", "out", "c", None)),
    )
    check_eq("balance: Mixer 差 100 kW → 超差", res["M1"]["balance_state"], balance_rules.BAD)

    # ⑨ 塔:ΔH 应等于「首级(冷凝器)+ 末级(再沸器)」—— 2026-11-14 起纳入校验
    t_blocks = _blocks({"name": "T1", "type": "RadFrac"})
    t_conns = _conns(
        ("T1", "in", "feed", None), ("T1", "out", "top", None), ("T1", "out", "bottom", None)
    )
    stages = {
        "T1": [
            {"stage": 1, "duty_kw": -60.0},
            {"stage": 5, "duty_kw": 0.0},
            {"stage": 10, "duty_kw": 160.0},
        ]
    }
    res = balance_rules.compute(
        _streams(feed=0.0, top=80.0, bottom=20.0), t_blocks, t_conns, stages=stages
    )
    check_eq("balance: 塔 ΔH = 冷凝 + 再沸 → ok", res["T1"]["balance_state"], balance_rules.OK)
    check_eq("balance: 塔的对照值取首末级之和", res["T1"]["ref_kw"], 100.0)
    check_eq("balance: 塔的参照类型标 stage", res["T1"]["ref_kind"], "stage")
    res = balance_rules.compute(
        _streams(feed=0.0, top=80.0, bottom=60.0), t_blocks, t_conns, stages=stages
    )
    check_eq(
        "balance: 塔 ΔH 与净热负荷差太多 → 超差", res["T1"]["balance_state"], balance_rules.BAD
    )
    # 没有逐级剖面时退回块上的冷凝 / 再沸负荷
    res = balance_rules.compute(
        _streams(feed=0.0, top=80.0, bottom=20.0),
        _blocks({"name": "T2", "type": "RadFrac", "cond_duty_kw": -60.0, "reb_duty_kw": 160.0}),
        _conns(
            ("T2", "in", "feed", None), ("T2", "out", "top", None), ("T2", "out", "bottom", None)
        ),
    )
    check_eq("balance: 塔无剖面 → 退回块字段", res["T2"]["balance_state"], balance_rules.OK)
    # 两者都没有 → 无法校验(不是不校验)
    res = balance_rules.compute(
        _streams(feed=0.0, top=80.0, bottom=20.0),
        _blocks({"name": "T3", "type": "RadFrac"}),
        t_conns,
    )
    check_eq(
        "balance: 塔既无剖面也无块字段 → 无法校验",
        res["T3"]["balance_state"],
        balance_rules.NO_DATA,
    )

    # ⑨b 反应器:ΔH 应等于反应热 QCALC;带换热伴流的多股反应器判无法校验
    res = balance_rules.compute(
        _streams(feed=-10.0, prod=-110.0),
        _blocks({"name": "R1", "type": "RPlug", "duty_kw": -100.0}),
        _conns(("R1", "in", "feed", None), ("R1", "out", "prod", None)),
    )
    check_eq("balance: 反应器 ΔH = QCALC → ok", res["R1"]["balance_state"], balance_rules.OK)
    res = balance_rules.compute(
        _streams(feed=-10.0, prod=-90.0),
        _blocks({"name": "R2", "type": "RPlug", "duty_kw": -100.0}),
        _conns(("R2", "in", "feed", None), ("R2", "out", "prod", None)),
    )
    check_eq(
        "balance: 反应器 ΔH 与 QCALC 差 20 kW → 超差", res["R2"]["balance_state"], balance_rules.BAD
    )
    # 多股反应器:总焓差就够用(实测 2进2出 的反应器总 ΔH 也等于 QCALC)
    res = balance_rules.compute(
        _streams(a=-10.0, b=-20.0, c=-130.0),
        _blocks({"name": "R3", "type": "RCSTR", "duty_kw": -100.0}),
        _conns(("R3", "in", "a", None), ("R3", "in", "b", None), ("R3", "out", "c", None)),
    )
    check_eq("balance: 多股反应器总焓差对上 → ok", res["R3"]["balance_state"], balance_rules.OK)
    # 带换热伴流:总 ΔH≈0 被伴流抵消,但「过程股对」的焓差等于 QCALC(case 33 R0101 的真实形态)
    res = balance_rules.compute(
        _streams(a=-10.0, b=500.0, c=-9528.95, d=10018.95),
        _blocks({"name": "R4", "type": "RPlug", "duty_kw": -9518.95}),
        _conns(
            ("R4", "in", "a", None),
            ("R4", "in", "b", None),
            ("R4", "out", "c", None),
            ("R4", "out", "d", None),
        ),
    )
    check_eq("balance: 带伴流的反应器靠过程股对判 ok", res["R4"]["balance_state"], balance_rules.OK)
    check("balance: 带伴流的说明点出过程股", "伴流" in res["R4"]["balance_note"])

    # ⑩ 缺焓值(能量流 / 没 Run 过)→ 无法校验,不是失败
    res = balance_rules.compute(
        _streams(feed=None, prod=110.0),
        _blocks({"name": "H1", "type": "Heater", "duty_kw": 100.0}),
        _conns(("H1", "in", "feed", None), ("H1", "out", "prod", None)),
    )
    check_eq("balance: 流股缺焓值 → 无法校验", res["H1"]["balance_state"], balance_rules.NO_DATA)

    # ⑪ 连接不全(读不到进口)→ 无法校验
    res = balance_rules.compute(
        _streams(prod=110.0),
        _blocks({"name": "H1", "type": "Heater", "duty_kw": 100.0}),
        _conns(("H1", "out", "prod", None)),
    )
    check_eq("balance: 连接不全 → 无法校验", res["H1"]["balance_state"], balance_rules.NO_DATA)

    # ⑫ summarize() 与 compute() 一致
    counts = balance_rules.summarize(
        {
            "a": {"balance_state": balance_rules.OK},
            "b": {"balance_state": balance_rules.OK},
            "c": {"balance_state": balance_rules.SKIP},
        }
    )
    check_eq("balance: summarize 计数", (counts["ok"], counts["skip"]), (2, 1))


def test_mass_balance() -> None:
    """mass_balance.build():质量守恒是新结论,判据是相对 0.5%,不碰焓平衡。"""
    # ① 单进单出:进 = 出 → 通过
    res = mass_rules.build(
        _mb_streams(("0101", 100.0, 5.0, 25.0, 1.0), ("0102", 100.0, 5.0, 60.0, 1.0)),
        _mb_blocks(("E0101", "Heater")),
        _mb_conns(("E0101", "in", "0101"), ("E0101", "out", "0102")),
    )
    dev = res["devices"][0]
    check_eq("mass: 单进单出 → pass", dev["state"], mass_rules.PASS)
    check_eq("mass: 单进单出 相对误差 0", dev["rel_err"], 0.0)
    check("mass: 质量守恒与焓平衡状态不共用字段", "balance_state" not in dev)

    # ② 多进多出(Mixer 三进一出):合计算对
    res = mass_rules.build(
        _mb_streams(("0201", 30.0), ("0202", 20.0), ("0203", 50.0), ("0204", 100.0)),
        _mb_blocks(("M0201", "Mixer")),
        _mb_conns(
            ("M0201", "in", "0201"),
            ("M0201", "in", "0202"),
            ("M0201", "in", "0203"),
            ("M0201", "out", "0204"),
        ),
    )
    dev = res["devices"][0]
    check_eq("mass: 多进多出 进料合计", dev["mass_in_kgh"], 100.0)
    check_eq("mass: 多进多出 出料合计", dev["mass_out_kgh"], 100.0)
    check_eq("mass: 多进多出 → pass", dev["state"], mass_rules.PASS)

    # ③ 缺失流股:缺出口连接 → 无法校验(不是 fail)
    res = mass_rules.build(
        _mb_streams(("0301", 100.0)),
        _mb_blocks(("E0301", "Heater")),
        _mb_conns(("E0301", "in", "0301")),
    )
    check_eq("mass: 缺出口连接 → 无法校验", res["devices"][0]["state"], mass_rules.NO_DATA)
    # 流股存在但缺质量流量 → 也是无法校验,不能当 0 加
    res = mass_rules.build(
        _mb_streams(("0302", None), ("0303", 100.0)),
        _mb_blocks(("E0302", "Heater")),
        _mb_conns(("E0302", "in", "0302"), ("E0302", "out", "0303")),
    )
    check_eq("mass: 缺质量流量 → 无法校验", res["devices"][0]["state"], mass_rules.NO_DATA)

    # ④ 误差临界:恰 0.5% 通过,略超 0.5% 超差
    res = mass_rules.build(
        _mb_streams(("0401", 1000.0), ("0402", 995.0)),
        _mb_blocks(("E0401", "Heater")),
        _mb_conns(("E0401", "in", "0401"), ("E0401", "out", "0402")),
    )
    check_eq("mass: 恰 0.5% → pass(≤ 边界)", res["devices"][0]["state"], mass_rules.PASS)
    res = mass_rules.build(
        _mb_streams(("0403", 1000.0), ("0404", 994.0)),
        _mb_blocks(("E0402", "Heater")),
        _mb_conns(("E0402", "in", "0403"), ("E0402", "out", "0404")),
    )
    check_eq("mass: 0.6% → fail", res["devices"][0]["state"], mass_rules.FAIL)
    check("mass: 超差说明写明疑似漏流股", "漏流股" in res["devices"][0]["note"])

    # ⑤ 小流量豁免:进出都 < 1 kg/h 时不做判定
    res = mass_rules.build(
        _mb_streams(("0501", 0.2), ("0502", 0.1)),
        _mb_blocks(("V0501", "Valve")),
        _mb_conns(("V0501", "in", "0501"), ("V0501", "out", "0502")),
    )
    check_eq("mass: 小流量 → too_small", res["devices"][0]["state"], mass_rules.TOO_SMALL)

    # ⑥ HeatX 冷侧:质量守恒按整体(冷热两侧一起),与焓平衡「只校冷侧」不同
    res = mass_rules.build(
        _mb_streams(("0601", 100.0), ("0602", 100.0), ("0603", 50.0), ("0604", 50.0)),
        _mb_blocks(("EX0601", "HeatX")),
        _mb_conns(
            ("EX0601", "in", "0601", "hot"),
            ("EX0601", "out", "0602", "hot"),
            ("EX0601", "in", "0603", "cold"),
            ("EX0601", "out", "0604", "cold"),
        ),
    )
    dev = res["devices"][0]
    check_eq("mass: HeatX 整体进料合计(冷+热)", dev["mass_in_kgh"], 150.0)
    check_eq("mass: HeatX 两侧都算 → pass", dev["state"], mass_rules.PASS)

    # ⑦ 塔:进料 = 顶 + 底
    res = mass_rules.build(
        _mb_streams(("0701", 1000.0), ("0702", 400.0), ("0703", 600.0)),
        _mb_blocks(("T0701", "RadFrac")),
        _mb_conns(("T0701", "in", "0701"), ("T0701", "out", "0702"), ("T0701", "out", "0703")),
    )
    check_eq("mass: 塔 进=顶+底 → pass", res["devices"][0]["state"], mass_rules.PASS)

    # ⑧ 工段判定四层:配置 > 设备名 > 流股众数 > 公用工程
    check_eq("工段: 设备名尾四位", section_rules.section_of_block("EX0101"), "01")
    check_eq("工段: 带后缀的塔名", section_rules.section_of_block("T0303-A"), "03")
    check_eq("工段: 无段号设备名", section_rules.section_of_block("B3"), None)
    check_eq("工段: 流股段号", section_rules.section_of_stream("0101"), "01")
    check_eq("工段: 公用工程流股无段号", section_rules.section_of_stream("CW1"), None)
    check_eq(
        "工段: 配置覆盖设备名",
        section_rules.resolve("E0101", ["0101"], ["0102"], {"E0101": "09"}),
        "09",
    )
    check_eq(
        "工段: 空配置等于没配",
        section_rules.resolve("E0101", ["0101"], ["0102"], {"E0101": ""}),
        "01",
    )
    check_eq("工段: 设备名优先于流股众数", section_rules.resolve("E0101", ["0201"], ["0202"]), "01")
    check_eq(
        "工段: 设备名取不到 → 流股众数",
        section_rules.resolve("B3", ["0301", "0302"], ["0303"]),
        "03",
    )
    check_eq("工段: 进口优先于出口(打平时)", section_rules.resolve("B4", ["0401"], ["0501"]), "04")
    check_eq(
        "工段: 都判不出 → 公用工程",
        section_rules.resolve("B5", ["CW1"], ["CK1"]),
        section_rules.UTILITY_KEY,
    )

    # ⑨ 界区配置:按工况 id 读,支持 {"cases": {...}} 包装,坏数据不抛异常
    parts, devs, gone = section_rules.load(
        {"40": {"01": {"label": "第一工段", "in": ["FEED-H"], "out": ["1TO2"]}}}, 40
    )
    check_eq(
        "界区配置: 进/出界流股", (parts["01"]["in"], parts["01"]["out"]), (["FEED-H"], ["1TO2"])
    )
    check_eq("界区配置: 标签", parts["01"]["label"], "第一工段")
    wrapped, _, _ = section_rules.load({"cases": {"40": {"02": {"in": ["X"]}}}}, 40)
    check_eq("界区配置: cases 包装也能读", wrapped["02"]["in"], ["X"])
    check_eq("界区配置: 别的工况读不到", section_rules.load({"41": {}}, 40), ({}, {}, []))
    check_eq("界区配置: 坏数据不抛异常", section_rules.load(["not", "a", "dict"], 40), ({}, {}, []))
    _, devs, _ = section_rules.load(
        {"7": {"01": {}, "device_sections": {"E0101": "09", "E0102": ""}}}, 7
    )
    check_eq("界区配置: 设备归堆覆盖只收非空", devs, {"E0101": "09"})
    # 人工删掉的工段要能读回来,并且合并不再把它从草稿里长出来
    parts2, _, gone2 = section_rules.load({"40": {"deleted": ["03", "05"]}}, 40)
    check_eq("界区配置: 读到删除清单", (parts2, gone2), ({}, ["03", "05"]))
    merged2 = section_rules.merge(
        {
            "01": {"label": "01 工段", "in": ["a"], "out": [], "blocks": []},
            "03": {"label": "03 工段", "in": ["b"], "out": [], "blocks": []},
        },
        {},
        ["03"],
    )
    check_eq("界区合并: 删掉的工段不会从草稿里复活", list(merged2), ["01"])

    # ⑩ 草稿推导:按设备名段号切界区(只作起点,真正算的是确认后的边界)
    draft = section_rules.draft(
        _mb_blocks(("E0101", "Heater"), ("E0201", "Heater"), ("B9", "Heater")),
        _mb_conns(
            ("E0101", "in", "FEED"),
            ("E0101", "out", "1TO2"),
            ("E0201", "in", "1TO2"),
            ("E0201", "out", "WATER"),
            ("B9", "in", "JUNK"),
            ("B9", "out", "JUNK2"),
        ),
    )
    check_eq("界区草稿: 01 段进界=外部进料", draft["01"]["in"], ["FEED"])
    check_eq("界区草稿: 01 段出界=去 02 段", draft["01"]["out"], ["1TO2"])
    check_eq("界区草稿: 02 段进界=来自 01 段", draft["02"]["in"], ["1TO2"])
    check_eq("界区草稿: 02 段出界=外排", draft["02"]["out"], ["WATER"])
    check("界区草稿: 无段号的设备不产生工段", "B9" not in draft)

    # ⑪ 合并:确认过的以确认为准,空列表是有效决定(不回退草稿)
    merged = section_rules.merge(
        {
            "01": {"label": "01 工段", "in": ["d1"], "out": ["d2"], "blocks": ["E0101"]},
            "02": {"label": "02 工段", "in": ["d3"], "out": [], "blocks": []},
        },
        {"01": {"label": "反应工段", "in": [], "out": ["c1"], "blocks": []}},
    )
    check_eq("界区合并: 确认的空列表不回退草稿", merged["01"]["in"], [])
    check_eq("界区合并: 确认项覆盖标签", merged["01"]["label"], "反应工段")
    check_eq("界区合并: 草稿多出来的工段保留", merged["02"]["in"], ["d3"])

    # ⑫ 界区衡算:Σ进界 = Σ出界,容差与逐设备一致
    res = mass_rules.boundary_build(
        _mb_streams(
            ("FEED", 100.0, 5.0, 25.0, 1.0),
            ("1TO2", 100.0, 5.0, 60.0, 1.0),
            ("WATER", 100.0, 5.0, 40.0, 1.0),
        ),
        {
            "01": {"label": "01 工段", "in": ["FEED"], "out": ["1TO2"]},
            "02": {"label": "02 工段", "in": ["1TO2"], "out": ["WATER"]},
        },
    )
    check_eq(
        "界区: 两段都闭合 → pass",
        [p["state"] for p in res["parts"]],
        [mass_rules.PASS, mass_rules.PASS],
    )
    check_eq("界区: 进界合计", res["parts"][0]["mass_in_kgh"], 100.0)
    check_eq("界区: 汇总台数", res["summary"]["total"], 2)

    # 缺口径:配置里写了库里没有的流股 → 无法校验,并列出缺哪股
    res = mass_rules.boundary_build(
        _mb_streams(("FEED", 100.0)),
        {"01": {"in": ["FEED"], "out": ["NOT_THERE"]}},
    )
    part = res["parts"][0]
    check_eq("界区: 配置流股不存在 → 无法校验", part["state"], mass_rules.NO_DATA)
    check_eq("界区: 记录缺失流股", part["missing"], ["NOT_THERE"])
    check("界区: 说明里点出缺哪股", "NOT_THERE" in part["note"])

    # 空边界与超差
    res = mass_rules.boundary_build(
        _mb_streams(("FEED", 100.0)), {"01": {"in": ["FEED"], "out": []}}
    )
    check_eq("界区: 没有出界流股 → 无法校验", res["parts"][0]["state"], mass_rules.NO_DATA)
    res = mass_rules.boundary_build(
        _mb_streams(("FEED", 1000.0), ("1TO2", 990.0)),
        {"01": {"in": ["FEED"], "out": ["1TO2"]}},
    )
    check_eq("界区: 1% 误差 → 超差", res["parts"][0]["state"], mass_rules.FAIL)
    check("界区: 超差说明写明疑似漏流股", "漏流股" in res["parts"][0]["note"])
    res = mass_rules.boundary_build(
        _mb_streams(("FEED", 0.3), ("1TO2", 0.1)),
        {"01": {"in": ["FEED"], "out": ["1TO2"]}},
    )
    check_eq("界区: 极小流量 → too_small", res["parts"][0]["state"], mass_rules.TOO_SMALL)

    # ⑬ 分组顺序:段号升序,公用工程排最后;组内保持传入顺序
    res = mass_rules.build(
        _mb_streams(
            ("0801", 10.0), ("0802", 10.0), ("CW1", 5.0), ("CW2", 5.0), ("0101", 1.0), ("0102", 1.0)
        ),
        _mb_blocks(("X1", "Welding"), ("E0101", "Heater"), ("H1", "Heater")),
        _mb_conns(
            ("X1", "in", "CW1"),
            ("X1", "out", "CW2"),
            ("E0101", "in", "0101"),
            ("E0101", "out", "0102"),
            ("H1", "in", "0801"),
            ("H1", "out", "0802"),
        ),
    )
    keys = [g["key"] for g in res["groups"]]
    check_eq("mass: 分组按段号升序、公用工程最后", keys, ["01", "08", section_rules.UTILITY_KEY])
    check_eq(
        "mass: 设备名取不到时按流股众数落段(08)",
        [d["block"] for d in res["groups"][1]["devices"]],
        ["H1"],
    )
    check_eq("mass: 公用工程分组标签", res["groups"][-1]["label"], section_rules.UTILITY_LABEL)

    # ⑭ 勾选:只算选中的设备;不认识的名字记下来,不抛异常
    res = mass_rules.build(
        _mb_streams(("0901", 10.0), ("0902", 10.0), ("0903", 10.0), ("0904", 10.0)),
        _mb_blocks(("E0901", "Heater"), ("E0902", "Heater")),
        _mb_conns(
            ("E0901", "in", "0901"),
            ("E0901", "out", "0902"),
            ("E0902", "in", "0903"),
            ("E0902", "out", "0904"),
        ),
        picked=["E0902", "不存在"],
    )
    check_eq("mass: 只算选中的设备", [d["block"] for d in res["devices"]], ["E0902"])
    check_eq("mass: 不认识的设备名被记录", res["unknown_picked"], ["不存在"])
    check_eq("mass: 汇总台数随勾选变", res["summary"]["total"], 1)

    # ⑮ 汇总:状态计数 + 最大误差
    res = mass_rules.build(
        _mb_streams(("1001", 1000.0), ("1002", 1000.0), ("1003", 1000.0), ("1004", 900.0)),
        _mb_blocks(("E1001", "Heater"), ("E1002", "Heater")),
        _mb_conns(
            ("E1001", "in", "1001"),
            ("E1001", "out", "1002"),
            ("E1002", "in", "1003"),
            ("E1002", "out", "1004"),
        ),
    )
    check_eq(
        "mass: 汇总 通过/超差 台数",
        (res["summary"][mass_rules.PASS], res["summary"][mass_rules.FAIL]),
        (1, 1),
    )
    check_eq("mass: 汇总记最大误差设备", res["summary"]["worst"]["block"], "E1002")

    # ⑯ 阳性夹具:在样本库副本上删一条进口连接,造出真实的 >0.5% 越界
    #    真实 9 个工况 0 越界,「标红」路径只能这样撞出来(不需要 Aspen)。
    scratch = Path("data/preview/_selftest-mass.db")
    scratch.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile("samples/case-23-error.db", scratch)
        conn = db.open_db(str(scratch))
        case_id = db.list_cases(conn)[0]["id"]
        streams = [dict(r) for r in db.read_streams(conn, case_id)]
        blocks = [dict(r) for r in db.read_blocks(conn, case_id)]
        conns = [dict(r) for r in db.read_connections(conn, case_id)]
        before = mass_rules.build(streams, blocks, conns)
        check(
            "夹具⑯ 样本库基线:没有超差设备",
            before["summary"][mass_rules.FAIL] == 0,
            f"实际 {before['summary'][mass_rules.FAIL]} 台",
        )

        # 界区草稿在真实样本上必须是闭合的(草稿只是起点,但起点就得站得住)
        # 注意在删连接之前算 —— 删连接会真的破坏界区,那是下一步的事
        draft = section_rules.draft(blocks, conns)
        check("夹具⑯ 样本库能推出工段草稿", len(draft) >= 1, f"实际 {len(draft)} 个")
        zone = mass_rules.boundary_build(streams, draft)
        check(
            "夹具⑯ 样本库界区草稿全部闭合",
            zone["summary"][mass_rules.FAIL] == 0,
            f"超差 {zone['summary'][mass_rules.FAIL]} 个工段",
        )
        check(
            "夹具⑯ 界区草稿每条边界流股都能查到质量流量",
            all(not p["missing"] for p in zone["parts"]),
        )

        # 挑一台「多进口」设备,删掉流量最大的那条进口 → 误差 = 该股占比
        mass = {s["name"]: s.get("mass_flow_kgh") or 0.0 for s in streams}
        by_block: dict[str, list[str]] = {}
        for row in conns:
            if row["direction"] == "in":
                by_block.setdefault(row["block"], []).append(row["stream"])
        target = None
        for block, names in by_block.items():
            if len(names) >= 2 and all(mass.get(n) for n in names):
                target = (block, max(names, key=lambda n: mass[n]))
                break
        check("夹具⑯ 找到可删的进口流股", target is not None)
        if target:
            block, drop = target
            conn.execute(
                "delete from connections where case_id=? and block=? "
                "and direction='in' and stream=?",
                (case_id, block, drop),
            )
            conn.commit()
            conns2 = [dict(r) for r in db.read_connections(conn, case_id)]
            after = mass_rules.build(streams, blocks, conns2)
            state = {d["block"]: d["state"] for d in after["devices"]}
            check_eq(
                f"夹具⑯ 删一条进口({block}/{drop})后 → fail", state.get(block), mass_rules.FAIL
            )
            # 同一条连接删掉后,它所在的界区也必须跟着垮 —— 说明界区表真在读连接
            zone_after = mass_rules.boundary_build(streams, section_rules.draft(blocks, conns2))
            check(
                "夹具⑯ 删连接后界区草稿不再全闭合",
                zone_after["summary"][mass_rules.FAIL] > 0
                or zone_after["summary"][mass_rules.NO_DATA] > 0,
            )
        conn.close()
    finally:
        try:
            scratch.unlink(missing_ok=True)
        except OSError:
            pass


def test_mole_balance() -> None:
    """mole_balance.build():摩尔只在无反应设备上守恒,是「质量/摩尔 = 分子量」的可判定形式。"""
    # ① 单进单出:质量、摩尔都对得上 → pass,表观分子量就是 mass/mole
    res = mole_rules.build(
        _mb_streams(("0101", 100.0, 5.0), ("0102", 100.0, 5.0)),
        _mb_blocks(("E0101", "Heater")),
        _mb_conns(("E0101", "in", "0101"), ("E0101", "out", "0102")),
    )
    dev = res["devices"][0]
    check_eq("mole: 单进单出 → pass", dev["state"], mole_rules.PASS)
    check_close("mole: 表观分子量 = 质量/摩尔", dev["mw_in_kgkmol"], 20.0)

    # ② 摩尔口径错(质量对得上、摩尔砍半):必须报 fail,并报两端表观分子量
    res = mole_rules.build(
        _mb_streams(("0201", 100.0, 5.0), ("0202", 100.0, 2.5)),
        _mb_blocks(("E0201", "Heater")),
        _mb_conns(("E0201", "in", "0201"), ("E0201", "out", "0202")),
    )
    dev = res["devices"][0]
    check_eq("mole: 摩尔砍半 → fail", dev["state"], mole_rules.FAIL)
    check_close("mole: 出口表观分子量翻倍", dev["mw_out_kgkmol"], 40.0)
    check("mole: 超差说明指向口径", "单位、字段" in dev["note"], dev["note"])
    # 同一份数据质量守恒仍然 pass —— 两条判据各管一件事,不能互相顶替
    mass = mass_rules.build(
        _mb_streams(("0201", 100.0, 5.0), ("0202", 100.0, 2.5)),
        _mb_blocks(("E0201", "Heater")),
        _mb_conns(("E0201", "in", "0201"), ("E0201", "out", "0202")),
    )
    check_eq("mole: 质量守恒对这条错误是瞎的", mass["devices"][0]["state"], mass_rules.PASS)

    # ③ 反应器:摩尔数本来会变 → 整台不判,但要出现在 skipped 里
    res = mole_rules.build(
        _mb_streams(("0301", 100.0, 5.0), ("0302", 100.0, 6.0)),
        _mb_blocks(("R0301", "RCSTR")),
        _mb_conns(("R0301", "in", "0301"), ("R0301", "out", "0302")),
    )
    check_eq("mole: 反应器不判", len(res["devices"]), 0)
    check("mole: 反应器进 skipped 并写明原因", "反应器" in res["skipped"][0]["reason"])

    # ④ 塔:RadFrac 可以挂反应,同样不判
    res = mole_rules.build(
        _mb_streams(("0401", 100.0, 5.0), ("0402", 60.0, 3.0), ("0403", 40.0, 2.0)),
        _mb_blocks(("T0401", "RadFrac")),
        _mb_conns(("T0401", "in", "0401"), ("T0401", "out", "0402"), ("T0401", "out", "0403")),
    )
    check_eq("mole: 塔不判", len(res["devices"]), 0)
    check("mole: 塔的豁免原因写明", "塔" in res["skipped"][0]["reason"])

    # ⑤ 倍增块:出口是进口的缩放副本,进出本就不相等 → 不判
    res = mole_rules.build(
        _mb_streams(("0501", 100.0, 5.0), ("0502", 200.0, 10.0)),
        _mb_blocks(("X0501", "Mult")),
        _mb_conns(("X0501", "in", "0501"), ("X0501", "out", "0502")),
    )
    check_eq("mole: 倍增块不判", len(res["devices"]), 0)

    # ⑥ 缺摩尔流量 → 无法校验(不能当 0 加)
    res = mole_rules.build(
        _mb_streams(("0601", 100.0), ("0602", 100.0, 5.0)),
        _mb_blocks(("E0601", "Heater")),
        _mb_conns(("E0601", "in", "0601"), ("E0601", "out", "0602")),
    )
    check_eq("mole: 缺摩尔流量 → 无法校验", res["devices"][0]["state"], mole_rules.NO_DATA)

    # ⑦ 小流量豁免按质量判:进 0.2、出 0.1 kg/h,摩尔差 100% 也不判
    res = mole_rules.build(
        _mb_streams(("0701", 0.2, 0.01), ("0702", 0.1, 0.005)),
        _mb_blocks(("V0701", "Valve")),
        _mb_conns(("V0701", "in", "0701"), ("V0701", "out", "0702")),
    )
    check_eq("mole: 小流量 → too_small", res["devices"][0]["state"], mole_rules.TOO_SMALL)

    # ⑧ 不认识的块类型保守不判,并进 skipped(不静默跳过)
    res = mole_rules.build(
        _mb_streams(("0801", 100.0, 5.0), ("0802", 100.0, 5.0)),
        _mb_blocks(("Z0801", "Foo")),
        _mb_conns(("Z0801", "in", "0801"), ("Z0801", "out", "0802")),
    )
    check_eq("mole: 不认识类型不判", len(res["devices"]), 0)
    check_eq("mole: 不认识类型进 skipped", len(res["skipped"]), 1)

    # ⑨ picked 只判勾选设备,不认识的名字单独记
    res = mole_rules.build(
        _mb_streams(("0901", 100.0, 5.0), ("0902", 100.0, 5.0)),
        _mb_blocks(("E0901", "Heater"), ("E0902", "Heater")),
        _mb_conns(("E0901", "in", "0901"), ("E0901", "out", "0902")),
        picked=["E0901", "不存在"],
    )
    check_eq("mole: picked 只判勾选的", len(res["devices"]), 1)
    check_eq("mole: 不认识的名字记下来", res["unknown_picked"], ["不存在"])
    check_eq("mole: summary 统计台数", res["summary"]["total"], 1)


def test_energy_report() -> None:
    """energy_report.build():ΔH 引用注入的 compute() 结果,只补进出口合计。"""
    # ① Heater:热负荷类,偏差 = ΔH − duty
    blocks = [{"name": "E0101", "type": "Heater", "duty_kw": 100.0}]
    streams = [
        {"name": "0101", "enthalpy_flow_kw": 10.0},
        {"name": "0102", "enthalpy_flow_kw": 110.0},
    ]
    conns = _mb_conns(("E0101", "in", "0101"), ("E0101", "out", "0102"))
    res = _energy(
        "E0101", balance_rules.OK, dh=100.0, ref=100.0, kind="duty", note="ΔH 与 duty 一致"
    )
    rep = energy_rules.build(blocks, streams, conns, res)
    row = rep["devices"][0]
    check_eq("energy: Heater 进口焓流合计", row["h_in_kw"], 10.0)
    check_eq("energy: Heater 出口焓流合计", row["h_out_kw"], 110.0)
    check_eq("energy: Heater ΔH 取注入值", row["dh_kw"], 100.0)
    check_eq("energy: Heater 对照值取 duty", (row["dev_kw"], row["dev_kind"]), (100.0, "duty"))
    check_eq("energy: Heater 偏差", row["delta_kw"], 0.0)
    check_eq("energy: Heater 状态沿用注入判定", row["state"], balance_rules.OK)

    # ② 泵:机械功类,轴功率进汇总
    blocks = [{"name": "P0201", "type": "Pump", "power_kw": 50.0}]
    res = _energy("P0201", balance_rules.OK, dh=50.0, ref=50.0, kind="power")
    rep = energy_rules.build(
        blocks,
        [{"name": "0201", "enthalpy_flow_kw": 1.0}, {"name": "0202", "enthalpy_flow_kw": 51.0}],
        _mb_conns(("P0201", "in", "0201"), ("P0201", "out", "0202")),
        res,
    )
    check_eq("energy: 泵对照值取轴功率", rep["devices"][0]["dev_kind"], "power")
    check_eq("energy: 泵轴功进汇总", rep["summary"]["shaft_kw"], 50.0)

    # ③ HeatX:冷热两侧各一行,热侧不重复判
    blocks = [{"name": "EX0101", "type": "HeatX", "duty_kw": 1600.0}]
    streams = [
        {"name": "h1", "enthalpy_flow_kw": 5200.0},
        {"name": "h2", "enthalpy_flow_kw": 3600.0},
        {"name": "c1", "enthalpy_flow_kw": 800.0},
        {"name": "c2", "enthalpy_flow_kw": 2400.0},
    ]
    conns = _mb_conns(
        ("EX0101", "in", "h1", "hot"),
        ("EX0101", "out", "h2", "hot"),
        ("EX0101", "in", "c1", "cold"),
        ("EX0101", "out", "c2", "cold"),
    )
    res = _energy("EX0101", balance_rules.OK, dh=1600.0, ref=1600.0, kind="hx_cold")
    rep = energy_rules.build(blocks, streams, conns, res)
    cold, hot = rep["devices"]
    check_eq("energy: HeatX 出两行", len(rep["devices"]), 2)
    check_eq("energy: HeatX 冷侧行", (cold["side"], cold["dh_kw"]), ("冷", 1600.0))
    check_eq(
        "energy: HeatX 冷侧的对照值也是热负荷(HX_DUTY)",
        (cold["dev_kw"], cold["dev_kind"]),
        (1600.0, "duty"),
    )
    check_eq("energy: HeatX 冷侧合计只算冷侧", (cold["h_in_kw"], cold["h_out_kw"]), (800.0, 2400.0))
    check_eq("energy: HeatX 热侧不判 ΔH", hot["dh_kw"], None)
    check_eq("energy: HeatX 热侧状态", hot["state"], balance_rules.SKIP)
    check("energy: HeatX 热侧说明点明 −冷侧", "冷侧" in hot["note"])
    check_eq("energy: HeatX 热侧合计只算热侧", (hot["h_in_kw"], hot["h_out_kw"]), (5200.0, 3600.0))

    # ④ 塔:焓平衡不校验,负荷走明细表
    blocks = [
        {"name": "T0101", "type": "RadFrac", "cond_duty_kw": -4809.23, "reb_duty_kw": 4950.80}
    ]
    res = _energy("T0101", balance_rules.SKIP, note="塔不校验")
    tower = energy_rules.tower_duties(blocks, {})
    check_eq("energy: 塔负荷优先取块字段", tower["T0101"]["source"], "block")
    rep = energy_rules.build(blocks, [], [], res, tower)
    check_eq("energy: 塔也出设备行(附塔负荷)", len(rep["devices"]), 1)
    check_eq("energy: 塔状态沿用不校验", rep["devices"][0]["state"], balance_rules.SKIP)
    check_eq("energy: 塔负荷明细冷凝器", rep["towers"][0]["cond_kw"], -4809.23)
    check_eq("energy: 塔负荷进汇总(再沸)", rep["summary"]["tower_reb_kw"], 4950.80)

    # ⑤ 塔负荷回退:没有块字段就用逐级首/末级(部分冷凝器只算近似)
    blocks = [{"name": "T1", "type": "RadFrac"}]
    stages = {
        "T1": [
            {"stage": 1, "duty_kw": -100.0},
            {"stage": 2, "duty_kw": 0.0},
            {"stage": 3, "duty_kw": 120.0},
        ]
    }
    tower = energy_rules.tower_duties(blocks, stages)
    check_eq(
        "energy: 塔负荷回退逐级", (tower["T1"]["cond_kw"], tower["T1"]["reb_kw"]), (-100.0, 120.0)
    )
    check_eq("energy: 塔负荷回退来源标逐级", tower["T1"]["source"], "stage")
    tower = energy_rules.tower_duties([{"name": "T2", "type": "RadFrac"}], {})
    check_eq("energy: 无块字段也无剖面 → 留空", tower["T2"]["cond_kw"], None)
    check_eq(
        "energy: 非塔设备不进负荷明细",
        energy_rules.tower_duties([{"name": "E1", "type": "Heater"}], {}),
        {},
    )

    # ⑥ 汇总:正/负 duty + 塔 + 轴功;反应器反应热不计入总量
    blocks = [
        {"name": "E1", "type": "Heater", "duty_kw": 100.0},
        {"name": "E2", "type": "Heater", "duty_kw": -200.0},
        {"name": "P1", "type": "Pump", "power_kw": 50.0},
        {"name": "R1", "type": "RPlug", "duty_kw": 7337.97},
        {"name": "T1", "type": "RadFrac", "reb_duty_kw": 500.0, "cond_duty_kw": -400.0},
    ]
    res = {}
    res.update(_energy("E1", balance_rules.OK, dh=100.0, ref=100.0, kind="duty"))
    res.update(_energy("E2", balance_rules.OK, dh=-200.0, ref=-200.0, kind="duty"))
    res.update(_energy("P1", balance_rules.OK, dh=50.0, ref=50.0, kind="power"))
    res.update(_energy("R1", balance_rules.SKIP))
    res.update(_energy("T1", balance_rules.SKIP))
    rep = energy_rules.build(blocks, [], [], res, energy_rules.tower_duties(blocks, {}))
    s = rep["summary"]
    check_eq("energy: 汇总 正 duty", s["heat_pos_kw"], 100.0)
    check_eq("energy: 汇总 负 duty", s["cool_neg_kw"], -200.0)
    check_eq("energy: 汇总 总加热含再沸器", s["heat_total_kw"], 600.0)
    check_eq("energy: 汇总 总冷却含冷凝器", s["cool_total_kw"], -600.0)
    check_eq("energy: 汇总 轴功", s["shaft_kw"], 50.0)
    check_eq("energy: 汇总 反应器反应热单独记,不进总量", s["reaction_net_kw"], 7337.97)
    check_eq("energy: 汇总 塔数", s["tower_count"], 1)

    # ⑦ 缺焓值:合计留空,状态沿用注入判定(不自己补 0)
    blocks = [{"name": "E3", "type": "Heater", "duty_kw": 10.0}]
    res = _energy("E3", balance_rules.NO_DATA, note="流股没焓值")
    rep = energy_rules.build(blocks, [], [], res)
    row = rep["devices"][0]
    check_eq("energy: 缺焓值 → 进口合计留空", row["h_in_kw"], None)
    check_eq("energy: 缺焓值 → 状态沿用无法校验", row["state"], balance_rules.NO_DATA)

    # ⑧ 分组按设备家族,顺序与 block_family 一致
    blocks = [
        {"name": "T1", "type": "RadFrac"},
        {"name": "E1", "type": "Heater"},
        {"name": "M1", "type": "Mixer"},
    ]
    rep = energy_rules.build(blocks, [], [], {})
    check_eq(
        "energy: 分组顺序按家族表", [g["key"] for g in rep["groups"]], ["column", "heat", "mix"]
    )
