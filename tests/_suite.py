"""用例清单:显示名 → 用例函数。

`tools/selftest.py` 按这份清单跑(不装 pytest 也能跑);
pytest 直接按 `tests/test_*.py` 收集,两边的用例是同一批。

显示名沿用迁移前 `tools/selftest.py` 里那套说法,免得日志和文档对不上。
"""

from __future__ import annotations

from tests import (
    test_api,
    test_aspen,
    test_balance,
    test_calc_manuals_consistency,
    test_heat,
    test_hx_l2,
    test_judge,
    test_pipe,
    test_pump,
    test_pump_line,
    test_report,
    test_store,
    test_tools,
)

CASES: list[tuple[str, object]] = [
    ("判定规则 judge()", test_judge.test_judge),
    ("空模型 mark_empty()", test_judge.test_empty),
    ("焓平衡 balance.compute()", test_balance.test_balance),
    ("物料衡算 mass_balance", test_balance.test_mass_balance),
    ("摩尔守恒 mole_balance", test_balance.test_mole_balance),
    ("能量衡算 energy_report", test_balance.test_energy_report),
    ("物料平衡宽表 stream_table", test_report.test_stream_table),
    ("衡算报告 report_export", test_report.test_report_export),
    ("衡算报告-勾选设备", test_report.test_report_picked),
    ("报错波及范围 impact.build()", test_report.test_impact),
    ("管路选型 design.pipe", test_pipe.test_pipe),
    ("2026 工程校核 §17", test_pipe.test_pipe_2026_rules),
    ("真空代号 L 与布尔解耦", test_pipe.test_pipe_vacuum_code_and_wall),
    ("人工覆盖贯通导出与 V3", test_pipe.test_pipe_run_overrides_export_and_validate),
    ("统一壁厚口径", test_pipe.test_design_wall_contract),
    ("第三轮精细化 §19", test_pipe.test_pipe_third_round),
    ("收尾: DN1000/R1E/缺值/外压", test_pipe.test_pipe_followup_guards),
    ("管路编号等级号占位", test_pipe.test_pipe_tag_grade_placeholder),
    ("R1E 缺许用应力文案按材质", test_pipe.test_pipe_r1e_missing_reason_by_material),
    ("管路边界: DN1000 与人工零/负值", test_pipe.test_pipe_boundary_regressions),
    ("大口径 Ia 壁厚序列", test_pipe.test_pipe_welded_wall_series),
    ("选型校验 validate() V1–V15", test_pipe.test_pipe_checks),
    ("标准表补录说明 setup_guide()", test_pipe.test_pipe_setup),
    ("标准表导入 import_standards()", test_pipe.test_std_import),
    ("标准表模板与填写提示词", test_pipe.test_std_template),
    ("牌号范围与材质类别", test_pipe.test_material_scope),
    ("管道等级表 piping-class", test_pipe.test_piping_class),
    ("标准表版本化", test_pipe.test_std_versioning),
    ("管路阻力 Re / λ / Σhf", test_pipe.test_hydraulics),
    ("校核备注温度提示去重", test_pipe.test_check_remark_no_duplicate_temp_notes),
    ("管材下拉按母材类别分组", test_pipe.test_grade_groups_and_csv_bom),
    ("内置示例库 install_sample()", test_tools.test_install_sample),
    ("设备家族 block_family", test_tools.test_block_family),
    ("塔剖面诊断 column_diag", test_tools.test_column_diag),
    ("探针离线冒烟 probe_*", test_tools.test_probe_smoke),
    ("泵口径 design.pump", test_pump.test_pump),
    ("泵 p_v 读取", test_pump.test_pump_pv_read),
    ("设备表泵列 blocks", test_pump.test_block_columns),
    ("设备进出温度", test_pump.test_side_temperatures),
    ("分类导出 excel", test_pump.test_family_export),
    ("选泵提示词 pump_prompt", test_pump.test_pump_prompt),
    ("泵 p_v 存储", test_pump.test_pump_pv_store),
    ("泵 p_v 沿用", test_pump.test_pump_pv_inherit),
    ("泵导出:两相 NPSH 一致", test_pump.test_pump_export_two_phase_npsh),
    ("单位表 units", test_aspen.test_units),
    ("边界夹具 fixtures", test_aspen.test_fixtures),
    ("组成整条替换 stream_components", test_aspen.test_composition_replace),
    ("组分字典 CAS + 中文名", test_aspen.test_component_dict),
    ("COM 夹具回放 read_case", test_aspen.test_reader_fixture),
    ("COM 夹具回放(真机采集)", test_aspen.test_reader_fixture_real),
    ("出口一致性 exports", test_report.test_export_consistency),
    ("同源校验 guard", test_api.test_guard),
    ("HTTP 入口 body / 静态路径", test_api.test_http_entry),
    ("路由表与分发", test_api.test_routes),
    ("模型身份与选型台账", test_store.test_model_identity_and_selection),
    ("同模型旧版本兜底", test_store.test_model_identity_fallback),
    ("设计参数来源透传", test_store.test_pump_design_source_passthrough),
    ("λ 口径持久化与校验", test_store.test_lambda_method),
    ("泵两侧管段计算与存储", test_pump_line.test_pump_line_formula),
    ("选型扬程十进制边界", test_pump_line.test_selection_head_decimal_boundaries),
    ("泵管段缺一侧", test_pump_line.test_pump_line_missing_side),
    ("泵两侧流股待指定", test_pump_line.test_pump_line_ambiguous_and_pick),
    ("管段记录乐观锁", test_pump_line.test_pump_line_store),
    ("台账用修正 NPSH", test_pump_line.test_pump_line_selection_corrected),
    ("泵两侧:两相与反向", test_pump_line.test_pump_line_two_phase_and_reverse),
    ("泵两侧:λ 过渡区", test_pump_line.test_pump_line_lambda_transition),
    ("泵两侧:Σζ 缺失与 λ 上限", test_pump_line.test_pump_line_zeta_and_lambda_bounds),
    ("泵人工 DN 壁厚与缺项合并", test_pump_line.test_pump_line_custom_wall_and_missing),
    ("泵管段密度非正/非有限", test_pump_line.test_pump_line_density_positive_finite),
    ("电机分段裕量(API 610)", test_pump.test_pump_motor_margin_segments),
    ("建议电机设计扬程优先", test_pump_line.test_pump_line_motor_design_first_and_fallback),
    ("管道页参数校验", test_pump_line.test_pipe_options_validate),
    ("设备表共用一次全表选型", test_pump_line.test_pump_line_context_shared_table),
    ("管段记录 HTTP 往返", test_pump_line.test_line_http_roundtrip),
    ("换热器 L1 单位与 LMTD", test_heat.test_heat_units),
    ("换热物性节点与相态", test_heat.test_heat_prop_nodes),
    ("Heater 空侧工艺流股", test_heat.test_heat_heater_side_streams),
    ("无流股侧不算缺物性", test_heat.test_heat_streamless_side_not_missing),
    ("HEAT 热流股不算工艺侧", test_heat.test_heat_heat_stream_filter),
    ("换热器 LMTD 边界", test_heat.test_heat_lmtd),
    ("换热器设计条件", test_heat.test_heat_design_conditions),
    ("HeatX 工况反算", test_heat.test_heat_heatx_case),
    ("换热器边界拦截", test_heat.test_heat_blockers),
    ("公用工程等温相变", test_heat.test_heat_utility_isothermal),
    ("管壳程分配规则", test_heat.test_heat_tube_fluid_rules),
    ("GB/T 151 型号编码", test_heat.test_heat_model_code),
    ("换热器配置与存储", test_heat.test_heat_config_and_store),
    ("换热器拼行与 EDR", test_heat.test_heat_assemble_and_edr),
    ("换热器双通道归类", test_heat.test_heat_channels),
    ("Heater 自动补 U", test_heat.test_heat_auto_u_heater),
    ("零负荷 Heater 过滤", test_heat.test_heat_trivial_filter),
    ("换热器导出与配置恢复", test_heat.test_heat_exports_and_config_reset),
    ("L2 物性单位换算", test_hx_l2.test_hx_l2_units),
    ("L2 物性路径与扫描", test_hx_l2.test_hx_l2_read_props),
    ("L2 cp 由 Q/m/ΔT 反推", test_hx_l2.test_hx_l2_cp_fallback),
    ("L2 标准表解析校验", test_hx_l2.test_hx_l2_standards_parse),
    ("L2 标准表状态与导入", test_hx_l2.test_hx_l2_standards_state),
    ("L2 几何与水力", test_hx_l2.test_hx_l2_geometry),
    ("L2 五态判定", test_hx_l2.test_hx_l2_rating_states),
    ("L2 缺表降级与三页签导出", test_hx_l2.test_hx_l2_degrade_and_export),
    ("L2 换热器详情接口", test_hx_l2.test_hx_l2_api_detail),
    (
        "计算说明书常量与引用一致",
        test_calc_manuals_consistency.test_calc_manuals_consistency,
    ),
]


