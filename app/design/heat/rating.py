"""L2 五态判定(方案 §7)。**唯一给结论的地方**,几何与水力的数值只作为输入。

五态
----
* `blocked` —— L1 已拦截(相变/温度交叉/多股…),L2 不越界重算;
* `unknown` —— 缺关键数据(物性、流量、负荷、ΔTm、U),不给结论;
* `fail`    —— 简捷计算明确不过(压降超允许、面积不足);
* `reference` —— 算得出但**含任意假设输入**(经验 U、F 未修正、公用工程预设、
               cp 反推、标准表 partial、人工覆盖几何…),只能当参考;
* `pass`    —— 全部输入是实测、压降与面积都过。**只有这一态能写"简捷通过"**,
               且必须在台账里同时注明"未做 EDR rating"。

为什么把"假设"顶到 reference
--------------------------
用户最容易被"通过"两个字误导。经验 U 或 F=1 假设下算出来的"通过"并不成立,
所以只要有一条假设,结论就封顶在 reference —— 这是 §7.3 的硬约束,不是风格选择。
"""

from __future__ import annotations

from . import geometry, params, standards

VERDICT_TEXT = {
    "blocked": "已拦截",
    "unknown": "缺数据",
    "fail": "简捷未通过",
    "reference": "参考",
    "pass": "简捷通过",
}
VERDICT_RANK = {"pass": 0, "reference": 1, "fail": 2, "unknown": 3, "blocked": 4}

#: 这些 L1 警告本身就说明输入是假设 → 判定封顶 reference
ASSUMPTION_WARNINGS = {
    "U_ASSUMED",
    "F_UNCOVERED",
    "UTILITY_PRESET",
    "UTILITY_MANUAL",
    "TUBE_FLUID_RULE",
    "DTLM_FROM_ASPEN",
}

NOTE_TEXT = {
    "AREA_SHORT": "标准公称面积小于需求面积",
    "AREA_LARGE": "标准公称面积比需求面积大 30% 以上,偏保守",
    "AREA_NEED": "按本次算出的 U,这条几何的真实需求面积超过了它的公称面积",
    "DP_TUBE": "管程压降超过允许值",
    "DP_SHELL": "壳程压降超过允许值",
    "DP_UNKNOWN": "缺流量/密度/物性,压降算不出",
    "ASSUMED_INPUT": "含假设输入(见 warnings),结论只能是参考",
    "OVERSIZE_PASS": "压降与面积都过,但含假设输入",
}

#: 面积比阈值:低于这个算不足,高于这个算偏保守(方案 §7.2)
AREA_SHORT_RATIO = 0.98
AREA_LARGE_RATIO = 1.30
#: 真实需求面积/公称面积超过这个 → 判不足
AREA_NEED_LIMIT = 1.05


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def assumptions_for(
    case: dict, side_props: dict | None = None, std_state: str = "ok", geom_source=None
) -> list[str]:
    """列出这台设备身上的"假设输入"。空列表 = 全部实测。"""
    out: list[str] = []
    for code in case.get("warnings") or []:
        if code in ASSUMPTION_WARNINGS and params.WARNING_TEXT.get(code, code) not in out:
            out.append(params.WARNING_TEXT.get(code, code))
    for key, info in (side_props or {}).items():
        sources = (info or {}).get("source") or {}
        if sources.get("cp") == "derived":
            label = f"{key} 侧 cp 由 Q/(m·ΔT) 反推"
            if label not in out:
                out.append(label)
    if std_state in ("partial", "missing", "invalid"):
        out.append("标准表不是完整 ok 状态")
    if geom_source == "manual":
        out.append("几何为人工覆盖")
    return out


