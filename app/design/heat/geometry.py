"""L2 几何初选 + 水力估算(纯函数,不碰 Aspen / 不读盘)。

只做**简捷级**估计,不代替 EDR rating(方案 §5、§9):
* 管程:Dittus-Boelter 膜系数、Darcy 压降复用 `app.design.hydraulics`;
* 壳程:Kern 口径(横向流面积 + 当量直径 + Nu=0.36Re^0.55Pr^1/3);
* 标准系列行来自 `standards.snapshot()`;缺表时不枚举,只给 A_req 与经验范围。

三条硬约束
----------
1. **不编数据**:物性缺失(尤其 cp / λ)就不算膜系数,候选只留几何与 A_std;
2. **不越界**:只算单相显热 —— 相变、多股、温度交叉已由 L1 拦截,这里不重复判;
3. **单位**:mm / kg/h / kW 进,计算内部一律 SI(m、kg/s、W),输出再还原。
"""

from __future__ import annotations

import math

from app.design import hydraulics

from . import standards

# ---------------------------------------------------------------- 口径常量

#: 管程流速建议区间(m/s,方案 §5.8)。超出只告警,不判失败。
TUBE_VELOCITY_RANGE = (0.5, 1.5)
#: 接管目标流速(m/s):液体 1.5、气体 15(只用于估算接管口径)
NOZZLE_VELOCITY = {"liquid": 1.5, "gas": 15.0}
#: 折流板间距默认 = 0.30×壳体内径,夹在 0.20~1.00 D 之间(方案 §5.10)
BAFFLE_RATIO = 0.30
BAFFLE_RATIO_RANGE = (0.20, 1.00)
#: 管壁粗糙度(mm)。换热管是新管,取 0.03;与管道默认 0.05 区分开。
TUBE_ROUGHNESS_MM = 0.03
#: 膜系数合理区间(W/(m²·K))。超出说明物性/几何有错,给告警而不是照发。
H_RANGE = (20.0, 20000.0)
#: 反算 U 与几何算的 U 相差超过这个比例 → 告警(方案 §7.2)
U_SELF_CONSISTENCY = 0.30

WARN_TEXT = {
    "TUBE_VELOCITY": "管程流速超出 0.5~1.5 m/s 建议区间",
    "H_OUT_OF_RANGE": "膜系数超出 20~20000 W/(m²·K),物性或几何可能有问题",
    "U_MISMATCH": "反算 U 与按几何算的 U 相差超过 30%",
    "NOZZLE_ESTIMATED": "接管口径按目标流速估算后圆整,未做接管压降核算",
    "PITCH_NOT_STANDARD": "管心距不是常见值(φ19 用 25、φ25 用 32)",
}

BLOCK_HINT = {
    "NO_PROPS": "缺 cp / λ / μ,算不了 Re、Pr、Nu",
    "NO_FLOW": "缺体积流量或质量流量,算不了流速",
    "NO_DUTY": "缺热负荷",
    "NO_DT": "缺有效平均温差 ΔTm",
    "NO_U": "缺反算 U 或 Aspen 设计 U",
    "NO_STD": "未加载标准系列表",
}


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


# ---------------------------------------------------------------- 单项水力


def baffle_spacing_mm(shell_id_mm: float | None) -> float | None:
    """折流板间距默认值 = 0.30×D_s,按 10 mm 取整并夹在 0.2~1.0 D。"""
    ds = _num(shell_id_mm)
    if ds is None or ds <= 0:
        return None
    b = round(ds * BAFFLE_RATIO / 10.0) * 10.0
    lo, hi = ds * BAFFLE_RATIO_RANGE[0], ds * BAFFLE_RATIO_RANGE[1]
    return round(min(max(b, lo), hi), 1)