def verify_coverage() -> list[str]:
    """核对清单没漏用例、也没写错名字。

    为什么要这个
    ------------
    `CASES` 是手工清单,而 pytest 是直接扫 `tests/test_*.py` 收用例的 —— 两边各有一份
    "有哪些用例"的事实。新增用例时忘了登记进 `CASES`,`python -m tools.selftest`
    就会**少跑一条**而且一声不响(退出码还是 0)。这里把两份事实对齐,漏了当场报。

    返回问题描述列表(空 = 一致)。
    """
    import inspect

    problems: list[str] = []
    listed = {fn for _name, fn in CASES}
    for module in (
        test_api,
        test_aspen,
        test_balance,
        test_calc_manuals_consistency,
        test_heat,
        test_hx_l2,
        test_judge,
        test_pipe,
        test_pump,
        test_pump_line,
        test_report,
        test_store,
        test_tools,
    ):
        for name, fn in vars(module).items():
            if not name.startswith("test_") or not inspect.isfunction(fn):
                continue
            if fn not in listed:
                problems.append(f"{module.__name__}.{name} 没登记进 CASES")
    for name, fn in CASES:
        if not callable(fn):
            problems.append(f"CASES 里的 {name} 不可调用")
            continue
        owner = getattr(fn, "__module__", "")
        if owner not in {
            m.__name__
            for m in (
                test_api,
                test_aspen,
                test_balance,
                test_calc_manuals_consistency,
                test_heat,
                test_hx_l2,
                test_judge,
                test_pipe,
                test_pump,
                test_pump_line,
                test_report,
                test_store,
                test_tools,
            )
        }:
            problems.append(f"CASES 里的 {name} 指向别的模块({owner})")
    return problems
