"""批量编排：一组流股逐条走完十步，产出主表与「计算书」所需的行。

本模块只做流程串联（挑流股、取本次标准表快照、逐条调用 `step_*`、
汇总 missing / flagged / summary），所有数值口径都在 `step_*.py`。
口径来源：`docs/pipe-selection-plan.md` §14.5、§17。"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from . import data as pipe_data
from .constants import DN_SEAMLESS_MAX, VELOCITY_RANGE, TablesNotReady
from .step_design import (
    allowable_stress,
    design_pressure,
    design_temperature,
    dn1000_oversize_pct,
    hp_ss_required_wall,
    hp_stainless_force_code,
    is_material_stream,
    press_to_abs_kpa,
)
from .step_dn import inner_diameter, select_dn, usable_dn_series
from .step_grade import grade_code, pn_code_for
from .step_phase import medium_code, phase_of, velocity_for
from .step_wall import (
    calc_wall_thickness,
    external_pressure_min_wall,
    near_boiling_liquid,
    round_up_wall,
)
from .validate import actual_velocity


def _pump_streams(
    connections: Iterable[Mapping[str, Any]], blocks: Iterable[Mapping[str, Any]]
) -> tuple[set[str], set[str]]:
    """分出"泵入口流股"与"泵出口流股"。

    `connections` 的行是 (block, direction, stream):`direction="in"` 表示
    该流股**进入**这台设备,`"out"` 表示由这台设备产出。
    泵入口按 0.8 m/s 选型,泵出口不能沿用这个口径(V8)。
    """
    # 泵入口流速只对泵成立;位号 K 开头的透平不算泵(见 block_family 注释)
    from app.design import block_family

    pump_names = {b["name"] for b in blocks if block_family.family_of_block(b) == "pump"}
    inlets, outlets = set(), set()
    for c in connections:
        if c.get("block") not in pump_names:
            continue
        d = str(c.get("direction", "")).lower()
        if d == "in":
            inlets.add(c["stream"])
        elif d == "out":
            outlets.add(c["stream"])
    return inlets, outlets


def _tables_for(tables: Mapping[str, Any] | None) -> tuple[dict, dict, dict, dict, Any]:
    """取本次选型用的三张表。都来自同一次加载的快照,不会新旧混用。"""
    snap = pipe_data.tables_snapshot()
    tbl = dict(tables or {})
    dn_to_od = tbl.get("dn_to_od", pipe_data.DN_TO_OD)
    od_to_walls = tbl.get("od_to_walls", snap.od_to_walls)
    stress = tbl.get("stress", snap.stress)

    if not dn_to_od or not stress:
        empty = [
            label for label, tbl in (("DN→外径(T1)", dn_to_od), ("许用应力(T3)", stress)) if not tbl
        ]
        raise TablesNotReady(
            "标准表未录入:" + "、".join(empty) + " 为空。"
            "把 GB/T 17395-2024 表 1/表 4 与 GB/T 20801.1-2025 表 B.1 整理成 CSV,"
            "放进 data/preview/std/docx/,再重启服务(页码与列名见 docs/pipe-standards-index.md)。"
        )

    return tbl, od_to_walls, stress, dn_to_od, snap


@dataclass(frozen=True)
class _BuildContext:
    """一次选型用的常量集合:计算侧只看它,不再依赖满屏闭包变量。"""

    opts: dict[str, Any]
    tbl: dict[str, Any]
    snap: Any
    dn_to_od: Mapping
    od_to_walls: Mapping
    stress: Mapping
    max_dn: int
    default_material: str
    wall_grade: str
    global_c2: float | None
    min_wall_map: Mapping | None
    od_to_dn: dict
    pump_in: set
    pump_out: set
    min_dn: int | None = None
    #: 两相流的内径下限(由上游纯液相进料反推),键是流股名
    min_inner_d: Mapping[str, float] | None = None
    #: 屏蔽非优先 DN 后实际参与选型的系列(§17.2)
    dn_series: Mapping[int, float] | None = None
    #: 泵排出口/明显升压流股(§17.8 汽蚀排除),键是流股名
    pressurized: frozenset = frozenset()


def _min_wall_ctx(ctx: _BuildContext, dn: int | None) -> float | None:
    """DN → 最小壁厚下限。options 里能覆盖(键可能是字符串,统一转 int)。"""
    if not ctx.min_wall_map or dn is None:
        return None
    for upper in sorted(ctx.min_wall_map, key=int):
        if dn <= int(upper):
            return float(ctx.min_wall_map[upper])
    return None


def _resolve_material(
    ctx: _BuildContext, name: str, medium: str, design_c: float, warnings: list[str]
) -> tuple[str, dict, float, Mapping, float | None]:
    """定材质、腐蚀裕量与许用应力:人工指定 > 管道等级表 > 全局默认。

    工具**不判断介质是否腐蚀**,只按用户定义的表套用(见 pipe_data 第 5 节)。
    """
    opts = ctx.opts
    tbl = ctx.tbl
    snap = ctx.snap
    stress = ctx.stress
    od_to_walls = ctx.od_to_walls
    default_material = ctx.default_material
    global_c2 = ctx.global_c2
    # 材质来源优先级:人工指定 > 管道等级表 > 全局默认
    cls = pipe_data.resolve_piping_class(
        name, medium, rules=opts.get("piping_rules"), default=opts.get("piping_default")
    )
    override = (opts.get("material_for_stream") or {}).get(name)
    material = str(override or cls["grade"] or default_material)
    if (
        not override
        and cls["source"] == "none"
        and (opts.get("piping_rules") or pipe_data.PIPING_CLASS_RULES)
    ):
        warnings.append(f"介质 {medium} 未在管道等级表里定义材质,按默认 {default_material} 暂算")
    if cls["corrosion_mm"] is not None:
        corrosion_c2 = float(cls["corrosion_mm"])
    elif global_c2 is not None:
        corrosion_c2 = float(global_c2)
    else:
        corrosion_c2 = 1.0 if material == "20#" else 0.0
    od_to_walls_used = od_to_walls
    if material.startswith("S3"):
        od_to_walls_used = tbl.get("od_to_walls_sst", snap.od_to_walls_sst) or od_to_walls
    sigma = allowable_stress(material, design_c, dict(stress))
    return material, cls, corrosion_c2, od_to_walls_used, sigma


def _size_pipe(  # noqa: PLR0913, PLR0917 —— 纯选型步骤,入参显式列出便于单测
    ctx: _BuildContext,
    q: float,
    pd: dict,
    d_i: float,
    sigma: float | None,
    corrosion_c2: float,
    od_to_walls_used: Mapping,
    is_pump_in: bool,
    phase: str,
    warnings: list[str],
    force_pn_code: str | None = None,
    design_c: float | None = None,
    material: str = "",
    hot_vacuum: bool = False,
) -> tuple[int, float, dict, dict, float | None, float, str, float | None] | None:
    """选 DN、定壁厚、核实际流速。

    返回 (dn, od, w, wall, min_wall_mm, t_req, pipe_type, ext_wall_mm);选不出档位时 None。
    `force_pn_code` 是高压不锈钢升 R1E(§17.3/§17.8)的代号。
    **R1E 不锈钢族(升档或自然)按 §17.8 公式算壁厚**,不吃整档 PN 类保护 ——
    类保护按 PN10 算会让 DN600 两相线要 25+ mm 超出标准系列,而旧的 16 mm 下限
    又让 190 ℃ 管比 173 ℃ 管薄(倒挂);公式化后同一压力族内"温度越高壁越厚"。
    `hot_vacuum`:操作 ≥250 ℃ 的真空管,壁厚下限提到 10 mm(§17.8 高温外压失稳)。
    """
    dn_to_od = ctx.dn_series if ctx.dn_series is not None else ctx.dn_to_od
    od_to_dn = ctx.od_to_dn
    wall_grade = ctx.wall_grade
    max_dn = ctx.max_dn
    min_dn = ctx.min_dn

    hp_formula = bool(
        material in pipe_data.HP_SS_GRADES
        and (force_pn_code == "R" or pn_code_for(pd["design_mpa"] or 0.0) == "R")
    )

    # 压力等级保护:管道得扛得住自己那个**等级**(PN),而不只是扛住算出来的 Pd。
    # 否则 PN16 的 DN50 按 11 MPa 算出 3 mm —— 许用应力够、试压按 PN16 走,
    # 壁厚却停在 PN10 的水平,现场无从解释。
    pn_code = force_pn_code or pn_code_for(pd["design_mpa"] or 0.0)
    class_mpa = pipe_data.PN_CODES.get(pn_code) if pn_code else None
    if pd["vacuum"]:
        class_mpa = None  # 真空按外压校核,不吃内压等级保护
    if hp_formula:
        class_mpa = None  # R1E 不锈钢族走 §17.8 公式,按设计压力算,不按整档 PN

    def _walls_of(od: float) -> tuple[Sequence[float] | None, bool]:
        """该外径的标准壁厚序列;没录到且是大口径时用焊接管常用序列兜底。"""
        walls = od_to_walls_used.get(od)
        if walls:
            return walls, False
        if od > 630.0:
            return pipe_data.WELDED_WALL_SERIES, True
        return None, False

    def _t_required(od: float) -> float | None:
        """所需壁厚。

        R1E 不锈钢族:t = P·D/(2[σ]^t·E_j+P) + C 后 ÷0.875(§17.8,负偏差已含);
        其余:max(按 Pd 算, 按等级 PN 算) + 腐蚀裕量,负偏差在圆整时另扣。
        """
        if hp_formula:
            return hp_ss_required_wall(
                pd["design_mpa"] or 0.0,
                od,
                design_c,
                ctx.opts,
                sigma_override=None if material == "S30408" else sigma,
            )
        w = calc_wall_thickness(pd["design_mpa"] or 0.0, od, sigma or 0.0)
        if not w["applicable"]:
            return None
        t = w["t_calc_mm"] or 0.0
        if class_mpa:
            wc = calc_wall_thickness(class_mpa, od, sigma or 0.0)
            if wc["t_calc_mm"]:
                t = max(t, wc["t_calc_mm"])
        return t + corrosion_c2

    def _process_min_wall(dn: int | None, od: float) -> float | None:
        """最小壁厚下限:工艺下限 + 真空外压下限 + R1E 小管 3 mm + 高温真空 10 mm,取大。"""
        mw = _min_wall_ctx(ctx, dn)
        if pd["vacuum"]:
            ext = external_pressure_min_wall(od, ctx.opts)
            if ext is not None:
                mw = ext if mw is None else max(mw, ext)
            if hot_vacuum and dn is not None and dn >= pipe_data.VACUUM_HOT_MIN_DN:
                floor_hot = float(
                    ctx.opts.get("vacuum_hot_min_wall_mm", pipe_data.VACUUM_HOT_MIN_WALL_MM)
                )
                mw = floor_hot if mw is None else max(mw, floor_hot)
                warnings.append(f"高温真空 DN{dn}:壁厚下限 {floor_hot:g} mm 已计入(§17.8)")

        if hp_formula:
            # R1E 小口径机械强度保护:DN15 一类不小于 3 mm(§17.8)
            hp_min = float(ctx.opts.get("hp_ss_min_wall_mm", pipe_data.HP_SS_MIN_WALL_MM))
            mw = hp_min if mw is None else max(mw, hp_min)
        return mw

    def _wall_for(od: float) -> float | None:
        """某外径下,考虑许用应力、负偏差与最小壁厚下限后最终选定的标准壁厚。"""
        t_req = _t_required(od)
        if t_req is None:
            return None
        walls, _welded = _walls_of(od)
        mw = _process_min_wall(od_to_dn.get(float(od)), od)
        if hp_formula:
            # 公式里已 ÷0.875 扣过负偏差,圆整时不能再叠一次(§17.8)
            return round_up_wall(t_req, walls, None, None, mw)["wall_mm"]
        return round_up_wall(t_req, walls, od, wall_grade, mw)["wall_mm"]

    # §19.3:DN1000(Φ1016×10 → 内径 996 mm)允许 +5%,即计算内径 996–1045 mm
    # 仍选 DN1000 焊接管,不再踢进未选型。options 可关掉(tol=0)或改区间。
    tol = float(ctx.opts.get("oversize_tolerance", pipe_data.OVERSIZE_TOLERANCE) or 0.0)
    band = (
        float(ctx.opts.get("dn1000_tol_min_mm", pipe_data.DN1000_STD_INNER_MM)),
        float(ctx.opts.get("dn1000_tol_max_mm", pipe_data.DN1000_TOL_MAX_MM)),
    )
    sel = select_dn(
        d_i,
        dn_to_od,
        od_to_walls_used,
        _wall_for,
        min_dn=min_dn,
        oversize_tol=tol,
        oversize_band=band,
    )
    dn, od = sel["dn"], sel["od_mm"]
    if dn is None:
        return None
    if dn > max_dn:
        warnings.append(f"DN{dn} 超出本期上限 DN{max_dn},需专项确认")
    if sel.get("tolerated"):
        pct = dn1000_oversize_pct(d_i, ctx.opts)
        # 两种表述都保留:既说"流速略超上限+内径",也说"略超DN1000上限+百分比",
        # 让《未选型流股》里原来的 0222 / 0215 一类流股在备注里能直接对上号。
        warnings.append(f"流速略超上限(内径{d_i:.1f}mm),建议复核压降")
        warnings.append(f"计算内径略超DN1000上限(约{pct:.1f}%),需复核实际压降")

    w = calc_wall_thickness(pd["design_mpa"] or 0.0, od, sigma or 0.0)
    if not w["applicable"]:
        warnings.append(w["note"])

    t_req = _t_required(od) or 0.0
    walls, welded = _walls_of(od)
    if welded:
        warnings.append(
            f"DN{dn} 属大口径焊接管:壁厚按 {pipe_data.WELDED_WALL_SERIES} 常用序列圆整,"
            "需按 GB/T 3091 / GB/T 9711 复核"
        )
    ext_wall_mm = external_pressure_min_wall(od, ctx.opts) if pd["vacuum"] else None
    if pd["vacuum"]:
        note = (
            f"真空流股:内压按最小壁厚,外压失稳筛查 t_min={ext_wall_mm:.2f} mm(§17.1),"
            "真实支撑跨距需按 GB/T 20801.3 复核"
        )
        warnings.append(note)

    min_wall_mm = _process_min_wall(dn, od)
    if hp_formula:
        # 公式口径:负偏差已 ÷0.875 含入 t_req,圆整只看标准序列与下限(§17.8)
        wall = round_up_wall(t_req, walls, None, None, min_wall_mm)
    else:
        wall = round_up_wall(t_req, walls, od, wall_grade, min_wall_mm)
    if wall["wall_mm"] is None:
        warnings.append(wall["note"])
    else:
        c1 = (
            pipe_data.negative_deviation(od, wall["wall_mm"], wall_grade) if not hp_formula else 0.0
        )
        v_act = actual_velocity(float(q), od - 2.0 * wall["wall_mm"])
        if v_act is not None:
            lo, hi = VELOCITY_RANGE.get("pump_inlet" if is_pump_in else phase, (0.0, float("inf")))
            too_slow = v_act < lo - 1e-9 and phase != "two"  # 两相慢下来是刻意的
            if too_slow or v_act > hi + 1e-9:
                warnings.append(
                    f"实际流速 {v_act:.2f} m/s 超出推荐 {lo:g}–{hi:g} m/s(负偏差 {c1:.2f} mm)"
                )
    if hp_formula:
        warnings.append(
            f"R1E 公式口径:t={t_req:.2f} mm(按 [σ]^t 插值、C=1.0、÷0.875),"
            "圆整到该外径标准壁厚(§17.8)"
        )
    pipe_type = "无缝" if dn <= DN_SEAMLESS_MAX else "焊接"
    return dn, od, w, wall, min_wall_mm, t_req, pipe_type, ext_wall_mm


def _upstream_liquid_inner(
    rows: Sequence[Mapping[str, Any]], conns: Sequence[Mapping[str, Any]]
) -> dict[str, float]:
    """两相流的内径下限:产出它的设备,其纯液相进料有多粗,它就是多粗。

    闪蒸多发生在"液相进料 → 节流/加热"这一步。两相出口比进口细,就等于
    "液体闪蒸后管径骤缩"—— 现场表现为段塞、振动与冲蚀,靠流速区间拦不住。
    """
    inner = {str(r["stream"]): float(r["inner_d_mm"]) for r in rows}
    phase = {str(r["stream"]): r["phase"] for r in rows}
    producers: dict[str, set[str]] = {}
    feeds: dict[str, list[str]] = {}
    for c in conns:
        direction = str(c.get("direction", "")).lower()
        block = str(c.get("block"))
        stream = str(c.get("stream"))
        if direction == "out":
            producers.setdefault(stream, set()).add(block)
        elif direction == "in":
            feeds.setdefault(block, []).append(stream)

    out: dict[str, float] = {}
    for r in rows:
        if r["phase"] != "two":
            continue
        ups = [
            inner.get(s, 0.0)
            for b in producers.get(str(r["stream"]), set())
            for s in feeds.get(b, [])
            if phase.get(s) == "liquid"
        ]
        if ups and max(ups) > float(r["inner_d_mm"]):
            out[str(r["stream"])] = max(ups)
    return out


def _pressurized_lines(
    streams: Sequence[Mapping[str, Any]],
    conns: Sequence[Mapping[str, Any]],
    options: Mapping[str, Any] | None = None,
) -> set[str]:
    """泵排出口 / 明显升压流股(§17.8):与上游同流量同温度流股比压力。

    判据:产出它的设备里存在一条进料,流量差 ≤2%、温差 ≤2 ℃,而本流股压力比它
    高出阈值(默认 0.05 MPa)—— 那就是泵/压缩机加压后的排出口,严禁按吸入侧
    汽蚀限速放大管径(如 PL-0136/PL-0511)。真正的泵出口另有拓扑标记,两者取并集。
    """
    opts = options or {}
    rise_bar = (
        float(opts.get("cavitation_pressure_rise_mpa", pipe_data.CAVITATION_PRESSURE_RISE_MPA))
        * 10.0
    )
    by_name = {str(s.get("name")): s for s in streams}
    producers: dict[str, set[str]] = {}
    feeds: dict[str, list[str]] = {}
    for c in conns:
        direction = str(c.get("direction", "")).lower()
        block = str(c.get("block"))
        stream = str(c.get("stream"))
        if direction == "out":
            producers.setdefault(stream, set()).add(block)
        elif direction == "in":
            feeds.setdefault(block, []).append(stream)

    out: set[str] = set()
    for name, s in by_name.items():
        my_p = s.get("pressure_bar")
        my_q = s.get("vol_flow_m3h")
        my_t = s.get("temperature_c")
        if my_p is None:
            continue
        for b in producers.get(name, set()):
            for fin in feeds.get(b, []):
                f = by_name.get(fin)
                if not f or f.get("pressure_bar") is None:
                    continue
                same_q = (
                    isinstance(my_q, (int, float))
                    and isinstance(f.get("vol_flow_m3h"), (int, float))
                    and abs(float(my_q) - float(f["vol_flow_m3h"])) <= 0.02 * max(float(my_q), 1e-9)
                )
                same_t = (
                    isinstance(my_t, (int, float))
                    and isinstance(f.get("temperature_c"), (int, float))
                    and abs(float(my_t) - float(f["temperature_c"])) <= 2.0
                )
                if same_q and same_t and float(my_p) > float(f["pressure_bar"]) + rise_bar:
                    out.add(name)
    return out


def _select_stream(
    ctx: _BuildContext, s: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """算一条流股。返回 (row, missing):两者只会有一个非 None。"""
    opts = ctx.opts
    dn_to_od = ctx.dn_to_od
    pump_in = ctx.pump_in
    pump_out = ctx.pump_out

    name = s.get("name")
    q = s.get("vol_flow_m3h")
    zero_limit = float(opts.get("zero_flow_m3h", pipe_data.ZERO_FLOW_M3H))
    if q is None or q <= 0:
        return None, {"stream": name, "reason": "缺体积流量,未选型"}
    if 0 < q <= zero_limit:
        # 零流量/备用或放空管线:不按常规物流定死管径(§17.5)
        return None, {
            "stream": name,
            "reason": "零流量/备用或放空管线,需人工确认管径",
            "zero_flow": True,
            "vol_flow_m3h": float(q),
        }
    phase = phase_of(s.get("vap_frac"))
    is_pump_in = name in pump_in
    v = velocity_for(phase, is_pump_in, opts)
    warnings: list[str] = []

    # 绝压先算出来,供真空/汽蚀/高压升档共用(§17)
    abs_kpa = press_to_abs_kpa(s.get("pressure_bar"))

    # 近常压沸点液相 → 汽蚀限速 0.8 m/s(§17.2/§17.8)。三个条件同时满足才触发:
    # 温度 80–105 ℃、绝压 95–115 kPa(微正压/常压吸入侧),且**不是**泵排出口
    # 或明显升压管线(PL-0136/PL-0511 一类加压线严禁按吸入侧放大管径)。
    cavitation = (
        near_boiling_liquid(phase, abs_kpa, s.get("temperature_c"), opts)
        and not is_pump_in
        and str(name) not in (ctx.pressurized or pump_out)
    )
    if cavitation:
        v = min(v, float(opts.get("cavitation_velocity_ms", pipe_data.CAVITATION_VELOCITY_MS)))
        warnings.append(f"近常压沸点液相反汽蚀:流速限到 {v:g} m/s(§17.8)")

    d_i = inner_diameter(float(q), v)

    # 两相流:动压头 ρv² 是把"段塞 / 振动"折成数字的常用判据。
    # 混合密度直接用 Aspen 给的质量流量 ÷ 体积流量 —— 两相混合物的体积流量就是它,
    # 不需要再把气液两相各自拆开(库里也没存分相流量)。
    if phase == "two":
        mass = s.get("mass_flow_kgh")
        limit = float(opts.get("two_phase_dp_limit_pa", 2000.0))
        if isinstance(mass, (int, float)) and mass > 0 and q > 0 and limit > 0:
            rho_m = float(mass) / float(q)
            v_limit = math.sqrt(limit / rho_m)
            if v_limit < v:
                d_i = max(d_i, inner_diameter(float(q), v_limit))
                warnings.append(
                    f"两相动压头 ρv² ≤ {limit:g} Pa(ρm={rho_m:.1f} kg/m³)→ 限速 {v_limit:.2f} m/s"
                )
    # 闪蒸后骤缩是段塞流的直接诱因:两相段的内径不得小于上游纯液相进料
    floor_d = (ctx.min_inner_d or {}).get(str(name))
    if floor_d and floor_d > d_i:
        d_i = floor_d
        warnings.append(f"两相流管径不小于上游液相进料(内径 {floor_d:.1f} mm)")
    pd = design_pressure(s.get("pressure_bar"), opts)
    td = design_temperature(s.get("temperature_c"), opts.get("insulated"), opts)
    medium = medium_code(phase, opts.get("medium_codes"))
    material, cls, corrosion_c2, od_to_walls_used, sigma = _resolve_material(
        ctx, name, medium, td["design_c"], warnings
    )

    # 高压不锈钢升 R1E(§17.3/§17.8):代号与壁厚公式都按新口径走
    force_code = hp_stainless_force_code(material, abs_kpa, opts)
    hp_upgraded = bool(force_code)
    if force_code:
        warnings.append(
            f"高压不锈钢({abs_kpa / 1000:.2f} MPa,操作 {s.get('temperature_c')} ℃):"
            f"等级升 {force_code}1E,按 [σ]^t 公式重算壁厚(§17.8)"
        )

    # 高温真空管(≥250 ℃,§17.8):DN≥400 的壁厚下限 10 mm 由 _size_pipe 落实
    hot_vacuum = bool(
        pd["vacuum"]
        and isinstance(s.get("temperature_c"), (int, float))
        and float(s["temperature_c"])
        >= float(opts.get("vacuum_hot_temp_c", pipe_data.VACUUM_HOT_TEMP_C))
    )
    if hot_vacuum:
        warnings.append(
            f"操作 {float(s['temperature_c']):g} ℃ 真空管:高温弹性模量下降,"
            "需按实际壁温做外压失稳专项复核(§17.8)"
        )

    # §19.2 深真空(绝压 ≤ 10 kPa)专项:防闪蒸 + 高温弹性模量折减。
    # 与 §17.8 的 hot_vacuum(≥250 ℃ 真空管壁厚下限)是两条独立口径,标记都保留。
    op_temp = s.get("temperature_c")
    deep_vacuum = bool(
        pd["vacuum"]
        and abs_kpa is not None
        and abs_kpa <= float(opts.get("deep_vacuum_abs_kpa", pipe_data.DEEP_VACUUM_ABS_KPA))
    )
    deep_hot_vacuum = bool(
        deep_vacuum
        and isinstance(op_temp, (int, float))
        and float(op_temp)
        >= float(opts.get("deep_vacuum_hot_temp_c", pipe_data.DEEP_VACUUM_HOT_TEMP_C))
    )
    if deep_vacuum:
        warnings.append("高真空(≤10kPa):严控吸入压降防闪蒸,建议全焊透/高真空法兰防漏气")
    if deep_hot_vacuum:
        warnings.append("高温深真空:弹性模量折减,严格核算外压失稳支撑跨距")

    # §19.4 温度专项:操作温度(不是设计温度)≤ −20 ℃ / ≥ 300 ℃ 时追加应力与韧性提示。
    low_temp = bool(
        isinstance(op_temp, (int, float))
        and float(op_temp) <= float(opts.get("low_temp_note_c", pipe_data.LOW_TEMP_NOTE_C))
    )
    high_temp = bool(
        isinstance(op_temp, (int, float))
        and float(op_temp) >= float(opts.get("high_temp_note_c", pipe_data.HIGH_TEMP_NOTE_C))
    )
    if low_temp:
        warnings.append("低温工况:复核焊缝低温韧性及低温螺栓选型")
        warnings.append("深冷工况:复核焊缝低温韧性及低温螺栓选型")
    if high_temp:
        warnings.append("高温管线:需做柔性应力分析(热膨胀补偿)")
        warnings.append("高温管线(≥300℃):需做热膨胀柔性应力分析")

    sized = _size_pipe(
        ctx,
        q,
        pd,
        d_i,
        sigma,
        corrosion_c2,
        od_to_walls_used,
        is_pump_in or cavitation,
        phase,
        warnings,  # 汽蚀限速口径同泵入口(0.5–1.0 m/s)
        force_pn_code=force_code,
        design_c=td["design_c"],
        material=material,
        hot_vacuum=hot_vacuum,
    )
    if sized is None:
        od_max = max(dn_to_od.values()) if dn_to_od else None
        # 未选型流股按相态分类给设计出路(§17.8):气相可走风管,两相必须并联
        if phase == "gas":
            tail = ",可按低压风管(矩形/圆形薄壁风道)专项设计"
        elif phase == "two":
            tail = ",需多管并联设计"
        else:
            tail = ",需并联或专项设计"
        return None, {
            "stream": name,
            "phase": phase,
            "reason": f"计算内径 {d_i:.1f} mm 超出可选档位"
            + (f"(最大 Φ{od_max:g} = DN{max(dn_to_od)})" if od_max else "")
            + tail,
        }

    dn, od, w, wall, min_wall_mm, t_req, pipe_type, ext_wall_mm = sized
    mcode, _source = pipe_data.material_code_for(material)
    if mcode is None:  # 判不了材质:标"待人工确认",不硬猜一个代号
        mcode = "E"
        warnings.append(f"材质 {material!r} 的类别无法判定,等级号末字母按 E 暂记,待人工确认")
    # 按前缀推断出来的类别不逐行刷警告(一百条流股会刷一百条);
    # 推断结果在「等级表」弹窗的牌号下拉里以「E·推断」标出,由用户核对。

    # 等级号:真空走 V,高压不锈钢升档走 force_code,其余按设计压力(§17.1/§17.3)
    gc_opts = dict(opts)
    if pd["vacuum"]:
        gc_opts["vacuum"] = True
    elif force_code:
        gc_opts["force_pn_code"] = force_code
    gc = grade_code(pd["design_mpa"] or 0.0, mcode, td["design_c"], "", gc_opts)
    if gc["code"] is None:
        warnings.append(gc["note"])

    # 大口径两相流分层/波状流风险标记(§17.2)
    stratified = bool(
        phase == "two"
        and dn is not None
        and dn >= int(opts.get("stratified_flow_min_dn", pipe_data.STRATIFIED_FLOW_MIN_DN))
    )
    if stratified:
        warnings.append(f"DN{dn} 大口径两相流:均相 ρv² 判据不足,分层流/波状流风险需专项核算(§17.2)")

    # DN≥700 真空焊接管(§17.8):光靠壁厚不经济,标注加强圈
    stiffener = bool(
        pd["vacuum"]
        and dn is not None
        and dn >= int(opts.get("vacuum_stiffener_min_dn", pipe_data.VACUUM_STIFFENER_MIN_DN))
    )
    if stiffener:
        warnings.append(f"DN{dn} 真空焊接管:需设外压加强圈,严禁全真空抽吸(§17.8)")

    # 非标准流水号(§17.5):只标记,不强行重映射
    pattern = str(opts.get("stream_no_pattern", pipe_data.STREAM_NO_PATTERN))
    nonstandard = re.match(pattern, str(name)) is None

    if phase == "two":
        warnings.append("气液共存:按两相流速选型,需专项核算")
    return {
        "stream": name,
        "phase": phase,
        "medium_code": medium,
        "material_grade": material,
        "piping_class_note": cls["note"],
        "piping_class_source": cls["source"],
        "velocity_ms": v,
        "pump_inlet": is_pump_in,
        "pump_outlet": name in pump_out,
        "cavitation_limited": cavitation,
        "nonstandard_no": nonstandard,
        "hp_ss_upgraded": hp_upgraded,
        "stratified_risk": stratified,
        "hot_vacuum": hot_vacuum,
        "vacuum_stiffener": stiffener,
        # §19 第三轮新增标记:Word 表「校核备注」与命令行审计共用
        "deep_vacuum": deep_vacuum,
        "deep_hot_vacuum": deep_hot_vacuum,
        "low_temp": low_temp,
        "high_temp": high_temp,
        "oversize_tolerated": bool(
            wall["wall_mm"] is not None and d_i > od - 2.0 * wall["wall_mm"] + 1e-9
        ),
        "operating_temp_c": float(op_temp) if isinstance(op_temp, (int, float)) else None,
        "inner_d_mm": d_i,
        "dn": dn,
        "od_mm": od,
        "pipe_type": pipe_type,
        "vol_flow_m3h": float(q),
        "design_pressure_mpa": pd["design_mpa"],
        "gauge_pressure_mpa": pd["gauge_mpa"],
        "abs_pressure_kpa": abs_kpa,
        "vacuum": pd["vacuum"],
        "external_wall_mm": ext_wall_mm,
        "design_temp_c": td["design_c"],
        "insulated": td["insulated"],
        "material": material,
        "stress_mpa": sigma,
        "t_calc_mm": w["t_calc_mm"],
        "c2_mm": corrosion_c2,
        "t_required_mm": t_req,
        "min_wall_mm": min_wall_mm,
        "wall_mm": wall["wall_mm"],
        "wall_floored": bool(wall.get("floored")),
        "grade_code": gc["code"],
        "insulation": gc.get("insulation", ""),
        "warnings": warnings,
    }, None


def build(
    streams: Iterable[Mapping[str, Any]],
    blocks: Iterable[Mapping[str, Any]] | None = None,
    connections: Iterable[Mapping[str, Any]] | None = None,
    options: Mapping[str, Any] | None = None,
    tables: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """把一批流股算成管表。

    参数
    ----
    streams : 每条要有 `name`、`vol_flow_m3h`、`temperature_c`、`pressure_bar`(绝压)、`vap_frac`
    blocks / connections : 用来识别"泵入口"(可省,省了就当都是普通管段)
    options : 流速、压力系数、保温阈值、材质等可调口径
    tables  : `{"dn_to_od": {...}, "od_to_walls": {...}, "stress": {...}}`;
              不传则用 `pipe_data` 里的(目前是空的 → 会明确报 TablesNotReady)
    """
    opts = dict(options or {})
    tbl, od_to_walls, stress, dn_to_od_full, snap = _tables_for(tables)
    # 屏蔽非优先 DN(§17.2);od_to_dn 反查表跟着筛选后的系列走
    dn_to_od = usable_dn_series(dn_to_od_full, opts)
    blocks = list(blocks or [])
    conns = list(connections or [])
    pump_in, pump_out = _pump_streams(conns, blocks)
    all_streams = list(streams)
    stream_list = list(all_streams)
    # 泵排出口/明显升压流股(§17.8):汽蚀限速的排除名单
    pressurized = frozenset(_pressurized_lines(stream_list, conns, opts)) | frozenset(pump_out)
    # §19.1:Aspen 热量流(HEAT,如 Q1–Q4)与功流(WORK)不是物流,直接剔除;
    # 它们既不进主表也不进《未选型流股》清单(与"未选中的管线"是两回事)。
    stream_list = [s for s in stream_list if is_material_stream(s, opts)]
    ctx = _BuildContext(
        opts=opts,
        tbl=tbl,
        snap=snap,
        dn_to_od=dn_to_od_full,
        od_to_walls=od_to_walls,
        stress=stress,
        max_dn=int(opts.get("max_dn", pipe_data.MAX_DN)),
        default_material=str(opts.get("material", "S30408")),
        wall_grade=str(opts.get("wall_tol_grade", pipe_data.DEFAULT_WALL_TOL_GRADE)),
        # 腐蚀裕量:显式给了就用;否则按材质给默认值(碳钢 1.0、不锈钢 0)
        global_c2=opts.get("corrosion_mm"),
        # 工艺最小壁厚下限:{DN 上限: 壁厚};传 False 表示这一层不启用
        min_wall_map=opts.get("min_wall", pipe_data.MIN_WALL_BY_DN),
        # 反查表跟着"实际用的" DN→外径 走,注入自定义表时下限也认得出来
        od_to_dn={float(od): int(dn) for dn, od in dn_to_od.items()},
        pump_in=pump_in,
        pump_out=pump_out,
        min_dn=int(opts["min_dn_process"])
        if opts.get("min_dn_process") is not None
        else int(pipe_data.MIN_DN_PROCESS),
        dn_series=dn_to_od,
        pressurized=pressurized,
    )

    rows: list[dict[str, Any]] = []

    missing: list[dict[str, Any]] = []
    for s in stream_list:
        row, miss = _select_stream(ctx, s)
        if miss is not None:
            missing.append(miss)
        else:
            rows.append(row)

    # 第二遍:两相流的内径不得小于上游纯液相进料(第一遍算完才知道别人多粗)。
    # 只重算受影响的那几条,不动其它流股。
    min_inner = _upstream_liquid_inner(rows, conns)
    if min_inner:
        ctx2 = replace(ctx, min_inner_d=min_inner)
        rows, missing = [], []
        for s in stream_list:
            row, miss = _select_stream(ctx2, s)
            if miss is not None:
                missing.append(miss)
            else:
                rows.append(row)

    # 零流量/备用流股单列,不与"内径超限"混在一起(§17.5)
    flagged = [m for m in missing if m.get("zero_flow")]
    return {
        "rows": rows,
        "missing": missing,
        "flagged": flagged,
        "summary": {
            "streams": len(rows) + len(missing),
            "excluded_nonmaterial": len(all_streams) - len(stream_list),
            "deep_vacuum": sum(1 for r in rows if r["deep_vacuum"]),
            "low_temp": sum(1 for r in rows if r["low_temp"]),
            "high_temp": sum(1 for r in rows if r["high_temp"]),
            "oversize_tolerated": sum(1 for r in rows if r["oversize_tolerated"]),
            "selected": len(rows),
            "missing": len(missing),
            "zero_flow": len(flagged),
            "vacuum": sum(1 for r in rows if r["vacuum"]),
            "upgraded": sum(1 for r in rows if r["hp_ss_upgraded"]),
            "warned": sum(1 for r in rows if r["warnings"]),
        },
    }
