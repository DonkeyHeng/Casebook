"""读取层:单位表、节点树夹具回放、组成与组分字典。

用例正文原样搬自 `tools/selftest.py`(迁移到 pytest 时只改了位置)。
"""

from __future__ import annotations

from app.aspen import reader
from tests._harness import (
    REPO_ROOT,
    check,
    check_close,
    check_eq,
)
from tools import fake_session


def test_units() -> None:
    """units:四种实测出现过的泵字段写法都要认(泵的扬程/密度全靠它)。"""
    from app.aspen import units as u

    cases = [
        ("length", 41.54, "meter", 41.54),  # METCBAR
        ("length", 12.7728155, "m-kgf/kg", 12.7728155),  # MET
        ("length", 1000.0, "ft-lbf/lbm", 304.8),  # ENG(按定义推的,未实测)
        ("molar_density", 47.86, "kmol/cum", 47.86),  # METCBAR
        ("molar_density", 0.027296226, "mol/cc", 27.296226),  # MET
        ("volume_flow", 465.043291, "l/min", 27.90259746),
        ("volume_flow", 9.68000453, "cum/hr", 9.68000453),
        ("pressure", 3.95, "bar", 3.95),
    ]
    for quantity, value, unit, want in cases:
        got = u.convert(quantity, value, unit)
        check(
            f"单位:{quantity} {unit}",
            got is not None and abs(got - want) < 1e-6,
            f"期望 {want},实际 {got}",
        )

    check_eq("单位:不认识的单位返回 None", u.convert("length", 1.0, "furlong"), None)
    check_eq("单位:值缺失返回 None", u.convert("length", None, "meter"), None)

    # 泵字段单位认不出来时,unit_gaps 必须报出来(否则泵页会静默变空)
    fake = [
        {
            "name": "P1",
            "type": "Pump",
            "head_m": None,
            "flow_m3h": None,
            "dp_bar": None,
            "rho_kgm3": None,
            "raw": {"HEAD_CAL": {"value": 1.0, "unit": "furlong"}},
        }
    ]
    gaps = reader.unit_gaps([], fake)
    check("单位缺口:报出泵字段", gaps["count"] == 1, f"实际 {gaps}")
    check("单位缺口:带上原始单位", gaps["units"] == ["furlong"], f"实际 {gaps['units']}")


def test_fixtures() -> None:
    """五类边界夹具:没有塔 / 没有泵 / 未知类型 / 泵缺流股 / NPSHₐ 为负。

    这些分支用真实工况一次都撞不上(现有 4 份模型都有塔有泵、全单相),
    所以只能造 —— 用假 dict 喂纯函数,或者在副本库上删一条连接。
    """
    from pathlib import Path

    from app.design import block_family as fam
    from app.design import pump as pump_rules
    from app.export import aipack
    from app.store import db
    from tools import repair_cases as repair

    # ① 没有塔:默认家族要落到第一个非空,而不是「塔」显示 0 行
    check_eq("夹具① 无塔:全泵模型落「泵」", fam.pick_default([{"type": "Pump"}]), "pump")
    check_eq(
        "夹具① 无塔:换热+泵落「换热」",
        fam.pick_default([{"type": "Heater"}, {"type": "Pump"}]),
        "heat",
    )

    # ② 没有泵:导出不该生成 pumps.csv
    check_eq("夹具② 无泵:has_pumps(空)", aipack.has_pumps([]), False)
    check_eq("夹具② 无泵:只有换热器", aipack.has_pumps([{"type": "Heater"}]), False)
    check("夹具② 无泵:有泵才生成", aipack.has_pumps([{"type": "Pump"}]))
    check_eq(
        "夹具② 无泵:空表只有表头",
        len(aipack._pumps_csv([]).decode("utf-8-sig").strip().splitlines()),
        1,
    )

    # ③ 未知类型:落「其他」,不能把设备丢掉
    check_eq("夹具③ 未知类型落「其他」", fam.family_of("Welding"), "other")
    check_eq("夹具③ 未知类型进计数", fam.counts([{"type": "Welding"}]), {"other": 1})

    # ④ 泵缺进出口流股:压力取不到、回填不能炸(副本库上真跑一次)
    scratch = Path("data/preview/_selftest-fixture.db")
    scratch.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = db.open_db(str(scratch))
        cid = db.save_case(
            conn,
            {
                "model": {"path": "x.apw", "name": "x.apw", "read_at": "2026-11-08 00:00:00"},
                "convergence": {},
                "streams": [],
                "columns": [],
                "connections": [],
                "blocks": [
                    {
                        "name": "P1",
                        "type": "Pump",
                        "power_kw": 10.0,
                        "status": 0,
                        "message": "",
                        "raw": {},
                        "balance": {},
                    }
                ],
                "stats": {
                    "stream_count": 0,
                    "block_count": 1,
                    "column_count": 0,
                    "connection_count": 0,
                    "elapsed_sec": 0,
                },
                "unit_gaps": {},
            },
        )
        case = dict(db.get_case(conn, cid))
        case["errtxt"] = case.get("errtxt") or ""
        result = repair.repair_case(conn, case, apply=False)
        row = dict(db.read_blocks(conn, cid)[0])
        check("夹具④ 缺流股:回填不抛异常", isinstance(result.get("changed"), list))
        check_eq("夹具④ 缺流股:p_in 仍为空", row["p_in_bar"], None)
        check_eq("夹具④ 缺流股:p_out 仍为空", row["p_out_bar"], None)
        conn.close()
    finally:
        try:
            scratch.unlink(missing_ok=True)
        except OSError:
            pass

    # ⑤ p_进口 < p_v:NPSHₐ 必须是负值(说明会汽蚀),不能吞成 None 或 0
    npsh = pump_rules.npsh_available_m(0.5, 1.5, 950.0)
    check("夹具⑤ NPSHₐ 为负", npsh is not None and npsh < 0, f"实际 {npsh}")


