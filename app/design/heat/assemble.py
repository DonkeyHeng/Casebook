"""把库里的设备/流股/人工输入拼成 L1 结果行(纯函数)。

API 与导出共用这一份,避免"页面看到的"和"导出的"两套口径。
本模块不读 Aspen、不开库:调用方把数据取好传进来。
"""

from __future__ import annotations

from app.design.heat import model_code, params

HEAT_TYPES = ("HeatX", "MHeatX", "Heater", "HeaterX")


def build_row(block: dict, conns, streams, stored: dict, selection: dict, configs: dict) -> dict:
    """一台设备 → 一行(含 case 结果、编码、已存的设计输入)。"""
    result = params.build_design(
        block,
        conns,
        streams,
        fouling_cfg=configs.get("fouling"),
        u_cfg=configs.get("u-reference"),
        preset_cfg=configs.get("utility-presets"),
        fouling_tube_key=stored.get("fouling_tube_key"),
        fouling_shell_key=stored.get("fouling_shell_key"),
        u_ref_key=stored.get("u_ref_key"),
        tube_fluid=stored.get("tube_fluid"),
        tube_fluid_source=stored.get("tube_fluid_source"),
        utility_preset_key=stored.get("utility_key"),
        utility_manual=_utility_manual(stored),
        margin=stored.get("margin") if stored.get("margin") is not None else 0.30,
        f_correction=stored.get("f_correction"),
        area_manual_m2=stored.get("area_manual_m2"),
    )

    # 人工覆盖的设计条件优先(保留计算值在 case.design 里供对照)
    tube_over = {
        "p_design_bar": stored.get("p_design_tube_bar"),
        "t_design_c": stored.get("t_design_tube_c"),
        "dp_allow_bar": stored.get("dp_allow_tube_bar"),
    }
    shell_over = {
        "p_design_bar": stored.get("p_design_shell_bar"),
        "t_design_c": stored.get("t_design_shell_c"),
        "dp_allow_bar": stored.get("dp_allow_shell_bar"),
    }
    for cond, over in (
        (result["design"]["tube"], tube_over),
        (result["design"]["shell"], shell_over),
    ):
        for key, value in over.items():
            if value is not None:
                cond[key] = value

    code = model_code.build_code(
        front=stored.get("head_code") or "B",
        shell=stored.get("shell_code") or "E",
        rear=stored.get("rear_code") or "M",
        dn_mm=stored.get("geom_shell_id_mm"),
        p_tube_bar=result["design"]["tube"].get("p_design_bar"),
        p_shell_bar=result["design"]["shell"].get("p_design_bar"),
        area_nominal_m2=stored.get("area_nominal_m2"),
        tube_length_m=(stored.get("geom_tube_length_mm") or 0) / 1000 or None,
        tube_od_mm=stored.get("geom_tube_od_mm"),
        passes=stored.get("geom_tube_passes"),
        bundle_class=stored.get("bundle_grade") or "I",
    )

    return {
        "block": block["name"],
        "type": block.get("type"),
        "case": result,
        "design_row": stored,
        "selection": selection,
        "model_code": code,
    }


def assemble(blocks, conns, streams, designs: dict, selections: dict, configs: dict) -> list[dict]:
    """批量拼行;只处理换热类设备。"""
    rows = []
    for block in blocks:
        if (block.get("type") or "") not in HEAT_TYPES:
            continue
        name = block["name"]
        rows.append(
            build_row(
                block,
                conns,
                streams,
                designs.get(name, {}),
                selections.get(name, {}),
                configs,
            )
        )
    return rows


def _utility_manual(stored: dict) -> dict | None:
    if stored.get("utility_mode") != "manual":
        return None
    if stored.get("utility_t_in_c") is None or stored.get("utility_t_out_c") is None:
        return None
    return {
        "t_in_c": stored["utility_t_in_c"],
        "t_out_c": stored["utility_t_out_c"],
        "phase": stored.get("utility_phase"),
    }


