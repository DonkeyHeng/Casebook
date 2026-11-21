"""十步选型第 8–9 步：计算壁厚 → 圆整到标准壁厚。

含外压（真空）最小壁厚、近沸点液体判定；`round_up_wall()` 同时叠加
压力等级最小壁厚、负偏差与工艺最小壁厚下限。
口径来源：`docs/pipe-selection-plan.md` §14 第 8–9 步。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from . import data as pipe_data
from .step_design import hp_ss_allowable_stress, hp_ss_required_wall
from .step_grade import pn_code_for


def calc_wall_thickness(
    p_mpa: float, od_mm: float, stress_mpa: float, y: float = pipe_data.Y_VALUE, phi_w: float = 1.0
) -> dict[str, Any]:
    """GB/T 20801.1-2025 6.2.1 直管内压:t = P·D_o / [2(S·Φw + P·Y)]。

    适用范围校验:`t < D_o/6`;超出说明公式不适用(要另按厚壁/外压算),只标记不硬算。
    """
    if stress_mpa is None or stress_mpa <= 0:
        return {"t_calc_mm": None, "applicable": False, "note": "缺许用应力"}
    denominator = 2.0 * (stress_mpa * phi_w + p_mpa * y)
    t = p_mpa * od_mm / denominator
    applicable = t < od_mm / 6.0
    return {
        "t_calc_mm": t,
        "applicable": applicable,
        "note": "" if applicable else f"t={t:.2f} ≥ D/6={od_mm / 6:.2f},公式不适用,需另行校核",
    }


def external_pressure_min_wall(
    od_mm: float | None, options: Mapping[str, Any] | None = None
) -> float | None:
    """真空管道外压失稳最小壁厚(mm,§17.1)。

    长圆筒弹性屈曲:`P_cr = 2E/(1−ν²)·(t/D_o)³`,取外压 0.1 MPa、安全系数 SF,
    反解 `t_min = D_o·(SF·P/(2E/(1−ν²)))^(1/3)`。只作筛查下限,与内压壁厚取大;
    真实支撑跨距、初始椭圆度、材料温度折减需另按 GB/T 20801.3 外压章节复核。

    这些系数都在 `pipe_data` 里,可经 options 覆盖;不返回 None 除非缺外径。
    """
    if od_mm is None or od_mm <= 0:
        return None
    opts = options or {}
    p = float(opts.get("vacuum_external_p_mpa", pipe_data.VACUUM_EXTERNAL_P_MPA))
    sf = float(opts.get("vacuum_buckling_sf", pipe_data.VACUUM_BUCKLING_SF))
    e = float(opts.get("vacuum_buckling_e_mpa", pipe_data.VACUUM_BUCKLING_E_MPA))
    nu = float(opts.get("vacuum_buckling_nu", pipe_data.VACUUM_BUCKLING_NU))
    base = 2.0 * e / (1.0 - nu * nu)
    return float(od_mm) * (sf * p / base) ** (1.0 / 3.0)


def near_boiling_liquid(
    phase: str,
    abs_kpa: float | None,
    temp_c: float | None,
    options: Mapping[str, Any] | None = None,
) -> bool:
    """近常压沸点液相 → 泵吸入易汽蚀,限速 0.8 m/s(§17.2/§17.8)。

    判据:液相 + 操作温度 80–105 ℃ + 绝压 95–115 kPa(微正压/常压吸入侧)。
    只限速,不改相态;泵排出口/明显升压管线由调用方另行排除(§17.8)。
    """

    if phase != "liquid" or abs_kpa is None or temp_c is None:
        return False
    opts = options or {}
    lo_t, hi_t = opts.get("cavitation_temp_range_c", pipe_data.CAVITATION_TEMP_RANGE_C)
    lo_p, hi_p = opts.get("cavitation_abs_kpa_range", pipe_data.CAVITATION_ABS_KPA_RANGE)
    return float(lo_t) <= temp_c <= float(hi_t) and float(lo_p) <= abs_kpa <= float(hi_p)


def round_up_wall(
    t_required_mm: float,
    walls: Sequence[float] | None,
    od_mm: float | None = None,
    grade: str | None = None,
    min_wall: float | None = None,
) -> dict[str, Any]:
    """向上圆整到标准壁厚。

    `od_mm` + `grade` 给了的话,会**把壁厚负偏差一起算进去**:
    选中的 S 必须满足 `S − C1(S) ≥ 所需壁厚`(C1 依赖 S,所以要逐档试),
    否则就会出现"圆整完扣掉负偏差还不够"的情况(实测 #20 有 20 多条)。

    `min_wall` 是**工艺最小壁厚下限**(见 `pipe_data.MIN_WALL_BY_DN`):
    承压需要的厚度可能只有 0.07 mm,但 DN15 实际得从 2.0 mm 起,否则焊不出来。
    下限只抬高、不降低 —— 承压需要更厚时仍按承压;`floored` 说明这一档是不是下限定的。
    """
    if not walls:
        return {
            "wall_mm": None,
            "note": "缺标准壁厚序列(T2 未录入)",
            "min_wall_mm": min_wall,
            "floored": False,
        }
    usable = sorted(walls)
    t_req = float(t_required_mm)
    floor = t_req if min_wall is None else max(t_req, float(min_wall))

    def _pick(start: float) -> float | None:
        for s in usable:
            if s < start - 1e-9:
                continue
            if od_mm and grade:
                c1 = pipe_data.negative_deviation(od_mm, s, grade)
                if s - c1 < t_req - 1e-9:
                    continue
            return float(s)
        return None

    chosen = _pick(floor)
    if chosen is not None:
        # floored 只在**下限真把档位抬高**时为真:min_wall > t_req 但承压/负偏差
        # 本身已经要求同样或更厚的档位时,这一档不是下限定的,不能误标。
        floored = False
        if min_wall is not None and float(min_wall) > t_req:
            plain = _pick(t_req)
            floored = plain is None or chosen > plain + 1e-9
        return {"wall_mm": chosen, "note": "", "min_wall_mm": min_wall, "floored": floored}

    s_max = float(max(usable))
    net_top = s_max - pipe_data.negative_deviation(od_mm, s_max, grade) if od_mm and grade else None
    if min_wall is not None and float(min_wall) > s_max + 1e-9:
        # 名义下限就超过序列最大档:与负偏差无关,分开说
        note = (
            f"工艺最小壁厚下限 {float(min_wall):g} mm 超出该外径标准壁厚序列最大档 "
            f"{s_max:g} mm,需改用更大外径、并联或专项设计"
        )
        if net_top is not None and net_top < t_req - 1e-9:
            note += f";另:最大档扣负偏差后净厚 {net_top:.2f} mm 仍小于所需承压厚度 {t_req:.2f} mm"
    elif net_top is not None and net_top < t_req - 1e-9:
        # 扣负偏差后净厚不足:只比 t_req,不把名义 floor 混进来
        note = (
            f"所需承压厚度 {t_req:.2f} mm 超过该外径最大标准壁厚 {s_max:g} mm "
            f"扣除负偏差后的净厚 {net_top:.2f} mm,需并联或专项设计"
        )
    else:
        note = f"所需承压厚度 {t_req:.2f} mm 超过该外径最大标准壁厚 {s_max:g} mm,需并联或专项设计"
    return {
        "wall_mm": None,
        "note": note,
        "min_wall_mm": min_wall,
        "floored": bool(min_wall is not None and float(min_wall) > t_req),
    }


def design_wall_for_od(
    od_mm: float,
    design_pressure_mpa: float | None,
    *,
    dn: int | None = None,
    material: str = "",
    sigma: float | None = None,
    design_temp_c: float | None = None,
    c2_mm: float = 0.0,
    vacuum: bool = False,
    hot_vacuum: bool = False,
    hp_c_mm: float | None = None,
    force_pn_code: str | None = None,
    wall_grade: str = pipe_data.DEFAULT_WALL_TOL_GRADE,
    options: Mapping[str, Any] | None = None,
    od_to_walls: Mapping[float, Sequence[float]] | None = None,
    min_wall_map: Mapping[int, float] | None = None,
) -> dict[str, Any]:
    """指定外径 + 材质 + 工况 → 最终标准壁厚（管道页与泵面板共用）。

    覆盖管道页的全部壁厚口径：
      * 表 1 / 表 4 材质路由（S3xxxx 用不锈钢序列，其余用碳钢/合金钢序列）；
      * 非真空、非 R1E 时的 PN 整档保护（设计压力与等级压力取大）；
      * S30408/S31603 的 R1E 高压公式（含 3 mm 小管下限、负偏差已含）；
      * 真空外压失稳筛查与高温真空下限；
      * 工艺最小壁厚 `MIN_WALL_BY_DN`；
      * DN>600 未录标准壁厚时按 `WELDED_WALL_SERIES` 兜底。

    `od_to_walls` 由调用方给时可传入本次标准表快照（selection 用）；
    不传则按材质从 `data` 的当前快照里选表。返回 `wall_mm`、`t_required_mm`、
    `min_wall_mm`、`hp_formula`、`welded` 与说明 `note`。
    """
    opts = options or {}
    opts_dict = dict(opts)
    if design_pressure_mpa is None:
        return {
            "wall_mm": None,
            "t_required_mm": None,
            "t_calc_mm": None,
            "min_wall_mm": None,
            "floored": False,
            "note": "缺设计压力，无法计算壁厚",
            "hp_formula": False,
            "welded": False,
            "walls": None,
            "ext_wall_mm": None,
            "pn_code": None,
        }
    p_mpa = float(design_pressure_mpa or 0.0)
    od = float(od_mm)
    mat = str(material or "")
    hp_formula = bool(
        design_temp_c is not None
        and mat in pipe_data.HP_SS_GRADES
        and (force_pn_code == "R" or pn_code_for(p_mpa) == "R")
    )
    pn_code = force_pn_code or pn_code_for(p_mpa)
    class_mpa = pipe_data.PN_CODES.get(pn_code) if pn_code else None
    if vacuum or hp_formula:
        # 真空按外压校核；R1E 按设计压力走 §17.8 公式，都不吃整档 PN 保护
        class_mpa = None

    walls_table = od_to_walls
    if walls_table is None:
        walls_table = pipe_data.OD_TO_WALLS_SST if mat.startswith("S3") else pipe_data.OD_TO_WALLS
    walls = walls_table.get(od) if walls_table else None
    welded = False
    if not walls and od > 630.0:
        walls = pipe_data.WELDED_WALL_SERIES
        welded = True

    t_calc = calc_wall_thickness(p_mpa, od, sigma or 0.0)
    t_note = ""
    if hp_formula:
        t_req = hp_ss_required_wall(
            p_mpa,
            od,
            design_temp_c,
            opts_dict,
            sigma_override=None if mat == "S30408" else sigma,
            c_mm=hp_c_mm,
        )
        if t_req is None:
            sigma_eff = (
                hp_ss_allowable_stress(design_temp_c, opts_dict) if mat == "S30408" else sigma
            )
            if design_temp_c is None or sigma_eff is None or sigma_eff <= 0:
                t_note = "R1E 壁厚未算出:缺设计温度或许用应力"
            else:
                ej = float(opts_dict.get("hp_ss_weld_factor", pipe_data.HP_SS_WELD_FACTOR))
                t_c = p_mpa * od / (2.0 * sigma_eff * ej + p_mpa)
                t_note = (
                    "超出薄壁圆筒公式适用范围（t_c ≥ D_o/6），需另行校核"
                    if t_c * 6.0 >= od
                    else "R1E 壁厚未算出,请复核设计参数"
                )
    else:
        if not t_calc.get("applicable"):
            return {
                "wall_mm": None,
                "t_required_mm": None,
                "t_calc_mm": t_calc.get("t_calc_mm"),
                "min_wall_mm": None,
                "floored": False,
                "note": str(t_calc.get("note") or "壁厚公式算不出"),
                "hp_formula": False,
                "welded": welded,
                "walls": walls,
                "ext_wall_mm": None,
                "pn_code": pn_code,
            }
        t_req = float(t_calc.get("t_calc_mm") or 0.0)
        if class_mpa:
            t_class = calc_wall_thickness(float(class_mpa), od, sigma or 0.0)
            if t_class.get("t_calc_mm"):
                t_req = max(t_req, float(t_class["t_calc_mm"]))
                if not t_class.get("applicable"):
                    # t_c(P_d) 可能在薄壁域内,但 PN 整档保护的压力把厚度顶到 D_o/6 以上:
                    # 不能静默按薄壁公式给结论,必须显式提示越出适用域。
                    t_note = (
                        f"PN 整档保护压力 {float(class_mpa):g} MPa 下的承压厚度 "
                        f"t_c={float(t_class['t_calc_mm']):.2f} mm ≥ D_o/6={od / 6.0:.2f} mm,"
                        "薄壁公式不适用,结果需按厚壁公式或专项设计复核"
                    )
        t_req += float(c2_mm or 0.0)

    min_wall = None
    min_wall_map = pipe_data.MIN_WALL_BY_DN if min_wall_map is None else min_wall_map
    if min_wall_map and dn is not None:
        for upper in sorted(min_wall_map, key=int):
            if dn <= int(upper):
                min_wall = float(min_wall_map[upper])
                break
    ext_wall = None
    if vacuum:
        ext_wall = external_pressure_min_wall(od, opts_dict)
        if ext_wall is not None and t_req is not None:
            # 外压失稳厚度是**承压厚度**,与内压厚度同口径:先叠腐蚀裕量 C2,
            # 再并入 t_req,让后面的 S−C1 ≥ t_req 把负偏差一起扣掉。
            # 不能只塞进 min_wall 名义下限,否则 S−C1 后实际外压厚度会少 C1+C2。
            t_req = max(float(t_req), float(ext_wall) + float(c2_mm or 0.0))
        if hot_vacuum and dn is not None and dn >= pipe_data.VACUUM_HOT_MIN_DN:
            floor_hot = float(
                opts_dict.get("vacuum_hot_min_wall_mm", pipe_data.VACUUM_HOT_MIN_WALL_MM)
            )
            min_wall = floor_hot if min_wall is None else max(min_wall, floor_hot)
    if hp_formula:
        hp_min = float(opts_dict.get("hp_ss_min_wall_mm", pipe_data.HP_SS_MIN_WALL_MM))
        min_wall = hp_min if min_wall is None else max(min_wall, hp_min)

    if t_req is None:
        return {
            "wall_mm": None,
            "t_required_mm": None,
            "t_calc_mm": t_calc.get("t_calc_mm"),
            "min_wall_mm": min_wall,
            "floored": False,
            "note": t_note,
            "hp_formula": hp_formula,
            "welded": welded,
            "walls": walls,
            "ext_wall_mm": ext_wall,
            "pn_code": pn_code,
        }
    # R1E 的负偏差已按 12.5% 除入 t_req，圆整时不再叠一次
    got = (
        round_up_wall(t_req, walls, None, None, min_wall)
        if hp_formula
        else round_up_wall(t_req, walls, od, wall_grade, min_wall)
    )
    return {
        "wall_mm": got.get("wall_mm"),
        "t_required_mm": float(t_req),
        "t_calc_mm": t_calc.get("t_calc_mm"),
        "min_wall_mm": min_wall,
        "floored": bool(got.get("floored")),
        "note": "；".join(x for x in (t_note, got.get("note")) if x),
        "hp_formula": hp_formula,
        "welded": welded,
        "walls": walls,
        "ext_wall_mm": ext_wall,
        "pn_code": pn_code,
    }