def test_reader_fixture() -> None:
    r"""read_case() 端到端回放:整棵节点树来自 `tools/fixtures/` 的脱敏 JSON。

    为什么不用单路径桩:reader 踩过的坑几乎都是"跨字段"的 —— V11 的 PBUB 少一层,
    单看一条路径没有异常,后果是泵页 p_v 整列静默变空。所以这里回放整棵树,
    从流股表一路查到焓平衡、单位缺口(unit_gaps)与组分字典。

    夹具来源见 `tools/fixtures/README.md`。断言分三类:
      1. 换算口径:夹具故意用 t/hr、kPa、sqft、ft、kcal/hr 这些非目标单位;
      2. 结构口径:目录下钻(HMX / PBUB)、PL 组分叶子不当读数、V11 布局;
      3. 夹具自身的体检:死数据、reader 依赖的路径是否齐全。
    """
    tree = fake_session.load("v15-typical")
    session = fake_session.FakeSession(tree)
    case = reader.read_case(session)

    # --- 规模与结构
    check_eq("夹具:流股 6 条", len(case["streams"]), 6)
    check_eq("夹具:设备 3 台", len(case["blocks"]), 3)
    check_eq("夹具:塔剖面 1 座", len(case["columns"]), 1)
    check_eq("夹具:连接行 9 条", len(case["connections"]), 9)
    check_eq("夹具:流股类型读的是属性 6", case["streams"][0]["stream_type"], "MATERIAL")
    check_eq("夹具:设备类型读的是属性 6", case["blocks"][0]["type"], "RadFrac")

    streams = {s["name"]: s for s in case["streams"]}
    blocks = {b["name"]: b for b in case["blocks"]}

    # --- 换算口径:同一份数据用非目标单位给出,必须换成项目固定单位
    s01 = streams["S01"]
    check_close("夹具:S01 温度(85 C 原样)", s01["temperature_c"], 85.0)
    check_close("夹具:S01 压力(120 kPa → 1.2 bar)", s01["pressure_bar"], 1.2)
    check_close("夹具:S01 质量流量(12 t/hr → 12000 kg/h)", s01["mass_flow_kgh"], 12000.0)
    check_close("夹具:S01 体积流量(cum/hr 原样)", s01["vol_flow_m3h"], 12.5)
    check_close("夹具:S01 质量焓(HMX_MASS\\MIXED,J/kg → kJ/kg)", s01["enthalpy_kjkg"], -15.6)
    check_close("夹具:S01 摩尔焓(HMX\\MIXED → kJ/kmol)", s01["enthalpy_kjkmol"], -312.0)
    check_close("夹具:S01 能量流由 m·h 现算(-52 kW)", s01["enthalpy_flow_kw"], -52.0)
    check_eq("夹具:S01 上游为空、下游是 HX1", (s01["source"], s01["destination"]), ("", "HX1"))

    hx = blocks["HX1"]
    check_close("夹具:HX1 热负荷(1000 kcal/hr → 1.163 kW)", hx["duty_kw"], 1.163)
    check_close("夹具:HX1 面积(100 sqft → 9.290304 m²)", hx["area_m2"], 9.290304)
    check("夹具:HX1 空着的 HX_AREAC 不挡后面的 HX_AREAC2", hx["area_m2"] is not None)

    col = blocks["COL1"]
    check_eq("夹具:塔理论级数(Input\\NSTAGE)", col["nstage"], 5)
    check_eq("夹具:塔顶/底温度取首末级", (col["top_temp_c"], col["bottom_temp_c"]), (95.0, 130.0))
    check_close("夹具:冷凝器负荷(cal/sec → kW,保持负号)", col["cond_duty_kw"], -5.02416)
    check_close("夹具:再沸器负荷(cal/sec → kW)", col["reb_duty_kw"], 5.44284)
    check_eq("夹具:塔板剖面 5 级", len(case["columns"][0]["stages"]), 5)

    pump = blocks["P01"]
    check_close("夹具:泵扬程(100 ft → 30.48 m)", pump["head_m"], 30.48)
    check_close("夹具:泵压升(IN_PRES 是 ΔP)", pump["dp_bar"], 3.2)
    check_close("夹具:泵 p_v(V15:PBUB\\MIXED\\TOTAL)", pump["pv_bar"], 0.53383137)
    check_close("夹具:泵粘度(MUMX\\MIXED\\TOTAL, cP 原样)", pump["mu_cp"], 0.483)
    check_close("夹具:泵密度优先用进口 m/V(7000/8)", pump["rho_kgm3"], 875.0)
    check_close("夹具:泵效率(无量纲原样)", pump["pump_eff"], 0.72)
    check_close("夹具:泵进口压力来自进口流股", pump["p_in_bar"], 2.0)
    check_close("夹具:泵出口压力来自出口流股", pump["p_out_bar"], 5.2)

    # --- 收敛判定与撕裂流
    conv = case["convergence"]
    check_eq("夹具:收敛判定为 ok", conv["severity"], "ok")
    check_eq("夹具:撕裂流读到 1 条", len(conv["tears"]), 1)
    check_eq(
        "夹具:撕裂流字段按数组下标对齐",
        (conv["tears"][0]["stream"], conv["tears"][0]["error"], conv["tears"][0]["tolerance"]),
        ("S02", 0.0012, 0.01),
    )

    # --- 焓平衡:夹具数值按热平衡反算,所以三台设备都该通过
    states = {name: row["balance_state"] for name, row in case["balance"].items()}
    check_eq("夹具:焓平衡三台设备全部 ok", states, {"COL1": "ok", "HX1": "ok", "P01": "ok"})

    # --- 单位诊断与完整性
    check_eq("夹具:没有单位认不出导致的缺口", case["unit_gaps"]["count"], 0)
    check_close("夹具:流股数值齐全(coverage=1)", reader.value_coverage(case["streams"]), 1.0)

    # --- 组分字典:COM 树里能拿到 CAS 时就不该退回解析模型文件
    meta = {row["component"]: row for row in case["component_meta"]}
    check_eq("夹具:组分清单来自流股 X", sorted(case["components"]), ["ETHANOL", "WATER"])
    check_eq(
        "夹具:组分 CAS 与数据库名",
        (meta["WATER"]["cas"], meta["ETHANOL"]["cas"], meta["WATER"]["dbname"]),
        ("7732-18-5", "64-17-5", "PURE"),
    )

    # --- 组成(只有在 include_composition=True 时才读)
    composed_session = fake_session.FakeSession(tree)
    composed = reader.read_case(composed_session, include_composition=True)
    water = next(r for r in composed["streams"][0]["composition"] if r["component"] == "WATER")
    check_eq("夹具:组成按组分展开", (water["x_liq"], water["mass_flow_kgh"]), (0.6, 7200.0))

    # --- PL 是目录节点:只有 PL、没有 PBUB 的流股不能读出 p_v
    check_eq(
        "夹具:PL 的组分分压不当混合物 p_v",
        reader.read_stream_vapor_pressure(session, "S03"),
        None,
    )

    # --- 夹具自身体检:①有没有"采了但没人读"的死数据(按最全的那一轮算:
    # 组成要 include_composition=True 才读,拿默认那一轮会把 X / Y / MASSFLOW3 误判成死数据)
    unused = composed_session.unused_outside(list(tree["meta"].get("unread_ok") or []))
    check("夹具:没有 allowlist 之外的死数据", not unused, "未读:" + "、".join(unused[:3]))

    # ②reader 依赖的路径在夹具里是否齐全(防路径改名后夹具悄悄失效)
    fence = fake_session.FakeSession(tree)
    missing = []
    for stream in case["streams"]:
        for _key, field, _quantity in reader.STREAM_FIELDS:
            path = rf"\Data\Streams\{stream['name']}\Output\{field}"
            if fence.find(path) is None:
                missing.append(path)
    run_base = r"\Data\Results Summary\Run-Status\Output"
    missing += [
        f"{run_base}\\{key}"
        for key in reader.RUN_STATUS_FIELDS
        if fence.find(f"{run_base}\\{key}") is None
    ]
    check("夹具:reader 依赖的流股/状态字段齐全", not missing, "缺:" + "、".join(missing[:3]))

    # --- 变异(V11 布局):PBUB 下没有 TOTAL 层,真值在 MIXED\LIQUID
    v11 = fake_session.load("v11-pump")
    v11_session = fake_session.FakeSession(v11)
    v11_case = reader.read_case(v11_session)
    v11_pump = v11_case["blocks"][0]
    check(
        "夹具 V11:确实没有 PBUB\\MIXED\\TOTAL 这一层",
        v11_session.find(r"\Data\Streams\S01\Output\STRM_UPP\PBUB\MIXED\TOTAL") is None,
    )
    check_close("夹具 V11:p_v 从 MIXED\\LIQUID 读到", v11_pump["pv_bar"], 0.1875)
    check_close("夹具 V11:粘度在 MIXED 叶子上(MUMX 无 TOTAL 层)", v11_pump["mu_cp"], 1.15)
    check_close(
        "夹具 V11:HMX_FLOW 有值时直接用,不再用 m·h 现算",
        v11_case["streams"][0]["enthalpy_flow_kw"],
        -35.1111,
    )

    # --- 变异:单点故障不能让整份工况崩
    fahrenheit = reader.read_case(
        fake_session.FakeSession(
            fake_session.patch(tree, r"\Data\Streams\S01\Output\RES_TEMP", value=185.0, unit="F")
        )
    )
    check_close(
        "夹具变异:单位换成 ℉ 仍按量纲换算(185 F → 85 C)",
        fahrenheit["streams"][0]["temperature_c"],
        85.0,
    )

    broken = reader.read_case(
        fake_session.FakeSession(
            fake_session.patch(tree, r"\Data\Streams\S01\Output\RES_PRES", err="COM 忙")
        )
    )
    check_eq(
        "夹具变异:取值抛异常时该字段留空、不中断读取", broken["streams"][0]["pressure_bar"], None
    )
    check_eq("夹具变异:其它流股照旧读出", len(broken["streams"]), 6)

    no_comp = reader.read_case(
        fake_session.FakeSession(fake_session.drop_subtree(tree, r"\Data\Streams\S01\Output\X"))
    )
    check_eq("夹具变异:流股没有 X 时组分清单留空", no_comp["components"], [])
    check_eq("夹具变异:没有组分名时组分字典留空", no_comp["component_meta"], [])

    no_btemp = reader.read_case(
        fake_session.FakeSession(
            fake_session.drop_subtree(tree, r"\Data\Blocks\COL1\Output\B_TEMP")
        )
    )
    check_eq("夹具变异:塔没有 B_TEMP 时顶/底温度留空", no_btemp["blocks"][0]["top_temp_c"], None)
    check_eq("夹具变异:塔没有 B_TEMP 时不出剖面", len(no_btemp["columns"]), 0)