def judge(
    case: dict,
    cand: dict,
    assumptions: list[str] | None = None,
    case_blocked: bool | None = None,
) -> dict:
    """判一条候选。返回 {verdict, notes, checks, warnings}。

    `case_blocked` 缺省看 `case["verdict"] == "blocked"` —— L2 遇到 L1 拦截就不重算。
    """
    checks: dict = {}
    notes: list[str] = []
    warnings: list[str] = list((cand.get("tube") or {}).get("warnings") or [])
    warnings.extend((cand.get("shell") or {}).get("warnings") or [])

    blocked = (case.get("verdict") == "blocked") if case_blocked is None else case_blocked
    if blocked:
        return {
            "verdict": "blocked",
            "text": VERDICT_TEXT["blocked"],
            "notes": ["L1 已拦截,不做 L2 几何判定"],
            "checks": checks,
            "warnings": [],
        }

    if _num(case.get("duty_kw")) is None:
        return _unknown("缺热负荷", checks, warnings)
    dt = _num((case.get("lmtd") or {}).get("used_c"))
    if dt is None:
        return _unknown("缺有效平均温差 ΔTm", checks, warnings)

    tube = cand.get("tube") or {}
    shell = cand.get("shell") or {}
    h_t = _num(tube.get("h_wm2k"))
    h_s = _num(shell.get("h_wm2k"))
    if h_t is None or h_s is None:
        missing = [f"管程 {m}" for m in (tube.get("missing") or [])] + [
            f"壳程 {m}" for m in (shell.get("missing") or [])
        ]
        return _unknown(
            "缺" + "、".join(dict.fromkeys(missing)) if missing else "缺膜系数", checks, warnings
        )
    if not _num(cand.get("u_dirty_wm2k")):
        return _unknown("U 算不出", checks, warnings)

    # 压降
    allow_t = _num(((case.get("design") or {}).get("tube") or {}).get("dp_allow_bar"))
    allow_s = _num(((case.get("design") or {}).get("shell") or {}).get("dp_allow_bar"))
    dp_t = _num(tube.get("dp_bar"))
    dp_s = _num(shell.get("dp_bar"))
    checks["dp_tube"] = _ratio_check(dp_t, allow_t, "DP_TUBE", notes)
    checks["dp_shell"] = _ratio_check(dp_s, allow_s, "DP_SHELL", notes)

    # 面积:A_std vs A_req(L1 需求面积,含裕量) + 真实需求面积 vs A_std
    a_std = _num(cand.get("area_std_m2"))
    a_req = _num(cand.get("area_req_m2"))
    ratio = _num(cand.get("area_ratio"))
    checks["area_ratio"] = ratio
    area_short = False
    if a_std and a_req:
        if ratio is not None and ratio < AREA_SHORT_RATIO:
            notes.append(NOTE_TEXT["AREA_SHORT"])
            area_short = True
        elif ratio is not None and ratio > AREA_LARGE_RATIO:
            notes.append(NOTE_TEXT["AREA_LARGE"])
            warnings.append("AREA_LARGE")
    need_ratio = _num(cand.get("area_need_ratio"))
    checks["area_need_ratio"] = need_ratio
    if need_ratio is not None and need_ratio > AREA_NEED_LIMIT:
        notes.append(NOTE_TEXT["AREA_NEED"])
        area_short = True

    # U 自洽:反算 U 与几何算的 U 差异过大只告警(可能 U 来自经验值)
    u_back = _num((case.get("u") or {}).get("back_calc_wm2k"))
    u_dirty = _num(cand.get("u_dirty_wm2k"))
    if u_back and u_dirty and abs(u_dirty - u_back) / u_back > geometry.U_SELF_CONSISTENCY:
        warnings.append("U_MISMATCH")

    # 接管:只提示未核算
    for key in ("tube_in", "shell_in"):
        nz = (cand.get("nozzle") or {}).get(key) or {}
        if nz and nz.get("dn_mm"):
            warnings.append("NOZZLE_ESTIMATED")
            break

    warnings = list(dict.fromkeys(warnings))

    if checks["dp_tube"].get("over") or checks["dp_shell"].get("over") or area_short:
        return {
            "verdict": "fail",
            "text": VERDICT_TEXT["fail"],
            "notes": notes,
            "checks": checks,
            "warnings": warnings,
        }

    if assumptions:
        notes.append(NOTE_TEXT["ASSUMED_INPUT"])
        return {
            "verdict": "reference",
            "text": VERDICT_TEXT["reference"],
            "notes": notes,
            "checks": checks,
            "warnings": warnings,
        }

    return {
        "verdict": "pass",
        "text": VERDICT_TEXT["pass"],
        "notes": notes + ["简捷通过,未做 EDR rating"],
        "checks": checks,
        "warnings": warnings,
    }


