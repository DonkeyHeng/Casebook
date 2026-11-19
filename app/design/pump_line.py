"""泵两侧管段(吸入 / 排出)的水力计算 —— 纯函数,不碰 Aspen、不读库。

口径来源:`docs/_internal/pump-line-sync-plan-draft.md`(D1–D21),对外口径稍后
并入 `docs/pump-selection-plan.md`。

为什么单独一个模块
------------------
「管径 + 管长 + Σζ → u / Re / λ / Σhf」以前只有一份,藏在泵面板的一次请求里,
结果还被当成计算值存进 `pump_design.hf_m`。现在管道页与泵面板共用一份管段记录,
计算必须只在这里做一次:页面、Excel、AI 包、报告四个出口拿同一组数。

三条硬约定
----------
* 缺输入就返回 `None` 并写明缺什么,不拿 0 或常见值顶替;
* 两侧分别算,合计 = 两项相加;缺一侧时只给「已计阻力」,设计扬程不给;
* 修正 NPSHₐ 必须由**吸入段自己的**位压头与 Σhf 扣出来,拆不出就判 `unknown`。
"""

from __future__ import annotations

import math

from app.design import hydraulics
from app.design import pump as pump_rules

#: 手册惯例,与前端 PUMP_DESIGN_MARGIN / pump_selection.DESIGN_MARGIN 一致
DESIGN_MARGIN = 1.1
#: §6.3 建议余量 0.5~1.0 m;人没填时用下限,并标明用的是建议值
NPSH_MARGIN_SUGGESTED = 0.5

#: 管段状态(界面按这几个值决定徽标与可编辑性)
ST_LINKED = "linked"  # 自动关联,管道页给出了规格
ST_MANUAL = "manual"  # 记录里有人工 DN / 自定义外径
ST_UNSET = "unset"  # 认出来了,但 L / Σζ 还没填
ST_UNLINKABLE = "unlinkable"  # 管道页给不出规格(零流量 / 超档 / 标准表未录)
ST_MISSING = "missing"  # 泵这一侧没有连接流股
ST_AMBIGUOUS = "ambiguous"  # 同一侧多于一条流股,待指定