def equivalent_diameter_mm(
    tube_od_mm: float, pitch_mm: float, arrangement: str = "triangular"
) -> float | None:
    """壳程当量直径(Kern)。正三角形:1.10/d_o·(P²−0.917d_o²);正方形:正方形流道。

    只用两种最常见的布管;`rotated_*` 与本型同值(Kern 只区分三角形/正方形)。
    """
    do = _num(tube_od_mm)
    p = _num(pitch_mm)
    if not do or not p or p <= do:
        return None
    if arrangement in ("square", "rotated_square"):
        return round(4 * (p * p - math.pi * do * do / 4) / (math.pi * do), 3)
    return round(1.10 / do * (p * p - 0.917 * do * do), 3)


def tube_side_flow(row: dict, side: dict, props: dict, tube_fluid: str | None) -> dict:
    """管程:流速、Re、Pr、Nu、h、压降。

    `row` 是标准行 dict(或手填几何,字段同名),`side` 是 L1 的侧记录。
    任何一项算不出就返回 None 并写 `missing`,不拿默认物性补。
    """
    di = _num(row.get("tube_id_mm")) or (
        (_num(row.get("tube_od_mm")) or 0) - 2 * (_num(row.get("tube_wall_mm")) or 0)
    )
    n = _num(row.get("tube_count"))
    np_ = _num(row.get("tube_passes")) or 1
    length_mm = _num(row.get("tube_length_mm"))
    out = {
        "tube_id_mm": round(di, 3) if di else None,
        "flow_area_m2": None,
        "velocity_ms": None,
        "reynolds": None,
        "prandtl": None,
        "nusselt": None,
        "h_wm2k": None,
        "dp_bar": None,
        "friction_factor": None,
        "density_kgm3": None,
        "missing": [],
        "warnings": [],
    }
    if not di or di <= 0 or not n or n <= 0:
        out["missing"].append("几何")
        return out

    area = n / np_ * math.pi * (di / 1000.0) ** 2 / 4.0
    out["flow_area_m2"] = round(area, 5)

    mass = _num(side.get("mass_flow_kgh"))
    vol = _num(side.get("vol_flow_m3h"))
    rho = (mass / vol) if (mass and vol and vol > 0) else None
    out["density_kgm3"] = round(rho, 3) if rho else None
    if vol and area > 0:
        out["velocity_ms"] = round((vol / 3600.0) / area, 3)

    mu = _num(props.get("mu_cp"))
    cp = _num(props.get("cp_jkgk"))
    k = _num(props.get("k_wmk"))
    if mu and mu > 0 and mass:
        # Re = 4·(m/N_p)/(π·d·μ):质量流量按程数分摊,与流速是否算出无关
        out["reynolds"] = round(
            4 * (mass / 3600.0) * np_ / (n * math.pi * (di / 1000.0) * (mu / 1000.0)), 1
        )
    if mu and cp and k:
        out["prandtl"] = round(mu * cp / (1000.0 * k), 3)

    if out["reynolds"] and out["prandtl"] and k:
        n_exp = 0.4 if side_is_cold(tube_fluid) else 0.3
        nu = 0.023 * out["reynolds"] ** 0.8 * out["prandtl"] ** n_exp
        out["nusselt"] = round(nu, 2)
        h = nu * k / (di / 1000.0)
        out["h_wm2k"] = round(h, 1)
        if not H_RANGE[0] <= h <= H_RANGE[1]:
            out["warnings"].append("H_OUT_OF_RANGE")
    else:
        for key, name in (("reynolds", "μ"), ("prandtl", "cp/λ")):
            if out[key] is None:
                out["missing"].append(name)

    v = out["velocity_ms"]
    if v and v > 0 and not TUBE_VELOCITY_RANGE[0] <= v <= TUBE_VELOCITY_RANGE[1]:
        out["warnings"].append("TUBE_VELOCITY")

    if out["reynolds"] and rho and out["velocity_ms"] and length_mm:
        lam = hydraulics.friction_factor(out["reynolds"], TUBE_ROUGHNESS_MM, di)
        out["friction_factor"] = round(lam, 5) if lam else None
        if lam:
            # 局部损失:进口 1.5 + 出口 1.0 + 每程回弯 1.5(方案 §5.9)
            zeta = 1.5 + 1.0 + 1.5 * (np_ - 1)
            hf = hydraulics.head_loss_m(length_mm / 1000.0 * np_, di, zeta, lam, out["velocity_ms"])
            if hf is not None:
                out["dp_bar"] = round(rho * hydraulics.G * hf / 1e5, 3)
    else:
        out["missing"].append("流量/几何")
    return out