def _unknown(reason: str, checks: dict, warnings: list[str]) -> dict:
    return {
        "verdict": "unknown",
        "text": VERDICT_TEXT["unknown"],
        "notes": [reason],
        "checks": checks,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _ratio_check(value, allow, code: str, notes: list[str]) -> dict:
    """压降检查:{value, allow, ratio, over}。缺任一值 → over=None(不是失败)。"""
    v, a = _num(value), _num(allow)
    if v is None or a is None or a <= 0:
        notes.append(NOTE_TEXT["DP_UNKNOWN"])
        return {"value": v, "allow": a, "ratio": None, "over": None}
    ratio = v / a
    if ratio > 1.0:
        notes.append(NOTE_TEXT[code])
    return {"value": v, "allow": a, "ratio": round(ratio, 3), "over": ratio > 1.0}


def select(
    case: dict,
    candidates: list[dict],
    side_props: dict | None = None,
    std_state: str = "ok",
    geom_source=None,
    limit: int = 8,
) -> dict:
    """给所有候选判一遍并按"更值得推荐"排序。"""
    assumptions = assumptions_for(case, side_props, std_state, geom_source)
    judged = []
    for cand in candidates:
        verdict = judge(case, cand, assumptions)
        item = dict(cand)
        item["verdict"] = verdict["verdict"]
        item["verdict_text"] = verdict["text"]
        item["notes"] = verdict["notes"]
        item["checks"] = verdict["checks"]
        item["warnings"] = verdict["warnings"]
        judged.append(item)

    judged.sort(
        key=lambda c: (
            VERDICT_RANK.get(c["verdict"], 9),
            c.get("area_need_ratio") if c.get("area_need_ratio") is not None else 9,
            c.get("area_ratio") if c.get("area_ratio") is not None else 9,
        )
    )
    kept = judged[:limit]
    recommended = next((c for c in kept if c["verdict"] in ("pass", "reference")), None)
    counts = {}
    for item in kept:
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1
    overall = _overall(case, kept, assumptions)

    return {
        "candidates": kept,
        "recommended_key": recommended.get("std_row_key") if recommended else None,
        "recommended": recommended,
        "assumptions": assumptions,
        "counts": counts,
        "verdict": overall,
        "verdict_text": VERDICT_TEXT[overall],
    }


def _overall(case: dict, kept: list[dict], assumptions: list[str]) -> str:
    """设备级结论:取候选里最好的那一态(仍受 L1 与假设封顶)。"""
    if case.get("verdict") == "blocked":
        return "blocked"
    if not kept:
        return "unknown"
    best = min(kept, key=lambda c: VERDICT_RANK.get(c["verdict"], 9))
    if best["verdict"] == "pass" and assumptions:
        return "reference"
    return best["verdict"]


def evaluate(
    case: dict,
    side_props: dict | None = None,
    stored: dict | None = None,
    snap: standards.Snapshot | None = None,
    limit: int = 8,
) -> dict:
    """一步到位:L1 case → 几何候选 + 五态判定 + 人工覆盖对照。

    API 的详情页与 xlsx 导出共用这一份 —— 页面看到的与导出的一定一致。
    `stored` 是 `hx_design` 的一行,用于取人工覆盖的几何与 `geom_source`。
    """
    stored = stored or {}
    geo = geometry.candidates(case, side_props, snap, limit)
    state = (snap or standards.snapshot()).state
    judged = select(
        case,
        geo.get("candidates") or [],
        side_props,
        std_state=state,
        geom_source=stored.get("geom_source"),
        limit=limit,
    )
    geo.update(judged)

    manual = None
    if stored.get("geom_source") == "manual":
        geom = {k[5:]: v for k, v in stored.items() if k.startswith("geom_")}
        geom["area_nominal_m2"] = stored.get("area_nominal_m2")
        manual = geometry.manual_candidate(case, geom, side_props)
        if manual:
            verdict = judge(case, manual, geo.get("assumptions") or [])
            manual.update(verdict)
            manual["std_row_key"] = stored.get("std_row_key")
            manual["selected"] = True
    geo["manual"] = manual
    return geo


def describe(case: dict, result: dict) -> str:
    """一句话结论(台账/导出共用)。"""
    text = f"{result.get('verdict_text') or VERDICT_TEXT['unknown']}("
    key = result.get("recommended_key")
    if key:
        text += f"推荐 {key}"
    else:
        text += "无推荐规格"
    if result.get("std_skipped"):
        text += ",未加载标准表"
    return text + ")"