def test_reader_fixture_real() -> None:
    r"""真机采集的夹具回放(2026-11-18 采自作者自己的全流程模型)。

    与 `test_reader_fixture()` 的分工:那份是**手写**的,数值按热平衡反算,用来钉
    换算口径;这份是**真机采集**的,结构、单位集、异常状态都来自实际模型,用来钉
    "真实 Aspen 树叶子的形状"(含 45 级塔、HeatX 双侧、泵的 STRM_UPP\PBUB\MUMX)。

    断言一律**按类型定位**,不按名字:采集时流股/设备名已被改写成 S01…/B01…,
    换成另一个模型重采后名字会变,类型不会。

    这份样本取自一个**未收敛**的工况(判定 error),所以还多守一条:
    判定为 error 时数值仍要读全,不能因为状态差就把表读空。
    """

    tree = fake_session.load("v15-real")
    session = fake_session.FakeSession(tree)
    case = reader.read_case(session, include_composition=True)

    check_eq("真机夹具:来源标记为 collector", tree["meta"].get("source"), "collector")
    check("真机夹具:采集没有被节点上限截断", tree["meta"].get("truncated") is False)
    check("真机夹具:体积在扫描阈值内", len(tree["nodes"]) < 2000, f"{len(tree['nodes'])} 节点")
    check_eq("真机夹具:流股 8 条", len(case["streams"]), 8)
    check_eq("真机夹具:设备 3 台", len(case["blocks"]), 3)

    by_type = {b["type"]: b for b in case["blocks"]}
    check_eq("真机夹具:设备类型齐全", sorted(by_type), ["HeatX", "Pump", "RadFrac"])

    # --- 数值完整性:模型判定 error,但表不能是空的
    check_close("真机夹具:流股数值覆盖率 100%", reader.value_coverage(case["streams"]), 1.0)
    check_eq("真机夹具:没有单位认不出的缺口", case["unit_gaps"]["count"], 0)
    no_temp = [s["name"] for s in case["streams"] if s["temperature_c"] is None]
    check("真机夹具:8 条流股都读到温度", not no_temp, "缺:" + "、".join(no_temp))
    no_enth = [s["name"] for s in case["streams"] if s["enthalpy_flow_kw"] is None]
    check("真机夹具:8 条流股都读到能量流", not no_enth, "缺:" + "、".join(no_enth))

    # 判定本身:这份样本就是 CVSTAT=1 的未收敛工况(见 samples/README.md)。
    # 换模型重采时这一条要跟着改,否则它会红。
    check_eq("真机夹具:判定为 error", case["convergence"]["severity"], "error")
    check("真机夹具:error 有对应理由", bool(case["convergence"]["reasons"]))

    # --- 三台设备的读数(按类型取,不按名字)
    hx = by_type["HeatX"]
    check("真机夹具:HeatX 读到热负荷", hx["duty_kw"] is not None and hx["duty_kw"] > 0)
    check("真机夹具:HeatX 读到面积(HX_AREAC2)", hx["area_m2"] is not None)
    cold = [r for r in case["connections"] if r["block"] == hx["name"] and r["side"] == "cold"]
    hot = [r for r in case["connections"] if r["block"] == hx["name"] and r["side"] == "hot"]
    check("真机夹具:HeatX 冷/热侧都识别出来", bool(cold) and bool(hot))

    pump = by_type["Pump"]
    check_close("真机夹具:泵扬程换算成 m", round(pump["head_m"], 4), 12.7728)
    check("真机夹具:泵 p_v 从 PBUB 读到", pump["pv_bar"] is not None and pump["pv_bar"] > 0)
    check("真机夹具:泵粘度从 MUMX 读到", pump["mu_cp"] is not None)
    check("真机夹具:泵密度由 m/V 现算", pump["rho_kgm3"] is not None)

    col = by_type["RadFrac"]
    check(
        "真机夹具:塔冷凝器负荷为负、再沸器为正",
        col["cond_duty_kw"] < 0 < col["reb_duty_kw"],
        f"cond={col['cond_duty_kw']} reb={col['reb_duty_kw']}",
    )
    check_eq("真机夹具:塔剖面按 45 级展开", len(case["columns"][0]["stages"]), 45)

    # --- 焓平衡:三台设备都该过(真机数据,不是构造值)
    states = {row["balance_state"] for row in case["balance"].values()}
    check_eq("真机夹具:三台设备焓平衡全部 ok", states, {"ok"})

    # --- 连接关系:既要有内部连接,也要容忍指向夹具外的设备(模型边界)
    outside = [
        r for r in case["connections"] if r["block"] not in {b["name"] for b in case["blocks"]}
    ]
    check("真机夹具:连接行 ≥ 8 条", len(case["connections"]) >= 8, str(len(case["connections"])))
    check("真机夹具:指向夹具外设备的连接不丢", bool(outside), f"{len(outside)} 条")

    # --- 组成与组分字典
    check_eq("真机夹具:组分清单来自流股 X", len(case["components"]), 18)
    check_eq("真机夹具:组分字典逐条给出", len(case["component_meta"]), 18)
    check(
        "真机夹具:COM 树里没有 CAS(与 aspen-field-map §3.1 一致,靠模型文件兜底)",
        all(row["cas"] is None for row in case["component_meta"]),
    )

    # --- 采集白名单的栅栏:reader 依赖的路径一条都不能漏
    fence = fake_session.FakeSession(tree)
    missing = []
    for stream in case["streams"]:
        for _key, field, _quantity in reader.STREAM_FIELDS:
            path = rf"\Data\Streams\{stream['name']}\Output\{field}"
            if fence.find(path) is None:
                missing.append(path)
    check("真机夹具:白名单覆盖 reader 依赖的字段", not missing, "缺:" + "、".join(missing[:3]))