def side_is_cold(side_key: str | None) -> bool:
    """被加热的一侧(cold)用 Dittus-Boelter 的 n=0.4,被冷却的(n=0.3)。"""
    return (side_key or "") == "cold"


def shell_side_flow(row: dict, side: dict, props: dict, baffle_mm: float | None) -> dict:
    """壳程:Kern 口径的横向流面积、质量流速、Re、Pr、Nu、h 与压降。"""
    ds = _num(row.get("shell_id_mm"))
    do = _num(row.get("tube_od_mm"))
    pitch = _num(row.get("tube_pitch_mm"))
    length_mm = _num(row.get("tube_length_mm"))
    arrangement = str(row.get("arrangement") or "triangular")
    out = {
        "shell_id_mm": ds,
        "baffle_mm": baffle_mm,
        "baffles": None,
        "cross_area_m2": None,
        "de_mm": None,
        "g_kgm2s": None,
        "reynolds": None,
        "prandtl": None,
        "nusselt": None,
        "h_wm2k": None,
        "dp_bar": None,
        "friction_factor": None,
        "missing": [],
        "warnings": [],
    }
    de = equivalent_diameter_mm(do, pitch, arrangement)
    out["de_mm"] = de
    if not ds or not de or not baffle_mm or not length_mm:
        out["missing"].append("几何")
        return out

    clearance = (pitch - do) if (pitch and do) else None
    if not clearance or clearance <= 0:
        out["missing"].append("管心距")
        return out
    area = (ds / 1000.0) * (clearance / 1000.0) * (baffle_mm / 1000.0) / (pitch / 1000.0)
    out["cross_area_m2"] = round(area, 5)

    mass = _num(side.get("mass_flow_kgh"))
    if not mass or mass <= 0 or area <= 0:
        out["missing"].append("壳程流量")
        return out
    g = mass / 3600.0 / area
    out["g_kgm2s"] = round(g, 3)

    mu = _num(props.get("mu_cp"))
    cp = _num(props.get("cp_jkgk"))
    k = _num(props.get("k_wmk"))
    vol = _num(side.get("vol_flow_m3h"))
    rho = (mass / vol) if (mass and vol and vol > 0) else None
    if mu and mu > 0:
        out["reynolds"] = round((de / 1000.0) * g / (mu / 1000.0), 1)
    if mu and cp and k:
        out["prandtl"] = round(mu * cp / (1000.0 * k), 3)
    if out["reynolds"] and out["prandtl"] and k:
        nu = 0.36 * out["reynolds"] ** 0.55 * out["prandtl"] ** (1.0 / 3.0)
        out["nusselt"] = round(nu, 2)
        h = nu * k / (de / 1000.0)
        out["h_wm2k"] = round(h, 1)
        if not H_RANGE[0] <= h <= H_RANGE[1]:
            out["warnings"].append("H_OUT_OF_RANGE")
    else:
        for key, name in (("reynolds", "μ"), ("prandtl", "cp/λ")):
            if out[key] is None:
                out["missing"].append(name)

    if out["reynolds"] and rho:
        nb = max(1, int(round(length_mm / baffle_mm)) - 1)
        out["baffles"] = nb
        f_s = math.exp(0.576 - 0.19 * math.log(out["reynolds"]))
        out["friction_factor"] = round(f_s, 5)
        dp_pa = f_s * g * g * (nb + 1) * (ds / 1000.0) / (2 * rho * (de / 1000.0))
        out["dp_bar"] = round(dp_pa / 1e5, 3)
    else:
        out["missing"].append("密度")
    return out


# ---------------------------------------------------------------- 接管圆整


