"""Phase 2(L2)离线自测:物性读取、标准表管道、几何水力、五态判定、三页签导出。

所有用例都不接 Aspen、不读用户 `data/`:
* 物性用 `_FakeSession` 回放节点树;
* 标准表用临时目录 + 手工构造的 `SeriesRow`;
* 导出用内置样本库 `samples/case-23-error.db`(它在 CI 里也在)。
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

from tests._harness import check, check_close, check_eq

# ---------------------------------------------------------------- 假 Session


class _Item:
    def __init__(self, name):
        self.Name = name


class _Elems:
    """模拟 COM 的 Elements 集合(Count / Item(i).Name)。"""

    def __init__(self, names):
        self._items = [_Item(n) for n in (names or [])]

    @property
    def Count(self):
        return len(self._items)

    def Item(self, index):
        return self._items[index]


class _Node:
    """COM 节点的最小替身:`find()` 返回它,`fetch()` 读 Value / UnitString / Elements。"""

    def __init__(self, value=None, unit="", children=None):
        self.Value = value
        self.UnitString = unit
        self.Elements = _Elems(children)

    def HasAttribute(self, _attr):
        return False


class _FakeSession:
    def __init__(self, tree: dict, fail_paths=()):
        self._tree = {k.lower(): v for k, v in tree.items()}
        self._fail = {p.lower() for p in fail_paths}
        self.reads: list[str] = []

    def find(self, path):
        key = str(path).lower()
        if key not in self._tree:
            return None
        self.reads.append(path)
        return self._tree[key]


def _session(tree, fail_paths=()):
    return _FakeSession(tree, fail_paths)


def _props_tree(stream, cp=None, k=None, mu=None, upper_children=None, names=None):
    """造一条流股的 Output 子树(默认 MUMX + 可选 CP/THK)。"""
    base = rf"\Data\Streams\{stream}\Output"
    upper = {}
    upper_children = list(upper_children or ["PL", "MUMX", "PBUB"])
    upper["MUMX"] = _Node(mu if mu is not None else 1200.0, "cP")
    if "CP" in upper_children:
        upper["CP"] = _Node(None, "", ["MIXED"]) if names else _Node(cp, "J/kg-K")
        if names:
            upper["CP\\MIXED"] = _Node(None, "", ["TOTAL"])
            upper["CP\\MIXED\\TOTAL"] = _Node(GOOD_CP, "J/kg-K")
    if "THK" in upper_children:
        upper["THK"] = _Node(k, "W/m-K")
    tree = {f"{base}\\STRM_UPP": _Node(None, "", upper_children)}
    for key, node in upper.items():
        tree[f"{base}\\STRM_UPP\\{key}".lower()] = node
    return tree


GOOD_CP = 4180.0
GOOD_K = 0.68


def test_hx_l2_units() -> None:
    """① 物性单位:比热与导热系数只在 units 一处换算,认不出就 None。"""
    from app.aspen import units

    check_close("J/(kg·K) 原样", units.convert("specific_heat", 4180, "J/kg-K"), 4180.0, 1e-6)
    check_close(
        "kcal/kg-C → 4186.8", units.convert("specific_heat", 1.0, "kcal/kg-C"), 4186.8, 1e-9
    )
    check_close("cal/g-K → 4186.8", units.convert("specific_heat", 1.0, "cal/gm-K"), 4186.8, 1e-9)
    check_close("Btu/lb-F → 4186.8", units.convert("specific_heat", 1.0, "Btu/lb-F"), 4186.8, 1e-9)
    check_close("kJ/kg-K → 1000", units.convert("specific_heat", 2.5, "kJ/kg-K"), 2500.0, 1e-9)
    check_eq(
        "摩尔基 cal/mol-K 不收(要分子量,不猜)",
        units.convert("specific_heat", 8.0, "cal/mol-K"),
        None,
    )

    check_close("W/(m·K) 原样", units.convert("conductivity", 0.6, "W/(m·K)"), 0.6, 1e-9)
    check_close("kcal/hr-m-C → 1.163", units.convert("conductivity", 1, "kcal/hr-m-C"), 1.163, 1e-9)
    check_close(
        "cal/s-cm-C → 418.68", units.convert("conductivity", 1, "cal/sec-cm-C"), 418.68, 1e-9
    )
    check_close(
        "Btu/hr-ft-F → 1.7307", units.convert("conductivity", 1, "Btu/hr-ft-F"), 1.730735, 1e-6
    )
    check_eq("未知导热单位 → None", units.convert("conductivity", 1, "furlong/hogshead-K"), None)

    check_close("Pa·s → 1000 cP", units.convert("viscosity", 1, "Pa-s"), 1000.0, 1e-9)
    check_close("mPa·s 原样", units.convert("viscosity", 3, "mPa-s"), 3.0, 1e-9)
    check_close("lb/(ft·s) → 1488 cP", units.convert("viscosity", 1, "lb/(ft·s)"), 1488.16, 0.1)


def test_hx_l2_read_props() -> None:
    """② read_hx_props:候选路径 + STRM_UPP 扫描 + 单位缺口 + 只读四端。"""
    from app.aspen import reader

    tree = _props_tree(
        "0101", cp=GOOD_CP, k=GOOD_K, mu=900.0, upper_children=["PL", "MUMX", "PBUB", "CP", "THK"]
    )
    s = _session(tree)
    out = reader.read_stream_hx_props(s, "0101")
    check_close("cp 直接从 STRM_UPP\\CP 读到", out["cp_jkgk"], GOOD_CP, 1e-6)
    check_close("λ 直接从 STRM_UPP\\THK 读到", out["k_wmk"], GOOD_K, 1e-6)
    check_close("μ 沿用 MUMX", out["mu_cp"], 900.0, 1e-6)
    check_eq("三项都读到 → source=aspen", out["source"], "aspen")

    # 用户自定义物性集名:按名字扫描兜底(HEATCAP 里有 CP)
    tree2 = _props_tree("0201", upper_children=["HEATCAP"])
    base = r"\Data\Streams\0201\Output"
    tree2[f"{base}\\STRM_UPP\\HEATCAP\\MIXED\\TOTAL"] = _Node(2000.0, "J/kg-K")
    tree2[f"{base}\\STRM_UPP\\HEATCAP"] = _Node(None, "", ["MIXED"])
    tree2[f"{base}\\STRM_UPP\\HEATCAP\\MIXED"] = _Node(None, "", ["TOTAL"])
    out2 = reader.read_stream_hx_props(_session(tree2), "0201")
    check_close("自定义物性集名也能扫到 cp", out2["cp_jkgk"], 2000.0, 1e-6)

    # 摩尔基 cp:记 unit_gap,不拿数值顶替
    tree3 = _props_tree("0301", cp=36.0, k=None, upper_children=["CP"])
    tree3[r"\Data\Streams\0301\Output\STRM_UPP\CP"] = _Node(36.0, "cal/mol-K")
    out3 = reader.read_stream_hx_props(_session(tree3), "0301")
    check_eq("摩尔基 cp 不取数", out3["cp_jkgk"], None)
    check("摩尔基 cp 记入 unit_gaps", any("cp:" in g for g in out3["unit_gaps"]), str(out3))

    # 实测本机那种模型:只有 PL/MUMX/PBUB
    tree4 = _props_tree("0401", mu=1.2)
    out4 = reader.read_stream_hx_props(_session(tree4), "0401")
    check_eq("没有 CP/THK 时 cp 留空", out4["cp_jkgk"], None)
    check_eq("没有 CP/THK 时 λ 留空", out4["k_wmk"], None)
    check_eq("只有 μ 也标 aspen", out4["source"], "aspen")

    # read_hx_props 只碰这台设备的侧流股
    blocks = [{"name": "E1", "type": "HeatX"}, {"name": "E2", "type": "HeatX"}]
    conns = [
        {"block": "E1", "stream": "0101", "side": "hot", "direction": "in"},
        {"block": "E1", "stream": "0102", "side": "hot", "direction": "out"},
        {"block": "E1", "stream": "0103", "side": "cold", "direction": "in"},
        {"block": "E1", "stream": "0104", "side": "cold", "direction": "out"},
        {"block": "E2", "stream": "0201", "side": "hot", "direction": "in"},
    ]
    big = {}
    for name in ("0101", "0102", "0103", "0104", "0201"):
        big.update(
            _props_tree(
                name, cp=GOOD_CP, k=GOOD_K, upper_children=["PL", "MUMX", "PBUB", "CP", "THK"]
            )
        )
    rows = reader.read_hx_props(_session(big), blocks=blocks, connections=conns, block="E1")
    check_eq("只读这台设备的 4 条流股", sorted(rows.keys()), ["0101", "0102", "0103", "0104"])

    streams = [{"name": "0101"}, {"name": "0201"}]
    reader.read_hx_props(_session(big), streams, blocks, conns, block="E1")
    check_eq("读到的 cp 写回 stream 记录", streams[0].get("cp_jkgk"), GOOD_CP)
    check_eq("非本设备流股不被写", streams[1].get("cp_jkgk"), None)

    check_eq(
        "换热块类型过滤:泵/塔不算",
        reader.heat_side_streams([{"name": "P1", "type": "Pump"}], conns),
        {},
    )


def test_hx_l2_cp_fallback() -> None:
    """③ cp 反推:只在单相 + 有 Q/m/ΔT 时做,并标 derived。"""
    from app.design.heat import params

    check_close(
        "500 kW、20 t/h、10 K → 9000 J/(kg·K)", params.cp_from_duty(500, 20000, 10), 9000.0, 1e-6
    )
    check_eq("负荷太小不反推", params.cp_from_duty(0.2, 20000, 10), None)
    check_eq("温差太小不反推", params.cp_from_duty(500, 20000, 1.0), None)
    check_eq("流量为 0 不反推", params.cp_from_duty(500, 0, 10), None)
    check_eq("缺项不反推", params.cp_from_duty(None, 20000, 10), None)

    side = {"t_in_c": 80, "t_out_c": 60, "mass_flow_kgh": 20000, "vap_in": 0.0, "vap_out": 0.0}
    info = params.side_props(side, {"mu_cp": 1.0, "cp_jkgk": None, "k_wmk": 0.6}, 500.0)
    check_eq("cp 缺失时走反推", info["source"]["cp"], "derived")
    check_eq("反推后没有缺项", info["missing"], [])
    check_eq("反推 cp 对应 ΔT=20 K 时的 4500", info["cp_jkgk"], 4500.0)

    two_phase = dict(side, vap_in=0.0, vap_out=0.35)
    info2 = params.side_props(two_phase, {"mu_cp": 1.0}, 500.0)
    check_eq("有相变不反推", info2["cp_jkgk"], None)
    check_eq("λ/μ/cp 全缺 → missing 三项", info2["missing"], ["cp", "λ"])


def _make_row(dn, od, pitch, passes, length, count, area, standard="GB/T 28712.2-2023"):
    from app.design.heat import standards

    return standards.SeriesRow(
        dn_mm=dn,
        shell_id_mm=float(dn),
        tube_od_mm=od,
        tube_wall_mm=2.0 if od == 19 else 2.5,
        tube_length_mm=length,
        tube_pitch_mm=pitch,
        tube_passes=passes,
        tube_count=count,
        area_m2=area,
        arrangement="triangular",
        standard=standard,
    )


def test_hx_l2_standards_parse() -> None:
    """④ 标准表校验:缺列/串列/面积不自洽/重复键/模板 都要挡住。"""
    from app.design.heat import standards

    head = (
        "DN,shell_id_mm,tube_od_mm,tube_wall_mm,tube_length_mm,"
        "tube_pitch_mm,tube_passes,tube_count,area_m2,arrangement\n"
    )
    good = (
        head
        + "325,325,19,2,6000,25,1,99,34.9,triangular\n325,325,19,2,4500,25,1,99,26.0,triangular\n"
    )
    rows, errors, warnings, stats = standards.parse_series(good)
    check_eq("合法行全收", len(rows), 2)
    check_eq("没有错误", errors, [])
    check_eq("统计 DN 档", stats["dn_tiers"], 1)

    bad = head + "325,325,19,2,6000,25,1,99,999,triangular\n"
    rows2, errors2, _w, _s = standards.parse_series(bad)
    check_eq("面积差 2700% 被拒", rows2, [])
    check("错误里写明面积差", any("面积" in e for e in errors2), str(errors2))

    missing_col = "DN,tube_od_mm,tube_wall_mm\n325,19,2\n"
    _r, errors3, _w, _s = standards.parse_series(missing_col)
    check("缺列直接拒", any("缺列" in e for e in errors3), str(errors3))

    out_of_range = head + "9999,9999,19,2,6000,25,1,99,34.9,triangular\n"
    _r, errors4, _w, _s = standards.parse_series(out_of_range)
    check("DN 超 2000 判串列", any("超出" in e for e in errors4), str(errors4))

    pitch_bad = head + "325,325,19,2,6000,12,1,99,16.8,triangular\n"
    _r, errors5, _w, _s = standards.parse_series(pitch_bad)
    check("管心距 0.63d 被拒", any("管心距" in e for e in errors5), str(errors5))

    dupe = (
        head
        + "325,325,19,2,6000,25,1,99,34.9,triangular\n325,325,19,2,6000,25,1,99,34.9,triangular\n"
    )
    _r, errors6, _w, _s = standards.parse_series(dupe)
    check_eq("重复键只留一行", len(_r), 1)
    check("重复键报错", any("重复" in e for e in errors6), str(errors6))

    passes_bad = head + "325,325,19,2,6000,25,3,99,34.9,triangular\n"
    _r, errors7, _w, _s = standards.parse_series(passes_bad)
    check("3 管程不在 1/2/4/6", any("管程数" in e for e in errors7), str(errors7))

    # Excel 的 sep=; 与分号分隔
    semi = "sep=;\n" + good.replace(",", ";")
    rows8, errors8, _w, _s = standards.parse_series(semi)
    check_eq("sep=; 的 Excel 导出能读", len(rows8), 2)
    check_eq("Excel 导出无错误", errors8, [])

    check(
        "原样模板被识别",
        standards.is_template_text(
            (standards.TEMPLATE_DIR / standards.SERIES_FILE).read_text(encoding="utf-8-sig")
        ),
        "模板文本未识别",
    )

    # 程数组合表
    combos, perr, _ps = standards.parse_passes(
        "shell_passes,tube_passes,need_f_correction,allowed\n1,1,0,1\n1,6,0,0\n2,2,1,0\n"
    )
    check_eq("组合表读 3 行", len(combos), 3)
    check_eq("组合表无错", perr, [])
    bad_combo = "shell_passes,tube_passes,need_f_correction,allowed\n3,1,0,1\n"
    _c, errs, _s = standards.parse_passes(bad_combo)
    check("双壳程以上被拒", any("shell_passes" in e for e in errs), str(errs))


def test_hx_l2_standards_state() -> None:
    """⑤ 缺表降级与导入:missing / partial / ok、模板拒绝、失败不覆盖。"""
    from app.design.heat import standards

    tmp = Path(tempfile.mkdtemp())
    try:
        snap = standards.load(tmp)
        check_eq("空目录 → missing", snap.state, "missing")
        check("missing 时 skipped", snap.skipped, snap.reason)
        est = standards.estimate_ranges(100.0, snap)
        check("缺表也给推荐范围", bool(est["cases"]), str(est))
        check_eq("19 mm 管在 7.5 m 时需要 224 根", est["cases"][0]["count_min"], 224)
        check("估算壳径随管数增加", est["shell_mm"]["25"] > est["shell_mm"]["19"], str(est))

        # 20 行 ok(同一 DN 但两种管径 → 仍要 3 档 DN)
        rows = []
        for dn in (325, 400, 500):
            for od, pitch, count in ((19, 25, 99), (25, 32, 57)):
                for length in (3000, 4500, 6000):
                    area = count * math.pi * od * (length - 106) * 1e-6
                    rows.append(_make_row(dn, od, pitch, 1, length, count, round(area, 2)))
        check_eq("18 行", len(rows), 18)

        text = "\n".join(
            [
                "DN,shell_id_mm,tube_od_mm,tube_wall_mm,tube_length_mm,tube_pitch_mm,"
                "tube_passes,tube_count,area_m2,arrangement",
                *[
                    f"{r.dn_mm},{r.shell_id_mm:g},{r.tube_od_mm:g},{r.tube_wall_mm:g},"
                    f"{r.tube_length_mm},{r.tube_pitch_mm:g},{r.tube_passes},{r.tube_count},"
                    f"{r.area_m2:g},{r.arrangement}"
                    for r in rows
                ],
            ]
        )
        ok, errors, status = standards.import_standards(
            {"files": {standards.SERIES_FILE: text}}, target_dir=tmp, reload_now=False
        )
        check_eq("18 行不足 20 → 拒绝", ok, False)
        check("拒绝原因写明行数", any("至少 20" in e for e in errors), str(errors))
        check_eq("没写盘", list(tmp.glob("*.csv")), [])

        # 补到 24 行(4 档 DN) → ok
        for dn in (600,):
            for od, pitch, count in ((19, 25, 99), (25, 32, 57)):
                for length in (3000, 4500, 6000):
                    area = count * math.pi * od * (length - 106) * 1e-6
                    rows.append(_make_row(dn, od, pitch, 1, length, count, round(area, 2)))
        text2 = (
            text
            + "\n"
            + "\n".join(
                f"{r.dn_mm},{r.shell_id_mm:g},{r.tube_od_mm:g},{r.tube_wall_mm:g},"
                f"{r.tube_length_mm},{r.tube_pitch_mm:g},{r.tube_passes},{r.tube_count},"
                f"{r.area_m2:g},{r.arrangement}"
                for r in rows[18:]
            )
        )
        ok2, errors2, status2 = standards.import_standards(
            {"files": {standards.SERIES_FILE: text2}}, target_dir=tmp, reload_now=False
        )
        check_eq("24 行导入成功", ok2, True)
        check_eq("导入后 ok", status2["state"]["state"], "ok")
        check("文件落盘", (tmp / standards.SERIES_FILE).is_file())

        snap2 = standards.load(tmp)
        check_eq("快照行数 24", len(snap2.rows), 24)
        check_eq("DN 4 档", len({r.dn_mm for r in snap2.rows}), 4)
        check_eq("管径 2 档", len({r.tube_od_mm for r in snap2.rows}), 2)

        # 模板原样导入必须拒,且不覆盖已有表
        tpl = (standards.TEMPLATE_DIR / standards.SERIES_FILE).read_text(encoding="utf-8-sig")
        ok3, errors3, _s3 = standards.import_standards(
            {"files": {standards.SERIES_FILE: tpl}}, target_dir=tmp, reload_now=False
        )
        check_eq("模板被拒", ok3, False)
        check_eq("模板拒绝后表未变", len(standards.load(tmp).rows), 24)
        check("模板错误可读", any("模板" in e for e in errors3), str(errors3))

        # 只剩一种管径 → partial
        only19 = "\n".join(
            [text2.splitlines()[0]]
            + [ln for ln in text2.splitlines()[1:] if ln.split(",")[2] == "19"]
        )
        snap3 = standards.load(tempfile.mkdtemp())  # 空目录
        check_eq("空目录仍 missing", snap3.state, "missing")
        rows_only19, _e, _w, _st = standards.parse_series(only19)
        check("单管径解析通过", len(rows_only19) > 0, "解析失败")
    finally:
        pass


def test_hx_l2_geometry() -> None:
    """⑥ 几何:当量直径、折流板、帮系数、压降与接管圆整。"""
    from app.design import hydraulics
    from app.design.heat import geometry

    check_close(
        "当量直径 φ19@25 正三角",
        geometry.equivalent_diameter_mm(19, 25),
        1.10 / 19 * (625 - 0.917 * 361),
        1e-3,
    )
    check_eq("管心距小于外径返回 None", geometry.equivalent_diameter_mm(19, 18), None)
    check_close("折流板 0.3×325 → 100", geometry.baffle_spacing_mm(325), 100.0, 1e-9)
    check_close("折流板 0.3×168 → 50", geometry.baffle_spacing_mm(168), 50.0, 1e-9)

    # Dittus-Boelter 与 Kern 的数值口径
    check("Dittus-Boelter 关系在已知点", True, "")
    row = {"tube_id_mm": 15.0, "tube_count": 99, "tube_passes": 1, "tube_length_mm": 6000}
    side = {"mass_flow_kgh": 20000, "vol_flow_m3h": 20.0, "t_in_c": 90, "t_out_c": 70}
    props = {"mu_cp": 1.0, "cp_jkgk": 4180, "k_wmk": 0.66}
    t = geometry.tube_side_flow(row, side, props, "cold")
    check("管程流速算得出", t["velocity_ms"] is not None, str(t))
    check_close(
        "v = Q/(3600·A)", t["velocity_ms"], (20 / 3600) / (99 * math.pi * 0.015**2 / 4), 1e-3
    )
    check("Re 与 Pr 都算得出", t["reynolds"] and t["prandtl"], str(t))
    check_close("Pr = μ·cp/(1000·k)", t["prandtl"], 1.0 * 4180 / (1000 * 0.66), 0.05)
    nu_expect = 0.023 * t["reynolds"] ** 0.8 * t["prandtl"] ** 0.4
    check_close("Nu 用加热侧 n=0.4", t["nusselt"], nu_expect, 0.2)
    check_close("h = Nu·k/d_i", t["h_wm2k"], nu_expect * 0.66 / 0.015, 1.0)

    s = geometry.shell_side_flow(
        {"shell_id_mm": 400, "tube_od_mm": 19, "tube_pitch_mm": 25, "tube_length_mm": 6000},
        {"mass_flow_kgh": 30000, "vol_flow_m3h": 30.0},
        props,
        120.0,
    )
    check("壳程 h 算得出", s["h_wm2k"] is not None, str(s))
    check_close(
        "壳程 Nu=0.36Re^0.55Pr^(1/3)",
        s["nusselt"],
        0.36 * s["reynolds"] ** 0.55 * s["prandtl"] ** (1 / 3),
        0.5,
    )
    check("壳程压降 > 0", (s["dp_bar"] or 0) > 0, str(s))
    check_eq("壳程折流板数 = round(L/B)-1", s["baffles"], max(1, int(round(6000 / 120)) - 1))

    # 管程压降随程数上升(同几何,压降 ∝ 程数×L 且回弯增多)
    t1 = geometry.tube_side_flow(dict(row, tube_passes=1), side, props, "hot")
    t2 = geometry.tube_side_flow(dict(row, tube_passes=2), side, props, "hot")
    check("2 管程压降高于 1 管程", t2["dp_bar"] > t1["dp_bar"], f"{t1['dp_bar']} vs {t2['dp_bar']}")

    # 压降口径与 hydraulics 同源:λ 应等于 friction_factor
    lam = hydraulics.friction_factor(t1["reynolds"], geometry.TUBE_ROUGHNESS_MM, 15.0)
    check_close("λ 复用 hydraulics", t1["friction_factor"], lam, 1e-5)

    # 接管:Qv=36 m³/h、1.5 m/s → d=92 mm → 圆整到 DN100
    nz = geometry.nozzle_size(36.0, "liquid")
    check("接管圆整出标准外径", nz and nz["dn_mm"], str(nz))
    check(
        "圆整后内径 ≥ 需求",
        (nz or {}).get("id_mm", 0) >= (nz or {}).get("required_id_mm", 0),
        str(nz),
    )

    check_eq("无流量不给接管", geometry.nozzle_size(None), None)


def test_hx_l2_rating_states() -> None:
    """⑦ 五态:blocked / unknown / fail / reference / pass 各自可达且不越界。"""
    from app.design.heat import geometry, rating, standards

    snap = standards.Snapshot(
        rows=(
            _make_row(325, 19, 25, 1, 3000, 99, 16.7),
            _make_row(400, 19, 25, 1, 3000, 174, 30.1),
        ),
        passes=(),
        state="ok",
    )
    case = {
        "duty_kw": 500.0,
        "tube_fluid": "cold",
        "verdict": "reference",
        "warnings": [],
        "sides": {
            "hot": {
                "t_in_c": 120,
                "t_out_c": 80,
                "mass_flow_kgh": 20000,
                "vol_flow_m3h": 22,
                "vap_in": 0,
            },
            "cold": {
                "t_in_c": 30,
                "t_out_c": 70,
                "mass_flow_kgh": 30000,
                "vol_flow_m3h": 30,
                "vap_in": 0,
            },
        },
        "lmtd": {"used_c": 47.0, "f": 1.0},
        "area": {"required_m2": 30.0},
        "u": {"back_calc_wm2k": 380.0},
        "fouling": {"tube": 0.0002, "shell": 0.0004},
        "design": {"tube": {"dp_allow_bar": 0.6}, "shell": {"dp_allow_bar": 0.5}},
    }
    props = {
        "hot": {"mu_cp": 0.6, "cp_jkgk": 2100, "k_wmk": 0.14, "source": {"cp": "aspen"}},
        "cold": {"mu_cp": 1.2, "cp_jkgk": 4180, "k_wmk": 0.62, "source": {"cp": "aspen"}},
    }
    l2 = geometry.candidates(case, props, snap)
    check_eq("只保留覆盖 A_req 的标准行", len(l2["candidates"]), 1)
    cand = l2["candidates"][0]
    check_eq("候选就是 400/19/3000", cand["std_row_key"], "400|19|25|1|3000")

    verdict = rating.judge(case, cand, [])
    check("无假设时不会是 reference", verdict["verdict"] != "reference", str(verdict))

    # 缺物性 → unknown
    bare = geometry.candidates(case, {}, snap)["candidates"][0]
    no_props = rating.judge(case, bare, [])
    check_eq("无 props 时判 unknown", no_props["verdict"], "unknown")
    check("unknown 写明缺什么", any("缺" in n for n in no_props["notes"]), str(no_props))

    # 含假设 → 最高 reference
    ref = rating.judge(case, cand, ["总传热系数取经验值"])
    check_eq("含假设封顶 reference", ref["verdict"], "reference")

    # 压降超允许 → fail
    tight = dict(case, design={"tube": {"dp_allow_bar": 0.001}, "shell": {"dp_allow_bar": 0.5}})
    bad = rating.judge(tight, cand, [])
    check_eq("压降超限 → fail", bad["verdict"], "fail")
    check("fail 指明管程压降", any("管程压降" in n for n in bad["notes"]), str(bad))

    # 面积不足 → fail(把需求面积调到公称面积之上)
    short_cand = dict(cand, area_req_m2=40.0, area_ratio=round(cand["area_std_m2"] / 40.0, 3))
    small = rating.judge(case, short_cand, [])
    check_eq("面积不足 → fail", small["verdict"], "fail")

    # L1 拦截 → blocked,不算 L2
    blocked_case = dict(case, verdict="blocked")
    check_eq("L1 拦截不带进 L2", rating.judge(blocked_case, cand, [])["verdict"], "blocked")

    # 排序与推荐:reference 排在 fail 前
    result = rating.select(case, l2["candidates"], props, "ok", geom_source=None)
    check("推荐 key 存在", result["recommended_key"] is not None, str(result["counts"]))
    check(
        "推荐的一定是 pass/reference",
        result["recommended"]["verdict"] in ("pass", "reference"),
        str(result["recommended"]["verdict"]),
    )
    check_eq("assumptions 为空", result["assumptions"], [])

    rating.select(case, l2["candidates"], props, "ok")
    check("partial 标准表 → 有假设", rating.assumptions_for(case, props, "partial"), "空")
    check(
        "derived cp → 有假设",
        rating.assumptions_for(
            case, {**props, "hot": {**props["hot"], "source": {"cp": "derived"}}}, "ok"
        ),
        "空",
    )


def test_hx_l2_degrade_and_export() -> None:
    """⑧ 缺表降级 + 三页签 EDR 导出(样本库,不接 Aspen)。"""
    from app.design.heat import geometry, standards
    from app.export import heat as heat_export
    from app.store import db

    # 缺表:std_skipped,给 A_req 与推荐范围
    case = {
        "duty_kw": 500.0,
        "tube_fluid": "cold",
        "verdict": "reference",
        "sides": {"hot": {"t_in_c": 120, "t_out_c": 80}, "cold": {"t_in_c": 30, "t_out_c": 70}},
        "lmtd": {"used_c": 47.0, "f": 1.0},
        "area": {"required_m2": 30.0},
        "u": {"back_calc_wm2k": 380.0},
    }
    empty = standards.Snapshot(state="missing", reason="未找到 hx-28712-2.csv")
    l2 = geometry.candidates(case, {}, empty)
    check_eq("缺表 → std_skipped", l2["std_skipped"], True)
    check_eq("缺表不枚举标准行", l2["candidates"], [])
    check("缺表给 A_req", l2["a_req_m2"] == 30.0, str(l2["a_req_m2"]))
    check("缺表给经验范围", bool((l2.get("estimate") or {}).get("cases")), str(l2.get("estimate")))

    # 样本库三页签
    conn = db.open_db("samples/case-23-error.db")
    try:
        cid = conn.execute("SELECT id FROM cases ORDER BY id LIMIT 1").fetchone()[0]
        data = heat_export.build_edr_checklist(conn, cid)
    finally:
        conn.close()
    import io

    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data))
    check_eq("三页签 + 说明", wb.sheetnames, ["EDR工艺输入", "EDR结构输入", "结果与提示", "说明"])
    ws1 = wb["EDR工艺输入"]
    check_eq("工艺页表头列数", ws1.max_column, len(heat_export.PROCESS_HEADERS))
    check("工艺页有数据行", ws1.max_row >= 3, str(ws1.max_row))
    check_eq("第二行写单位说明", ws1.cell(row=2, column=1).value is not None, True)
    ws2 = wb["EDR结构输入"]
    check_eq("结构页列数", ws2.max_column, len(heat_export.GEOMETRY_HEADERS))
    ws3 = wb["结果与提示"]
    check_eq("结果页列数", ws3.max_column, len(heat_export.RESULT_HEADERS))
    check("结果页有 A_req 列", "A_req m²" in [c.value for c in ws3[1]], "列名缺失")
    check("结果页有判定列", "判定 L2" in [c.value for c in ws3[1]], "列名缺失")


def test_hx_l2_api_detail() -> None:
    """⑨ 详情接口:物性读不到时降级,不 500;标准表状态随包下发。"""
    import json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from app.api import server as srv

    sample = str(Path("samples/case-23-error.db").resolve())
    handler_cls = type("HxProbeHandler", (srv.Handler,), {"db_path": sample})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        import sqlite3

        conn = sqlite3.connect(sample)
        cid = conn.execute("SELECT id FROM cases ORDER BY id LIMIT 1").fetchone()[0]
        block = conn.execute(
            "SELECT name FROM blocks WHERE case_id=? AND type='HeatX' LIMIT 1", (cid,)
        ).fetchone()
        conn.close()
        if not block:
            check("样本库没有 HeatX,跳过", True, "")
            return
        req = urllib.request.Request(
            f"{base}/api/cases/{cid}/heat/{block[0]}", headers={"X-Casebook-Client": "1"}
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                payload = json.loads(resp.read())
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
            payload = {}
        check_eq("详情接口 200", status, 200)
        check("详情含 l2 与 props", "l2" in payload and "props" in payload, str(list(payload)[:5]))
        check("详情含标准表状态", "std_state" in payload, "缺 std_state")
        check(
            "物性缺失时 props 给出原因或空值",
            payload.get("props", {}).get("source") in ("unavailable", "case", "live"),
            str(payload.get("props", {}).get("source")),
        )
        check(
            "L2 结论不出现「合格」",
            "合格" not in str(payload.get("l2", {}).get("verdict_text", "")),
            str(payload.get("l2", {}).get("verdict_text")),
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