def edr_checklist(rows: list[dict]) -> list[dict]:
    """按「冷热侧物性 / 允许压降 / 污垢 / L2 初选几何」分组的 EDR 录入清单。

    Phase 1 只填已知项;未知项留空并写 `note`,不做假值。
    """
    out = []
    for row in rows:
        c = row["case"]
        stored = row.get("design_row") or {}
        selection = row.get("selection") or {}
        hot = c.get("hot") or c.get("sides", {}).get("hot") or {}
        cold = c.get("cold") or c.get("sides", {}).get("cold") or {}
        tube_side = c.get("sides", {}).get(c.get("tube_fluid")) or {}
        shell_side = {}
        for key in ("hot", "cold", "process", "utility"):
            if key in c.get("sides", {}) and key != c.get("tube_fluid"):
                shell_side = c["sides"][key]
                break

        out.append(
            {
                "设备位号": row["block"],
                "设备类型": row.get("type"),
                "用途/名称": selection.get("service_name") or "",
                "台数": selection.get("qty") or 1,
                "热负荷 kW": c.get("duty_kw"),
                "热侧进口 ℃": hot.get("t_in_c"),
                "热侧出口 ℃": hot.get("t_out_c"),
                "冷侧进口 ℃": cold.get("t_in_c"),
                "冷侧出口 ℃": cold.get("t_out_c"),
                "ΔTm(自算) ℃": c.get("lmtd", {}).get("self_c"),
                "ΔTm(Aspen) ℃": c.get("lmtd", {}).get("aspen_c"),
                "F": c.get("lmtd", {}).get("f"),
                "面积 m²": c.get("area", {}).get("used_m2"),
                "面积来源": c.get("area", {}).get("source"),
                "需求面积 m²": c.get("area", {}).get("required_m2"),
                "U 反算 W/(m²·K)": c.get("u", {}).get("back_calc_wm2k"),
                "U 设计 W/(m²·K)": c.get("u", {}).get("aspen_wm2k"),
                "U 来源": c.get("u", {}).get("source"),
                "管程侧": c.get("tube_fluid"),
                "管程设计压力 bar": c.get("design", {}).get("tube", {}).get("p_design_bar"),
                "壳程设计压力 bar": c.get("design", {}).get("shell", {}).get("p_design_bar"),
                "管程设计温度 ℃": c.get("design", {}).get("tube", {}).get("t_design_c"),
                "壳程设计温度 ℃": c.get("design", {}).get("shell", {}).get("t_design_c"),
                "管程允许压降 bar": c.get("design", {}).get("tube", {}).get("dp_allow_bar"),
                "壳程允许压降 bar": c.get("design", {}).get("shell", {}).get("dp_allow_bar"),
                "管程污垢 m²·K/W": c.get("fouling", {}).get("tube"),
                "壳程污垢 m²·K/W": c.get("fouling", {}).get("shell"),
                "管程介质": tube_side.get("stream"),
                "壳程介质": shell_side.get("stream"),
                "公用工程": (c.get("utility") or {}).get("source") or "无",
                "公用工程进口 ℃": (c.get("utility") or {}).get("t_in_c"),
                "公用工程出口 ℃": (c.get("utility") or {}).get("t_out_c"),
                "管程数": stored.get("geom_tube_passes"),
                "管长 mm": stored.get("geom_tube_length_mm"),
                "管外径 mm": stored.get("geom_tube_od_mm"),
                "壳体内径 mm": stored.get("geom_shell_id_mm"),
                "折流板间距 mm": stored.get("geom_baffle_spacing_mm"),
                "型号": row.get("model_code", {}).get("code")
                or row.get("model_code", {}).get("code_partial"),
                "判定": c.get("verdict"),
                "备注": "未做 EDR rating;空值 = 未取得,不是 0",
            }
        )
    return out