def round_nozzle(required_id_mm: float | None, pipe_table: dict | None = None) -> dict | None:
    """按 GB/T 17395 的钢管规格把估算内径圆整上去(取最小可达外径)。

    `pipe_table` 是 `{外径: (壁厚, ...)}`;标准表没导入时返回 None(由页面标"未圆整")。
    """
    req = _num(required_id_mm)
    if not req or req <= 0:
        return None
    table = pipe_table if pipe_table is not None else _pipe_table()
    if not table:
        return None
    best = None
    for od in sorted(table):
        for wall in sorted(table[od], reverse=True):  # 同外径下壁厚越薄、内径越大
            id_mm = od - 2 * wall
            if id_mm >= req:
                best = (od, wall, id_mm)
                break
        if best:
            break
    if not best:
        return None
    return {
        "dn_mm": best[0],
        "wall_mm": best[1],
        "id_mm": round(best[2], 1),
        "required_id_mm": round(req, 1),
    }


def _pipe_table() -> dict:
    try:
        from app.design.pipe import data as pipe_data

        return pipe_data.OD_TO_WALLS or {}
    except Exception:  # 管道标准表坏了不该把换热器抽屉打挂
        return {}


def nozzle_size(vol_m3h: float | None, phase: str = "liquid") -> dict | None:
    """接管内径估算 + 圆整;液体 1.5 m/s、气体 15 m/s。"""
    vol = _num(vol_m3h)
    if not vol or vol <= 0:
        return None
    v = NOZZLE_VELOCITY.get(str(phase or "liquid"), NOZZLE_VELOCITY["liquid"])
    d_req = math.sqrt(4 * (vol / 3600.0) / (v * math.pi)) * 1000.0
    rounded = round_nozzle(d_req)
    if rounded:
        rounded["target_velocity_ms"] = v
        return rounded
    return {
        "dn_mm": None,
        "wall_mm": None,
        "id_mm": round(d_req, 1),
        "required_id_mm": round(d_req, 1),
        "target_velocity_ms": v,
    }


# ---------------------------------------------------------------- 候选组装


def _side_phase(side: dict) -> str:
    vap = _num(side.get("vap_in"))
    if vap is not None and vap >= 0.5:
        return "gas"
    return "liquid"


def thermal_resistance(h_i: float | None, h_o: float | None) -> float | None:
    """1/U_clean(不含污垢)。h 缺失返回 None。"""
    if not h_i or not h_o or h_i <= 0 or h_o <= 0:
        return None
    return 1.0 / h_i + 1.0 / h_o


def u_from_h(h_i: float | None, h_o: float | None, r_tube, r_shell) -> dict:
    """由两侧膜系数与污垢算 U_clean / U_dirty。缺 h 就返回 None,不猜。"""
    base = thermal_resistance(h_i, h_o)
    if base is None:
        return {"u_clean_wm2k": None, "u_dirty_wm2k": None, "r_total": None}
    rt = _num(r_tube) or 0.0
    rs = _num(r_shell) or 0.0
    u_clean = 1.0 / base
    u_dirty = 1.0 / (base + rt + rs)
    return {
        "u_clean_wm2k": round(u_clean, 1),
        "u_dirty_wm2k": round(u_dirty, 1),
        "r_total": round(base + rt + rs, 6),
    }


def _row_geometry(row) -> dict:
    if isinstance(row, standards.SeriesRow):
        return row.as_dict()
    return dict(row)