def test_composition_replace() -> None:
    """组成写入是"整条替换":新结果组分更少时,旧组分不能留在库里。

    以前只做 INSERT OR REPLACE,fill_composition.py --force 重读出更少的组分后,
    多出来的旧组分仍留在库中,页面与 AI 导出会混用新旧两版。
    """
    import shutil

    from app.store import db

    root = REPO_ROOT
    src = root / "samples" / "case-23-error.db"
    if not src.is_file():
        check("组成替换: 跳过(仓库里没有内置示例库)", True)
        return
    tmp = root / "data" / "preview" / "_selftest-comp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, tmp / "c.db")
    conn = db.open_db(str(tmp / "c.db"))
    try:
        case_id = db.list_cases(conn)[0]["id"]
        stream = "SELFTEST-STREAM"
        before = conn.execute(
            "SELECT COUNT(*) FROM stream_components WHERE case_id=?", (case_id,)
        ).fetchone()[0]
        db.save_stream_composition(
            conn,
            case_id,
            stream,
            [
                {"component": "A", "x_liq": 0.5, "y_vap": None, "mass_flow_kgh": 1.0},
                {"component": "B", "x_liq": 0.3, "y_vap": None, "mass_flow_kgh": 1.0},
                {"component": "C", "x_liq": 0.2, "y_vap": None, "mass_flow_kgh": 1.0},
            ],
        )
        db.save_stream_composition(
            conn,
            case_id,
            stream,
            [{"component": "A", "x_liq": 1.0, "y_vap": None, "mass_flow_kgh": 2.0}],
        )
        left = [r["component"] for r in db.read_stream_composition(conn, case_id, stream)]
        check_eq("组成替换: 旧组分被清掉,只剩新的一条", left, ["A"])
        after = conn.execute(
            "SELECT COUNT(*) FROM stream_components WHERE case_id=?", (case_id,)
        ).fetchone()[0]
        check_eq("组成替换: 其它流股的行数不受影响", after, before + 1)
        db.save_stream_composition(conn, case_id, stream, [])
        check_eq("组成替换: 空结果也清空", db.read_stream_composition(conn, case_id, stream), [])
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_component_dict() -> None:
    """组分字典:CAS 归一、中文名查表、两行显示与 AI 包的兜底规则。

    盯的是三条约定(2026-11-15 定的):
      * 页面显示"中文名 + CAS",认不出来的只显示 Aspen 原名且不给悬浮;
      * AI 包组分列用 CAS,没有 CAS 才退回原名;
      * Word / Excel 两个出口保持 Aspen 原名,不受这里影响。
    """
    from app import component_names as names
    from app.api import composition
    from app.export import aipack

    # --- CAS 归一:只认 数字-数字-数字,别的东西一律当"没有"
    check_eq("组分字典: 正常 CAS 原样保留", names.clean_cas("504-63-2"), "504-63-2")
    check_eq("组分字典: 去前后空白", names.clean_cas("  64-19-7 "), "64-19-7")
    check_eq("组分字典: 带引号的 CAS 也认", names.clean_cas('"7732-18-5"'), "7732-18-5")
    check_eq("组分字典: 不是 CAS 的一律返回空", names.clean_cas("DIMHPA"), "")
    check_eq("组分字典: 只有两段的返回空", names.clean_cas("64-19"), "")
    check_eq("组分字典: 空值返回空", names.clean_cas(None), "")
    check_eq("组分字典: 带 CAS 前缀的也抠得出来", names.clean_cas("CAS 64-19-7"), "64-19-7")
    check_eq("组分字典: 带别名前缀的也抠得出来", names.clean_cas("乙酸 64-19-7"), "64-19-7")
    check_eq("组分字典: 抠不出就还是空", names.clean_cas("PSEUDOCOMPONENT-11"), "")

    # --- 查表
    check_eq("组分字典: 1,3-丙二醇", names.cn_name("504-63-2"), "1,3-丙二醇")
    check_eq("组分字典: 丙烯酸", names.cn_name("79-10-7"), "丙烯酸")
    check_eq("组分字典: 没收录的 CAS 查不到", names.cn_name("999-99-9"), "")
    check_eq("组分字典: 认不出时退回 Aspen 原名", names.label("", "DIMHPA"), "DIMHPA")
    check_eq("组分字典: 认得时用中文名", names.label("504-63-2", "PDO"), "1,3-丙二醇")

    # --- AI 包:有 CAS 用 CAS,没有用原名
    check_eq("组分字典: AI 包标识优先 CAS", names.cas_or_name("504-63-2", "PDO"), "504-63-2")
    check_eq("组分字典: AI 包无 CAS 退回原名", names.cas_or_name(None, "DIMHPA"), "DIMHPA")

    # --- 组成 payload 的装饰:cas / cn / cas_found
    payload = composition._payload(
        "S1",
        "case",
        [
            {"component": "PDO", "x_liq": 0.6, "y_vap": None},
            {"component": "DIMHPA", "x_liq": 0.4, "y_vap": None},
        ],
        meta={"PDO": {"cas": "504-63-2"}, "DIMHPA": {"cas": None}},
    )
    by_name = {c["component"]: c for c in payload["components"]}
    check_eq("组分字典: payload 带上 CAS", by_name["PDO"]["cas"], "504-63-2")
    check_eq("组分字典: payload 带上中文名", by_name["PDO"]["cn"], "1,3-丙二醇")
    check_eq("组分字典: 无 CAS 的组分为 None(页面据此不给悬浮)", by_name["DIMHPA"]["cas"], None)
    check_eq("组分字典: 无 CAS 的组分没有中文名", by_name["DIMHPA"]["cn"], None)
    check_eq("组分字典: cas_found 计数", payload["cas_found"], 1)
    check_eq("组分字典: cas_total 计数", payload["cas_total"], 2)

    # --- read_component_meta:组件树形状不同也要能取到(不需要 Aspen)
    #
    # 2026-11-15 的坑:\Data\Components 第一层是分组名(Attr-Comps / Comp-Lists …),
    # 不是组分。这里用假 session 把三种真实可能的结构各钉一条。
    from app.aspen import reader as reader_mod

    def _Session(tree):
        """`{路径: 值}` 的极简夹具:值直接给,单位一律当空。"""
        return fake_session.FakeSession(
            {"case": "inline-comp", "nodes": {p: {"v": v} for p, v in tree.items()}}
        )

    # 形状一:CAS / DBNAME1 直接挂在 分组\组分 下
    tree_direct = {
        r"\Data\Components\Attr-Comps\PDO\CAS": "504-63-2",
        r"\Data\Components\Attr-Comps\PDO\DBNAME1": "1,3-PROPANEDIOL",
    }
    meta = reader_mod.read_component_meta(_Session(tree_direct), ["PDO"])
    check_eq("组分字典: 直接挂在分组下能读到 CAS", meta[0]["cas"], "504-63-2")
    check_eq("组分字典: 同时读到数据库名", meta[0]["dbname"], "1,3-PROPANEDIOL")

    # 形状二:CAS 藏在下一层(如 Alias\CAS),父节点换成 Comp-Lists
    tree_nested = {
        r"\Data\Components\Comp-Lists\AC\Alias\CAS": "CAS 64-19-7",
        r"\Data\Components\Comp-Lists\AC\DBName": "ACETIC-ACID",
    }
    meta = reader_mod.read_component_meta(_Session(tree_nested), ["AC"])
    check_eq("组分字典: 嵌一层的 CAS 也找得到", meta[0]["cas"], "64-19-7")
    check_eq("组分字典: 字段名不分大小写", meta[0]["dbname"], "ACETIC-ACID")

    # 形状三:自定义组分没有 CAS —— 如实留空,不能编一个
    tree_missing = {r"\Data\Components\Attr-Comps\DIMHPA\Type": "CONV"}
    meta = reader_mod.read_component_meta(_Session(tree_missing), ["DIMHPA"])
    check_eq("组分字典: 没有 CAS 就留空", meta[0]["cas"], None)

    # 组分节点完全找不到时不能抛异常
    meta = reader_mod.read_component_meta(_Session({}), ["GHOST"])
    check_eq("组分字典: 找不到组分节点不崩", meta[0]["cas"], None)

    # 分组名绝不能被当成组分(回归:2026-11-15 曾把 14 个分组写进库)
    tree_group = {r"\Data\Components\Attr-Comps\PDO\CAS": "504-63-2"}
    meta = reader_mod.read_component_meta(_Session(tree_group), ["PDO"])
    check_eq("组分字典: 只按传入的组分名读", [m["component"] for m in meta], ["PDO"])

    # --- AI 包 composition.csv:组分列是 CAS
    csv_text = aipack._composition_csv(
        [{"stream": "S1", "component": "PDO", "x_liq": 0.6, "y_vap": 0.0}],
        {"PDO": "504-63-2"},
    ).decode("utf-8-sig")
    check("组分字典: AI 包 composition.csv 用 CAS", "504-63-2" in csv_text, csv_text[:120])
    check("组分字典: AI 包 composition.csv 不再出现 Aspen 原名 PDO", "PDO" not in csv_text)
    csv_fallback = aipack._composition_csv(
        [{"stream": "S1", "component": "DIMHPA", "x_liq": 0.4, "y_vap": 0.0}],
        {},
    ).decode("utf-8-sig")
    check("组分字典: AI 包无 CAS 时保留原名", "DIMHPA" in csv_fallback, csv_fallback[:120])

    # --- 模型文件兜底:COMPONENTS MAIN + DSET CASN 位置配对(不需要 Aspen)
    from app.aspen import bkp_components

    sample = (
        "COMPONENTS MAIN ? \\ COMPONENTS CID = H2O ANAME = H2O OUTNAME = H2O DBNAME1 = \n"
        '"WATER" ANAME1 = "H2O" /  CID = DIMHPA OUTNAME = DIMHPA /  CID = PDO ANAME = \n'
        'C3H8O2-3 OUTNAME = PDO DBNAME1 = "1,3-PROPANEDIOL" ANAME1 = "C3H8O2-3" \\ ? '
        'COMPONENTS "ADA/PCS" "LE-PROP-DATA" ? ; "METCHEM_MOLE" ; ? \n'
        "IDSET CASN (PROPERTIES) (37100 *6)\n"
        'DSET CASN CASN @CASN ( "7732-18-5" "" "504-63-2" )\n'
    )
    parsed = bkp_components.parse_text(sample)
    check_eq("模型文件: 组分数", len(parsed), 3)
    check_eq("模型文件: 顺序与定义一致", [p["component"] for p in parsed], ["H2O", "DIMHPA", "PDO"])
    check_eq("模型文件: 第 1 个 CAS", parsed[0]["cas"], "7732-18-5")
    check_eq("模型文件: 自定义组分留空", parsed[1]["cas"], None)
    check_eq("模型文件: 数据库名", parsed[2]["dbname"], "1,3-PROPANEDIOL")
    check_eq("模型文件: 无 CASN 段时不报错", bkp_components.parse_text("COMPONENTS MAIN ?"), [])
    check_eq("模型文件: 空文本返回空", bkp_components.parse_model_file(""), [])

    # 段头被换行拆开(实测某个 .bkp 是 "COMPONENTS \r\nMAIN")
    split_sample = sample.replace("COMPONENTS MAIN", "COMPONENTS \nMAIN")
    check_eq(
        "模型文件: 段头被换行拆开也能读",
        [p["component"] for p in bkp_components.parse_text(split_sample)],
        ["H2O", "DIMHPA", "PDO"],
    )

    # .apw 是二进制、没有文本组分段;同目录有同名 .bkp 时必须自动回退到它
    import shutil as _shutil

    root0 = REPO_ROOT
    sibling = root0 / "data" / "preview" / "_selftest-bkp-apw"
    _shutil.rmtree(sibling, ignore_errors=True)
    sibling.mkdir(parents=True, exist_ok=True)
    try:
        (sibling / "m.apw").write_bytes(b"\x00\x01binary-no-text")
        (sibling / "m.bkp").write_text(split_sample, encoding="latin-1")
        rows = bkp_components.parse_model_file(str(sibling / "m.apw"))
        check_eq("模型文件: .apw 读不到时回退同名 .bkp", len(rows), 3)
        check_eq("模型文件: 回退后 CAS 正常", rows[2]["cas"], "504-63-2")
    finally:
        _shutil.rmtree(sibling, ignore_errors=True)

    # COM 一个 CAS 都读不到时,必须自动翻模型文件(端到端,不需要 Aspen)
    # 落在 data/preview 下而不是系统临时目录:沙箱对系统 temp 的删除会拒绝(实测 WinError 5)
    import shutil

    tmpdir = REPO_ROOT / "data" / "preview" / "_selftest-bkp"
    shutil.rmtree(tmpdir, ignore_errors=True)
    tmpdir.mkdir(parents=True, exist_ok=True)
    try:
        model = tmpdir / "m.bkp"
        model.write_text(sample, encoding="latin-1")
        sess = _Session({})
        sess.model_path = str(model)
        meta = reader_mod.read_component_meta(sess, ["H2O", "DIMHPA", "PDO"])
        check_eq(
            "模型文件: COM 读不到时用文件兜底",
            [m["cas"] for m in meta],
            ["7732-18-5", None, "504-63-2"],
        )
        check_eq("模型文件: 兜底也带数据库名", meta[2]["dbname"], "1,3-PROPANEDIOL")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # --- components 表:整份替换 + 删工况时一起清
    import shutil

    from app.store import db

    root = REPO_ROOT
    src = root / "samples" / "case-23-error.db"
    if not src.is_file():
        check("组分字典: 表读写跳过(仓库里没有内置示例库)", True)
        return
    tmp = root / "data" / "preview" / "_selftest-compdict"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, tmp / "c.db")
    conn = db.open_db(str(tmp / "c.db"))
    try:
        case_id = db.list_cases(conn)[0]["id"]
        # 示例库现在带真实组分(CAS 是读到的),先清空,测的才是「老库刚建表、还没写过」
        conn.execute("DELETE FROM components WHERE case_id=?", (case_id,))
        conn.commit()
        check_eq("组分字典: 老库建表后默认是空的", db.read_components(conn, case_id), [])
        db.save_components(
            conn,
            case_id,
            [
                {"component": "PDO", "cas": "504-63-2", "dbname": "1,3-PROPANEDIOL"},
                {"component": "DIMHPA", "cas": None, "dbname": None},
            ],
        )
        got = {r["component"]: r["cas"] for r in db.read_components(conn, case_id)}
        check_eq("组分字典: 写入 2 条", len(got), 2)
        check_eq("组分字典: CAS 存住了", got.get("PDO"), "504-63-2")
        check_eq("组分字典: 无 CAS 的存成 NULL", got.get("DIMHPA"), None)
        db.save_components(conn, case_id, [{"component": "PDO", "cas": "504-63-2"}])
        check_eq(
            "组分字典: 整份替换后旧组分不残留",
            [r["component"] for r in db.read_components(conn, case_id)],
            ["PDO"],
        )
        db.delete_case(conn, case_id)
        check_eq("组分字典: 删工况时一起清掉", db.read_components(conn, case_id), [])
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)

    # --- 设计扬程修正项:ΔZ / Σhf 按模型+位号存,加法与 1.1 取整由前端做
    tmp3 = root / "data" / "preview" / "_selftest-pumpdesign"
    shutil.rmtree(tmp3, ignore_errors=True)
    tmp3.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, tmp3 / "c.db")
    conn = db.open_db(str(tmp3 / "c.db"))
    try:
        row0 = conn.execute("SELECT * FROM cases ORDER BY id LIMIT 1").fetchone()
        model = str(row0["model_name"] or "")
        check_eq("设计扬程: 默认没有值", db.read_pump_design(conn, model, "P0101"), None)
        db.save_pump_design(conn, model, "P0101", 9.45, 5.93)
        got = db.read_pump_design(conn, model, "P0101")
        check_eq("设计扬程: ΔZ 存住了", got["dz_m"], 9.45)
        check_eq("设计扬程: Σhf 存住了", got["hf_m"], 5.93)
        db.save_pump_design(conn, model, "P0101", 11.0, None)
        got = db.read_pump_design(conn, model, "P0101")
        check_eq("设计扬程: 可以只改一项", (got["dz_m"], got["hf_m"]), (11.0, None))
        db.save_pump_design(conn, model, "P0101", None, None)
        check_eq("设计扬程: 两项都清掉后不再返回", db.read_pump_design(conn, model, "P0101"), None)
    finally:
        conn.close()
        shutil.rmtree(tmp3, ignore_errors=True)

    # --- 手填 CAS:按模型存,只补空缺,不覆盖读到的值
    tmp2 = root / "data" / "preview" / "_selftest-cas"
    shutil.rmtree(tmp2, ignore_errors=True)
    tmp2.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, tmp2 / "c.db")
    conn = db.open_db(str(tmp2 / "c.db"))
    try:
        row0 = conn.execute("SELECT * FROM cases ORDER BY id LIMIT 1").fetchone()
        cid = row0["id"]
        mname = str(row0["model_name"] or "")
        db.save_components(
            conn,
            cid,
            [
                {"component": "H2O", "cas": "7732-18-5", "dbname": "WATER"},
                {"component": "DIMHPA", "cas": None, "dbname": None},
            ],
        )
        db.save_component_cas(conn, mname, "DIMHPA", "2134-29-4")
        db.save_component_cas(conn, mname, "H2O", "999-99-9")  # 不该盖掉读数
        merged = db.read_cas_map(conn, cid, mname)
        check_eq("手填 CAS: 补上空缺", merged.get("DIMHPA"), "2134-29-4")
        check_eq("手填 CAS: 不覆盖读到的值", merged.get("H2O"), "7732-18-5")

        meta = composition._read_meta(conn, row0)
        check_eq("手填 CAS: 来源标记 manual", meta["DIMHPA"]["source"], "manual")
        check_eq("手填 CAS: 读数来源标记 aspen", meta["H2O"]["source"], "aspen")
        payload = composition._payload(
            "S1", "case", [{"component": "DIMHPA", "x_liq": 0.1, "y_vap": None}], meta=meta
        )
        check_eq(
            "手填 CAS: payload 带 cas_source", payload["components"][0]["cas_source"], "manual"
        )

        db.save_component_cas(conn, mname, "DIMHPA", None)
        check_eq("手填 CAS: 清除后不再补", db.read_cas_map(conn, cid, mname).get("DIMHPA"), None)
    finally:
        conn.close()
        shutil.rmtree(tmp2, ignore_errors=True)
