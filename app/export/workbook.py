"""整本 Excel 工作簿(概览 / 流股 / 设备 / 泵 / 连接 / 塔剖面 / 管道计算书)。

与 `family_xlsx`(设备页只导当前分类)是同一套字段口径,两张表不能各写一份。
拆成纯函数,自测就能在内存里生成并逐字段比对,不用起 HTTP 服务。

Raises:
    LookupError: 没有这个工况。
    ImportError: 环境里没有 openpyxl。
"""

from __future__ import annotations

import io
import json

from app.store import db


def build_workbook(conn, case_id: int) -> bytes:
    """按工况生成 Excel 工作簿字节(概览 / 流股 / 设备 / 泵 / 连接 / 塔剖面 / 管道计算书)。

    拆成模块函数,自测就能在内存里生成并逐字段比对,不用起 HTTP 服务。

    Raises:
        LookupError: 没有这个工况。
        ImportError: 环境里没有 openpyxl。
    """
    import openpyxl
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    case = db.get_case(conn, case_id)
    if case is None:
        raise LookupError(case_id)

    streams = db.read_streams(conn, case_id)
    blocks = db.read_blocks(conn, case_id)
    columns = db.list_columns(conn, case_id)
    connections = db.read_connections(conn, case_id)

    status_text = {0: "正常", 1: "错误", 2: "告警"}
    balance_text = {"ok": "通过", "bad": "超差", "no_data": "无法校验", "skip": "不校验"}

    wb = openpyxl.Workbook()

    def fill_sheet(ws, headers, rows, widths=None):
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center")
        for row in rows:
            ws.append(list(row))
        ws.freeze_panes = "A2"
        if widths:
            for i, w in enumerate(widths, 1):
                ws.column_dimensions[get_column_letter(i)].width = w

    # --- 概要 ---
    ws = wb.active
    ws.title = "概要"
    reasons = json.loads(case["reasons_json"] or "[]")
    summary = [
        ("模型", case["model_path"]),
        ("读取时间", case["read_at"]),
        ("运行 ID", case["run_id"]),
        ("运行时间", case["run_date"]),
        ("Aspen 版本", case["aspen_version"]),
        (
            "收敛判定",
            {"ok": "收敛", "warning": "有告警", "error": "有错误"}.get(
                case["severity"], case["severity"]
            ),
        ),
        ("是否可用于统计对比", "是" if case["converged"] else "否"),
        ("流股数", case["stream_count"]),
        ("设备数", case["block_count"]),
        ("连接关系数", len(connections)),
        (
            "焓平衡 通过/超差/无法校验/不校验",
            "/".join(
                str(sum(1 for b in blocks if b["balance_state"] == st))
                for st in ("ok", "bad", "no_data", "skip")
            ),
        ),
        ("结论", "\n".join(f"- {r}" for r in reasons) or "无异常"),
    ]
    for key, value in summary:
        ws.append([key, value])
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=1):
        row[0].font = Font(bold=True)
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 90
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=2, max_col=2):
        row[0].alignment = Alignment(wrap_text=True, vertical="top")

    # --- 流股 ---
    ws = wb.create_sheet("流股")
    fill_sheet(
        ws,
        [
            "流股",
            "温度 ℃",
            "压力 bar",
            "质量流量 kg/h",
            "摩尔流量 kmol/h",
            "汽化率",
            "体积流量 m³/h",
            "质量焓 kJ/kg",
            "摩尔焓 kJ/kmol",
            "能量流 kW",
        ],
        [
            (
                s["name"],
                s["temperature_c"],
                s["pressure_bar"],
                s["mass_flow_kgh"],
                s["mole_flow_kmolh"],
                s["vap_frac"],
                s["vol_flow_m3h"],
                s["enthalpy_kjkg"],
                s["enthalpy_kjkmol"],
                s["enthalpy_flow_kw"],
            )
            for s in streams
        ],
        [18, 12, 12, 16, 18, 10, 16, 16, 18, 16],
    )

    # --- 设备 ---
    ws = wb.create_sheet("设备")
    fill_sheet(
        ws,
        [
            "设备",
            "类型",
            "状态",
            "热负荷 kW",
            "功率 kW",
            "换热面积 m²",
            "理论级数",
            "顶温 ℃",
            "底温 ℃",
            "ΔH kW",
            "参照 kW",
            "焓平衡",
            "平衡说明",
            "提示",
            "进口 ℃",
            "出口 ℃",
            "出口汽化率",
            "冷凝器 kW",
            "再沸器 kW",
        ],
        [
            (
                b["name"],
                b["type"],
                status_text.get(b["status"], ""),
                b["duty_kw"],
                b["power_kw"],
                b["area_m2"],
                b["nstage"],
                b["top_temp_c"],
                b["bottom_temp_c"],
                b["dh_kw"],
                b["ref_kw"],
                balance_text.get(b["balance_state"], ""),
                b["balance_note"],
                b["message"],
                b["inlet_temp_c"],
                b["outlet_temp_c"],
                b["outlet_vap_frac"],
                b["cond_duty_kw"],
                b["reb_duty_kw"],
            )
            for b in blocks
        ],
        [14, 12, 8, 14, 12, 14, 10, 12, 12, 14, 14, 10, 46, 60, 10, 10, 12, 14, 14],
    )

    # --- 泵参数(只有 Pump 块有值;老工况这些列是空的,不是 0)---
    from app.design import block_family as _bf

    # 位号 K 开头的透平在 Aspen 里也是 Pump 类型,但不算泵(见 block_family 注释)
    pumps = [b for b in blocks if _bf.family_of_block(b) == "pump"]
    if pumps:
        from app.design import pump as pump_rules

        def pump_npsh(row):
            # 口径与页面一致:p_v 只认用户填的值,程序不估算
            return pump_rules.npsh_available_m(row["p_in_bar"], row["pv_bar"], row["rho_kgm3"])

        ws = wb.create_sheet("泵")
        fill_sheet(
            ws,
            [
                "设备",
                "状态",
                "流量 m³/h",
                "扬程 m",
                "效率 %",
                "轴功率 kW",
                "建议电机 kW",
                "进口压力 bar",
                "出口压力 bar",
                "压升 bar",
                "密度 kg/m³",
                "粘度 cP",
                "进口汽化率",
                "p_v bar",
                "NPSHₐ m",
                "提示",
            ],
            [
                (
                    b["name"],
                    status_text.get(b["status"], ""),
                    b["flow_m3h"],
                    b["head_m"],
                    None if b["pump_eff"] is None else round(b["pump_eff"] * 100, 1),
                    b["power_kw"],
                    b["motor_kw"],
                    b["p_in_bar"],
                    b["p_out_bar"],
                    b["dp_bar"],
                    b["rho_kgm3"],
                    b["mu_cp"],
                    b["pump_vap_frac"],
                    b["pv_bar"],
                    pump_npsh(b),
                    b["message"],
                )
                for b in pumps
            ],
            [14, 8, 12, 10, 10, 12, 14, 14, 14, 12, 12, 10, 12, 10, 12, 50],
        )

    # --- 连接关系 ---
    ws = wb.create_sheet("连接")
    fill_sheet(
        ws,
        ["设备", "方向", "流股", "分侧", "端口", "进料板", "来源"],
        [
            (
                c["block"],
                "进" if c["direction"] == "in" else "出",
                c["stream"],
                c["side"] or "",
                c["port"] or "",
                c["stage"],
                c["source"] or "",
            )
            for c in connections
        ],
        [14, 8, 18, 10, 10, 10, 14],
    )

    # --- 塔剖面 ---
    ws = wb.create_sheet("塔剖面")
    stage_rows = []
    for col in columns:
        for st in db.read_column_stages(conn, case_id, col["block"]):
            stage_rows.append(
                (
                    col["block"],
                    st["stage"],
                    st["temperature_c"],
                    st["pressure_bar"],
                    st["liq_flow"],
                    st["vap_flow"],
                    st["duty_kw"],
                )
            )
    fill_sheet(
        ws,
        ["塔", "级", "温度 ℃", "压力 bar", "液流量 kmol/h", "气流量 kmol/h", "热负荷 kW"],
        stage_rows,
        [12, 8, 12, 12, 16, 16, 14],
    )

    # --- 管道(管路选型:实时算,不写库)---
    # 标准表没录全时不硬造表,只写一张说明页
    try:
        from app.design import pipe as pipe_calc

        pres = pipe_calc.build(
            [dict(s) for s in streams], [dict(b) for b in blocks], [dict(c) for c in connections]
        )
        pchecks = pipe_calc.validate(pres["rows"])
        hit_by_stream: dict[str, list[str]] = {}
        for f in pchecks["findings"]:
            hit_by_stream.setdefault(f["stream"], []).append(f["code"])

        # 管道选型一览表不放进工作簿:那 9 列是版面表(列宽 / 宋体五号 / 跨页表头),
        # 单独出 Word 才贴得进报告(见 app/export/pipe_schedule.py 与「管道」页的「导出 Word」)。
        # 工作簿这边只留审计用的「管道计算书」和未选型清单。
        # 未选型的流股单独一张表:混在选型表里会破坏那 9 列的可粘贴性
        if pres["missing"]:
            ws2 = wb.create_sheet("未选型流股")
            fill_sheet(
                ws2,
                ["流股", "原因"],
                [(m["stream"], m["reason"]) for m in pres["missing"]],
                [16, 78],
            )

        ws = wb.create_sheet("管道计算书")
        fill_sheet(
            ws,
            [
                "流股",
                "相态",
                "介质代号",
                "流速 m/s",
                "计算内径 mm",
                "外径 mm",
                "设计压力 MPa",
                "表压 MPa",
                "设计温度 ℃",
                "许用应力 MPa",
                "计算壁厚 mm",
                "腐蚀裕量 mm",
                "所需壁厚 mm",
                "最小壁厚 mm",
                "选定壁厚 mm",
                "负偏差 mm",
                "壁厚是否按下限",
                "等级号",
                "校验",
                "备注",
            ],
            [
                (
                    r["stream"],
                    r["phase"],
                    r["medium_code"],
                    r["velocity_ms"],
                    r["inner_d_mm"],
                    r["od_mm"],
                    r["design_pressure_mpa"],
                    r["gauge_pressure_mpa"],
                    r["design_temp_c"],
                    r["stress_mpa"],
                    r["t_calc_mm"],
                    r["c2_mm"],
                    r["t_required_mm"],
                    r.get("min_wall_mm"),
                    r["wall_mm"],
                    (
                        pipe_calc.pipe_data.negative_deviation(r["od_mm"], r["wall_mm"])
                        if r["wall_mm"]
                        else None
                    ),
                    "是" if r.get("wall_floored") else "",
                    r["grade_code"],
                    " ".join(hit_by_stream.get(r["stream"], [])),
                    "; ".join(r["warnings"]),
                )
                for r in pres["rows"]
            ],
            [16, 10, 12, 12, 14, 12, 14, 12, 14, 14, 14, 14, 14, 14, 14, 12, 14, 12, 10, 46],
        )
    except pipe_calc.TablesNotReady as exc:  # 标准表没录:写明原因与补录办法,不造数
        ws = wb.create_sheet("管道")
        ws.append(["管路选型暂时不可用"])
        ws.append([str(exc)])
        guide = pipe_calc.pipe_data.setup_guide()
        ws.append([])
        ws.append(["为什么不在安装包里:", guide["why"]])
        ws.append(["放进哪个目录:", guide["dir"]])
        ws.append([])
        ws.append(["要准备的文件"])
        for item in guide["files"]:
            ws.append([item["name"], item["from"], item["columns"]])
        ws.append([])
        ws.append(["步骤"])
        for i, step in enumerate(guide["steps"], 1):
            ws.append([i, step])
        ws.append([])
        ws.append(["详细页码与分页结构:", guide["doc"]])
    except Exception as exc:  # 选型出问题不该拖垮整份 Excel
        ws = wb.create_sheet("管道")
        ws.append(["管路选型出错"])
        ws.append([f"{type(exc).__name__}: {exc}"])

    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()