def candidates(
    case: dict,
    side_props: dict | None = None,
    snap: standards.Snapshot | None = None,
    limit: int = 8,
) -> dict:
    """枚举标准行候选并做水力估算。

    Args:
        case: `assemble.build_row()["row"]["case"]`(含 sides / lmtd / area / u / fouling)
        side_props: `hx_props.get()` 的 `sides` 段({hot: {mu_cp,...}});缺省时按无物性处理
        snap: 标准表快照;缺省取当前生效快照
        limit: 返回候选条数上限(默认 8,方案 §9.1)

    返回:
        {
          "std_skipped": bool, "a_req_m2": float|None, "estimate": dict|None,
          "candidates": [...], "props_missing": [...], "notes": [...], "blocked": bool
        }
    """
    source = snap or standards.snapshot()
    side_props = side_props or {}
    a_req = _num((case.get("area") or {}).get("required_m2"))
    duty = _num(case.get("duty_kw"))
    lmtd = case.get("lmtd") or {}
    dt = _num(lmtd.get("used_c"))
    f = _num(lmtd.get("f")) or 1.0
    dt_eff = (dt * f) if dt else None
    u_back = _num((case.get("u") or {}).get("back_calc_wm2k")) or _num(
        (case.get("u") or {}).get("aspen_wm2k")
    )

    notes: list[str] = []
    props_missing: list[str] = []
    for key, info in side_props.items():
        for item in info.get("missing") or []:
            label = f"{key}:{item}"
            if label not in props_missing:
                props_missing.append(label)

    base = {
        "std_skipped": source.skipped,
        "a_req_m2": a_req,
        "dt_eff_c": round(dt_eff, 2) if dt_eff else None,
        "duty_kw": duty,
        "u_back_wm2k": u_back,
        "candidates": [],
        "props_missing": props_missing,
        "notes": notes,
        "std_state": "ok" if source.usable else source.state,
    }

    if source.skipped:
        base["estimate"] = standards.estimate_ranges(a_req, source)
        notes.append(
            f"标准表状态 {source.state}:{source.reason or '未加载'};不枚举标准规格,"
            "只给理论需求面积与经验推荐范围"
        )
        return base

    if duty is None:
        notes.append(BLOCK_HINT["NO_DUTY"])
        return base
    if dt_eff is None:
        notes.append(BLOCK_HINT["NO_DT"])
        return base
    if a_req is None:
        notes.append("缺需求面积 A_req(需要 U 或经验 U)")

    tube_fluid = case.get("tube_fluid")
    (case.get("sides") or {}).get(tube_fluid) if tube_fluid else None
    shell_key = None
    for key in ("hot", "cold", "process", "utility"):
        if key in (case.get("sides") or {}) and key != tube_fluid:
            shell_key = key
            break
    (case.get("sides") or {}).get(shell_key) if shell_key else None

    rows = _filter_rows(source, a_req)
    if not rows:
        notes.append("标准表里没有管长 1.5~6 m、管径 19/25 的行可用")
        return base

    built = []
    for st in rows:
        geom = _row_geometry(st)
        built.append(
            _build_candidate(
                case,
                geom,
                side_props,
                tube_fluid,
                shell_key,
                a_req,
                duty,
                dt_eff,
                std_row_key=geom.get("key"),
            )
        )

    base["candidates"] = _rank(built, limit)
    kept = len(base["candidates"])
    if kept < len(built):
        notes.append(
            f"共 {len(built)} 条标准行符合几何条件,按面积与需求面积的接近程度取前 {kept} 条"
        )
    return base


def manual_candidate(case: dict, geom: dict, side_props: dict | None = None) -> dict | None:
    """把人工覆盖的几何算成一条候选(与标准行同口径,便于对照)。

    `geom` 字段与 `hx_design` 的 `geom_*` 对应(zip 后去掉前缀):
    shell_id_mm / tube_od_mm / tube_wall_mm / tube_length_mm / tube_passes /
    tube_pitch_mm / tube_count / arrangement / area_nominal_m2
    """
    do = _num(geom.get("tube_od_mm"))
    if not do:
        return None
    tube_fluid = case.get("tube_fluid")
    shell_key = None
    for key in ("hot", "cold", "process", "utility"):
        if key in (case.get("sides") or {}) and key != tube_fluid:
            shell_key = key
            break
    a_req = _num((case.get("area") or {}).get("required_m2"))
    duty = _num(case.get("duty_kw"))
    dt = _num((case.get("lmtd") or {}).get("used_c"))
    f = _num((case.get("lmtd") or {}).get("f")) or 1.0
    dt_eff = (dt * f) if dt else None
    full = {
        "std_row_key": None,
        "dn_mm": _num(geom.get("shell_id_mm")),
        "shell_id_mm": _num(geom.get("shell_id_mm")),
        "tube_od_mm": do,
        "tube_wall_mm": _num(geom.get("tube_wall_mm")) or 0.0,
        "tube_id_mm": do - 2 * (_num(geom.get("tube_wall_mm")) or 0.0),
        "tube_length_mm": _num(geom.get("tube_length_mm")),
        "tube_pitch_mm": _num(geom.get("tube_pitch_mm")),
        "tube_passes": int(_num(geom.get("tube_passes")) or 1),
        "tube_count": int(_num(geom.get("tube_count")) or 0) or None,
        "area_m2": _num(geom.get("area_nominal_m2")),
        "arrangement": geom.get("arrangement") or "triangular",
        "geom_source": "manual",
    }
    return _build_candidate(
        case, full, side_props or {}, tube_fluid, shell_key, a_req, duty, dt_eff
    )