def _num(v):
    """只认有限数字;缺数据一律 None,不拿 0 顶替。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def stream_side(connections, block: str, direction: str, pick: str | None = None) -> dict:
    """泵某一侧的流股候选。

    返回 `{status, streams, stream, reason}`:
      * 恰好一条(或人工指定在候选里)→ linked;
      * 0 条 → missing;
      * 多条且没指定 → ambiguous,由界面给下拉让人指定,绝不按名字猜。
    """
    names = sorted(
        {
            str(c.get("stream") or "").strip()
            for c in connections or []
            if str(c.get("block") or "") == str(block)
            and str(c.get("direction") or "").lower() == direction
            and str(c.get("stream") or "").strip()
        }
    )
    chosen = str(pick or "").strip()
    if chosen and chosen in names:
        return {"status": ST_LINKED, "streams": names, "stream": chosen, "reason": ""}
    if len(names) == 1:
        return {"status": ST_LINKED, "streams": names, "stream": names[0], "reason": ""}
    if not names:
        label = "进口" if direction == "in" else "出口"
        return {
            "status": ST_MISSING,
            "streams": [],
            "stream": None,
            "reason": f"泵缺{label}流股,无法关联管路",
        }
    label = "进口" if direction == "in" else "出口"
    return {
        "status": ST_AMBIGUOUS,
        "streams": names,
        "stream": None,
        "reason": f"泵有 {len(names)} 条{label}流股,请指定哪一条是这条管路",
    }


def _stream_index(streams) -> dict:
    return {str(s.get("name")): dict(s) for s in streams or []}


def _row_index(rows) -> dict:
    return {str(r.get("stream")): dict(r) for r in rows or []}


def _missing_index(missing) -> dict:
    out = {}
    for item in missing or []:
        if isinstance(item, dict) and item.get("stream"):
            out[str(item["stream"])] = item
    return out


def _custom_wall(dn: int, od_mm: float, pipe_row: dict | None) -> tuple[float | None, str]:
    """人工 DN 时按该外径重算标准壁厚;算不出就返回 (None, 原因)。

    不沿用自动档位的壁厚 —— 那等于拿 DN65 的壁厚配 DN125 的管子。
    需要设计压力与许用应力:管道页已算过(auto row 里有),没有就不给,
    让人填「自定义外径 + 壁厚」,不猜。
    """
    if not pipe_row:
        return None, "缺设计压力 / 许用应力,无法按该 DN 重算壁厚;请填自定义外径与壁厚"
    try:
        from app.design.pipe import calc_wall_thickness, round_up_wall
        from app.design.pipe import data as pipe_data
    except Exception:  # pragma: no cover - 标准表模块导入失败时按缺数据处理
        return None, "标准表模块不可用,无法重算壁厚"

    pressure = _num(pipe_row.get("design_pressure_mpa"))
    sigma = _num(pipe_row.get("stress_mpa"))
    if pressure is None or sigma is None:
        return None, "缺设计压力 / 许用应力,无法按该 DN 重算壁厚;请填自定义外径与壁厚"
    calc = calc_wall_thickness(pressure, od_mm, sigma)
    t_calc = _num(calc.get("t_calc_mm"))
    if t_calc is None:
        return None, str(calc.get("note") or "壁厚公式算不出")
    c2 = _num(pipe_row.get("c2_mm")) or 0.0
    t_req = t_calc + c2
    walls = pipe_data.OD_TO_WALLS.get(float(od_mm)) or pipe_data.OD_TO_WALLS_SST.get(float(od_mm))
    if not walls:
        return None, f"Φ{od_mm:g} 没有已录入的标准壁厚(T2),无法重算"
    min_wall = pipe_data.MIN_WALL_BY_DN.get(int(dn))
    got = round_up_wall(t_req, walls, od_mm, pipe_data.DEFAULT_WALL_TOL_GRADE, min_wall)
    wall = _num(got.get("wall_mm"))
    if wall is None:
        return None, str(got.get("note") or "所需壁厚超出该外径标准序列")
    return wall, ""


def _dn_from_overrides(run: dict | None, pipe_row: dict | None) -> dict:
    """这条流股的管径口径:人工优先,否则跟随管道页当前结果。

    返回 `{dn, od_mm, wall_mm, source, note}`。source 取 auto / manual / custom。
    """
    run = run or {}
    dn_override = _num(run.get("dn_override"))
    od_override = _num(run.get("od_override_mm"))
    wall_override = _num(run.get("wall_override_mm"))
    if od_override and wall_override:
        return {
            "dn": int(dn_override) if dn_override else None,
            "od_mm": od_override,
            "wall_mm": wall_override,
            "source": "custom",
            "note": "自定义外径 / 壁厚(专项设计)",
        }
    if dn_override:
        dn = int(dn_override)
        try:
            from app.design.pipe import data as pipe_data
        except Exception:  # pragma: no cover
            pipe_data = None
        od = float(pipe_data.DN_TO_OD.get(dn)) if pipe_data else None
        if od is None:
            return {
                "dn": dn,
                "od_mm": None,
                "wall_mm": None,
                "source": "manual",
                "note": f"DN{dn} 不在已录入的 DN→外径表里,请改填自定义外径与壁厚",
            }
        wall, note = _custom_wall(dn, od, pipe_row)
        return {"dn": dn, "od_mm": od, "wall_mm": wall, "source": "manual", "note": note}
    if pipe_row:
        return {
            "dn": pipe_row.get("dn"),
            "od_mm": _num(pipe_row.get("od_mm")),
            "wall_mm": _num(pipe_row.get("wall_mm")),
            "source": "auto",
            "note": "",
        }
    return {"dn": None, "od_mm": None, "wall_mm": None, "source": "auto", "note": ""}


def _inner_mm(od_mm, wall_mm):
    od, wall = _num(od_mm), _num(wall_mm)
    if od is None or wall is None:
        return None
    inner = od - 2.0 * wall
    return inner if inner > 0 else None


def _line_common(role: str, stream: str, status: str, reason: str = "") -> dict:
    return {
        "role": role,
        "stream": stream,
        "status": status,
        "reason": reason,
        "dn": None,
        "od_mm": None,
        "wall_mm": None,
        "id_mm": None,
        "dn_source": "auto",
        "flow_m3h": None,
        "rho_kgm3": None,
        "mu_cp": None,
        "mu_source": "",
        "u_ms": None,
        "re": None,
        "lam": None,
        "hf_m": None,
        "l_m": None,
        "zeta_sum": None,
        "dz_m": None,
        "lambda_method": hydraulics.METHOD_COLEBROOK,
        "fittings": {},
        "missing": [],
        "note": "",
    }


def compute_line(
    role: str,
    side: dict,
    *,
    pipe_rows,
    pipe_missing,
    streams,
    block: dict,
    run: dict | None,
    eps_mm=None,
) -> dict:
    """算一侧管段。role 取 `suction` / `discharge`。

    `side` 是 `stream_side()` 的结果;`pipe_rows` / `pipe_missing` 来自管道页
    同一次 `pipe.build()`(同一套参数),不再自己另算一遍。
    """
    sidx = _stream_index(streams)
    ridx = _row_index(pipe_rows)
    midx = _missing_index(pipe_missing)
    run = run or {}
    stream = side.get("stream")
    out = _line_common(role, stream, side.get("status") or ST_MISSING, side.get("reason") or "")
    if not stream:
        return out

    s = sidx.get(stream, {})
    row = ridx.get(stream)
    miss = midx.get(stream)
    dninfo = _dn_from_overrides(run, row)
    out["dn"] = dninfo["dn"]
    out["od_mm"] = dninfo["od_mm"]
    out["wall_mm"] = dninfo["wall_mm"]
    out["dn_source"] = dninfo["source"]
    out["note"] = dninfo["note"]
    out["id_mm"] = _inner_mm(dninfo["od_mm"], dninfo["wall_mm"])

    flow = _num(row.get("vol_flow_m3h")) if row else _num(s.get("vol_flow_m3h"))
    out["flow_m3h"] = flow
    mass = _num(s.get("mass_flow_kgh"))
    vol = _num(s.get("vol_flow_m3h"))
    if mass is not None and vol and vol > 0:
        out["rho_kgm3"] = mass / vol
    mu_manual = _num(run.get("mu_cp"))
    if mu_manual is not None:
        out["mu_cp"] = mu_manual
        out["mu_source"] = "manual"
    else:
        out["mu_cp"] = _num(block.get("mu_cp"))
        out["mu_source"] = "aspen" if out["mu_cp"] is not None else ""

    l_m = _num(run.get("l_m"))
    zeta = _num(run.get("zeta_sum"))
    dz = _num(run.get("dz_m"))
    method = str(run.get("lambda_method") or hydraulics.METHOD_COLEBROOK)
    if method not in (
        hydraulics.METHOD_COLEBROOK,
        hydraulics.METHOD_GU,
        hydraulics.METHOD_MANUAL,
    ):
        method = hydraulics.METHOD_COLEBROOK
    out["l_m"] = l_m
    out["zeta_sum"] = zeta
    out["dz_m"] = dz
    out["lambda_method"] = method
    fittings = run.get("fittings")
    out["fittings"] = dict(fittings) if isinstance(fittings, dict) else {}

    # 管道页给不出规格:位置已经认出来,但管径没有着落。
    # 若人工填了外径/壁厚或 DN,仍继续算;否则明确说明原因。
    if row is None and dninfo["source"] == "auto":
        out["status"] = ST_UNLINKABLE
        out["reason"] = str((miss or {}).get("reason") or "管道页没有给出这条流股的规格")
        return out

    if out["id_mm"] is None:
        out["status"] = ST_UNLINKABLE
        if not out["reason"]:
            out["reason"] = out["note"] or "没有可用的管内径"
        return out

    missing = []
    if flow is None:
        missing.append("体积流量")
    if out["rho_kgm3"] is None:
        missing.append("密度")
    if out["mu_cp"] is None:
        missing.append("粘度")
    if l_m is None:
        missing.append("管长 L")
    if zeta is None:
        missing.append("管件系数 Σζ")
    out["missing"] = missing

    # 有管径就先算 u,让人看见「DN 选得对不对」;L/Σζ 缺了才没有 Σhf
    if flow is not None:
        out["u_ms"] = hydraulics.velocity_ms(flow, out["id_mm"])
    solved = hydraulics.solve(
        vol_m3h=flow,
        rho_kgm3=out["rho_kgm3"],
        mu_cp=out["mu_cp"],
        id_mm=out["id_mm"],
        l_m=l_m,
        zeta_sum=zeta,
        eps_mm=eps_mm,
        method=method,
        lam_manual=_num(run.get("lambda_manual")),
    )
    out["u_ms"] = solved.get("u_ms")
    out["re"] = solved.get("re")
    out["lam"] = solved.get("lam")
    out["hf_m"] = solved.get("hf_m")
    out["missing"] = solved.get("missing") or missing

    if out["hf_m"] is None:
        out["status"] = ST_UNSET
    elif dninfo["source"] in ("manual", "custom"):
        out["status"] = ST_MANUAL
    else:
        out["status"] = ST_LINKED
    return out


def _sum(values):
    nums = [_num(v) for v in values]
    return None if any(v is None for v in nums) else sum(nums)


def compute(
    block: dict,
    connections,
    streams,
    pipe_rows,
    pipe_missing,
    runs: dict | None = None,
    saved: dict | None = None,
    eps_mm=None,
    picks: dict | None = None,
) -> dict:
    """一台泵的两侧管段 + 合计 + 修正 NPSHₐ + 三态。

    `runs` 是 `{stream: pipe_run 行}`;没有记录时传空 dict。
    `saved` 是选型台账行(只取 NPSHᵣ 与余量),没有就传 None。
    `picks` 是人工指定的 `{"suction": 流股, "discharge": 流股}`,没指定传 None。
    """
    runs = runs or {}
    saved = saved or {}
    picks = picks or {}
    side_in = stream_side(connections, str(block.get("name") or ""), "in", picks.get("suction"))
    side_out = stream_side(connections, str(block.get("name") or ""), "out", picks.get("discharge"))

    def _run(stream):
        return runs.get(stream) if stream else None

    suction = compute_line(
        "suction",
        side_in,
        pipe_rows=pipe_rows,
        pipe_missing=pipe_missing,
        streams=streams,
        block=block,
        run=_run(side_in.get("stream")),
        eps_mm=eps_mm,
    )
    discharge = compute_line(
        "discharge",
        side_out,
        pipe_rows=pipe_rows,
        pipe_missing=pipe_missing,
        streams=streams,
        block=block,
        run=_run(side_out.get("stream")),
        eps_mm=eps_mm,
    )

    hf = _sum([suction.get("hf_m"), discharge.get("hf_m")])
    dz = _sum([suction.get("dz_m"), discharge.get("dz_m")])
    head = _num(block.get("head_m"))
    design = _sum([head, dz, hf]) if head is not None else None
    selection = math.ceil(design * DESIGN_MARGIN) if design is not None else None

    # 缺一侧时给「已计」值:看得见进展,但不让它冒充设计扬程
    partial_hf = sum(v for v in (suction.get("hf_m"), discharge.get("hf_m")) if _num(v) is not None)
    partial_dz = sum(v for v in (suction.get("dz_m"), discharge.get("dz_m")) if _num(v) is not None)
    partial = _num(head) + partial_hf + partial_dz if _num(head) is not None else None

    p_in = _num(block.get("p_in_bar"))
    p_v = _num(block.get("pv_bar"))
    rho = _num(block.get("rho_kgm3"))
    device = pump_rules.npsh_available_m(p_in, p_v, rho)

    npsh_missing = []
    if p_v is None:
        npsh_missing.append("p_v")
    if p_in is None:
        npsh_missing.append("进口压力")
    if not rho:
        npsh_missing.append("密度")
    corrected = None
    if device is not None:
        if suction.get("dz_m") is not None and suction.get("hf_m") is not None:
            corrected = device - suction["dz_m"] - suction["hf_m"]
        else:
            npsh_missing.append("吸入段阻力 / 位压头")
    elif not npsh_missing:
        npsh_missing.append("NPSHₐ(读数不完整)")

    npshr = _num(saved.get("npshr_m"))
    margin_given = _num(saved.get("margin_m"))
    margin = margin_given if margin_given is not None else NPSH_MARGIN_SUGGESTED

    if npsh_missing:
        state = "unknown"
        reason = "缺 " + "、".join(npsh_missing) + ",无法判定"
        threshold = None
    elif npshr is None:
        state = "unknown"
        reason = "缺 NPSHᵣ,无法判定"
        threshold = None
    else:
        threshold = round(npshr + margin, 4)
        state = "ok" if threshold <= corrected else "bad"
        reason = (
            f"NPSHᵣ {npshr:g} + 余量 {margin:g} = {threshold:g} m "
            f"{'≤' if state == 'ok' else '>'} NPSHₐ(修正) {corrected:g} m"
            + (f"(装置值 {device:g} m)" if device is not None else "")
        )

    return {
        "suction": suction,
        "discharge": discharge,
        "totals": {
            "head_m": head,
            "dz_m": dz,
            "hf_m": hf,
            "design_head_m": design,
            "selection_head_m": selection,
            "partial_head_m": partial,
            "partial_hf_m": partial_hf,
            "partial_dz_m": partial_dz,
        },
        "npsh": {
            "device_m": device,
            "corrected_m": corrected,
            "npsh_r_m": npshr,
            "margin_used_m": margin,
            "margin_is_default": margin_given is None,
            "threshold_m": threshold,
            "state": state,
            "reason": reason,
            "missing": npsh_missing,
        },
        "design_margin": DESIGN_MARGIN,
        "rule": "H = 压升扬程 + (ΔZ_吸 + ΔZ_排) + (Σhf_吸 + Σhf_排);"
        "NPSHₐ(修正) = NPSHₐ(装置) − ΔZ_吸 − Σhf_吸",
    }