def _build_candidate(
    case: dict,
    geom: dict,
    side_props: dict,
    tube_fluid: str | None,
    shell_key: str | None,
    a_req: float | None,
    duty: float | None,
    dt_eff: float | None,
    std_row_key: str | None = None,
) -> dict:
    """把一条几何(标准行或人工填)算成候选:两侧水力 + U + 接管。"""
    tube_side = (case.get("sides") or {}).get(tube_fluid) if tube_fluid else None
    shell_side = (case.get("sides") or {}).get(shell_key) if shell_key else None
    b = baffle_spacing_mm(geom.get("shell_id_mm"))
    t = tube_side_flow(geom, tube_side or {}, side_props.get(tube_fluid or "", {}), tube_fluid)
    s = shell_side_flow(geom, shell_side or {}, side_props.get(shell_key or "", {}), b)
    u = u_from_h(
        t["h_wm2k"],
        s["h_wm2k"],
        (case.get("fouling") or {}).get("tube"),
        (case.get("fouling") or {}).get("shell"),
    )
    item = {
        **geom,
        "std_row_key": std_row_key if std_row_key is not None else geom.get("std_row_key"),
        "area_std_m2": _num(geom.get("area_m2")),
        "area_req_m2": a_req,
        "area_ratio": round(_num(geom.get("area_m2")) / a_req, 3)
        if (a_req and _num(geom.get("area_m2")))
        else None,
        "area_needed_m2": (
            round(duty * 1000.0 / (u["u_dirty_wm2k"] * dt_eff), 1)
            if (u["u_dirty_wm2k"] and dt_eff and duty)
            else None
        ),
        "tube": t,
        "shell": s,
        **u,
        "nozzle": {
            "tube_in": nozzle_size(
                (tube_side or {}).get("vol_flow_m3h"), _side_phase(tube_side or {})
            ),
            "shell_in": nozzle_size(
                (shell_side or {}).get("vol_flow_m3h"), _side_phase(shell_side or {})
            ),
        },
        "baffle_mm": b,
        "geom_source": geom.get("geom_source") or "standard",
    }
    item["area_need_ratio"] = (
        round(item["area_needed_m2"] / item["area_std_m2"], 3)
        if (item["area_needed_m2"] and item["area_std_m2"])
        else None
    )
    return item


def _filter_rows(source: standards.Snapshot, a_req: float | None) -> list:
    """只保留能覆盖 A_req 的行;没有够大的就取最大三档,避免给一堆小壳。"""
    rows = list(source.rows)
    if not rows:
        return []
    if a_req:
        adequate = [r for r in rows if r.area_m2 >= a_req * 0.85]
        if adequate:
            return adequate
        rows.sort(key=lambda r: r.area_m2, reverse=True)
        return rows[:6]
    return rows


def _rank(items: list[dict], limit: int) -> list[dict]:
    """按"公称面积/需求面积"最接近 1 排序(压降与判定由 `rating` 做,这里不越界)。"""

    def key(item):
        ratio = item.get("area_ratio")
        return abs((ratio or 0) - 1.0)

    return sorted(items, key=key)[:limit]
